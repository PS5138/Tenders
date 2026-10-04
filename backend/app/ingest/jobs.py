"""The ``ingest_document`` job: runs the ingestion pipeline stage by stage.

Stage to step (plan, ingestion pipeline): ``parsing`` is step 1, ``classifying`` step 2,
``extracting`` steps 3 and 4, ``embedding`` step 5, ``linking`` steps 6 to 8, then ``ready``.
A status is written when its stage starts and ``jobs.total`` is the number of stages the
document runs: 5 for a library document, 1 for a parse-only tender document.

Resume: a requeued job starts at the first step of the stage recorded in ``ingest_status``.
Before running a stage other than parsing or classifying from a resume, the handler calls
``app.ingest.corrections.rerun_from_stage`` so the stage's earlier outputs are deleted first;
sections are never deleted and ``persist_sections`` is idempotent on its own.

Every step is a plain import: a missing or renamed step fails at import time rather than
letting a document reach ``ready`` without embeddings, deduplication or supersession.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import enums as e
from app.db.models import Document, DocumentSection, Job, KnowledgeItem
from app.ingest.chunk import chunk_reference
from app.ingest.classify import classify_document
from app.ingest.corrections import rerun_from_stage
from app.ingest.dedup import deduplicate
from app.ingest.embed import embed_items
from app.ingest.extract import extract_pairs
from app.ingest.facts import evaluate_fact_supersession, persist_facts
from app.ingest.parse import parse_document, persist_sections
from app.ingest.storage import storage_file
from app.ingest.supersession import evaluate_document_supersession
from app.jobs import enqueue, register, set_progress

logger = logging.getLogger(__name__)

LIBRARY_STAGES: tuple[str, ...] = (
    e.IngestStatus.PARSING.value,
    e.IngestStatus.CLASSIFYING.value,
    e.IngestStatus.EXTRACTING.value,
    e.IngestStatus.EMBEDDING.value,
    e.IngestStatus.LINKING.value,
)
TENDER_STAGES: tuple[str, ...] = (e.IngestStatus.PARSING.value,)

# Stages whose outputs rerun_from_stage must delete before a re-run.
_RERUN_STAGES = frozenset(
    {
        e.IngestStatus.EXTRACTING.value,
        e.IngestStatus.EMBEDDING.value,
        e.IngestStatus.LINKING.value,
    }
)


class IngestError(RuntimeError):
    pass


# --- Helpers ------------------------------------------------------------------------------------


def stages_for(document: Document) -> tuple[str, ...]:
    if document.doc_type == e.DocType.TENDER_DOCUMENT.value:
        return TENDER_STAGES
    return LIBRARY_STAGES


def enqueue_ingest(session: Session, document: Document, actor: str) -> Job:
    """Queue an ``ingest_document`` job for ``document``. Flushes; the caller commits."""
    return enqueue(
        session,
        e.JobKind.INGEST_DOCUMENT,
        {"document_id": str(document.id), "actor": actor},
        total=len(stages_for(document)),
        org_id=document.org_id,
    )


def _sections(session: Session, document: Document) -> list[DocumentSection]:
    return list(
        session.scalars(
            select(DocumentSection)
            .where(DocumentSection.document_id == document.id)
            .order_by(DocumentSection.order_index)
        ).all()
    )


def _items(session: Session, document: Document) -> list[KnowledgeItem]:
    return list(
        session.scalars(
            select(KnowledgeItem)
            .where(KnowledgeItem.document_id == document.id)
            .order_by(KnowledgeItem.created_at, KnowledgeItem.id)
        ).all()
    )


def _enter_stage(session: Session, document: Document, stage: str) -> None:
    """Write the stage's status as it starts and commit so pollers see it."""
    document.ingest_status = stage
    document.ingest_error = None
    session.commit()


# --- Stages -------------------------------------------------------------------------------------


def _run_parsing(session: Session, document: Document) -> list[DocumentSection]:
    path = storage_file(document.storage_path)
    if not path.exists():
        raise IngestError(f"Stored file not found for {document.filename}: {path}")
    parsed = parse_document(path, document.filename)
    sections = persist_sections(session, document, parsed)
    if not sections:
        raise IngestError(
            f"No text could be extracted from {document.filename}; "
            "scanned PDFs are not supported."
        )
    return sections


def _run_classifying(session: Session, document: Document) -> None:
    classify_document(session, document, _sections(session, document))


def _run_extracting(session: Session, document: Document) -> None:
    sections = _sections(session, document)
    if document.doc_type == e.DocType.PAST_SUBMISSION.value:
        raw_facts = extract_pairs(session, document, sections).raw_facts
    elif document.doc_type == e.DocType.REFERENCE.value:
        raw_facts = chunk_reference(session, document, sections).raw_facts
    else:
        raise IngestError(
            f"{document.filename} has no document type; classification must run first."
        )
    # Facts are persisted here rather than in linking so a job resumed at a later stage does
    # not lose them: RawFacts exist only in memory. Step 7's supersession rule runs in linking.
    persist_facts(session, document, raw_facts)


def _run_embedding(session: Session, document: Document) -> None:
    embed_items(session, _items(session, document))


def _run_linking(session: Session, document: Document) -> None:
    items = _items(session, document)
    if items:
        deduplicate(session, document.org_id, items)
    evaluate_fact_supersession(session, document)
    evaluate_document_supersession(session, document)


_STAGE_RUNNERS: dict[str, Callable[[Session, Document], Any]] = {
    e.IngestStatus.PARSING.value: _run_parsing,
    e.IngestStatus.CLASSIFYING.value: _run_classifying,
    e.IngestStatus.EXTRACTING.value: _run_extracting,
    e.IngestStatus.EMBEDDING.value: _run_embedding,
    e.IngestStatus.LINKING.value: _run_linking,
}


# --- Orchestration ------------------------------------------------------------------------------


def resume_point(document: Document, stages: tuple[str, ...]) -> tuple[int, str | None]:
    """Where a run starts and which stage, if any, must be cleaned first.

    ``queued`` starts at the beginning with nothing to clean. A stage name resumes there and
    cleans that stage when it has outputs to delete. ``failed`` restarts from parsing after
    cleaning from ``extracting``, since a terminal failure may have left partial outputs.
    """
    status = document.ingest_status
    if status in stages:
        index = stages.index(status)
        return index, status if status in _RERUN_STAGES else None
    if status == e.IngestStatus.FAILED.value:
        return 0, e.IngestStatus.EXTRACTING.value if len(stages) > 1 else None
    return 0, None


def run_ingest(session: Session, document: Document, job: Job | None = None) -> None:
    """Run the pipeline from the document's current stage to ``ready``. Commits per stage."""
    stages = stages_for(document)
    if job is not None and job.total != len(stages):
        set_progress(session, job, total=len(stages))

    if document.ingest_status == e.IngestStatus.READY.value:
        logger.info("%s is already ready; nothing to do", document.filename)
        if job is not None:
            set_progress(session, job, done=len(stages))
        return

    start, clean_from = resume_point(document, stages)
    if clean_from is not None:
        logger.info(
            "%s: resuming at %s; cleaning from %s", document.filename, stages[start], clean_from
        )
        rerun_from_stage(session, document, clean_from)
        session.flush()

    for index in range(start, len(stages)):
        stage = stages[index]
        _enter_stage(session, document, stage)
        logger.info("%s: stage %s (%d/%d)", document.filename, stage, index + 1, len(stages))
        _STAGE_RUNNERS[stage](session, document)
        session.commit()
        if job is not None:
            set_progress(session, job, done=index + 1)

    document.ingest_status = e.IngestStatus.READY.value
    document.ingest_error = None
    session.commit()
    logger.info("%s: ready", document.filename)


@register(e.JobKind.INGEST_DOCUMENT)
def ingest_document(session: Session, job: Job) -> None:
    """Job handler. Raises to fail the attempt; the worker applies the retry rule."""
    payload = job.payload or {}
    raw_id = payload.get("document_id")
    if not raw_id:
        raise IngestError("ingest_document job has no document_id in its payload")
    document = session.get(Document, uuid.UUID(str(raw_id)))
    if document is None:
        raise IngestError(f"document {raw_id} not found")
    run_ingest(session, document, job)


__all__ = [
    "LIBRARY_STAGES",
    "TENDER_STAGES",
    "IngestError",
    "enqueue_ingest",
    "ingest_document",
    "resume_point",
    "run_ingest",
    "stages_for",
]
