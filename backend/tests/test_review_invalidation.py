"""Fact invalidation idempotency and the plan's fact-expiry test."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.review.invalidation import invalidate, run_expiry_sweep
from app.review.versions import set_current_ai_version
from tests.test_review_helpers import (
    ACTOR,
    doc_source,
    events_for,
    fixture_sections,
    make_answer,
    make_fact,
    make_question,
    seg,
)


def _answer_citing_fact(db_session: Session, fact, *, current: bool = True):  # noqa: ANN001, ANN202
    section = fixture_sections(db_session)[0]
    question = make_question(db_session)
    segments = [
        seg(
            0,
            "Our ISO 27001 certificate is current.",
            "supported",
            [doc_source(section, source_type="fact", source_id=fact.id)],
        ),
        seg(1, "We also hold Cyber Essentials Plus.", "supported", [doc_source(section)]),
        seg(2, "Both are renewed annually.", "connective"),
    ]
    checklist = [
        {
            "fact_id": str(fact.id),
            "statement": fact.statement,
            "effective_date": fact.effective_date.isoformat(),
            "status": "current",
        }
    ]
    answer = make_answer(db_session, question, segments, fact_checklist=checklist, current=current)
    if current:
        set_current_ai_version(db_session, question, answer, ACTOR)
    return question, answer


def test_fact_expiry_sweep_per_the_plan(db_session: Session) -> None:
    section = fixture_sections(db_session)[0]
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    fact = make_fact(db_session, section, expires_on=yesterday)
    still_valid = make_fact(db_session, section, expires_on=date.today() + timedelta(days=30))
    question, answer = _answer_citing_fact(db_session, fact)
    assert question.needs_review is False

    expired = run_expiry_sweep(db_session)
    assert expired == 1
    assert fact.expired_at is not None and still_valid.expired_at is None
    assert answer.segments[0]["support_status"] == "weak"
    assert answer.segments[1]["support_status"] == "supported"
    assert answer.fact_checklist[0]["status"] == "expired"
    assert question.needs_review is True
    assert question.status == "ai_draft", "invalidation never writes status"
    assert answer.support_summary["score"] == 0.5
    assert len(events_for(db_session, question.id, "answer_needs_review")) == 1
    assert len(events_for(db_session, fact.id, "fact_expired")) == 1

    # A repeated sweep changes nothing.
    assert run_expiry_sweep(db_session) == 0
    assert len(events_for(db_session, question.id, "answer_needs_review")) == 1
    assert len(events_for(db_session, fact.id, "fact_expired")) == 1


def test_invalidate_is_idempotent_and_leaves_history_alone(db_session: Session) -> None:
    section = fixture_sections(db_session)[0]
    fact = make_fact(db_session, section)
    question, old = _answer_citing_fact(db_session, fact)
    # A newer current version that also cites the fact; the old one is history.
    newer = make_answer(
        db_session,
        question,
        [
            seg(
                0,
                "Newer.",
                "supported",
                [doc_source(section, source_type="fact", source_id=fact.id)],
            )
        ],
        fact_checklist=list(old.fact_checklist),
    )
    db_session.refresh(old)
    assert old.is_current is False

    assert invalidate(db_session, "fact", fact.id, "superseded") == 1
    assert newer.segments[0]["support_status"] == "weak"
    assert newer.fact_checklist[0]["status"] == "superseded"
    assert old.segments[0]["support_status"] == "supported", "older versions are left alone"
    assert question.needs_review is True
    events = events_for(db_session, question.id, "answer_needs_review")
    assert len(events) == 1
    assert events[0].actor == "system"
    assert events[0].payload["segment_indices"] == [0]

    assert invalidate(db_session, "fact", fact.id, "superseded") == 0
    assert len(events_for(db_session, question.id, "answer_needs_review")) == 1

    # An unrelated source affects nothing.
    assert invalidate(db_session, "knowledge_item", uuid.uuid4(), "removed") == 0


def test_invalidate_removed_knowledge_item(db_session: Session) -> None:
    section = fixture_sections(db_session)[0]
    item_id = uuid.uuid4()
    question = make_question(db_session)
    answer = make_answer(
        db_session,
        question,
        [
            seg(0, "Cited.", "supported", [doc_source(section, source_id=item_id)]),
            seg(1, "Already weak.", "weak", [doc_source(section, source_id=item_id)]),
            seg(2, "Other.", "supported", [doc_source(section)]),
        ],
    )
    set_current_ai_version(db_session, question, answer, ACTOR)
    assert invalidate(db_session, "knowledge_item", item_id, "removed") == 1
    assert [s["support_status"] for s in answer.segments] == ["weak", "weak", "supported"]
    assert question.needs_review is True
    events = events_for(db_session, question.id, "answer_needs_review")
    assert events[0].payload["segment_indices"] == [0], "the already-weak segment is left alone"
