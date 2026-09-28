"""Transition matrix, gates and blockers (plan: Review pipeline)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.review.transitions import TransitionBlocked, allowed_transitions, transition
from app.review.versions import set_current_ai_version
from tests.test_review_helpers import (
    ACTOR,
    attestation,
    doc_source,
    events_for,
    fixture_sections,
    make_answer,
    make_question,
    seg,
    supported_answer,
)


def _by_target(rows: list[dict]) -> dict[str, dict]:
    return {row["to"]: row for row in rows}


def test_not_started_blocks_everything(db_session: Session) -> None:
    question = make_question(db_session)
    rows = _by_target(allowed_transitions(db_session, question))
    assert set(rows) == {"not_started", "ai_draft", "writer_edited", "sme_verified", "approved"}
    assert rows["not_started"] == {
        "to": "not_started", "allowed": False, "blockers": [{"kind": "system_only"}],
    }
    assert rows["ai_draft"]["blockers"] == [{"kind": "system_only"}]
    for target in ("writer_edited", "sme_verified", "approved"):
        assert rows[target]["allowed"] is False
        assert rows[target]["blockers"] == [{"kind": "no_answer"}]
    with pytest.raises(TransitionBlocked) as excinfo:
        transition(db_session, question, "approved", ACTOR)
    assert excinfo.value.blockers == [{"kind": "no_answer"}]
    assert question.status == "not_started"


def test_system_only_targets_are_refused_to_people(db_session: Session) -> None:
    question = make_question(db_session)
    supported_answer(db_session, question)
    with pytest.raises(TransitionBlocked) as excinfo:
        transition(db_session, question, "ai_draft", ACTOR)
    assert excinfo.value.blockers == [{"kind": "system_only"}]
    # Pipeline code reaches ai_draft through the system path.
    transition(db_session, question, "ai_draft", ACTOR, system=True)
    assert question.status == "ai_draft"


def test_fully_supported_answer_may_go_straight_to_approved(db_session: Session) -> None:
    question = make_question(db_session)
    answer = supported_answer(db_session, question)
    set_current_ai_version(db_session, question, answer, ACTOR)
    assert question.status == "ai_draft"
    rows = _by_target(allowed_transitions(db_session, question))
    assert rows["writer_edited"]["allowed"] is True
    assert rows["sme_verified"]["allowed"] is True
    assert rows["approved"]["allowed"] is True

    transition(db_session, question, "approved", ACTOR)
    assert question.status == "approved"
    events = events_for(db_session, question.id, "status_changed")
    assert [(e.payload["from"], e.payload["to"]) for e in events] == [
        ("not_started", "ai_draft"),
        ("ai_draft", "approved"),
    ]
    assert events[-1].actor == ACTOR

    # Backwards is allowed, and writer_edited always is with a current version.
    transition(db_session, question, "writer_edited", ACTOR)
    assert question.status == "writer_edited"


def test_same_status_is_a_no_op_with_no_event(db_session: Session) -> None:
    question = make_question(db_session)
    answer = supported_answer(db_session, question)
    set_current_ai_version(db_session, question, answer, ACTOR)
    transition(db_session, question, "writer_edited", ACTOR)
    before = len(events_for(db_session, question.id))
    transition(db_session, question, "writer_edited", ACTOR)
    assert len(events_for(db_session, question.id)) == before


def test_sme_gate_lists_blocking_segments_and_needs_review(db_session: Session) -> None:
    section = fixture_sections(db_session)[0]
    question = make_question(db_session)
    segments = [
        seg(0, "Supported sentence.", "supported", [doc_source(section)]),
        seg(1, "Weak sentence.", "weak", [doc_source(section)]),
        seg(2, "Unsupported sentence.", "unsupported", [doc_source(section, locate=False)]),
        seg(3, "A person wrote this.", "human_authored"),
        seg(4, "Attested by a person.", "supported", [attestation()]),
        seg(5, "Connective.", "connective"),
    ]
    answer = make_answer(db_session, question, segments, gaps=["A named deputy."])
    set_current_ai_version(db_session, question, answer, ACTOR)
    question.needs_review = True

    rows = _by_target(allowed_transitions(db_session, question))
    assert rows["writer_edited"]["allowed"] is True
    assert rows["sme_verified"]["allowed"] is False
    assert rows["sme_verified"]["blockers"] == [
        {"kind": "segment", "index": 1},
        {"kind": "segment", "index": 2},
        {"kind": "segment", "index": 3},
        {"kind": "needs_review"},
    ]
    assert rows["approved"]["blockers"] == [
        *rows["sme_verified"]["blockers"],
        {"kind": "gap", "gap": "A named deputy."},
    ]
    with pytest.raises(TransitionBlocked) as excinfo:
        transition(db_session, question, "sme_verified", ACTOR)
    assert excinfo.value.to == "sme_verified"
    assert {b["kind"] for b in excinfo.value.blockers} == {"segment", "needs_review"}
    assert question.status == "ai_draft"


def test_approved_gate_matches_acknowledgements_by_normalised_string(
    db_session: Session,
) -> None:
    question = make_question(db_session)
    answer = supported_answer(
        db_session, question, gaps=["A named deputy for the CSO.", "DCB0160 evidence."]
    )
    set_current_ai_version(db_session, question, answer, ACTOR)
    # Curly apostrophe, different case and spacing: still a match after normalisation.
    question.gap_acknowledgements = [
        {"gap": "A  named Deputy for the CSO.", "acknowledged_by": ACTOR, "note": None, "at": "x"},
        {"gap": "A reworded gap that matches nothing", "acknowledged_by": ACTOR, "note": None,
         "at": "x"},
    ]
    rows = _by_target(allowed_transitions(db_session, question))
    assert rows["sme_verified"]["allowed"] is True
    assert rows["approved"]["blockers"] == [{"kind": "gap", "gap": "DCB0160 evidence."}]

    question.gap_acknowledgements = [
        *question.gap_acknowledgements,
        {"gap": "dcb0160 evidence.", "acknowledged_by": ACTOR, "note": None, "at": "x"},
    ]
    assert _by_target(allowed_transitions(db_session, question))["approved"]["allowed"] is True
    transition(db_session, question, "approved", ACTOR)
    assert question.status == "approved"


def test_echoing_a_system_only_status_is_a_no_op(db_session: Session) -> None:
    """A request for the current status writes nothing, for every status including the
    system-only ones; a different system-only target stays refused and the matrix still
    reports ``system_only`` for both."""
    question = make_question(db_session)
    before = len(events_for(db_session, question.id))
    transition(db_session, question, "not_started", ACTOR)
    assert question.status == "not_started"
    assert len(events_for(db_session, question.id)) == before

    set_current_ai_version(db_session, question, supported_answer(db_session, question), ACTOR)
    before = len(events_for(db_session, question.id))
    transition(db_session, question, "ai_draft", ACTOR)
    assert question.status == "ai_draft"
    assert len(events_for(db_session, question.id)) == before

    with pytest.raises(TransitionBlocked) as excinfo:
        transition(db_session, question, "not_started", ACTOR)
    assert excinfo.value.blockers == [{"kind": "system_only"}]
    rows = _by_target(allowed_transitions(db_session, question))
    assert rows["not_started"]["blockers"] == [{"kind": "system_only"}]
    assert rows["ai_draft"]["blockers"] == [{"kind": "system_only"}]
