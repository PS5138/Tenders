"""Question-pack ingestion: the ``extract_questions`` job (plan: Question pack ingestion, step 1).

Parse the pack through owner A's ``app.ingest.parse`` functions (sections already persisted
by an earlier attempt are reused, never re-parsed), then one LLM call per prose section or
per batch of about twenty-five consecutive row sections, each returning the questions it
holds. Questions are upserted on (``tender_id``, ``section``, ``number``) so a re-run updates
rather than duplicates; each new question gets its thread; every question text extracted in
the run is embedded in one batched call; per-item outcomes go to ``jobs.results``; and the
job ends by enqueueing ``triage_tender``, writing its id to ``next_job_id`` and to
``tenders.triage_job_id``. The pack's ``ingest_status`` runs parsing -> extracting -> ready.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import enums as e
from app.db.models import Document, DocumentSection, Job, Organisation, Question, Tender, Thread
from app.errors import format_error
from app.jobs import enqueue, register, set_progress
from app.llm import embed, get_llm, load_prompt, prompt_version

logger = logging.getLogger(__name__)

PROMPT_NAME = "extract_questions"
ROW_BATCH_SIZE = 25
MAX_TOPICS = 3
_THREAD_TITLE_MAX = 512
_SECTION_MAX = 256
_NUMBER_MAX = 64

ResponseTypeLiteral = Literal["free_text", "yes_no", "attachment", "table", "pricing", "other"]


class ExtractionUnavailable(RuntimeError):
    """Raised when the pack cannot be parsed because the parsing module is not importable."""


# --- LLM output schema ------------------------------------------------------------------------


class ExtractedQuestion(BaseModel):
    section: str = Field(default="", description="The heading the question sits under.")
    number: str = Field(default="", description="The question's own reference, as written.")
    text: str
    word_limit: int | None = None
    weighting: float | None = None
    response_type: ResponseTypeLiteral = "free_text"
    mandatory: bool = False
    order_index: int = Field(default=0, description="0-based position within the given sections.")
    topics: list[str] = Field(default_factory=list, description="One to three taxonomy ids.")


class QuestionExtractionOutput(BaseModel):
    questions: list[ExtractedQuestion] = Field(default_factory=list)


# --- Parsing seam -------------------------------------------------------------------------------

ParseFn = Callable[[Path, str], Sequence[Any]]
PersistFn = Callable[[Session, Document, Sequence[Any]], Sequence[DocumentSection]]


def _load_parser() -> tuple[ParseFn, PersistFn]:
    """Owner A's parse functions, imported lazily so this module imports on its own."""
    try:
        from app.ingest.parse import parse_document, persist_sections
    except ImportError as exc:
        raise ExtractionUnavailable(
            "Question-pack parsing is unavailable: app.ingest.parse could not be imported "
            f"({exc})."
        ) from exc
    return parse_document, persist_sections


def _storage_file(storage_path: str) -> Path:
    """The on-disk path, through ``app.ingest.storage``."""
    from app.ingest.storage import storage_file

    return storage_file(storage_path)


def _sections_of(session: Session, document: Document) -> list[DocumentSection]:
    return list(
        session.scalars(
            select(DocumentSection)
            .where(DocumentSection.document_id == document.id)
            .order_by(DocumentSection.order_index)
        ).all()
    )


def parse_pack(session: Session, document: Document) -> list[DocumentSection]:
    """Persist the pack's sections (ingestion step 1) and return them in order.

    Sections are written once and never deleted, so a requeued job that already has them
    reuses them instead of parsing again.
    """
    existing = _sections_of(session, document)
    if existing:
        return existing
    parse_document, persist_sections = _load_parser()
    parsed = parse_document(_storage_file(document.storage_path), document.filename)
    persist_sections(session, document, parsed)
    session.flush()
    return _sections_of(session, document)


# --- Batching -----------------------------------------------------------------------------------


@dataclass
class ExtractionBatch:
    """One LLM call's worth of sections: a single prose section or up to 25 row sections."""

    sections: list[DocumentSection] = field(default_factory=list)

    @property
    def item_id(self) -> uuid.UUID:
        return self.sections[0].id

    @property
    def is_rows(self) -> bool:
        return _is_row(self.sections[0])


def _is_row(section: DocumentSection) -> bool:
    return section.table_index is not None or bool(section.cell_ref)


def build_batches(
    sections: Sequence[DocumentSection], *, row_batch_size: int = ROW_BATCH_SIZE
) -> list[ExtractionBatch]:
    """Prose sections one per batch; consecutive row sections in batches of ``row_batch_size``.
    Sections with no text are skipped."""
    batches: list[ExtractionBatch] = []
    rows: list[DocumentSection] = []

    def flush_rows() -> None:
        nonlocal rows
        for start in range(0, len(rows), row_batch_size):
            batches.append(ExtractionBatch(rows[start : start + row_batch_size]))
        rows = []

    for section in sections:
        if not (section.text or "").strip():
            continue
        if _is_row(section):
            rows.append(section)
            continue
        flush_rows()
        batches.append(ExtractionBatch([section]))
    flush_rows()
    return batches


# --- The LLM call -------------------------------------------------------------------------------


def _render_sections(batch: ExtractionBatch) -> str:
    parts = []
    for section in batch.sections:
        heading = " > ".join(section.heading_path or []) or "(no heading)"
        kind = "table row" if _is_row(section) else "prose"
        location = f", row {section.cell_ref}" if section.cell_ref else ""
        parts.append(
            f"[section {section.id}] ({kind}{location})\nHeading path: {heading}\n{section.text}"
        )
    return "\n\n".join(parts)


def extract_batch(
    batch: ExtractionBatch, taxonomy: Sequence[str], *, filename: str
) -> QuestionExtractionOutput:
    """One schema-validated call over the batch's sections."""
    settings = get_settings()
    user = (
        f"Question pack file: {filename}\n"
        f"Topic taxonomy (use only these identifiers): {', '.join(taxonomy)}\n\n"
        f"Sections:\n\n{_render_sections(batch)}"
    )
    return get_llm().parse(
        PROMPT_NAME,
        model=settings.model_main,
        system=load_prompt(PROMPT_NAME),
        user=user,
        output_model=QuestionExtractionOutput,
    )


# --- Normalisation of what the model returned ---------------------------------------------------


def _clean_topics(topics: Sequence[str], taxonomy: Sequence[str]) -> list[str]:
    allowed = set(taxonomy)
    seen: list[str] = []
    for topic in topics:
        value = (topic or "").strip().lower()
        if value in allowed and value not in seen:
            seen.append(value)
        if len(seen) == MAX_TOPICS:
            break
    return seen


def _fallback_section(batch: ExtractionBatch) -> str:
    for section in batch.sections:
        for heading in section.heading_path or []:
            if heading.strip():
                return heading.strip()
    return "General"


@dataclass
class NormalisedQuestion:
    section: str
    number: str
    text: str
    word_limit: int | None
    weighting: float | None
    response_type: str
    mandatory: bool
    topics: list[str]


def normalise_questions(
    output: QuestionExtractionOutput,
    batch: ExtractionBatch,
    taxonomy: Sequence[str],
    *,
    position_offset: int,
) -> list[NormalisedQuestion]:
    """Apply the plan's field rules and give every question a non-empty section and number."""
    ordered = sorted(enumerate(output.questions), key=lambda pair: (pair[1].order_index, pair[0]))
    result: list[NormalisedQuestion] = []
    fallback_section = _fallback_section(batch)
    for offset, (_, item) in enumerate(ordered):
        text = (item.text or "").strip()
        if not text:
            continue
        section = (item.section or "").strip() or fallback_section
        number = (item.number or "").strip() or str(position_offset + offset + 1)
        response_type = item.response_type
        if response_type not in e.values(e.ResponseType):
            response_type = e.ResponseType.OTHER.value
        word_limit = item.word_limit if item.word_limit and item.word_limit > 0 else None
        result.append(
            NormalisedQuestion(
                section=section[:_SECTION_MAX],
                number=number[:_NUMBER_MAX],
                text=text,
                word_limit=word_limit,
                weighting=item.weighting,
                response_type=response_type,
                mandatory=bool(item.mandatory),
                topics=_clean_topics(item.topics, taxonomy),
            )
        )
    return result


# --- Persistence --------------------------------------------------------------------------------


def _thread_title(question: Question) -> str:
    label = f"{question.section} {question.number}".strip()
    return (label or question.text)[:_THREAD_TITLE_MAX]


class QuestionUpserter:
    """Upsert questions on (tender_id, section, number) and create a thread per new question."""

    def __init__(self, session: Session, tender: Tender, document: Document) -> None:
        self.session = session
        self.tender = tender
        self.document = document
        existing = session.scalars(
            select(Question).where(Question.tender_id == tender.id)
        ).all()
        self.by_key: dict[tuple[str, str], Question] = {
            (question.section, question.number): question for question in existing
        }
        self.seen_keys: set[tuple[str, str]] = set()
        self.touched: list[Question] = []
        self.created = 0
        self.updated = 0
        self._batch_mark: tuple[int, int, int, set[tuple[str, str]]] | None = None

    def begin_batch(self) -> None:
        """Remember the state before a batch so a failed batch can be undone in memory."""
        self._batch_mark = (len(self.touched), self.created, self.updated, set(self.seen_keys))

    def rollback_batch(self) -> None:
        """Undo the in-memory effects of the batch since ``begin_batch`` (the database rows
        are undone by the caller's ``session.rollback()``)."""
        if self._batch_mark is None:
            return
        touched_len, created, updated, seen_keys = self._batch_mark
        for question in self.touched[touched_len:]:
            key = (question.section, question.number)
            if key not in seen_keys and key in self.by_key:
                del self.by_key[key]
        del self.touched[touched_len:]
        self.created, self.updated, self.seen_keys = created, updated, seen_keys
        self._batch_mark = None

    def _unique_key(self, section: str, number: str) -> tuple[str, str]:
        key = (section, number)
        suffix = 2
        while key in self.seen_keys:
            key = (section, f"{number} ({suffix})"[:_NUMBER_MAX])
            suffix += 1
        self.seen_keys.add(key)
        return key

    def upsert(self, item: NormalisedQuestion, order_index: int) -> Question:
        section, number = self._unique_key(item.section, item.number)
        question = self.by_key.get((section, number))
        if question is None:
            question = Question(
                org_id=self.tender.org_id,
                tender_id=self.tender.id,
                document_id=self.document.id,
                section=section,
                number=number,
                text=item.text,
                order_index=order_index,
                status=e.QuestionStatus.NOT_STARTED.value,
                coverage=e.Coverage.UNKNOWN.value,
                coverage_detail={},
            )
            self.session.add(question)
            self.by_key[(section, number)] = question
            self.created += 1
        else:
            self.updated += 1
        question.document_id = self.document.id
        question.text = item.text
        question.word_limit = item.word_limit
        question.weighting = item.weighting
        question.response_type = item.response_type
        question.mandatory = item.mandatory
        question.order_index = order_index
        question.topics = list(item.topics)
        self.session.flush()
        self._ensure_thread(question)
        self.touched.append(question)
        return question

    def _ensure_thread(self, question: Question) -> Thread:
        thread = self.session.scalars(
            select(Thread).where(Thread.question_id == question.id)
        ).first()
        if thread is None:
            thread = Thread(
                org_id=question.org_id,
                tender_id=question.tender_id,
                question_id=question.id,
                title=_thread_title(question),
            )
            self.session.add(thread)
            self.session.flush()
        return thread


def embed_questions(session: Session, questions: Sequence[Question]) -> None:
    """One batched embedding call over every question text; stored on ``questions.embedding``."""
    if not questions:
        return
    vectors = embed([question.text for question in questions])
    for question, vector in zip(questions, vectors, strict=True):
        question.embedding = vector
    session.flush()


def _taxonomy(session: Session, org_id: uuid.UUID) -> list[str]:
    organisation = session.get(Organisation, org_id)
    if organisation is not None and organisation.topic_taxonomy:
        return list(organisation.topic_taxonomy)
    return list(get_settings().default_topic_taxonomy)


# --- The job handler ----------------------------------------------------------------------------


def _load_job_targets(session: Session, job: Job) -> tuple[Tender, Document]:
    payload = job.payload or {}
    try:
        tender_id = uuid.UUID(str(payload["tender_id"]))
        document_id = uuid.UUID(str(payload["document_id"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("extract_questions payload needs tender_id and document_id") from exc
    tender = session.get(Tender, tender_id)
    document = session.get(Document, document_id)
    if tender is None or document is None:
        raise ValueError("extract_questions: tender or question pack no longer exists")
    if document.tender_id != tender.id:
        raise ValueError("extract_questions: the document does not belong to the tender")
    return tender, document


@register(e.JobKind.EXTRACT_QUESTIONS)
def extract_questions(session: Session, job: Job) -> None:
    """Handler for the ``extract_questions`` job. Commits through the job progress functions."""
    tender, document = _load_job_targets(session, job)
    actor = str((job.payload or {}).get("actor") or "system")

    # A requeued attempt starts its results afresh; sections and questions are reused.
    job.results = []
    document.ingest_status = e.IngestStatus.PARSING.value
    document.ingest_error = None
    set_progress(session, job, done=0)

    try:
        sections = parse_pack(session, document)
    except ExtractionUnavailable:
        document.ingest_status = e.IngestStatus.FAILED.value
        session.commit()
        raise

    batches = build_batches(sections)
    document.ingest_status = e.IngestStatus.EXTRACTING.value
    set_progress(session, job, done=0, total=len(batches))

    taxonomy = _taxonomy(session, tender.org_id)
    upserter = QuestionUpserter(session, tender, document)
    version = prompt_version(PROMPT_NAME)
    model = get_settings().model_main
    position = 0
    for done, batch in enumerate(batches, start=1):
        result: dict[str, Any] = {
            "item_id": str(batch.item_id),
            "sections": len(batch.sections),
            "outcome": "extracted",
            "detail": 0,
        }
        batch_start = position
        upserter.begin_batch()
        try:
            output = extract_batch(batch, taxonomy, filename=document.filename)
            items = normalise_questions(output, batch, taxonomy, position_offset=position)
            for item in items:
                upserter.upsert(item, position)
                position += 1
            result["detail"] = len(items)
        except Exception as exc:  # noqa: BLE001 - per-item failures never raise to the job
            logger.exception("extract_questions: batch %s failed", batch.item_id)
            session.rollback()
            upserter.rollback_batch()
            position = batch_start
            result["outcome"] = "failed"
            result["detail"] = format_error(exc)
        set_progress(session, job, done=done, result=result)

    embed_questions(session, upserter.touched)

    triage = enqueue(
        session,
        e.JobKind.TRIAGE_TENDER,
        {"tender_id": str(tender.id), "actor": actor, "scope": "unknown"},
        total=len(upserter.touched),
        org_id=tender.org_id,
    )
    job.next_job_id = triage.id
    tender.triage_job_id = triage.id
    document.ingest_status = e.IngestStatus.READY.value
    logger.info(
        "extract_questions: %d created, %d updated on tender %s (prompt %s, model %s)",
        upserter.created,
        upserter.updated,
        tender.id,
        version,
        model,
    )
    session.commit()


__all__ = [
    "ExtractedQuestion",
    "ExtractionBatch",
    "ExtractionUnavailable",
    "NormalisedQuestion",
    "QuestionExtractionOutput",
    "QuestionUpserter",
    "build_batches",
    "embed_questions",
    "extract_batch",
    "extract_questions",
    "normalise_questions",
    "parse_pack",
]
