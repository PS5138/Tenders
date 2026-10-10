"""The AI compliance suggestion for a specification requirement (plan: Specification
requirements, step 3).

Each requirement without a human ``compliance_class`` is retrieved against the library exactly
as a question is triaged (draft steps 2 to 4, free text embedded at call time and never stored,
with the requirement's topics as the topic list) and given the most current facts for its
topics (draft step 5). If ``best_vec`` is below ``coverage_floor`` no LLM call is made and
``suggested_class`` stays null with ``label_source = floor``: absence of evidence is not
non-compliance, so the floor never yields Red. Otherwise one FAST-model judgement over the
requirement, the top ``requirement_judgement_top`` candidates and up to
``requirement_judgement_facts`` facts returns ``A``, ``B``, ``C`` or null with a rationale and
evidence quotes. Every quote is verified with ``find_span`` against the cited item's own slice
(for a fact, its statement in its source section) through the same locator as support
verification; a quote that does not verify is dropped, and a suggestion left with no verified
evidence is withdrawn (stored null, with the withdrawn class recorded for audit).

A suggestion never counts until a person accepts it: it is written to ``suggested_class`` and
``suggestion`` only, never to ``compliance_class``.

``run_suggestions`` runs the judgements in a thread pool bounded like coverage triage (one
session per requirement, each thread bound to the job's organisation scope with
``run_in_context``) and reports one result per requirement to the job.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from concurrent.futures import Executor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.enums import LabelSource, SourceType
from app.db.models import Fact, Job, Requirement
from app.db.session import new_session
from app.errors import format_error
from app.generate.verification import SourceRows, locate_source
from app.jobs import set_progress
from app.llm import embed, get_llm, load_prompt, prompt_version
from app.llm.scope import run_in_context
from app.retrieve.coverage import format_candidate
from app.retrieve.facts import select_facts
from app.retrieve.fusion import Candidate
from app.retrieve.search import retrieve
from app.retrieve.triage import pool_workers

logger = logging.getLogger(__name__)

PROMPT_NAME = "requirement_judgement"

NO_EVIDENCE_RATIONALE = (
    "No library material resembles this requirement closely enough to suggest a rating."
)
WITHDRAWN_NOTE = "The suggestion was withdrawn because none of its evidence could be verified."


class EvidenceQuote(BaseModel):
    source: str = Field(default="", description="The label of a candidate (C1) or a fact (F1).")
    quote: str = ""


class RequirementJudgement(BaseModel):
    """The judge's structured output (see ``requirement_judgement.v1.md``)."""

    suggested_class: Literal["A", "B", "C"] | None = None
    rationale: str = ""
    evidence: list[EvidenceQuote] = Field(default_factory=list)


@dataclass
class SuggestionResult:
    suggested_class: str | None
    suggestion: dict[str, Any]


# --- The judgement input --------------------------------------------------------------------


def _format_fact(label: str, fact: Fact) -> str:
    parts = [f"[{label}] id={fact.id} kind={fact.fact_kind}"]
    if fact.fact_key:
        parts.append(f"key={fact.fact_key}")
    parts.append(f"effective {fact.effective_date.isoformat()}")
    if fact.expires_on:
        parts.append(f"expires {fact.expires_on.isoformat()}")
    return " ".join(parts) + f"\nStatement: {fact.statement.strip()}"


def build_judgement_input(
    requirement: Requirement, candidates: Sequence[Candidate], facts: Sequence[Fact]
) -> str:
    """The user message: the requirement, then the labelled candidates and facts."""
    parts = [
        "REQUIREMENT",
        f"Ref: {requirement.ref or '-'}",
        f"Priority: {requirement.priority or 'not stated'}",
        f"Topics: {', '.join(requirement.topics or []) or '-'}",
        requirement.text.strip(),
        "",
        f"CANDIDATES ({len(candidates)})",
    ]
    for position, candidate in enumerate(candidates, start=1):
        parts.append(format_candidate(f"C{position}", candidate))
        parts.append("")
    parts.append(f"FACTS ({len(facts)})")
    for position, fact in enumerate(facts, start=1):
        parts.append(_format_fact(f"F{position}", fact))
        parts.append("")
    parts.append("Return the JSON suggestion.")
    return "\n".join(parts)


def judge_requirement(
    requirement: Requirement, candidates: Sequence[Candidate], facts: Sequence[Fact]
) -> RequirementJudgement:
    """One FAST-model call. Raises ``LLMError`` from the client on failure."""
    settings = get_settings()
    return get_llm().parse(
        PROMPT_NAME,
        model=settings.model_fast,
        system=load_prompt(PROMPT_NAME),
        user=build_judgement_input(requirement, candidates, facts),
        output_model=RequirementJudgement,
        max_tokens=2000,
    )


# --- Evidence verification ------------------------------------------------------------------


def _source_targets(
    candidates: Sequence[Candidate], facts: Sequence[Fact]
) -> dict[str, tuple[str, uuid.UUID]]:
    """Every name the judge may use for a source (its label or its id) -> (type, id)."""
    targets: dict[str, tuple[str, uuid.UUID]] = {}
    for position, candidate in enumerate(candidates, start=1):
        target = (SourceType.KNOWLEDGE_ITEM.value, candidate.item.id)
        targets[f"c{position}"] = target
        targets[str(candidate.item.id)] = target
    for position, fact in enumerate(facts, start=1):
        target = (SourceType.FACT.value, fact.id)
        targets[f"f{position}"] = target
        targets[str(fact.id)] = target
    return targets


def verify_evidence(
    session: Session,
    evidence: Sequence[EvidenceQuote],
    candidates: Sequence[Candidate],
    facts: Sequence[Fact],
) -> list[dict[str, Any]]:
    """Document-source records for the quotes that verify, in the judge's order, each once.

    A quote naming a source that was not shown, or whose span is not found in the cited item's
    slice (or the fact's statement), is dropped."""
    targets = _source_targets(candidates, facts)
    rows = SourceRows(session)
    verified: list[dict[str, Any]] = []
    seen: set[tuple[str, Any, Any]] = set()
    for entry in evidence:
        name = entry.source.strip().strip("[]").lower()
        target = targets.get(name)
        quote = entry.quote.strip()
        if target is None or not quote:
            continue
        source_type, source_id = target
        located = locate_source(
            rows, {"source_type": source_type, "source_id": str(source_id), "quote": quote}
        )
        if not located.found:
            continue
        locator = located.source.get("locator") or {}
        key = (str(source_id), locator.get("start"), locator.get("end"))
        if key in seen:
            continue
        seen.add(key)
        verified.append(located.source)
    return verified


# --- One requirement ------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def suggest_for_requirement(
    session: Session, requirement: Requirement, query_vector: Sequence[float]
) -> SuggestionResult:
    """Suggest a class for one requirement. Reads only; the caller persists the result."""
    settings = get_settings()
    topics = list(requirement.topics or [])
    result = retrieve(
        session,
        requirement.org_id,
        query_text=requirement.text,
        query_vector=query_vector,
        topics=topics,
    )
    detail: dict[str, Any] = {
        "best_vec": round(result.best_vec, 6),
        "coverage_floor": settings.coverage_floor,
        "candidates": result.to_detail(),
    }
    if result.best_vec < settings.coverage_floor:
        detail.update(
            {
                "label_source": LabelSource.FLOOR.value,
                "rationale": NO_EVIDENCE_RATIONALE,
                "evidence": [],
                "model": None,
                "prompt_version": None,
            }
        )
        return SuggestionResult(None, detail)

    top = result.candidates[: settings.requirement_judgement_top]
    facts = select_facts(session, requirement.org_id, top, topics)[
        : settings.requirement_judgement_facts
    ]
    judgement = judge_requirement(requirement, top, facts)
    evidence = verify_evidence(session, judgement.evidence, top, facts)
    suggested = judgement.suggested_class
    rationale = judgement.rationale.strip()
    detail.update(
        {
            "label_source": LabelSource.LLM.value,
            "judged_item_ids": [str(candidate.item.id) for candidate in top],
            "fact_ids": [str(fact.id) for fact in facts],
            "rationale": rationale,
            "evidence": evidence,
            "model": settings.model_fast,
            "prompt_version": prompt_version(PROMPT_NAME),
        }
    )
    if suggested is not None and not evidence:
        detail["withdrawn_class"] = suggested
        detail["rationale"] = f"{rationale} {WITHDRAWN_NOTE}".strip()
        suggested = None
    return SuggestionResult(suggested, _jsonable(detail))


# --- The pool -------------------------------------------------------------------------------


@dataclass
class SuggestionOutcome:
    requirement_id: uuid.UUID
    suggested_class: str | None
    error: str | None = None

    def as_result(self) -> dict[str, Any]:
        """``{item_id, outcome}``: the suggested class, ``none`` for no suggestion, or
        ``failed`` with the error in ``detail``."""
        if self.error is not None:
            return {"item_id": str(self.requirement_id), "outcome": "failed", "detail": self.error}
        return {"item_id": str(self.requirement_id), "outcome": self.suggested_class or "none"}


def _make_executor(max_workers: int) -> Executor:
    return ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="requirements")


def suggest_one(requirement_id: uuid.UUID, query_vector: Sequence[float]) -> SuggestionOutcome:
    """Worker-thread unit: own session, suggest, persist the suggestion, commit."""
    session = new_session()
    try:
        requirement = session.get(Requirement, requirement_id)
        if requirement is None:
            return SuggestionOutcome(requirement_id, None, "requirement no longer exists")
        result = suggest_for_requirement(session, requirement, query_vector)
        requirement.suggested_class = result.suggested_class
        requirement.suggestion = result.suggestion
        session.commit()
        return SuggestionOutcome(requirement_id, result.suggested_class)
    except Exception as exc:  # noqa: BLE001 - recorded per item, never raises to the pool
        logger.exception("requirements: suggestion for %s failed", requirement_id)
        session.rollback()
        return SuggestionOutcome(requirement_id, None, format_error(exc))
    finally:
        session.close()


def run_suggestions(
    session: Session, job: Job, requirements: Sequence[Requirement], *, done_offset: int
) -> list[SuggestionOutcome]:
    """Suggest a class for every requirement given, reporting progress on ``job`` from
    ``done_offset``. The query vectors are embedded in one batched call on the job thread."""
    if not requirements:
        return []
    ids = [requirement.id for requirement in requirements]
    vectors = embed([requirement.text for requirement in requirements])
    settings = get_settings()
    outcomes: list[SuggestionOutcome] = []
    executor = _make_executor(pool_workers(session, settings.triage_concurrency))
    try:
        # Pool threads do not inherit the job's organisation scope (``app.llm.scope``).
        scoped = run_in_context(suggest_one)
        futures = [
            executor.submit(scoped, requirement_id, vector)
            for requirement_id, vector in zip(ids, vectors, strict=True)
        ]
        for done, future in enumerate(as_completed(futures), start=done_offset + 1):
            outcome = future.result()
            outcomes.append(outcome)
            set_progress(session, job, done=done, result=outcome.as_result())
    finally:
        executor.shutdown(wait=True)
    # The worker threads committed on their own sessions; drop stale copies in this one.
    session.expire_all()
    return outcomes


__all__ = [
    "NO_EVIDENCE_RATIONALE",
    "PROMPT_NAME",
    "EvidenceQuote",
    "RequirementJudgement",
    "SuggestionOutcome",
    "SuggestionResult",
    "build_judgement_input",
    "judge_requirement",
    "run_suggestions",
    "suggest_for_requirement",
    "suggest_one",
    "verify_evidence",
]
