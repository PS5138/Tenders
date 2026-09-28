"""Attest, dispute, gap acknowledgement, and the recompute function's ownership of
needs_review."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.review.actions import (
    ActionNotPermitted,
    GapNotFound,
    SegmentNotFound,
    acknowledge_gap,
    attest,
    dispute,
)
from app.review.support import recompute_support, summarise_support
from app.review.versions import set_current_ai_version
from tests.test_review_helpers import (
    ACTOR,
    doc_source,
    events_for,
    fixture_sections,
    make_answer,
    make_question,
    seg,
)


def _mixed_answer(db_session: Session):  # noqa: ANN202
    section = fixture_sections(db_session)[0]
    question = make_question(db_session)
    segments = [
        seg(0, "Supported sentence.", "supported", [doc_source(section)]),
        seg(1, "Weak sentence.", "weak", [doc_source(section)]),
        seg(2, "Unsupported sentence.", "unsupported", [doc_source(section, locate=False)]),
        seg(3, "A person wrote this.", "human_authored"),
        seg(4, "Connective.", "connective"),
    ]
    answer = make_answer(db_session, question, segments, gaps=["A named deputy for the CSO."])
    set_current_ai_version(db_session, question, answer, ACTOR)
    return question, answer


def test_recompute_clears_needs_review_only_when_nothing_is_weak_or_unsupported(
    db_session: Session,
) -> None:
    question, answer = _mixed_answer(db_session)
    question.needs_review = True
    recompute_support(db_session, answer)
    assert question.needs_review is True, "weak and unsupported segments remain"
    assert answer.support_summary == summarise_support(answer.segments)
    assert answer.support_summary["score"] == 0.25

    segments = [dict(s) for s in answer.segments]
    for s in segments:
        if s["support_status"] in {"weak", "unsupported"}:
            s["support_status"] = "supported"
    answer.segments = segments
    recompute_support(db_session, answer)
    assert question.needs_review is False, "human_authored alone does not keep the flag"
    assert answer.support_summary["supported"] == 3 and answer.support_summary["substantive"] == 4

    question.needs_review = False
    answer.segments = [{**s, "support_status": "weak"} for s in answer.segments]
    recompute_support(db_session, answer)
    assert question.needs_review is False, "recompute never sets needs_review true"


def test_recompute_on_a_non_current_version_leaves_the_flag(db_session: Session) -> None:
    question, answer = _mixed_answer(db_session)
    section = fixture_sections(db_session)[0]
    old = make_answer(
        db_session, question, [seg(0, "Old.", "supported", [doc_source(section)])], current=False
    )
    question.needs_review = True
    recompute_support(db_session, old)
    assert question.needs_review is True


def test_attest_rules(db_session: Session) -> None:
    question, answer = _mixed_answer(db_session)
    question.needs_review = True
    for index in (1, 2, 3):
        attest(db_session, answer, index, ACTOR, f"Checked {index}")
        segment = answer.segments[index]
        assert segment["support_status"] == "supported"
        human = segment["sources"][-1]
        assert human["source_type"] == "human_attestation"
        assert human["attested_by"] == ACTOR and human["note"] == f"Checked {index}"
        assert segment["dispute"] is None
    assert len(answer.segments[1]["sources"]) == 2, "document source kept beside the attestation"
    assert question.needs_review is False, "nothing weak or unsupported remains"
    assert answer.support_summary["score"] == 1.0
    assert len(events_for(db_session, question.id, "attested")) == 3

    with pytest.raises(ActionNotPermitted):
        attest(db_session, answer, 0, ACTOR, None)  # already supported
    with pytest.raises(ActionNotPermitted):
        attest(db_session, answer, 4, ACTOR, None)  # connective
    with pytest.raises(SegmentNotFound):
        attest(db_session, answer, 99, ACTOR, None)


def test_attest_and_dispute_refuse_a_non_current_version(db_session: Session) -> None:
    question, answer = _mixed_answer(db_session)
    section = fixture_sections(db_session)[0]
    newer = make_answer(
        db_session, question, [seg(0, "Newer.", "supported", [doc_source(section)])]
    )
    assert newer.is_current and not answer.is_current
    with pytest.raises(ActionNotPermitted):
        attest(db_session, answer, 1, ACTOR, None)
    with pytest.raises(ActionNotPermitted):
        dispute(db_session, answer, 0, ACTOR, "Wrong.")


def test_dispute_rules(db_session: Session) -> None:
    question, answer = _mixed_answer(db_session)
    assert question.needs_review is False
    dispute(db_session, answer, 0, ACTOR, "The safety case is out of date.")
    segment = answer.segments[0]
    assert segment["support_status"] == "unsupported"
    assert segment["sources"], "sources kept for context"
    assert segment["dispute"]["disputed_by"] == ACTOR
    assert segment["dispute"]["note"] == "The safety case is out of date."
    assert question.needs_review is True
    assert len(events_for(db_session, question.id, "disputed")) == 1

    with pytest.raises(ActionNotPermitted):
        dispute(db_session, answer, 1, ACTOR, "Not supported so cannot dispute.")
    with pytest.raises(ValueError):
        dispute(db_session, answer, 0, ACTOR, "")

    # Attesting the disputed sentence clears the dispute.
    attest(db_session, answer, 0, ACTOR, "Re-checked.")
    assert answer.segments[0]["dispute"] is None
    assert answer.segments[0]["support_status"] == "supported"
    assert question.needs_review is True, "weak and unsupported sentences remain"


def test_acknowledge_gap_matches_by_normalisation(db_session: Session) -> None:
    question, answer = _mixed_answer(db_session)
    acknowledge_gap(db_session, question, "a named  deputy for the cso.", "Will add later.", ACTOR)
    assert len(question.gap_acknowledgements) == 1
    ack = question.gap_acknowledgements[0]
    assert ack["acknowledged_by"] == ACTOR and ack["note"] == "Will add later."
    assert ack["gap"] == "a named  deputy for the cso."
    assert len(events_for(db_session, question.id, "gap_acknowledged")) == 1
    with pytest.raises(GapNotFound):
        acknowledge_gap(db_session, question, "Something the answer never listed.", None, ACTOR)
