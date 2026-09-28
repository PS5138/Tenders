"""Promotion of approved answers into the library on submission (the learning loop).

Every ``approved`` question with a current answer becomes a ``promoted_answer`` knowledge
item. Knowledge items and citations must point at a stored section, so promotion writes one
library document per tender (``doc_type = past_submission``, tagged with buyer, submission
date and outcome, confirmed, ``ready``) holding one section per promoted answer; the item's
``answer_text`` is the raw slice of that section, so ``text_verified`` is true by
construction. The item then goes through embedding and deduplication (owner B's functions
when importable, otherwise a direct embed and a canonical of its own).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import (
    DocType,
    EffectiveDateSource,
    EventType,
    IngestStatus,
    ItemType,
    QuestionStatus,
    SubmissionOutcome,
    TenderOutcome,
)
from app.db.models import Document, DocumentSection, Event, KnowledgeItem, Question, Tender
from app.review.events import EntityType, record_event
from app.review.transitions import current_answer

logger = logging.getLogger(__name__)

PROMOTION_STORAGE_PREFIX = "promoted/"


def _submission_outcome(tender: Tender) -> str | None:
    """The document outcome tag; ``pending`` on the tender means not yet known."""
    if tender.outcome in (TenderOutcome.WON.value, TenderOutcome.LOST.value):
        return SubmissionOutcome(tender.outcome).value
    if tender.outcome == TenderOutcome.UNKNOWN.value:
        return SubmissionOutcome.UNKNOWN.value
    return None


def promotion_document(session: Session, tender: Tender) -> Document:
    """The library document that holds this tender's promoted answers; created on first use
    and re-tagged with buyer, date and outcome on every call."""
    storage_path = f"{PROMOTION_STORAGE_PREFIX}{tender.id}"
    document = session.scalars(
        select(Document).where(
            Document.org_id == tender.org_id, Document.storage_path == storage_path
        )
    ).first()
    submitted = (tender.submitted_at or datetime.now(UTC)).date()
    if document is None:
        document = Document(
            org_id=tender.org_id,
            filename=f"Approved answers: {tender.name}",
            storage_path=storage_path,
            doc_type=DocType.PAST_SUBMISSION.value,
            effective_date=submitted,
            effective_date_source=EffectiveDateSource.USER.value,
            classification_confirmed=True,
            ingest_status=IngestStatus.READY.value,
        )
        session.add(document)
    document.buyer = tender.buyer
    document.submission_date = submitted
    document.outcome = _submission_outcome(tender)
    session.flush()
    return document


def retag_promotion_document(session: Session, tender: Tender) -> Document | None:
    """Update the buyer, date and outcome tags on the tender's promoted-answers document when
    promotion has happened; returns None (and creates nothing) when it has not. Called when
    ``PATCH /tenders/{id}`` sets the outcome later, so the learning loop's tags follow."""
    storage_path = f"{PROMOTION_STORAGE_PREFIX}{tender.id}"
    document = session.scalars(
        select(Document).where(
            Document.org_id == tender.org_id, Document.storage_path == storage_path
        )
    ).first()
    if document is None:
        return None
    return promotion_document(session, tender)


def _already_promoted(session: Session, question: Question, answer_id: Any) -> bool:
    events = session.scalars(
        select(Event).where(
            Event.entity_type == EntityType.QUESTION.value,
            Event.entity_id == question.id,
            Event.event_type == EventType.ANSWER_PROMOTED.value,
        )
    ).all()
    return any(str(event.payload.get("answer_id")) == str(answer_id) for event in events)


def _embed_items(session: Session, items: list[KnowledgeItem]) -> None:
    try:
        from app.ingest.embed import embed_items
    except ImportError:
        embed_items = None
    if embed_items is not None:
        embed_items(session, items)
        return
    from app.llm import embed

    for item in items:
        vectors = embed([item.question_text or "", item.answer_text])
        item.question_embedding = vectors[0] if item.question_text else None
        item.answer_embedding = vectors[1]


def _deduplicate(session: Session, org_id: Any, items: list[KnowledgeItem]) -> None:
    try:
        from app.ingest.dedup import deduplicate
    except ImportError:
        deduplicate = None
    if deduplicate is not None:
        deduplicate(session, org_id, items)
        return
    logger.warning("app.ingest.dedup is unavailable; promoted answers become their own canonical")
    for item in items:
        item.canonical_id = None
        item.is_canonical = True


def promote_on_submit(session: Session, tender: Tender, actor: str) -> int:
    """Promote every ``approved`` answer of ``tender`` into the library as a ``promoted_answer``
    item, tagged with buyer, date and outcome, embedded and passed through deduplication.
    Idempotent per answer version. Writes one ``answer_promoted`` event per promoted answer
    and returns how many were promoted. The caller commits."""
    questions = session.scalars(
        select(Question)
        .where(Question.tender_id == tender.id, Question.status == QuestionStatus.APPROVED.value)
        .order_by(Question.order_index)
    ).all()
    if not questions:
        return 0
    document = promotion_document(session, tender)
    next_order = (
        session.scalar(
            select(DocumentSection.order_index)
            .where(DocumentSection.document_id == document.id)
            .order_by(DocumentSection.order_index.desc())
            .limit(1)
        )
        or -1
    ) + 1

    created: list[tuple[Question, Any, KnowledgeItem]] = []
    for question in questions:
        answer = current_answer(session, question)
        if answer is None or not answer.text.strip():
            continue
        if _already_promoted(session, question, answer.id):
            continue
        heading = f"{question.section} {question.number}".strip()
        section_text = f"{heading}\n{question.text}\n\n{answer.text}"
        answer_start = len(section_text) - len(answer.text)
        section = DocumentSection(
            org_id=tender.org_id,
            document_id=document.id,
            order_index=next_order,
            heading_path=[heading],
            cell_ref=str(question.id),
            text=section_text,
        )
        next_order += 1
        session.add(section)
        session.flush()
        item = KnowledgeItem(
            org_id=tender.org_id,
            document_id=document.id,
            section_id=section.id,
            answer_start=answer_start,
            answer_end=len(section_text),
            question_section_id=None,
            question_start=len(heading) + 1,
            question_end=len(heading) + 1 + len(question.text),
            item_type=ItemType.PROMOTED_ANSWER.value,
            question_text=question.text,
            answer_text=section_text[answer_start:],
            text_verified=True,
            topics=list(question.topics or []),
            is_canonical=False,
            excluded_from_retrieval=False,
        )
        session.add(item)
        session.flush()
        created.append((question, answer, item))

    if not created:
        return 0
    items = [item for _, _, item in created]
    _embed_items(session, items)
    _deduplicate(session, tender.org_id, items)
    for question, answer, item in created:
        record_event(
            session,
            EntityType.QUESTION,
            question.id,
            EventType.ANSWER_PROMOTED,
            actor,
            {
                "answer_id": str(answer.id),
                "item_id": str(item.id),
                "document_id": str(document.id),
                "buyer": tender.buyer,
                "outcome": document.outcome,
            },
            org_id=tender.org_id,
        )
    session.flush()
    return len(created)
