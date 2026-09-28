"""Ingestion step 8: the document supersession gate, decision rows and manual decisions."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, SupersessionDecision
from app.ingest.supersession import apply_decision, evaluate_document_supersession
from tests.test_supersession_factories import (  # noqa: F401 - fixture import
    InvalidationRecorder,
    events_for,
    invalidations_fixture,
    make_document,
    make_fact,
    make_item,
)


def _rows(session: Session) -> list[SupersessionDecision]:
    return list(session.scalars(select(SupersessionDecision)))


def _row_for(session: Session, a: Document, b: Document) -> SupersessionDecision | None:
    first, second = sorted((a.id, b.id))
    return session.scalars(
        select(SupersessionDecision).where(
            SupersessionDecision.document_a_id == first,
            SupersessionDecision.document_b_id == second,
        )
    ).one_or_none()


def _two(
    session: Session,
    *,
    kind: str = "iso_27001",
    older: dict | None = None,
    newer: dict | None = None,
) -> tuple[Document, Document]:
    older_doc = make_document(
        session, filename="old.pdf", doc_kind=kind, effective_date=date(2024, 1, 1), **(older or {})
    )
    newer_doc = make_document(
        session, filename="new.pdf", doc_kind=kind, effective_date=date(2025, 1, 1), **(newer or {})
    )
    return older_doc, newer_doc


def test_automatic_supersession_when_both_confirmed_with_differing_extracted_dates(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older, newer = _two(db_session)
    item = make_item(db_session, older)
    current_fact = make_fact(db_session, older)
    stale_fact = make_fact(db_session, older, fact_kind="cyber_essentials_plus")
    stale_fact.superseded_by = make_fact(db_session, newer, fact_kind="cyber_essentials_plus").id
    db_session.flush()

    evaluate_document_supersession(db_session, newer)

    row = _row_for(db_session, older, newer)
    assert row is not None
    assert row.decision == "superseded" and row.reason == "auto"
    assert row.superseding_document_id == newer.id
    assert row.decided_by is None and row.decided_at is not None
    assert older.superseded_by == newer.id and newer.superseded_by is None
    db_session.refresh(item)
    assert item.excluded_from_retrieval is True
    # Only facts step 7 did not already supersede are invalidated.
    assert invalidations.of("fact", "superseded") == [current_fact.id]
    events = events_for(db_session, older.id, "document_superseded")
    assert len(events) == 1 and events[0].actor == "system"
    assert events[0].payload["superseded_by"] == str(newer.id)

    # Re-evaluation from either side changes nothing and writes nothing.
    evaluate_document_supersession(db_session, older)
    evaluate_document_supersession(db_session, newer)
    assert len(_rows(db_session)) == 1
    assert len(events_for(db_session, older.id, "document_superseded")) == 1
    assert len(invalidations.calls) == 1


def test_no_row_while_either_document_is_unconfirmed(db_session: Session) -> None:
    older, newer = _two(db_session, newer={"confirmed": False})
    evaluate_document_supersession(db_session, older)
    evaluate_document_supersession(db_session, newer)
    assert _rows(db_session) == []
    assert older.superseded_by is None

    newer.classification_confirmed = True
    evaluate_document_supersession(db_session, newer)
    row = _row_for(db_session, older, newer)
    assert row is not None and row.decision == "superseded"


def test_upload_time_date_gives_a_pending_date_decision(db_session: Session) -> None:
    older, newer = _two(db_session, newer={"source": "upload_time"})
    item = make_item(db_session, older)
    evaluate_document_supersession(db_session, newer)
    row = _row_for(db_session, older, newer)
    assert row is not None
    assert (row.decision, row.reason) == ("pending", "pending_date")
    assert row.superseding_document_id is None
    assert older.superseded_by is None and item.excluded_from_retrieval is False
    assert events_for(db_session, older.id) == []


def test_equal_dates_give_a_tie(db_session: Session) -> None:
    older, newer = _two(db_session)
    newer.effective_date = older.effective_date
    evaluate_document_supersession(db_session, newer)
    row = _row_for(db_session, older, newer)
    assert row is not None and (row.decision, row.reason) == ("pending", "tie")


def test_keyed_kinds_never_supersede_automatically(db_session: Session) -> None:
    older, newer = _two(db_session, kind="dspt_confirmation")
    evaluate_document_supersession(db_session, newer)
    row = _row_for(db_session, older, newer)
    assert row is not None and (row.decision, row.reason) == ("pending", "keyed_kind")
    assert older.superseded_by is None


def test_unsupersedable_kinds_write_no_row(db_session: Session) -> None:
    older, newer = _two(db_session, kind="product_description")
    evaluate_document_supersession(db_session, newer)
    assert _rows(db_session) == []
    other = make_document(db_session, doc_kind="other", effective_date=date(2026, 1, 1))
    evaluate_document_supersession(db_session, other)
    assert _rows(db_session) == []


def test_keep_both_is_not_overturned_unless_a_date_changes(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older, newer = _two(db_session, newer={"source": "upload_time"})
    item = make_item(db_session, older)
    evaluate_document_supersession(db_session, newer)
    row = _row_for(db_session, older, newer)
    assert row is not None
    apply_decision(db_session, row, "keep_both", None, "bid lead")
    assert row.decision == "keep_both" and row.decided_by == "bid lead"
    decided_at = row.decided_at
    assert len(events_for(db_session, row.id, "supersession_decided")) == 1

    # The newer document's date is later confirmed as extracted; without a date change the
    # human decision stands.
    newer.effective_date_source = "extracted"
    evaluate_document_supersession(db_session, newer)
    evaluate_document_supersession(db_session, older)
    assert row.decision == "keep_both" and row.decided_at == decided_at
    assert older.superseded_by is None and item.excluded_from_retrieval is False

    # A date change after decided_at re-opens the pair and the automatic rule applies.
    newer.effective_date = date(2025, 6, 1)
    newer.effective_date_source = "user"
    evaluate_document_supersession(db_session, newer, effective_date_changed=True)
    assert row.decision == "superseded" and row.reason == "auto"
    assert row.superseding_document_id == newer.id and row.decided_by is None
    assert older.superseded_by == newer.id
    db_session.refresh(item)
    assert item.excluded_from_retrieval is True


def test_human_superseded_decision_is_not_overturned(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older, newer = _two(db_session, kind="dspt_confirmation")
    older_item = make_item(db_session, older)
    newer_item = make_item(db_session, newer)
    evaluate_document_supersession(db_session, newer)
    row = _row_for(db_session, older, newer)
    assert row is not None and row.reason == "keyed_kind"

    # The person decides the *older* file is the one to keep.
    apply_decision(db_session, row, "superseded", older.id, "ig lead")
    assert row.decision == "superseded" and row.superseding_document_id == older.id
    assert row.decided_by == "ig lead"
    assert newer.superseded_by == older.id and older.superseded_by is None
    db_session.refresh(newer_item)
    db_session.refresh(older_item)
    assert newer_item.excluded_from_retrieval is True
    assert older_item.excluded_from_retrieval is False
    events = events_for(db_session, newer.id, "document_superseded")
    assert len(events) == 1 and events[0].actor == "ig lead"

    evaluate_document_supersession(db_session, older)
    evaluate_document_supersession(db_session, newer)
    assert row.superseding_document_id == older.id and row.decided_by == "ig lead"
    assert newer.superseded_by == older.id


def test_keep_both_after_an_automatic_supersession_reverses_the_exclusion(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older, newer = _two(db_session)
    item = make_item(db_session, older)
    evaluate_document_supersession(db_session, newer)
    row = _row_for(db_session, older, newer)
    assert row is not None and older.superseded_by == newer.id

    apply_decision(db_session, row, "keep_both", None, "bid lead")
    assert older.superseded_by is None
    db_session.refresh(item)
    assert item.excluded_from_retrieval is False
    # Reversal restores no segment: the earlier invalidation stands and nothing new is sent.
    assert len(invalidations.calls) == 0 or all(
        reason == "superseded" for _, _, reason in invalidations.calls
    )


def test_manual_superseded_decision_applies_the_step_8_effects(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older, newer = _two(db_session, newer={"source": "upload_time"})
    item = make_item(db_session, older)
    fact = make_fact(db_session, older)
    evaluate_document_supersession(db_session, newer)
    row = _row_for(db_session, older, newer)
    assert row is not None and row.decision == "pending"

    apply_decision(db_session, row, "superseded", newer.id, "bid lead")

    assert older.superseded_by == newer.id
    db_session.refresh(item)
    assert item.excluded_from_retrieval is True
    assert invalidations.of("fact", "superseded") == [fact.id]
    assert len(events_for(db_session, older.id, "document_superseded")) == 1
    assert len(events_for(db_session, row.id, "supersession_decided")) == 1


def test_a_superseded_document_is_not_paired_with_newcomers(db_session: Session) -> None:
    older, middle = _two(db_session)
    evaluate_document_supersession(db_session, middle)
    assert older.superseded_by == middle.id
    newest = make_document(db_session, effective_date=date(2026, 1, 1))
    evaluate_document_supersession(db_session, newest)
    rows = _rows(db_session)
    assert len(rows) == 2
    assert _row_for(db_session, older, newest) is None
    assert middle.superseded_by == newest.id
    assert older.superseded_by == middle.id


def test_a_date_change_can_reverse_an_automatic_supersession(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older, newer = _two(db_session)
    older_item = make_item(db_session, older)
    evaluate_document_supersession(db_session, newer)
    assert older.superseded_by == newer.id

    # The "older" document's date was mis-read; a person corrects it to 2026.
    older.effective_date = date(2026, 1, 1)
    older.effective_date_source = "user"
    evaluate_document_supersession(db_session, older, effective_date_changed=True)

    row = _row_for(db_session, older, newer)
    assert row is not None and row.superseding_document_id == older.id
    assert older.superseded_by is None and newer.superseded_by == older.id
    db_session.refresh(older_item)
    assert older_item.excluded_from_retrieval is False
