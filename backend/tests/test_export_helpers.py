"""Builders shared by the export tests. Contains no tests.

Everything is written directly to the database: a tender, its question pack document, the
questions in the buyer's order and, where asked, a current answer with stored-shape segments.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID
from app.db.models import Answer, Document, Question, Tender

SECTION_ID = uuid.UUID("00000000-0000-4000-8000-0000000000e1")
DOCUMENT_ID = uuid.UUID("00000000-0000-4000-8000-0000000000e2")
FACT_ID = uuid.UUID("00000000-0000-4000-8000-0000000000e3")
ITEM_ID = uuid.UUID("00000000-0000-4000-8000-0000000000e4")


def make_tender(
    session: Session, *, name: str = "Community EPR", buyer: str | None = "NHS Test ICB"
) -> Tender:
    tender = Tender(org_id=DEFAULT_ORG_ID, name=name, buyer=buyer)
    session.add(tender)
    session.flush()
    return tender


def make_question_pack(session: Session, tender: Tender) -> Document:
    document = Document(
        org_id=DEFAULT_ORG_ID,
        filename="question_pack.xlsx",
        storage_path="storage/question_pack.xlsx",
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
    tender: Tender,
    pack: Document,
    *,
    order_index: int,
    section: str,
    number: str,
    text: str,
    status: str = "not_started",
    needs_review: bool = False,
    response_type: str = "free_text",
    word_limit: int | None = 500,
) -> Question:
    question = Question(
        org_id=DEFAULT_ORG_ID,
        tender_id=tender.id,
        document_id=pack.id,
        section=section,
        number=number,
        text=text,
        word_limit=word_limit,
        response_type=response_type,
        order_index=order_index,
        status=status,
        needs_review=needs_review,
    )
    session.add(question)
    session.flush()
    return question


def make_answer(
    session: Session,
    question: Question,
    *,
    text: str,
    version: int = 1,
    is_current: bool = True,
    author_type: str = "ai",
    segments: list[dict[str, Any]] | None = None,
) -> Answer:
    answer = Answer(
        org_id=DEFAULT_ORG_ID,
        question_id=question.id,
        version=version,
        author_type=author_type,
        author_name=None if author_type == "ai" else "Alex Writer",
        text=text,
        word_count=len(text.split()),
        segments=segments or [],
        is_current=is_current,
        model="claude-opus-5" if author_type == "ai" else None,
        prompt_version="synthesis.v2" if author_type == "ai" else None,
    )
    session.add(answer)
    session.flush()
    return answer


def item_source(quote: str, *, located: bool = True) -> dict[str, Any]:
    return {
        "source_type": "knowledge_item",
        "source_id": str(ITEM_ID),
        "quote": quote,
        "locator": {
            "document_id": str(DOCUMENT_ID),
            "section_id": str(SECTION_ID),
            "start": 12 if located else None,
            "end": 12 + len(quote) if located else None,
            "page": 4,
            "table": None,
            "cell_ref": None,
            "heading_path": ["3", "3.2 Clinical safety"],
        },
        "document_title": "Past submission 2025.docx",
        "doc_type": "past_submission",
        "doc_kind": None,
        "effective_date": "2025-03-01",
    }


def fact_source(quote: str) -> dict[str, Any]:
    return {
        "source_type": "fact",
        "source_id": str(FACT_ID),
        "quote": quote,
        "locator": {
            "document_id": str(DOCUMENT_ID),
            "section_id": str(SECTION_ID),
            "start": 0,
            "end": len(quote),
            "page": None,
            "table": 1,
            "cell_ref": "Sheet1!5",
            "heading_path": ["Certificates"],
        },
        "document_title": "ISO 27001 certificate.pdf",
        "doc_type": "reference",
        "doc_kind": "iso_27001",
        "effective_date": "2026-01-15",
    }


def attestation_source() -> dict[str, Any]:
    return {
        "source_type": "human_attestation",
        "attested_by": "Sam Reviewer",
        "note": "Confirmed with the clinical safety officer.",
        "at": "2026-09-28T10:12:00Z",
    }


def segment(
    index: int,
    paragraph: int,
    text: str,
    *,
    kind: str = "substantive",
    sources: list[dict[str, Any]] | None = None,
    support_status: str = "supported",
    dispute: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "index": index,
        "paragraph": paragraph,
        "text": text,
        "kind": kind,
        "sources": sources or [],
        "dispute": dispute,
        "support_status": support_status,
    }


def traced_segments() -> list[dict[str, Any]]:
    """Two paragraphs, one segment in every support status, one with two sources."""
    return [
        segment(
            0,
            0,
            "We hold a clinical safety case for the product.",
            sources=[item_source("clinical safety case is maintained for the product")],
        ),
        segment(
            1,
            0,
            "It was last reviewed in January 2026.",
            sources=[
                item_source("reviewed in January"),
                fact_source("ISO 27001 certificate valid to 15 January 2027"),
            ],
        ),
        segment(2, 0, "In addition,", kind="connective", support_status="connective"),
        segment(
            3,
            1,
            "Our named clinical safety officer chairs the hazard log review.",
            sources=[attestation_source()],
            support_status="human_authored",
        ),
        segment(
            4, 1, "Every release passes a full regression suite.", support_status="unsupported"
        ),
        segment(
            5,
            1,
            "The hazard log is shared with the buyer quarterly.",
            sources=[item_source("hazard log", located=False)],
            support_status="unsupported",
            dispute={
                "disputed_by": "Sam Reviewer",
                "note": "We share it monthly, not quarterly.",
                "at": "2026-09-28T11:00:00Z",
            },
        ),
    ]


def traced_text() -> str:
    """The stored text derived from ``traced_segments``: sentences joined with a space within
    a paragraph and a blank line between paragraphs."""
    return (
        "We hold a clinical safety case for the product. It was last reviewed in January 2026. "
        "In addition,\n\n"
        "Our named clinical safety officer chairs the hazard log review. Every release passes a "
        "full regression suite. The hazard log is shared with the buyer quarterly."
    )
