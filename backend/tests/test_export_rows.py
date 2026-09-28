"""Row collection, the mode rules, ExportBlocked and the small helpers in app.export."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.export import (
    PLACEHOLDER,
    ExportBlocked,
    build_export,
    collect_rows,
    export_filename,
    split_paragraphs,
)
from tests.test_export_helpers import make_answer, make_question, make_question_pack, make_tender


def _tender_with_questions(session: Session):
    tender = make_tender(session)
    pack = make_question_pack(session, tender)
    approved = make_question(
        session, tender, pack, order_index=2, section="B", number="2.1",
        text="Approved question?", status="approved",
    )
    drafted = make_question(
        session, tender, pack, order_index=1, section="A", number="1.1",
        text="Drafted question?", status="ai_draft",
    )
    untouched = make_question(
        session, tender, pack, order_index=3, section="B", number="2.2",
        text="Untouched question?",
    )
    make_answer(session, approved, text="Approved answer.\n\nSecond paragraph.")
    make_answer(session, drafted, text="Old draft.", version=1, is_current=False)
    make_answer(session, drafted, text="Current draft.", version=2, is_current=True)
    return tender, approved, drafted, untouched


def test_submission_rows_follow_order_index_and_write_only_approved(db_session: Session) -> None:
    tender, approved, drafted, untouched = _tender_with_questions(db_session)

    rows = collect_rows(db_session, tender, mode="submission")

    assert [row.number for row in rows] == ["1.1", "2.1", "2.2"], "buyer's order_index"
    by_id = {row.question_id: row for row in rows}
    assert by_id[approved.id].answer_text == "Approved answer.\n\nSecond paragraph."
    assert by_id[approved.id].word_count == 4
    assert by_id[drafted.id].answer_text is None, "an ai_draft has no approved answer"
    assert by_id[untouched.id].answer_text is None
    assert by_id[approved.id].status_label == "Approved"
    assert by_id[drafted.id].status_label == "AI draft"


def test_review_rows_write_the_current_version_regardless_of_status(db_session: Session) -> None:
    tender, approved, drafted, untouched = _tender_with_questions(db_session)

    rows = {row.question_id: row for row in collect_rows(db_session, tender, mode="review")}

    assert rows[approved.id].answer_text == "Approved answer.\n\nSecond paragraph."
    assert rows[drafted.id].answer_text == "Current draft.", "the current version, not v1"
    assert rows[untouched.id].answer_text is None, "no version means the placeholder"


def test_submission_export_blocked_while_any_question_needs_review(db_session: Session) -> None:
    tender = make_tender(db_session)
    pack = make_question_pack(db_session, tender)
    fine = make_question(
        db_session, tender, pack, order_index=1, section="A", number="1", text="Q1?",
        status="approved",
    )
    flagged = make_question(
        db_session, tender, pack, order_index=2, section="A", number="2", text="Q2?",
        status="approved", needs_review=True,
    )
    flagged_too = make_question(
        db_session, tender, pack, order_index=3, section="A", number="3", text="Q3?",
        status="ai_draft", needs_review=True,
    )
    make_answer(db_session, fine, text="Fine.")
    make_answer(db_session, flagged, text="Flagged.")

    with pytest.raises(ExportBlocked) as excinfo:
        build_export(db_session, tender, format="docx", mode="submission")
    assert excinfo.value.question_ids == [flagged.id, flagged_too.id]
    assert "2 questions need review" in str(excinfo.value)

    with pytest.raises(ExportBlocked):
        build_export(db_session, tender, format="xlsx", mode="submission")

    # Review mode is never blocked.
    assert build_export(db_session, tender, format="docx", mode="review")
    assert build_export(db_session, tender, format="xlsx", mode="review")


def test_pricing_question_without_approval_gets_placeholder(db_session: Session) -> None:
    tender = make_tender(db_session)
    pack = make_question_pack(db_session, tender)
    pricing = make_question(
        db_session, tender, pack, order_index=1, section="Commercial", number="C1",
        text="Provide your pricing schedule.", response_type="pricing", status="writer_edited",
    )
    make_answer(db_session, pricing, text="Human pricing text.", author_type="user")

    rows = collect_rows(db_session, tender, mode="submission")
    assert rows[0].answer_text is None
    assert PLACEHOLDER == "[No approved answer]"


def test_empty_tender_exports_without_error(db_session: Session) -> None:
    tender = make_tender(db_session)
    assert collect_rows(db_session, tender, mode="submission") == []
    assert build_export(db_session, tender, format="docx", mode="submission")
    assert build_export(db_session, tender, format="xlsx", mode="submission")


def test_unknown_format_or_mode_rejected(db_session: Session) -> None:
    tender = make_tender(db_session)
    with pytest.raises(ValueError):
        build_export(db_session, tender, format="pdf", mode="submission")
    with pytest.raises(ValueError):
        build_export(db_session, tender, format="docx", mode="draft")


def test_export_filename_is_safe(db_session: Session) -> None:
    tender = make_tender(db_session, name="Community EPR / Lot 2: Acute", buyer=None)
    assert export_filename(tender, format="docx", mode="submission") == (
        "Community-EPR-Lot-2-Acute-submission.docx"
    )
    assert export_filename(tender, format="xlsx", mode="review").endswith("-review.xlsx")


def test_split_paragraphs_on_blank_lines() -> None:
    assert split_paragraphs("One.\n\nTwo.\n \nThree.") == ["One.", "Two.", "Three."]
    assert split_paragraphs("Single line") == ["Single line"]
    assert split_paragraphs("") == []
