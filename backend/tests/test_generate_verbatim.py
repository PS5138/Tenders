# ruff: noqa: F811  (pytest fixtures are imported and then named as parameters)
"""The verbatim option: segments built on read over the item's slice, and accept-verbatim."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Answer, Event, KnowledgeItem
from app.generate.errors import Conflict409, NotFound
from app.generate.segmentation import split_sentences
from app.generate.verbatim import accept_verbatim, build_verbatim_segments, verbatim_offer
from tests.test_generate_fixtures import (
    ANSWER_TEXT,
    Library,
    build_library,
    build_question,
    script_entailment,
    stubbed_modules,  # noqa: F401  (fixture)
)


def _sibling_item(session: Session, library: Library, **overrides: object) -> KnowledgeItem:
    """Another qa_pair in the library's section, by default a copy of the verified one."""
    values: dict[str, object] = {
        "org_id": library.item.org_id,
        "document_id": library.document.id,
        "section_id": library.section.id,
        "answer_start": library.item.answer_start,
        "answer_end": library.item.answer_end,
        "item_type": "qa_pair",
        "question_text": "A sibling question?",
        "answer_text": library.item.answer_text,
        "text_verified": True,
        "topics": ["clinical_safety"],
        "is_canonical": True,
        "question_embedding": library.item.question_embedding,
        "answer_embedding": library.item.answer_embedding,
    }
    values.update(overrides)
    item = KnowledgeItem(**values)
    session.add(item)
    session.flush()
    return item


def test_build_verbatim_segments_uses_the_splitter_and_section_offsets(db_session: Session) -> None:
    library = build_library(db_session)
    segments = build_verbatim_segments(db_session, library.item)
    assert [s["text"] for s in segments] == [s.text for s in split_sentences(ANSWER_TEXT)]
    assert len(segments) == 4
    assert [s["index"] for s in segments] == [0, 1, 2, 3]
    for segment in segments:
        assert segment["support_status"] == "supported"
        assert segment["kind"] == "substantive"
        assert segment["dispute"] is None
        (source,) = segment["sources"]
        assert source["source_type"] == "knowledge_item"
        assert source["source_id"] == str(library.item.id)
        locator = source["locator"]
        assert locator["section_id"] == str(library.section.id)
        assert (
            library.item.answer_start
            <= locator["start"]
            < locator["end"]
            <= library.item.answer_end
        )
        assert (
            library.section.text[locator["start"] : locator["end"]]
            == segment["text"]
            == source["quote"]
        )
        assert source["tier"] == "verbatim"
        assert source["document_title"] == library.document.filename
    assert verbatim_offer(db_session, None) is None
    offer = verbatim_offer(db_session, library.item.id)
    assert offer["source_item_id"] == str(library.item.id) and offer["segments"] == segments


def test_accept_verbatim_creates_a_current_ai_version_without_model(
    db_session: Session,
    stubbed_modules,  # noqa: ANN001
) -> None:
    library = build_library(db_session)
    question = build_question(db_session)
    script_entailment(stubbed_modules.fake_llm, "supported")

    answer = accept_verbatim(db_session, question, library.item.id, "test user")

    assert answer.author_type == "ai" and answer.author_name is None
    assert answer.model is None and answer.prompt_version is None
    assert answer.verbatim_source_item_id == library.item.id
    assert answer.verbatim_offer_item_id is None
    assert answer.is_current and answer.version == 1
    assert answer.text == ANSWER_TEXT
    assert answer.word_count == len(ANSWER_TEXT.split())
    assert answer.gaps == [] and answer.fact_checklist == []
    assert all(s["support_status"] == "supported" for s in answer.segments)
    assert answer.support_summary["score"] == 1.0
    assert question.status == "ai_draft"
    events = db_session.scalars(
        select(Event).where(Event.entity_id == question.id, Event.event_type == "verbatim_accepted")
    ).all()
    assert len(events) == 1 and events[0].payload["source_item_id"] == str(library.item.id)
    assert events[0].payload["answer_id"] == str(answer.id)
    # Verification ran (one entailment call over the four sentences).
    assert [c.name for c in stubbed_modules.fake_llm.calls] == ["entailment"]


def test_accept_verbatim_409_rules_and_404(db_session: Session, stubbed_modules) -> None:  # noqa: ANN001
    library = build_library(db_session)
    pricing = build_question(db_session, response_type="pricing", number="9.1")
    with pytest.raises(Conflict409) as excinfo:
        accept_verbatim(db_session, pricing, library.item.id, "test user")
    assert excinfo.value.code == "pricing"

    approved = build_question(db_session, status="approved", number="3.9")
    with pytest.raises(Conflict409) as excinfo:
        accept_verbatim(db_session, approved, library.item.id, "test user")
    assert excinfo.value.code == "displacement"
    assert db_session.scalars(select(Answer).where(Answer.question_id == approved.id)).all() == []

    import uuid

    with pytest.raises(NotFound):
        accept_verbatim(db_session, approved, uuid.uuid4(), "test user", confirm_displace=True)

    script_entailment(stubbed_modules.fake_llm)
    answer = accept_verbatim(
        db_session, approved, library.item.id, "test user", confirm_displace=True
    )
    assert answer.is_current and approved.status == "ai_draft"


def test_accept_verbatim_refuses_ineligible_and_empty_items_without_writing(
    db_session: Session,
    stubbed_modules,  # noqa: ANN001
) -> None:
    """A client-supplied ``source_item_id`` is held to the same predicate retrieval applies
    (``text_verified`` and not ``excluded_from_retrieval``), and a slice with no sentence never
    becomes an empty current version."""
    library = build_library(db_session)
    question = build_question(db_session)
    script_entailment(stubbed_modules.fake_llm, "supported")

    # An unverified pair is stored with offsets 0/0 (ingest keeps the copied text only).
    unverified = _sibling_item(
        db_session, library, text_verified=False, answer_start=0, answer_end=0
    )
    with pytest.raises(Conflict409) as excinfo:
        accept_verbatim(db_session, question, unverified.id, "test user")
    assert excinfo.value.code == "ineligible"

    # A pair from a superseded document is excluded from retrieval, and from citation.
    excluded = _sibling_item(db_session, library, excluded_from_retrieval=True)
    with pytest.raises(Conflict409) as excinfo:
        accept_verbatim(db_session, question, excluded.id, "test user")
    assert excinfo.value.code == "ineligible"

    # A verified item whose answer slice holds no sentence.
    empty = _sibling_item(
        db_session, library, answer_start=library.item.answer_start,
        answer_end=library.item.answer_start,
    )
    with pytest.raises(Conflict409) as excinfo:
        accept_verbatim(db_session, question, empty.id, "test user")
    assert excinfo.value.code == "empty"
    assert "no sentences" in excinfo.value.message

    assert db_session.scalars(select(Answer).where(Answer.question_id == question.id)).all() == []
    assert (
        db_session.scalars(
            select(Event).where(
                Event.entity_id == question.id, Event.event_type == "verbatim_accepted"
            )
        ).all()
        == []
    )
    assert question.status == "not_started"
    assert stubbed_modules.fake_llm.calls == [], "no entailment call is paid for a refusal"

    # The eligible sibling is still accepted.
    accepted = accept_verbatim(db_session, question, library.item.id, "test user")
    assert accepted.is_current and question.status == "ai_draft"
