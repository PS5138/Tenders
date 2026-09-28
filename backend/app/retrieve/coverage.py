"""Coverage triage for one question (question pack ingestion, step 2).

Retrieve with the stored question embedding (draft steps 2 to 4, no synthesis). Let
``best_vec`` be the highest vector score among the fused, collapsed candidates. If it is below
``coverage_floor`` the question is ``new`` with ``label_source = floor`` and no LLM call is made.
Otherwise one FAST-model judgement over the question and the top ``coverage_judgement_top``
candidates returns ``covered``, ``partial`` or ``new`` with a gap summary
(``label_source = llm``). The floor is a cost gate for obvious misses, not the only route to
``new``.

``coverage_for_question`` reads only and returns the label and the ``coverage_detail`` dict;
the triage job persists both on the question row and commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.enums import Coverage, LabelSource
from app.db.models import Question
from app.llm import get_llm, load_prompt, prompt_version
from app.retrieve.fusion import Candidate
from app.retrieve.query import get_query_vector
from app.retrieve.search import RetrievalResult, retrieve

PROMPT_NAME = "coverage_judgement"

# Characters of each candidate's text shown to the judge; the whole slice is rarely needed.
_CANDIDATE_TEXT_LIMIT = 3000

FLOOR_GAP_SUMMARY = (
    "No library material resembles this question closely enough to draft from; "
    "it needs new material."
)


class CoverageJudgement(BaseModel):
    """The judge's structured output (see ``coverage_judgement.v1.md``)."""

    coverage: Literal["covered", "partial", "new"]
    gap_summary: str = ""
    gaps: list[str] = Field(default_factory=list)


@dataclass
class CoverageResult:
    coverage: str
    detail: dict[str, Any]
    retrieval: RetrievalResult

    @property
    def label_source(self) -> str:
        return str(self.detail["label_source"])


def _truncate(text: str | None, limit: int = _CANDIDATE_TEXT_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_candidate(label: str, candidate: Candidate) -> str:
    item = candidate.item
    lines = [f"[{label}] id={item.id} type={item.item_type} topics={', '.join(item.topics) or '-'}"]
    if item.question_text:
        lines.append(f"Past question: {_truncate(item.question_text)}")
        lines.append(f"Past answer: {_truncate(item.answer_text)}")
    else:
        lines.append(f"Excerpt: {_truncate(item.answer_text)}")
    return "\n".join(lines)


def build_judgement_input(question: Question, candidates: list[Candidate]) -> str:
    """The user message for the judge: the question, its constraints and the candidates."""
    constraints: list[str] = [f"response type: {question.response_type}"]
    if question.word_limit:
        constraints.append(f"word limit: {question.word_limit}")
    if question.mandatory:
        constraints.append("mandatory")
    if question.weighting is not None:
        constraints.append(f"weighting: {question.weighting}")
    parts = [
        "QUESTION",
        f"{question.section} {question.number}".strip(),
        question.text.strip(),
        f"Constraints: {'; '.join(constraints)}",
        f"Topics: {', '.join(question.topics) or '-'}",
        "",
        f"CANDIDATES ({len(candidates)})",
    ]
    for position, candidate in enumerate(candidates, start=1):
        parts.append(format_candidate(f"C{position}", candidate))
        parts.append("")
    parts.append("Return the JSON judgement.")
    return "\n".join(parts)


def judge_coverage(question: Question, candidates: list[Candidate]) -> CoverageJudgement:
    """One FAST-model call. Raises ``LLMError`` from the client on failure."""
    settings = get_settings()
    return get_llm().parse(
        PROMPT_NAME,
        model=settings.model_fast,
        system=load_prompt(PROMPT_NAME),
        user=build_judgement_input(question, candidates),
        output_model=CoverageJudgement,
        max_tokens=2000,
    )


def coverage_for_question(session: Session, question: Question) -> CoverageResult:
    """Triage one question. Reads only; the caller persists ``coverage`` and ``detail``."""
    settings = get_settings()
    query_vector = get_query_vector(session, question=question)
    result = retrieve(
        session,
        question.org_id,
        query_text=question.text,
        query_vector=query_vector,
        topics=list(question.topics or []),
    )
    detail: dict[str, Any] = {
        "best_vec": round(result.best_vec, 6),
        "coverage_floor": settings.coverage_floor,
        "lexical_query": result.lexical_query,
        "candidates": result.to_detail(),
        "lists": {name: [str(item_id) for item_id in ids] for name, ids in result.lists.items()},
    }

    if result.best_vec < settings.coverage_floor:
        detail.update(
            {
                "label_source": LabelSource.FLOOR.value,
                "gap_summary": FLOOR_GAP_SUMMARY,
                "gaps": [],
                "model": None,
                "prompt_version": None,
            }
        )
        return CoverageResult(Coverage.NEW.value, detail, result)

    top = result.candidates[: settings.coverage_judgement_top]
    judgement = judge_coverage(question, top)
    detail.update(
        {
            "label_source": LabelSource.LLM.value,
            "judged_item_ids": [str(candidate.item.id) for candidate in top],
            "gap_summary": judgement.gap_summary.strip(),
            "gaps": [gap.strip() for gap in judgement.gaps if gap.strip()],
            "model": settings.model_fast,
            "prompt_version": prompt_version(PROMPT_NAME),
        }
    )
    return CoverageResult(Coverage(judgement.coverage).value, detail, result)


__all__ = [
    "FLOOR_GAP_SUMMARY",
    "PROMPT_NAME",
    "CoverageJudgement",
    "CoverageResult",
    "build_judgement_input",
    "coverage_for_question",
    "judge_coverage",
]
