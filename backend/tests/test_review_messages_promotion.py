"""Save-as-answer from a thread message and promotion of approved answers on submission."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID
from app.db.models import Document, KnowledgeItem, Message, Thread
from app.review.messages import MessageNotSaveable, save_message_as_answer
from app.review.promotion import promote_on_submit
from app.review.transitions import current_answer, transition
from app.review.versions import DisplacementRequired, set_current_ai_version
from tests.test_review_helpers import (
    ACTOR,
    doc_source,
    events_for,
    fixture_sections,
    make_fact,
    make_question,
    make_tender,
    seg,
    supported_answer,
)


def make_tender_free_document(db_session: Session) -> Document:
    document = Document(
        org_id=DEFAULT_ORG_ID,
        filename="newer.docx",
        storage_path="/tmp/newer.docx",
        doc_type="reference",
        doc_kind="other",
        classification_confirmed=True,
        ingest_status="ready",
    )
    db_session.add(document)
    db_session.flush()
    return document


def _cited_item(db_session: Session, section) -> KnowledgeItem:  # noqa: ANN001
    """A real knowledge item to cite, so the saved version's sources resolve."""
    item = KnowledgeItem(
        org_id=section.org_id,
        document_id=section.document_id,
        section_id=section.id,
        answer_start=0,
        answer_end=len(section.text),
        item_type="chunk",
        answer_text=section.text,
        text_verified=True,
        topics=[],
        excluded_from_retrieval=True,
    )
    db_session.add(item)
    db_session.flush()
    return item


def _assistant_message(  # noqa: ANN202
    db_session: Session, question, *, fact=None, **overrides  # noqa: ANN001
):
    section = fixture_sections(db_session)[0]
    thread = db_session.scalars(select(Thread).where(Thread.question_id == question.id)).one()
    item = _cited_item(db_session, section)
    fact = fact or make_fact(db_session, section)
    segments = [
        seg(0, "Shorter first sentence.", "supported", [doc_source(section, source_id=item.id)]),
        seg(
            1,
            "Shorter second sentence.",
            "weak",
            [doc_source(section, source_id=item.id)],
            paragraph=1,
        ),
        seg(
            2,
            "Our certificate is current.",
            "supported",
            [doc_source(section, source_type="fact", source_id=fact.id)],
            paragraph=1,
        ),
    ]
    fields = {
        "org_id": question.org_id,
        "thread_id": thread.id,
        "role": "assistant",
        "content": (
            "Shorter first sentence.\n\nShorter second sentence. Our certificate is current."
        ),
        "segments": segments,
        "gaps": ["A gap."],
        "fact_checklist": [{"fact_id": str(fact.id), "statement": "x", "status": "current"}],
        "model": "claude-opus-5",
        "prompt_version": "synthesis.v2",
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
    assert answer.model == "claude-opus-5" and answer.prompt_version == "synthesis.v2"
    assert answer.text == (
        "Shorter first sentence.\n\nShorter second sentence. Our certificate is current."
    )
    assert answer.word_count == 10
    assert answer.support_summary["score"] == round(2 / 3, 2)
    assert message.answer_id == answer.id
    assert question.status == "ai_draft"
    assert question.needs_review is False, "a saved message never raises needs_review"
    assert len(events_for(db_session, question.id, "answer_saved_from_chat")) == 1


@pytest.mark.parametrize("change", ["superseded", "expired", "document_superseded", "removed"])
def test_save_message_reflects_invalidations_since_the_message(
    db_session: Session, change: str
) -> None:
    section = fixture_sections(db_session)[0]
    question = make_question(db_session)
    fact = make_fact(db_session, section)
    message = _assistant_message(db_session, question, fact=fact)
    # The fact stops being current after the message was written; no current answer cited it
    # then, so nothing was invalidated at the time.
    if change == "superseded":
        successor = make_fact(db_session, section)
        fact.superseded_by = successor.id
    elif change == "expired":
        fact.expires_on = date(2020, 1, 1)
    elif change == "document_superseded":
        document = db_session.get(Document, section.document_id)
        newer = make_tender_free_document(db_session)
        document.superseded_by = newer.id
    else:
        db_session.delete(fact)
    db_session.flush()

    answer = save_message_as_answer(db_session, message, ACTOR)

    statuses = [s["support_status"] for s in answer.segments]
    assert statuses == ["supported", "weak", "weak"], "the stale fact's sentence is not supported"
    expected = {"superseded": "superseded", "document_superseded": "superseded",
                "expired": "expired", "removed": "unverified"}[change]
    assert answer.fact_checklist[0]["status"] == expected
    assert question.needs_review is True
    assert len(events_for(db_session, question.id, "answer_needs_review")) == 1


def test_save_message_citing_a_removed_item_downgrades_its_sentences(
    db_session: Session,
) -> None:
    question = make_question(db_session)
    message = _assistant_message(db_session, question)
    item_id = message.segments[0]["sources"][0]["source_id"]
    db_session.delete(db_session.get(KnowledgeItem, item_id))
    db_session.flush()

    answer = save_message_as_answer(db_session, message, ACTOR)

    assert [s["support_status"] for s in answer.segments] == ["weak", "weak", "supported"]
    assert question.needs_review is True


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
    from app.db.models import DocumentSection

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
