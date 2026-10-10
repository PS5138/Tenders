"""Specification requirements: the ``extract_requirements`` job (plan: Specification
requirements).

Once a tender's documents are in, the model reads every one of them whose ``ingest_status`` is
``ready`` (the question pack included) and lists the specification requirements they state:
obligations the supplier must meet or state compliance with, not the long-answer quality
questions that are already question cards, not pricing and not instructions to bidders.

1. Windows. Each document's sections are grouped into windows of about
   ``pair_extraction_window_tokens`` with one section of overlap (``app.ingest.extract
   .build_windows``), each section labelled with its id. One MAIN-model call per window.
2. Trace. Each requirement's copied text is located with ``find_span`` in the section the model
   named (then in the window's other sections); located requirements store the section's own
   text at the offsets, so the source pane highlights what the buyer wrote. A requirement whose
   named section is in the window but whose text is not found keeps the model's copy with null
   offsets; one whose section is unknown and whose text is found nowhere is dropped.
3. Identity. A requirement's ``key`` is its located span (``section:start-end``) or, when not
   located, a hash of its normalised text in its section; duplicates from the window overlap
   collapse on it. A re-run upserts on the key, then on an overlapping span in the same
   section, so extracted fields are refreshed while the human fields (``compliance_class``,
   ``compliant_by``, ``comment``, ``owner``, ``rated_by``, ``rated_at``) are never written.
   Rows a person has worked on are never deleted; rows nobody has touched that this run no
   longer finds are removed, but only from sections a successful window covered, so a failed
   window never deletes anything. ``order_index`` is renumbered in document order at the end.
4. Suggestions. Every requirement still without a ``compliance_class`` gets an AI suggestion
   (``app.retrieve.requirements``).

The job is enqueued by ``request_requirements_scan``: when a parse-only tender document reaches
``ready``, at the end of ``extract_questions`` and by ``POST /tenders/{id}/requirements/rescan``.
One queued run per tender is enough, because it reads every document when it starts.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import enums as e
from app.db.models import Document, DocumentSection, Job, Organisation, Requirement, Tender
from app.errors import format_error
from app.ingest.extract import build_windows
from app.ingest.normalise import find_span, normalise
from app.jobs import enqueue, register, set_progress
from app.llm import get_llm, load_prompt, prompt_version

logger = logging.getLogger(__name__)

PROMPT_NAME = "extract_requirements"
MAX_TOPICS = 3
_REF_MAX = 64
# Two spans in one section are the same requirement when they overlap by this share of their
# union: a re-run whose copy differs by a word still lands on the same row.
_SPAN_OVERLAP_MIN = 0.6

_PRIORITY_WORDS: dict[str, str] = {
    **dict.fromkeys(
        ("must", "m", "mandatory", "shall", "essential", "required", "minimum", "pass/fail"),
        e.RequirementPriority.MUST.value,
    ),
    **dict.fromkeys(
        ("should", "s", "desirable", "preferred", "important", "recommended"),
        e.RequirementPriority.SHOULD.value,
    ),
    **dict.fromkeys(
        ("could", "c", "may", "optional", "nice to have"),
        e.RequirementPriority.COULD.value,
    ),
}


# --- LLM output schema ------------------------------------------------------------------------


class ExtractedRequirement(BaseModel):
    section_id: str = Field(default="", description="The labelled id of the section it is in.")
    ref: str | None = Field(default=None, description="The buyer's reference, as written.")
    text: str = Field(default="", description="The requirement copied verbatim.")
    priority: str | None = Field(default=None, description="must, should, could or null.")
    topics: list[str] = Field(default_factory=list, description="Zero to three taxonomy ids.")


class RequirementExtractionOutput(BaseModel):
    requirements: list[ExtractedRequirement] = Field(default_factory=list)


# --- Prompt -------------------------------------------------------------------------------------


def _render_section(section: DocumentSection) -> str:
    path = " > ".join(section.heading_path) if section.heading_path else "(none)"
    location = f" | row {section.cell_ref}" if section.cell_ref else ""
    return (
        f"<<< section id={section.id} | order {section.order_index} | "
        f"heading path: {path}{location} >>>\n{section.text}"
    )


def build_window_prompt(
    document: Document, window: Sequence[DocumentSection], taxonomy: Sequence[str]
) -> str:
    rendered = "\n\n".join(_render_section(section) for section in window)
    return (
        f"Document: {document.filename}\n"
        f"Tender document kind: {document.tender_doc_kind or 'other'}\n\n"
        f"Topic taxonomy: {', '.join(f'`{topic}`' for topic in taxonomy)}\n\n"
        f"Sections in this window ({len(window)}), in document order. "
        f"Use the ids exactly as labelled.\n\n{rendered}"
    )


def extract_window(
    document: Document, window: Sequence[DocumentSection], taxonomy: Sequence[str]
) -> RequirementExtractionOutput:
    """One schema-validated MAIN-model call over the window."""
    return get_llm().parse(
        PROMPT_NAME,
        model=get_settings().model_main,
        system=load_prompt(PROMPT_NAME),
        user=build_window_prompt(document, window, taxonomy),
        output_model=RequirementExtractionOutput,
    )


# --- Normalisation of what the model returned ---------------------------------------------------


def normalise_priority(raw: str | None) -> str | None:
    """Map the buyer's wording to must / should / could; null when it says nothing usable."""
    value = re.sub(r"\s+", " ", (raw or "").strip().lower()).strip(" .")
    if not value:
        return None
    if value in _PRIORITY_WORDS:
        return _PRIORITY_WORDS[value]
    for word, priority in _PRIORITY_WORDS.items():
        if len(word) > 1 and re.search(rf"\b{re.escape(word)}\b", value):
            return priority
    return None


def clean_topics(topics: Sequence[str], taxonomy: Sequence[str]) -> list[str]:
    allowed = set(taxonomy)
    seen: list[str] = []
    for topic in topics:
        value = (topic or "").strip().lower()
        if value in allowed and value not in seen:
            seen.append(value)
        if len(seen) == MAX_TOPICS:
            break
    return seen


def requirement_key(section_id: uuid.UUID, start: int | None, end: int | None, text: str) -> str:
    """The stable identity a re-run upserts on."""
    if start is not None and end is not None:
        return f"{section_id}:{start}-{end}"
    digest = hashlib.sha1(normalise(text)[0].encode("utf-8")).hexdigest()[:20]
    return f"{section_id}:t:{digest}"


@dataclass
class LocatedRequirement:
    document_id: uuid.UUID
    section_id: uuid.UUID
    start: int | None
    end: int | None
    ref: str | None
    text: str
    priority: str | None
    topics: list[str]

    @property
    def key(self) -> str:
        return requirement_key(self.section_id, self.start, self.end, self.text)


def locate_requirement(
    item: ExtractedRequirement,
    window: Sequence[DocumentSection],
    taxonomy: Sequence[str],
) -> LocatedRequirement | None:
    """Find the requirement's text in the section the model named, then in the window's other
    sections. See the module docstring for what happens when it is not found."""
    copied = (item.text or "").strip()
    if not copied:
        return None
    by_id = {str(section.id): section for section in window}
    named = by_id.get((item.section_id or "").strip())
    ordered = ([named] if named is not None else []) + [s for s in window if s is not named]
    ref = (item.ref or "").strip()[:_REF_MAX] or None
    priority = normalise_priority(item.priority)
    topics = clean_topics(item.topics, taxonomy)
    for section in ordered:
        match = find_span(copied, section.text)
        if match is None:
            continue
        return LocatedRequirement(
            document_id=section.document_id,
            section_id=section.id,
            start=match.start,
            end=match.end,
            ref=ref,
            text=section.text[match.start : match.end],
            priority=priority,
            topics=topics,
        )
    if named is None:
        logger.info("extract_requirements: dropping a requirement found in no labelled section")
        return None
    return LocatedRequirement(
        document_id=named.document_id,
        section_id=named.id,
        start=None,
        end=None,
        ref=ref,
        text=copied,
        priority=priority,
        topics=topics,
    )


def locate_all(
    output: RequirementExtractionOutput,
    window: Sequence[DocumentSection],
    taxonomy: Sequence[str],
) -> list[LocatedRequirement]:
    """Locate every requirement in the window's output, collapsing duplicates on the key."""
    located: dict[str, LocatedRequirement] = {}
    for item in output.requirements:
        requirement = locate_requirement(item, window, taxonomy)
        if requirement is not None:
            located.setdefault(requirement.key, requirement)
    return list(located.values())


# --- Persistence --------------------------------------------------------------------------------


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    union = max(a_end, b_end) - min(a_start, b_start)
    if union <= 0:
        return 0.0
    return max(0, min(a_end, b_end) - max(a_start, b_start)) / union


class RequirementUpserter:
    """Upsert a run's requirements onto the tender's existing rows without touching the human
    fields; remember which rows this run found."""

    def __init__(self, session: Session, tender: Tender) -> None:
        self.session = session
        self.tender = tender
        rows = session.scalars(select(Requirement).where(Requirement.tender_id == tender.id)).all()
        self.rows: list[Requirement] = list(rows)
        self.by_key: dict[str, Requirement] = {row.key: row for row in self.rows}
        self.matched: set[uuid.UUID] = set()
        self.position = 0
        self.created = 0
        self.updated = 0

    def _overlapping(self, located: LocatedRequirement) -> Requirement | None:
        if located.start is None or located.end is None:
            return None
        best: tuple[float, Requirement] | None = None
        for row in self.rows:
            if row.id in self.matched or row.section_id != located.section_id:
                continue
            if row.start is None or row.end is None:
                continue
            share = _overlap(row.start, row.end, located.start, located.end)
            if share >= _SPAN_OVERLAP_MIN and (best is None or share > best[0]):
                best = (share, row)
        return best[1] if best else None

    def upsert(self, located: LocatedRequirement) -> Requirement | None:
        """Returns the row, or None when this run already wrote the same requirement (a
        duplicate from the window overlap)."""
        key = located.key
        row = self.by_key.get(key)
        if row is not None and row.id in self.matched:
            return None
        if row is None:
            row = self._overlapping(located)
        if row is None:
            row = Requirement(
                id=uuid.uuid4(),
                org_id=self.tender.org_id,
                tender_id=self.tender.id,
                suggestion={},
            )
            self.session.add(row)
            self.rows.append(row)
            self.created += 1
        else:
            self.updated += 1
            if row.key != key:
                self.by_key.pop(row.key, None)
        row.document_id = located.document_id
        row.section_id = located.section_id
        row.start = located.start
        row.end = located.end
        row.key = key
        row.ref = located.ref
        row.text = located.text
        row.priority = located.priority
        row.topics = list(located.topics)
        row.order_index = self.position
        self.position += 1
        self.by_key[key] = row
        self.matched.add(row.id)
        return row

    def upsert_many(self, located: Sequence[LocatedRequirement]) -> int:
        written = sum(1 for item in located if self.upsert(item) is not None)
        self.session.flush()
        return written

    def remove_unmatched(self, covered_sections: set[uuid.UUID]) -> int:
        """Delete the rows this run did not find, in sections a successful window covered,
        that nobody has worked on. The human-field conditions are in the statement itself, so
        a rating saved while the job ran survives."""
        candidates = [
            row.id
            for row in self.rows
            if row.id not in self.matched and row.section_id in covered_sections
        ]
        if not candidates:
            return 0
        untouched = and_(
            Requirement.compliance_class.is_(None),
            Requirement.compliant_by.is_(None),
            Requirement.owner.is_(None),
            Requirement.rated_at.is_(None),
            or_(Requirement.comment.is_(None), Requirement.comment == ""),
        )
        result = self.session.execute(
            delete(Requirement)
            .where(Requirement.id.in_(candidates), untouched)
            .execution_options(synchronize_session=False)
        )
        self.session.expire_all()
        return int(result.rowcount or 0)


def renumber(session: Session, tender_id: uuid.UUID) -> None:
    """``order_index`` in document order: document upload order, then section order, then the
    span's start, then the order the run found them in."""
    rows = session.execute(
        select(Requirement)
        .join(Document, Document.id == Requirement.document_id)
        .join(DocumentSection, DocumentSection.id == Requirement.section_id)
        .where(Requirement.tender_id == tender_id)
        .order_by(
            Document.created_at,
            Document.id,
            DocumentSection.order_index,
            Requirement.start.is_(None),
            Requirement.start,
            Requirement.order_index,
            Requirement.created_at,
        )
    ).scalars()
    for position, row in enumerate(rows):
        if row.order_index != position:
            row.order_index = position
    session.flush()


# --- Enqueueing ---------------------------------------------------------------------------------


def request_requirements_scan(session: Session, tender: Tender, actor: str) -> Job:
    """Enqueue ``extract_requirements`` for the tender, unless one is already queued (it reads
    every document when it starts, so one is enough). Records the job as the tender's latest
    requirements run. Flushes; the caller commits."""
    queued = session.scalars(
        select(Job)
        .where(
            Job.org_id == tender.org_id,
            Job.kind == e.JobKind.EXTRACT_REQUIREMENTS.value,
            Job.status == e.JobStatus.QUEUED.value,
            Job.payload["tender_id"].astext == str(tender.id),
        )
        .order_by(Job.created_at)
        .limit(1)
    ).first()
    job = queued or enqueue(
        session,
        e.JobKind.EXTRACT_REQUIREMENTS,
        {"tender_id": str(tender.id), "actor": actor},
        org_id=tender.org_id,
    )
    tender.requirements_job_id = job.id
    session.flush()
    return job


# --- The job handler ----------------------------------------------------------------------------


def _taxonomy(session: Session, org_id: uuid.UUID) -> list[str]:
    organisation = session.get(Organisation, org_id)
    if organisation is not None and organisation.topic_taxonomy:
        return list(organisation.topic_taxonomy)
    return list(get_settings().default_topic_taxonomy)


def ready_documents(session: Session, tender_id: uuid.UUID) -> list[Document]:
    return list(
        session.scalars(
            select(Document)
            .where(
                Document.tender_id == tender_id,
                Document.ingest_status == e.IngestStatus.READY.value,
            )
            .order_by(Document.created_at, Document.id)
        ).all()
    )


def _windows(
    session: Session, documents: Sequence[Document]
) -> list[tuple[Document, list[DocumentSection]]]:
    windows: list[tuple[Document, list[DocumentSection]]] = []
    for document in documents:
        sections = session.scalars(
            select(DocumentSection)
            .where(DocumentSection.document_id == document.id)
            .order_by(DocumentSection.order_index)
        ).all()
        with_text = [section for section in sections if (section.text or "").strip()]
        windows.extend((document, window) for window in build_windows(with_text))
    return windows


def _load_tender(session: Session, job: Job) -> Tender:
    try:
        tender_id = uuid.UUID(str((job.payload or {})["tender_id"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("extract_requirements payload needs tender_id") from exc
    tender = session.get(Tender, tender_id)
    if tender is None:
        raise ValueError("extract_requirements: tender no longer exists")
    return tender


@register(e.JobKind.EXTRACT_REQUIREMENTS)
def extract_requirements(session: Session, job: Job) -> None:
    """Handler for the ``extract_requirements`` job. Commits through the job progress
    functions; ``total`` is the number of windows plus, once extraction is done, the number of
    requirements to suggest a class for."""
    from app.retrieve.requirements import run_suggestions

    tender = _load_tender(session, job)
    tender_id = tender.id
    job.results = []
    windows = _windows(session, ready_documents(session, tender_id))
    set_progress(session, job, done=0, total=len(windows))

    taxonomy = _taxonomy(session, tender.org_id)
    upserter = RequirementUpserter(session, tender)
    covered: set[uuid.UUID] = set()
    errors: list[str] = []
    for done, (document, window) in enumerate(windows, start=1):
        result: dict[str, Any] = {
            "item_id": str(window[0].id),
            "document_id": str(document.id),
            "sections": len(window),
            "outcome": "extracted",
            "detail": 0,
        }
        try:
            output = extract_window(document, window, taxonomy)
        except Exception as exc:  # noqa: BLE001 - per-window failures never raise to the job
            logger.exception("extract_requirements: window at %s failed", window[0].id)
            result["outcome"] = "failed"
            result["detail"] = format_error(exc)
            errors.append(result["detail"])
        else:
            result["detail"] = upserter.upsert_many(locate_all(output, window, taxonomy))
            covered.update(section.id for section in window)
        set_progress(session, job, done=done, result=result)

    if windows and len(errors) == len(windows):
        raise RuntimeError("extract_requirements: every window failed; first error: " + errors[0])

    removed = upserter.remove_unmatched(covered)
    renumber(session, tender_id)
    session.commit()
    logger.info(
        "extract_requirements: %d created, %d updated, %d removed on tender %s (prompt %s)",
        upserter.created,
        upserter.updated,
        removed,
        tender_id,
        prompt_version(PROMPT_NAME),
    )

    unrated = list(
        session.scalars(
            select(Requirement)
            .where(Requirement.tender_id == tender_id, Requirement.compliance_class.is_(None))
            .order_by(Requirement.order_index)
        ).all()
    )
    set_progress(session, job, total=len(windows) + len(unrated))
    outcomes = run_suggestions(session, job, unrated, done_offset=len(windows))
    if outcomes and all(outcome.error is not None for outcome in outcomes):
        raise RuntimeError(
            "extract_requirements: every suggestion failed; first error: " + str(outcomes[0].error)
        )


__all__ = [
    "ExtractedRequirement",
    "LocatedRequirement",
    "RequirementExtractionOutput",
    "RequirementUpserter",
    "build_window_prompt",
    "clean_topics",
    "extract_requirements",
    "extract_window",
    "locate_all",
    "locate_requirement",
    "normalise_priority",
    "ready_documents",
    "renumber",
    "request_requirements_scan",
    "requirement_key",
]
