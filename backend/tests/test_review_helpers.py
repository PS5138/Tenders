"""Builders shared by the review tests (no tests in this module).

Everything is created inside the test's rolled-back session. Locators point at the seeded
fixture document's sections so every source resolves.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID, FIXTURE_DOCUMENT_ID
from app.db.models import (
    Answer,
    Document,
    DocumentSection,
    Event,
    Fact,
    Question,
    Tender,
    Thread,
)
from app.review.versions import make_current, next_version

ACTOR = "test user"


def fixture_sections(session: Session) -> list[DocumentSection]:
    return list(
        session.scalars(
            select(DocumentSection)
            .where(DocumentSection.document_id == FIXTURE_DOCUMENT_ID)
            .order_by(DocumentSection.order_index)
        ).all()
    )


def make_tender(session: Session, **overrides: Any) -> Tender:
    tender = Tender(
        org_id=DEFAULT_ORG_ID,
        name=overrides.pop("name", "Test tender"),
        buyer=overrides.pop("buyer", "Test NHS Trust"),
        **overrides,
    )
    session.add(tender)
    session.flush()
    return tender


def make_question_pack(session: Session, tender: Tender) -> Document:
    document = Document(
        org_id=DEFAULT_ORG_ID,
        filename="questions.xlsx",
        storage_path=f"tenders/{tender.id}/questions.xlsx",
        doc_type="tender_document",
        tender_id=tender.id,
        tender_doc_kind="question_pack",
        classification_confirmed=True,
        ingest_status="ready",
    )
    session.add(document)
    session.flush()
    return document


def make_question(
    session: Session,
    tender: Tender | None = None,
    pack: Document | None = None,
    *,
    with_thread: bool = True,
    **overrides: Any,
) -> Question:
    tender = tender or make_tender(session)
    pack = pack or make_question_pack(session, tender)
    number = overrides.pop("number", str(uuid.uuid4())[:6])
    question = Question(
        org_id=DEFAULT_ORG_ID,
        tender_id=tender.id,
        document_id=pack.id,
        section=overrides.pop("section", "3"),
        number=number,
        text=overrides.pop("text", "Describe your clinical safety management arrangements."),
        order_index=overrides.pop("order_index", 1),
        topics=overrides.pop("topics", ["clinical_safety"]),
        **overrides,
    )
    session.add(question)
    session.flush()
    if with_thread:
        session.add(
            Thread(
                org_id=DEFAULT_ORG_ID,
                tender_id=tender.id,
                question_id=question.id,
                title=f"{question.section}.{question.number}",
            )
        )
        session.flush()
    return question


def doc_source(
    section: DocumentSection,
    quote: str | None = None,
    *,
    source_type: str = "knowledge_item",
    source_id: uuid.UUID | str | None = None,
    locate: bool = True,
) -> dict[str, Any]:
    quote = quote if quote is not None else section.text[:40]
    start = end = None
    if locate:
        position = section.text.find(quote)
        if position >= 0:
            start, end = position, position + len(quote)
    return {
        "source_type": source_type,
        "source_id": str(source_id or uuid.uuid4()),
        "quote": quote,
        "locator": {
            "document_id": str(section.document_id),
            "section_id": str(section.id),
            "start": start,
            "end": end,
            "page": section.page_start,
            "table": section.table_index,
            "cell_ref": section.cell_ref,
            "heading_path": list(section.heading_path or []),
        },
        "document_title": "fixture.docx",
        "doc_type": "reference",
        "doc_kind": "other",
        "effective_date": "2025-03-01",
    }


def attestation(by: str = "Jane Doe", note: str | None = None) -> dict[str, Any]:
    return {
        "source_type": "human_attestation",
        "attested_by": by,
        "note": note,
        "at": "2026-09-28T10:12:00Z",
    }


def seg(
    index: int,
    text: str,
    support_status: str = "supported",
    sources: list[dict[str, Any]] | None = None,
    *,
    kind: str | None = None,
    paragraph: int = 0,
    dispute: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if kind is None:
        kind = "connective" if support_status == "connective" else "substantive"
    return {
        "index": index,
        "paragraph": paragraph,
        "text": text,
        "kind": kind,
        "sources": sources or [],
        "dispute": dispute,
        "support_status": support_status,
    }


def make_answer(
    session: Session,
    question: Question,
    segments: list[dict[str, Any]],
    *,
    current: bool = True,
    author_type: str = "ai",
    author_name: str | None = None,
    gaps: list[str] | None = None,
    fact_checklist: list[dict[str, Any]] | None = None,
    model: str | None = "claude-opus-5",
    prompt_version: str | None = "synthesis.v2",
) -> Answer:
    from app.generate.segmentation import join_segments
    from app.review.support import summarise_support

    text = join_segments(segments)
    answer = Answer(
        org_id=question.org_id,
        question_id=question.id,
        version=next_version(session, question),
        author_type=author_type,
        author_name=author_name,
        text=text,
        word_count=len(text.split()),
        segments=segments,
        gaps=list(gaps or []),
        fact_checklist=list(fact_checklist or []),
        support_summary=summarise_support(segments),
        model=model if author_type == "ai" else None,
        prompt_version=prompt_version if author_type == "ai" else None,
        is_current=False,
    )
    session.add(answer)
    session.flush()
    if current:
        make_current(session, question, answer)
    return answer


def make_fact(
    session: Session,
    section: DocumentSection,
    *,
    expires_on: date | None = None,
    statement: str | None = None,
    fact_kind: str = "iso_27001",
) -> Fact:
    fact = Fact(
        org_id=DEFAULT_ORG_ID,
        document_id=section.document_id,
        section_id=section.id,
        fact_kind=fact_kind,
        statement=statement or section.text[:60],
        value="ISO 27001 certificate 12345",
        effective_date=date(2025, 1, 1),
        expires_on=expires_on,
    )
    session.add(fact)
    session.flush()
    return fact


def events_for(
    session: Session, entity_id: uuid.UUID, event_type: str | None = None
) -> list[Event]:
    stmt = select(Event).where(Event.entity_id == entity_id).order_by(Event.created_at, Event.id)
    if event_type is not None:
        stmt = stmt.where(Event.event_type == event_type)
    return list(session.scalars(stmt).all())


def supported_answer(
    session: Session, question: Question, *, gaps: list[str] | None = None
) -> Answer:
    """A current AI version whose every substantive sentence is supported."""
    section = fixture_sections(session)[0]
    segments = [
        seg(0, "We maintain a clinical safety case.", "supported", [doc_source(section)]),
        seg(1, "It is reviewed annually.", "supported", [doc_source(section)]),
        seg(2, "These arrangements apply everywhere.", "connective"),
    ]
    return make_answer(session, question, segments, gaps=gaps)
