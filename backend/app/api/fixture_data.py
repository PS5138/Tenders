"""Builds the fixture answer, question and thread served by ``GET /fixtures/answer`` and
replayed by ``POST /fixtures/draft``.

Everything is derived from the seeded fixture document so every locator resolves and every
located ``quote`` equals the section text at its offsets. The stream segments are produced by
running the real ``Conformer`` over "model segments", one of which spans two sentences.
Nothing here is persisted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import AnswerRecord, FixtureAnswerResponse
from app.config import FIXTURE_DOCUMENT_ID, get_settings
from app.db.models import Document, DocumentSection
from app.generate.segmentation import (
    Conformer,
    join_segments,
    split_sentences,
    union_sources,
)
from app.llm.prompts import prompt_version
from app.review.support import summarise_support


def _fixed(suffix: str) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-0000000000{suffix}")


FIXTURE_TENDER_ID = _fixed("a0")
FIXTURE_QUESTION_ID = _fixed("a1")
FIXTURE_ANSWER_ID = _fixed("a2")
FIXTURE_THREAD_ID = _fixed("a3")
FIXTURE_QUESTION_PACK_ID = _fixed("a4")
FIXTURE_ITEM_CLINICAL_SAFETY_ID = _fixed("b1")
FIXTURE_ITEM_INFORMATION_GOVERNANCE_ID = _fixed("b2")
FIXTURE_FACT_CSO_ID = _fixed("c1")
FIXTURE_FACT_DSPT_ID = _fixed("c2")
FIXTURE_FACT_SAFETY_CASE_ID = _fixed("c3")
SECTION_CLINICAL_SAFETY_ID = _fixed("e1")
SECTION_INFORMATION_GOVERNANCE_ID = _fixed("e2")
_MESSAGE_USER_ID = _fixed("f1")
_MESSAGE_ASSISTANT_ID = _fixed("f2")
_COMMENT_ID = _fixed("f3")

_AT = "2026-09-28T09:00:00Z"
_AT_LATER = "2026-09-28T10:12:00Z"

QUESTION_TEXT = (
    "Describe your clinical safety management arrangements, including how you comply with "
    "DCB0129, how you will support the Trust in meeting its DCB0160 obligations, and name "
    "your Clinical Safety Officer."
)

GAPS = [
    "The question asks for DCB0160 compliance evidence per deploying organisation; no "
    "current document states it.",
    "A named deputy for the Clinical Safety Officer.",
]


class FixtureError(RuntimeError):
    pass


@dataclass
class FixtureBundle:
    question: dict[str, Any]
    answer: dict[str, Any]
    thread: dict[str, Any]
    stream_segments: list[dict[str, Any]]
    verbatim_event: dict[str, Any]

    def response(self) -> FixtureAnswerResponse:
        return FixtureAnswerResponse.model_validate(
            {"question": self.question, "answer": self.answer, "thread": self.thread}
        )

    def answer_json(self) -> dict[str, Any]:
        return AnswerRecord.model_validate(self.answer).model_dump(mode="json")


def _load(session: Session) -> tuple[Document, dict[uuid.UUID, DocumentSection]]:
    document = session.get(Document, FIXTURE_DOCUMENT_ID)
    if document is None:
        raise FixtureError("The fixture document is not seeded; run `python -m app.db.seed`.")
    sections = {
        section.id: section
        for section in session.scalars(
            select(DocumentSection).where(DocumentSection.document_id == document.id)
        )
    }
    for required in (SECTION_CLINICAL_SAFETY_ID, SECTION_INFORMATION_GOVERNANCE_ID):
        if required not in sections:
            raise FixtureError(f"Fixture section {required} is missing; re-run the seed.")
    return document, sections


def _document_source(
    document: Document,
    section: DocumentSection,
    *,
    source_type: str,
    source_id: uuid.UUID,
    quote: str,
    locate: bool = True,
    offsets: tuple[int, int] | None = None,
) -> dict[str, Any]:
    start: int | None = None
    end: int | None = None
    if offsets is not None:
        start, end = offsets
    elif locate:
        position = section.text.find(quote)
        if position < 0:
            raise FixtureError(f"Fixture quote not found in section {section.id}: {quote!r}")
        start, end = position, position + len(quote)
    if start is not None and end is not None and section.text[start:end] != quote:
        raise FixtureError(f"Fixture offsets do not match the quote in section {section.id}")
    return {
        "source_type": source_type,
        "source_id": str(source_id),
        "quote": quote,
        "locator": {
            "document_id": str(document.id),
            "section_id": str(section.id),
            "start": start,
            "end": end,
            "page": section.page_start,
            "table": section.table_index,
            "cell_ref": section.cell_ref,
            "heading_path": list(section.heading_path or []),
        },
        "document_title": document.filename,
        "doc_type": document.doc_type,
        "doc_kind": document.doc_kind,
        "effective_date": document.effective_date.isoformat() if document.effective_date else None,
    }


def _attestation(attested_by: str, note: str | None, at: str) -> dict[str, Any]:
    return {"source_type": "human_attestation", "attested_by": attested_by, "note": note, "at": at}


def _segment(
    index: int,
    paragraph: int,
    text: str,
    kind: str,
    support_status: str,
    sources: list[dict[str, Any]],
    dispute: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "index": index,
        "paragraph": paragraph,
        "text": text,
        "kind": kind,
        "sources": sources,
        "dispute": dispute,
        "support_status": support_status,
    }


def _pre_verification(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The model's claim about its sources: document sources only, offsets not yet located."""
    claimed: list[dict[str, Any]] = []
    for source in sources:
        if source["source_type"] == "human_attestation":
            continue
        copy = dict(source)
        copy["locator"] = {**source["locator"], "start": None, "end": None}
        claimed.append(copy)
    return claimed


def _model_segments(
    segments: list[dict[str, Any]], merged_pairs: list[tuple[int, int]]
) -> list[dict[str, Any]]:
    """Rebuild the model segments the conformer would have received; ``merged_pairs`` names
    stored segments that arrived as one model segment spanning two sentences."""
    merged_first = {first: second for first, second in merged_pairs}
    skip = set(merged_first.values())
    model_segments: list[dict[str, Any]] = []
    for segment in segments:
        index = segment["index"]
        if index in skip:
            continue
        if index in merged_first:
            partner = segments[merged_first[index]]
            model_segments.append(
                {
                    "text": f"{segment['text']} {partner['text']}",
                    "paragraph": segment["paragraph"],
                    "kind": "substantive"
                    if "substantive" in (segment["kind"], partner["kind"])
                    else "connective",
                    "sources": union_sources(
                        _pre_verification(segment["sources"]),
                        _pre_verification(partner["sources"]),
                    ),
                }
            )
        else:
            model_segments.append(
                {
                    "text": segment["text"],
                    "paragraph": segment["paragraph"],
                    "kind": segment["kind"],
                    "sources": _pre_verification(segment["sources"]),
                }
            )
    return model_segments


def _stream_segments(
    model_segments: list[dict[str, Any]], stored: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    conformer = Conformer()
    conformed = []
    for model_segment in model_segments:
        conformed.extend(conformer.feed(model_segment))
    conformed.extend(conformer.flush())
    if [segment.text for segment in conformed] != [segment["text"] for segment in stored]:
        raise FixtureError("The conformer did not reproduce the fixture's stored segments.")
    return [segment.to_dict() for segment in conformed]


def _verbatim_segments(document: Document, section: DocumentSection) -> list[dict[str, Any]]:
    """The verbatim alternative: the item's answer text split by the authoritative splitter,
    each sentence sourced to the item's section and offsets."""
    heading_end = section.text.find("\n") + 1
    body = section.text[heading_end:]
    segments: list[dict[str, Any]] = []
    for index, sentence in enumerate(split_sentences(body)):
        source = _document_source(
            document,
            section,
            source_type="knowledge_item",
            source_id=FIXTURE_ITEM_CLINICAL_SAFETY_ID,
            quote=sentence.text,
            offsets=(heading_end + sentence.start, heading_end + sentence.end),
        )
        segments.append(_segment(index, 0, sentence.text, "substantive", "supported", [source]))
    return segments


def build_fixture(session: Session) -> FixtureBundle:
    document, sections = _load(session)
    settings = get_settings()
    clinical = sections[SECTION_CLINICAL_SAFETY_ID]
    governance = sections[SECTION_INFORMATION_GOVERNANCE_ID]

    def clinical_item(quote: str, *, locate: bool = True) -> dict[str, Any]:
        return _document_source(
            document,
            clinical,
            source_type="knowledge_item",
            source_id=FIXTURE_ITEM_CLINICAL_SAFETY_ID,
            quote=quote,
            locate=locate,
        )

    cso_fact = _document_source(
        document,
        clinical,
        source_type="fact",
        source_id=FIXTURE_FACT_CSO_ID,
        quote=(
            "our Clinical Safety Officer, Dr Amara Okafor, a registered nurse trained in "
            "clinical risk management"
        ),
    )
    governance_item = _document_source(
        document,
        governance,
        source_type="knowledge_item",
        source_id=FIXTURE_ITEM_INFORMATION_GOVERNANCE_ID,
        quote=(
            "completed the Data Security and Protection Toolkit for 2024-25 with a status of "
            "Standards Met"
        ),
    )

    segments = [
        _segment(
            0,
            0,
            "Meridian Health Systems maintains a clinical safety case for the Meridian Care "
            "Record in accordance with DCB0129.",
            "substantive",
            "supported",
            [
                clinical_item(
                    "Meridian Health Systems maintains a clinical safety case for the Meridian "
                    "Care Record in accordance with DCB0129"
                )
            ],
        ),
        _segment(
            1,
            0,
            "The safety case was last reviewed on 14 January 2025 by our Clinical Safety "
            "Officer, Dr Amara Okafor, a registered nurse trained in clinical risk management.",
            "substantive",
            "supported",
            [
                clinical_item(
                    "The clinical safety case report was last reviewed on 14 January 2025 by "
                    "our Clinical Safety Officer, Dr Amara Okafor"
                ),
                cso_fact,
            ],
        ),
        _segment(
            2,
            0,
            "These arrangements apply to every deployment of the product.",
            "connective",
            "connective",
            [],
        ),
        _segment(
            3,
            1,
            "Hazards are logged and the hazard log is reviewed every month by the clinical "
            "safety group.",
            "substantive",
            "weak",
            [
                clinical_item(
                    "Hazards are recorded in a hazard log that is reviewed quarterly by the "
                    "clinical safety group"
                )
            ],
        ),
        _segment(
            4,
            1,
            "We hold a DCB0160 compliance certificate for every deploying Trust.",
            "substantive",
            "unsupported",
            [
                clinical_item(
                    "a DCB0160 compliance certificate for every deploying organisation",
                    locate=False,
                )
            ],
        ),
        _segment(
            5,
            1,
            "Before go-live our Clinical Safety Officer holds a joint hazard review with the "
            "Trust's own Clinical Safety Officer.",
            "substantive",
            "human_authored",
            [],
        ),
        _segment(
            6,
            2,
            "Clinical safety training is refreshed annually for all delivery staff.",
            "substantive",
            "supported",
            [
                _attestation(
                    "Jane Doe",
                    "Confirmed against the 2025 training register.",
                    _AT_LATER,
                )
            ],
        ),
        _segment(
            7,
            2,
            "Our Data Security and Protection Toolkit status for 2024-25 is Standards Met.",
            "substantive",
            "unsupported",
            [governance_item],
            dispute={
                "disputed_by": "Sam Patel",
                "note": "The 2025-26 assessment has since been published; confirm the current "
                "status before submission.",
                "at": _AT_LATER,
            },
        ),
        _segment(
            8,
            2,
            "We support each Trust in meeting its DCB0160 obligations by supplying the hazard "
            "log, the safety case report and a summary of residual risks before go-live.",
            "substantive",
            "supported",
            [
                clinical_item(
                    "We support each deploying organisation in meeting its own DCB0160 "
                    "obligations by supplying the hazard log, the safety case report and a "
                    "summary of residual risks before go-live"
                )
            ],
        ),
    ]

    # Segments 0 and 1 arrived from the model as one segment; the conformer splits them.
    model_segments = _model_segments(segments, merged_pairs=[(0, 1)])
    stream_segments = _stream_segments(model_segments, segments)

    text = join_segments(segments)
    fact_checklist = [
        {
            "fact_id": str(FIXTURE_FACT_CSO_ID),
            "statement": (
                "The clinical safety case report was last reviewed on 14 January 2025 by our "
                "Clinical Safety Officer, Dr Amara Okafor"
            ),
            "effective_date": "2025-01-14",
            "status": "current",
        },
        {
            "fact_id": str(FIXTURE_FACT_DSPT_ID),
            "statement": (
                "Meridian Health Systems Ltd (ODS code 8JX45) completed the Data Security and "
                "Protection Toolkit for 2024-25 with a status of Standards Met, published on "
                "28 June 2025"
            ),
            "effective_date": "2025-06-28",
            "status": "current",
        },
        {
            "fact_id": str(FIXTURE_FACT_SAFETY_CASE_ID),
            "statement": (
                "Meridian Health Systems maintains a clinical safety case for the Meridian "
                "Care Record in accordance with DCB0129"
            ),
            "effective_date": "2025-03-01",
            "status": "unverified",
        },
    ]
    verbatim = {
        "source_item_id": str(FIXTURE_ITEM_CLINICAL_SAFETY_ID),
        "similarity": 0.94,
        "segments": _verbatim_segments(document, clinical),
    }
    answer = {
        "id": str(FIXTURE_ANSWER_ID),
        "question_id": str(FIXTURE_QUESTION_ID),
        "version": 1,
        "author_type": "ai",
        "author_name": None,
        "text": text,
        "word_count": len(text.split()),
        "segments": segments,
        "gaps": list(GAPS),
        "fact_checklist": fact_checklist,
        "verbatim_offer_item_id": str(FIXTURE_ITEM_CLINICAL_SAFETY_ID),
        "verbatim_source_item_id": None,
        "verbatim": verbatim,
        "support_summary": summarise_support(segments),
        "model": settings.model_main,
        "prompt_version": prompt_version("synthesis"),
        "is_current": True,
        "created_at": _AT,
        "updated_at": _AT_LATER,
    }

    blocking_segments = [
        {"kind": "segment", "index": segment["index"]}
        for segment in segments
        if segment["kind"] == "substantive" and segment["support_status"] != "supported"
    ]
    sme_blockers = [*blocking_segments, {"kind": "needs_review"}]
    approved_blockers = [*sme_blockers, {"kind": "gap", "gap": GAPS[1]}]
    question = {
        "id": str(FIXTURE_QUESTION_ID),
        "org_id": str(document.org_id),
        "tender_id": str(FIXTURE_TENDER_ID),
        "document_id": str(FIXTURE_QUESTION_PACK_ID),
        "section": "3",
        "number": "3.2",
        "text": QUESTION_TEXT,
        "word_limit": 500,
        "weighting": 10.0,
        "response_type": "free_text",
        "mandatory": True,
        "order_index": 7,
        "topics": ["clinical_safety"],
        "coverage": "covered",
        "coverage_detail": {
            "best_vec": 0.81,
            "label_source": "llm",
            "candidates": [
                {
                    "item_id": str(FIXTURE_ITEM_CLINICAL_SAFETY_ID),
                    "fused_score": 0.0325,
                    "vector_score": 0.81,
                },
                {
                    "item_id": str(FIXTURE_ITEM_INFORMATION_GOVERNANCE_ID),
                    "fused_score": 0.0161,
                    "vector_score": 0.52,
                },
            ],
            "gap_summary": GAPS[0],
        },
        "compliance_class": "A",
        "compliant_by": None,
        "assignee": "Jane Doe",
        "status": "ai_draft",
        "needs_review": True,
        "gap_acknowledgements": [
            {
                "gap": GAPS[0],
                "acknowledged_by": "Jane Doe",
                "note": "We will attach the hazard log and a DCB0160 support statement instead.",
                "at": _AT_LATER,
            }
        ],
        "thread_id": str(FIXTURE_THREAD_ID),
        "created_at": _AT,
        "updated_at": _AT_LATER,
        "evidence": [
            {
                "document_id": str(document.id),
                "filename": document.filename,
                "note": "Clinical safety case report and hazard log summary.",
            }
        ],
        "comments": [
            {
                "id": str(_COMMENT_ID),
                "author": "Sam Patel",
                "text": "Please check the DSPT year before this goes to the SME.",
                "created_at": _AT_LATER,
            }
        ],
        "current_answer": answer,
        "draft_in_progress": False,
        "allowed_transitions": [
            {"to": "not_started", "allowed": False, "blockers": [{"kind": "system_only"}]},
            {"to": "ai_draft", "allowed": False, "blockers": [{"kind": "system_only"}]},
            {"to": "writer_edited", "allowed": True, "blockers": []},
            {"to": "sme_verified", "allowed": False, "blockers": sme_blockers},
            {"to": "approved", "allowed": False, "blockers": approved_blockers},
        ],
    }
    thread = {
        "id": str(FIXTURE_THREAD_ID),
        "tender_id": str(FIXTURE_TENDER_ID),
        "question_id": str(FIXTURE_QUESTION_ID),
        "title": "3.2 Clinical safety management",
        "reply_in_progress": False,
        "messages": [
            {
                "id": str(_MESSAGE_USER_ID),
                "role": "user",
                "content": "Draft an answer to this question.",
                "created_at": _AT,
            },
            {
                "id": str(_MESSAGE_ASSISTANT_ID),
                "role": "assistant",
                "content": text,
                "segments": segments,
                "support_summary": answer["support_summary"],
                "gaps": list(GAPS),
                "fact_checklist": fact_checklist,
                "verbatim": verbatim,
                "model": settings.model_main,
                "prompt_version": prompt_version("synthesis"),
                "answer_id": str(FIXTURE_ANSWER_ID),
                "created_at": _AT,
            },
        ],
    }
    verbatim_event = {
        "source_item_id": verbatim["source_item_id"],
        "similarity": verbatim["similarity"],
        "segments": verbatim["segments"],
    }
    return FixtureBundle(
        question=question,
        answer=answer,
        thread=thread,
        stream_segments=stream_segments,
        verbatim_event=verbatim_event,
    )
