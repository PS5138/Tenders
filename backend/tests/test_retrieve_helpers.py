"""Row factories shared by the retrieval tests. No tests live here."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID, get_settings
from app.db.models import Document, DocumentSection, Fact, KnowledgeItem, Question, Tender
from app.llm import embed


def unit(*components: float) -> list[float]:
    """A vector of the configured dimension whose leading entries are ``components``."""
    dimension = get_settings().embedding_dimension
    vector = [0.0] * dimension
    for index, value in enumerate(components):
        vector[index] = float(value)
    return vector


def make_document(
    session: Session,
    *,
    doc_type: str = "past_submission",
    doc_kind: str | None = None,
    effective_date: date | None = date(2025, 1, 1),
    effective_date_source: str = "extracted",
    superseded_by: uuid.UUID | None = None,
    filename: str | None = None,
) -> Document:
    document = Document(
        org_id=DEFAULT_ORG_ID,
        filename=filename or f"{doc_type}-{uuid.uuid4().hex[:6]}.docx",
        storage_path=f"storage/{uuid.uuid4().hex}.docx",
        doc_type=doc_type,
        doc_kind=doc_kind,
        effective_date=effective_date,
        effective_date_source=effective_date_source,
        classification_confirmed=True,
        superseded_by=superseded_by,
        ingest_status="ready",
    )
    session.add(document)
    session.flush()
    return document


_order: dict[uuid.UUID, int] = {}


def make_section(session: Session, document: Document, text: str) -> DocumentSection:
    index = _order.get(document.id, 0)
    _order[document.id] = index + 1
    section = DocumentSection(
        org_id=DEFAULT_ORG_ID,
        document_id=document.id,
        order_index=index,
        heading_path=["Section"],
        text=text,
    )
    session.add(section)
    session.flush()
    return section


def make_item(
    session: Session,
    document: Document,
    *,
    answer_text: str,
    question_text: str | None = None,
    item_type: str | None = None,
    topics: Sequence[str] = (),
    text_verified: bool = True,
    excluded: bool = False,
    canonical_id: uuid.UUID | None = None,
    is_canonical: bool = False,
    answer_embedding: list[float] | None | bool = True,
    question_embedding: list[float] | None | bool = True,
    section: DocumentSection | None = None,
) -> KnowledgeItem:
    """Insert one item whose answer is the whole of a fresh section.

    ``answer_embedding`` / ``question_embedding``: ``True`` embeds the text with the fake
    embedder, ``None`` leaves the column null, a list is stored as given.
    """
    section = section or make_section(session, document, answer_text)
    if item_type is None:
        item_type = "qa_pair" if question_text else "chunk"
    if answer_embedding is True:
        answer_embedding = embed([answer_text])[0]
    if question_embedding is True:
        question_embedding = embed([question_text])[0] if question_text else None
    item = KnowledgeItem(
        org_id=DEFAULT_ORG_ID,
        document_id=document.id,
        section_id=section.id,
        answer_start=0,
        answer_end=len(section.text),
        item_type=item_type,
        question_text=question_text,
        answer_text=answer_text,
        text_verified=text_verified,
        topics=list(topics),
        canonical_id=canonical_id,
        is_canonical=is_canonical,
        answer_embedding=answer_embedding if answer_embedding is not False else None,
        question_embedding=question_embedding if question_embedding is not False else None,
        excluded_from_retrieval=excluded,
    )
    session.add(item)
    session.flush()
    return item


def make_fact(
    session: Session,
    document: Document,
    *,
    fact_kind: str,
    fact_key: str | None = None,
    statement: str = "A fact.",
    value: str = "value",
    effective_date: date = date(2025, 1, 1),
    expires_on: date | None = None,
    expired_at: datetime | None = None,
    knowledge_item: KnowledgeItem | None = None,
    superseded_by: uuid.UUID | None = None,
    section: DocumentSection | None = None,
) -> Fact:
    section = section or make_section(session, document, statement)
    fact = Fact(
        org_id=DEFAULT_ORG_ID,
        document_id=document.id,
        section_id=section.id,
        knowledge_item_id=knowledge_item.id if knowledge_item else None,
        fact_kind=fact_kind,
        fact_key=fact_key,
        statement=statement,
        value=value,
        effective_date=effective_date,
        expires_on=expires_on,
        expired_at=expired_at,
        superseded_by=superseded_by,
    )
    session.add(fact)
    session.flush()
    return fact


def make_tender(session: Session) -> Tender:
    tender = Tender(org_id=DEFAULT_ORG_ID, name="Test tender", buyer="Test ICB")
    session.add(tender)
    session.flush()
    return tender


def make_question(
    session: Session,
    *,
    text: str,
    topics: Sequence[str] = (),
    embedding: list[float] | None | bool = True,
    tender: Tender | None = None,
    **fields: Any,
) -> Question:
    tender = tender or make_tender(session)
    pack = make_document(session, doc_type="tender_document", filename="pack.xlsx")
    if embedding is True:
        embedding = embed([text])[0]
    question = Question(
        org_id=DEFAULT_ORG_ID,
        tender_id=tender.id,
        document_id=pack.id,
        section=fields.pop("section", "1"),
        number=fields.pop("number", uuid.uuid4().hex[:6]),
        text=text,
        order_index=fields.pop("order_index", 0),
        topics=list(topics),
        embedding=embedding if embedding is not False else None,
        **fields,
    )
    session.add(question)
    session.flush()
    return question
