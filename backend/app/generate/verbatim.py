"""The verbatim option (draft pipeline step 6 and ``POST /questions/{id}/verbatim``).

Only the item id is persisted (``verbatim_offer_item_id``); the segments are built on read by
running the authoritative splitter over the item's answer slice, each sentence sourced to the
item's section and offsets, ``supported`` and conformed exactly as a draft is.

``accept_verbatim`` creates a new ``ai`` version from those segments with
``verbatim_source_item_id`` set and ``model`` / ``prompt_version`` null, runs support
verification, makes it current through the review owner's ``set_current_ai_version`` (which
applies the displacement rule and moves status to ``ai_draft``) and writes a
``verbatim_accepted`` event. The caller commits.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.enums import EventType, ResponseType, SegmentKind, SourceType, SupportStatus
from app.db.models import Answer, Document, DocumentSection, KnowledgeItem, Question
from app.generate.errors import Conflict409, NotFound
from app.generate.segmentation import split_sentences
from app.generate.verification import source_record, verify_segments


def build_verbatim_segments(session: Session, item: KnowledgeItem) -> list[dict[str, Any]]:
    """Stored-shape segments for the item's answer slice: one per splitter sentence, each
    sourced to the item's section at the sentence's offsets, ``supported``."""
    section = session.get(DocumentSection, item.section_id)
    if section is None:
        raise NotFound(f"Section {item.section_id} for knowledge item {item.id} does not exist.")
    document = session.get(Document, item.document_id)
    base = item.answer_start
    answer_slice = section.text[item.answer_start : item.answer_end]
    segments: list[dict[str, Any]] = []
    for index, sentence in enumerate(split_sentences(answer_slice)):
        start, end = base + sentence.start, base + sentence.end
        source = source_record(
            source_type=SourceType.KNOWLEDGE_ITEM.value,
            source_id=item.id,
            quote=section.text[start:end],
            section=section,
            document=document,
            start=start,
            end=end,
            tier="verbatim",
        )
        segments.append(
            {
                "index": index,
                "paragraph": sentence.paragraph,
                "text": sentence.text,
                "kind": SegmentKind.SUBSTANTIVE.value,
                "sources": [source],
                "dispute": None,
                "support_status": SupportStatus.SUPPORTED.value,
            }
        )
    return segments


def verbatim_offer(session: Session, item_id: uuid.UUID | None) -> dict[str, Any] | None:
    """``{source_item_id, similarity, segments}`` for an answer's or message's offer, built on
    read; ``None`` when there is no offer or the item no longer exists."""
    if item_id is None:
        return None
    item = session.get(KnowledgeItem, item_id)
    if item is None:
        return None
    try:
        segments = build_verbatim_segments(session, item)
    except NotFound:
        return None
    return {"source_item_id": str(item.id), "similarity": None, "segments": segments}


def accept_verbatim(
    session: Session,
    question: Question,
    item_id: uuid.UUID,
    actor: str,
    confirm_displace: bool = False,
) -> Answer:
    """Create and make current an ``ai`` version that is the verbatim copy of ``item_id``.

    Raises ``Conflict409`` for a pricing question, a displacement without the flag, an item
    that is not eligible to be cited (``ineligible``: ``text_verified`` false or
    ``excluded_from_retrieval`` true, the same predicate retrieval applies, so a client-supplied
    id can never cite what the pipeline would not) or an item whose answer slice holds no
    sentence (``empty``), and ``NotFound`` for an unknown item. Every refusal happens before
    anything is written or verified. Flushes; the caller commits.
    """
    from app.generate import pipeline  # circular at module level

    if question.response_type == ResponseType.PRICING.value:
        raise Conflict409("pricing", "Pricing questions are never drafted.")
    pipeline.check_displacement(session, question, confirm_displace)
    item = session.get(KnowledgeItem, item_id)
    if item is None or item.org_id != question.org_id:
        raise NotFound(f"Knowledge item {item_id} does not exist.")
    if not item.text_verified or item.excluded_from_retrieval:
        raise Conflict409(
            "ineligible",
            "This item is not eligible to be cited: its text is unverified or its document "
            "has been superseded.",
        )
    verbatim_segments = build_verbatim_segments(session, item)
    if not verbatim_segments:
        raise Conflict409("empty", "The item's answer slice contains no sentences.")
    segments = verify_segments(session, verbatim_segments)
    return pipeline.persist_ai_answer(
        session,
        question,
        actor=actor,
        confirm_displace=confirm_displace,
        segments=segments,
        gaps=[],
        fact_checklist=[],
        model=None,
        prompt_version=None,
        verbatim_source_item_id=item.id,
        event_type=EventType.VERBATIM_ACCEPTED,
        event_payload={"source_item_id": str(item.id)},
    )


__all__ = ["accept_verbatim", "build_verbatim_segments", "verbatim_offer"]
