"""Row factories shared by owner B's tests (ingestion steps 5 to 9 and the documents and
library API). This module holds no tests; it seeds documents, sections, items and facts
directly in the database with fake embeddings, and records calls to the review module's
fact-invalidation function so the ingestion rules can be asserted without the review module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID
from app.db.models import Document, DocumentSection, Event, Fact, KnowledgeItem, UnpairedFragment
from app.llm.embeddings import embed

ORG = DEFAULT_ORG_ID


def make_document(
    session: Session,
    *,
    filename: str = "doc.docx",
    doc_type: str | None = "reference",
    doc_kind: str | None = "iso_27001",
    effective_date: date | None = date(2025, 1, 1),
    source: str | None = "extracted",
    confirmed: bool = True,
    tender_id: uuid.UUID | None = None,
    ingest_status: str = "ready",
    buyer: str | None = None,
) -> Document:
    document = Document(
        org_id=ORG,
        filename=filename,
        storage_path=f"/tmp/{uuid.uuid4()}",
        doc_type=doc_type,
        doc_kind=doc_kind if doc_type == "reference" else None,
        tender_id=tender_id,
        effective_date=effective_date,
        effective_date_source=source if effective_date is not None else None,
        classification_confirmed=confirmed,
        ingest_status=ingest_status,
        buyer=buyer,
    )
    session.add(document)
    session.flush()
    return document


def make_section(
    session: Session,
    document: Document,
    text: str,
    *,
    order_index: int | None = None,
    heading_path: list[str] | None = None,
) -> DocumentSection:
    if order_index is None:
        existing = session.scalars(
            select(DocumentSection.order_index).where(DocumentSection.document_id == document.id)
        ).all()
        order_index = (max(existing) + 1) if existing else 0
    section = DocumentSection(
        org_id=ORG,
        document_id=document.id,
        order_index=order_index,
        heading_path=heading_path or [],
        text=text,
    )
    session.add(section)
    session.flush()
    return section


def make_item(
    session: Session,
    document: Document,
    section: DocumentSection | None = None,
    *,
    item_type: str = "qa_pair",
    question_text: str | None = "How do you keep data secure?",
    answer_text: str | None = None,
    text_verified: bool = True,
    topics: list[str] | None = None,
    with_embeddings: bool = True,
    canonical: bool = True,
) -> KnowledgeItem:
    if section is None:
        section = make_section(session, document, answer_text or "An answer about security.")
    if answer_text is None:
        answer_text = section.text
    start = section.text.find(answer_text) if answer_text in section.text else 0
    end = start + len(answer_text) if answer_text in section.text else len(section.text)
    if item_type == "chunk":
        question_text = None
    item = KnowledgeItem(
        org_id=ORG,
        document_id=document.id,
        section_id=section.id,
        answer_start=start,
        answer_end=end,
        item_type=item_type,
        question_text=question_text,
        answer_text=answer_text,
        text_verified=text_verified,
        topics=topics or [],
        is_canonical=canonical,
    )
    if with_embeddings:
        vectors = embed([question_text or "", answer_text])
        item.question_embedding = vectors[0] if question_text else None
        item.answer_embedding = vectors[1]
    session.add(item)
    session.flush()
    return item


def make_fact(
    session: Session,
    document: Document,
    section: DocumentSection | None = None,
    *,
    fact_kind: str = "iso_27001",
    fact_key: str | None = None,
    statement: str = "We hold ISO 27001 certificate IS 12345.",
    value: str = "IS 12345",
    effective_date: date | None = None,
    expires_on: date | None = None,
    item: KnowledgeItem | None = None,
) -> Fact:
    if section is None:
        section = make_section(session, document, statement)
    fact = Fact(
        org_id=ORG,
        document_id=document.id,
        section_id=section.id,
        knowledge_item_id=item.id if item is not None else None,
        fact_kind=fact_kind,
        fact_key=fact_key,
        statement=statement,
        value=value,
        effective_date=effective_date or document.effective_date or date(2025, 1, 1),
        expires_on=expires_on,
    )
    session.add(fact)
    session.flush()
    return fact


def make_fragment(
    session: Session,
    document: Document,
    section: DocumentSection,
    *,
    role: str = "question",
    text: str | None = None,
) -> UnpairedFragment:
    text = text or section.text
    start = section.text.find(text)
    fragment = UnpairedFragment(
        org_id=ORG,
        document_id=document.id,
        section_id=section.id,
        role=role,
        text=text,
        start=start if start >= 0 else None,
        end=start + len(text) if start >= 0 else None,
    )
    session.add(fragment)
    session.flush()
    return fragment


def events_for(
    session: Session, entity_id: uuid.UUID, event_type: str | None = None
) -> list[Event]:
    stmt = select(Event).where(Event.entity_id == entity_id).order_by(Event.created_at, Event.id)
    if event_type is not None:
        stmt = stmt.where(Event.event_type == event_type)
    return list(session.scalars(stmt))


@dataclass
class InvalidationRecorder:
    calls: list[tuple[str, uuid.UUID, str]] = field(default_factory=list)

    def __call__(
        self, session: Session, source_type: str, source_id: uuid.UUID, reason: str
    ) -> int:
        self.calls.append((str(source_type), source_id, str(reason)))
        return 0

    def of(self, source_type: str, reason: str | None = None) -> list[uuid.UUID]:
        return [
            source_id
            for kind, source_id, why in self.calls
            if kind == source_type and (reason is None or why == reason)
        ]


def _invalidations(monkeypatch: pytest.MonkeyPatch) -> InvalidationRecorder:
    """Replace the review module's invalidate() with a recorder for the duration of a test."""
    from app.review import invalidation

    recorder = InvalidationRecorder()
    monkeypatch.setattr(invalidation, "invalidate", recorder)
    return recorder


# Test modules import this attribute; pytest registers it as the ``invalidations`` fixture.
invalidations_fixture = pytest.fixture(name="invalidations")(_invalidations)
