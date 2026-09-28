"""Save-as-answer from a thread message and promotion of approved answers on submission."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KnowledgeItem, Message, Thread
from app.review.messages import MessageNotSaveable, save_message_as_answer
from app.review.promotion import promote_on_submit
from app.review.transitions import current_answer, transition
from app.review.versions import DisplacementRequired, set_current_ai_version
from tests.test_review_helpers import (
    ACTOR,
    doc_source,
    events_for,
    fixture_sections,
    make_question,
    make_tender,
    seg,
    supported_answer,
)


def _assistant_message(db_session: Session, question, **overrides):  # noqa: ANN001, ANN202
    section = fixture_sections(db_session)[0]
    thread = db_session.scalars(select(Thread).where(Thread.question_id == question.id)).one()
    segments = [
        seg(0, "Shorter first sentence.", "supported", [doc_source(section)]),
        seg(1, "Shorter second sentence.", "weak", [doc_source(section)], paragraph=1),
    ]
    fields = {
        "org_id": question.org_id,
        "thread_id": thread.id,
        "role": "assistant",
        "content": "Shorter first sentence.\n\nShorter second sentence.",
        "segments": segments,
        "gaps": ["A gap."],
        "fact_checklist": [{"fact_id": str(section.id), "statement": "x", "status": "current"}],
        "model": "claude-opus-5",
        "prompt_version": "synthesis.v1",
        **overrides,
    }
    message = Message(**fields)
    db_session.add(message)
    db_session.flush()
    return message


def test_save_message_as_answer(db_session: Session) -> None:
    question = make_question(db_session)
    message = _assistant_message(db_session, question)
    answer = save_message_as_answer(db_session, message, ACTOR)
    assert answer.is_current and answer.version == 1
    assert answer.author_type == "ai" and answer.author_name is None
    assert answer.segments == message.segments
    assert answer.gaps == ["A gap."] and answer.fact_checklist == message.fact_checklist
    assert answer.model == "claude-opus-5" and answer.prompt_version == "synthesis.v1"
    assert answer.text == "Shorter first sentence.\n\nShorter second sentence."
    assert answer.word_count == 6
    assert answer.support_summary["score"] == 0.5
    assert message.answer_id == answer.id
    assert question.status == "ai_draft"
    assert question.needs_review is False, "a saved message never raises needs_review"
    assert len(events_for(db_session, question.id, "answer_saved_from_chat")) == 1


def test_save_message_displacement_rule(db_session: Session) -> None:
    question = make_question(db_session)
    set_current_ai_version(db_session, question, supported_answer(db_session, question), ACTOR)
    transition(db_session, question, "writer_edited", ACTOR)
    message = _assistant_message(db_session, question)
    with pytest.raises(DisplacementRequired):
        save_message_as_answer(db_session, message, ACTOR, confirm_displace=False)
    assert message.answer_id is None and question.status == "writer_edited"
    answer = save_message_as_answer(db_session, message, ACTOR, confirm_displace=True)
    assert current_answer(db_session, question).id == answer.id and answer.version == 2
    assert question.status == "ai_draft"


def test_user_messages_and_tender_threads_cannot_be_saved(db_session: Session) -> None:
    question = make_question(db_session)
    user_message = _assistant_message(db_session, question, role="user", segments=None)
    with pytest.raises(MessageNotSaveable):
        save_message_as_answer(db_session, user_message, ACTOR)
    tender = make_tender(db_session)
    thread = Thread(org_id=tender.org_id, tender_id=tender.id, question_id=None, title="t")
    db_session.add(thread)
    db_session.flush()
    message = Message(
        org_id=tender.org_id, thread_id=thread.id, role="assistant", content="x", segments=[]
    )
    db_session.add(message)
    db_session.flush()
    with pytest.raises(MessageNotSaveable):
        save_message_as_answer(db_session, message, ACTOR)


def test_promote_on_submit(db_session: Session) -> None:
    tender = make_tender(db_session, buyer="Meridian NHS Trust")
    approved = make_question(db_session, tender, number="1.1", order_index=1)
    set_current_ai_version(db_session, approved, supported_answer(db_session, approved), ACTOR)
    transition(db_session, approved, "approved", ACTOR)
    not_approved = make_question(db_session, tender, number="1.2", order_index=2)
    set_current_ai_version(
        db_session, not_approved, supported_answer(db_session, not_approved), ACTOR
    )

    assert promote_on_submit(db_session, tender, ACTOR) == 1
    items = db_session.scalars(
        select(KnowledgeItem).where(KnowledgeItem.item_type == "promoted_answer")
    ).all()
    items = [item for item in items if item.question_text == approved.text]
    assert len(items) == 1
    item = items[0]
    answer = current_answer(db_session, approved)
    assert item.answer_text == answer.text
    assert item.text_verified is True
    assert item.topics == approved.topics
    assert item.answer_embedding is not None and item.question_embedding is not None
    # The trace chain holds: the item's slice of its section is its text.
    from app.db.models import Document, DocumentSection

    section = db_session.get(DocumentSection, item.section_id)
    assert section.text[item.answer_start : item.answer_end] == item.answer_text
    assert section.text[item.question_start : item.question_end] == approved.text
    document = db_session.get(Document, item.document_id)
    assert document.doc_type == "past_submission"
    assert document.buyer == "Meridian NHS Trust"
    assert document.classification_confirmed is True and document.ingest_status == "ready"
    assert document.outcome is None, "pending outcome is not yet a tag"
    assert len(events_for(db_session, approved.id, "answer_promoted")) == 1
    assert events_for(db_session, not_approved.id, "answer_promoted") == []

    # Idempotent per answer version.
    assert promote_on_submit(db_session, tender, ACTOR) == 0
    assert len(events_for(db_session, approved.id, "answer_promoted")) == 1


def test_pricing_question_message_cannot_be_saved(db_session: Session) -> None:
    """Pricing questions receive no AI draft (plan: Review pipeline). Save-as-answer refuses
    before anything is written, so no version, no link and no event."""
    question = make_question(db_session, response_type="pricing")
    message = _assistant_message(db_session, question)
    with pytest.raises(MessageNotSaveable, match="Pricing"):
        save_message_as_answer(db_session, message, ACTOR, confirm_displace=True)
    assert current_answer(db_session, question) is None
    assert message.answer_id is None
    assert question.status == "not_started"
    assert events_for(db_session, question.id) == []
