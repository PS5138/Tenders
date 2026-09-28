"""Events written by the tender endpoints: ``tender_submitted`` on submit, ``outcome_set`` on an
outcome change (which also re-tags the promoted-answers document when promotion has
happened), and ``supersession_reversed`` when a document exclusion is lifted."""

from __future__ import annotations

import uuid
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, Event, SupersessionDecision, Tender
from app.ingest.supersession import apply_decision, evaluate_document_supersession
from app.review.promotion import promotion_document, retag_promotion_document
from tests.test_supersession_factories import (  # noqa: F401 - fixture import
    InvalidationRecorder,
    invalidations_fixture,
    make_document,
    make_item,
)
from tests.test_tenders_api import create_tender


def _events(session: Session, entity_id: uuid.UUID, event_type: str) -> list[Event]:
    return list(
        session.scalars(
            select(Event)
            .where(Event.entity_id == entity_id, Event.event_type == event_type)
            .order_by(Event.created_at)
        )
    )


async def test_submit_writes_a_tender_submitted_event(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    created = await create_tender(app_client)
    response = await app_client.post(f"/tenders/{created['id']}/submit")
    assert response.status_code == 200, response.text
    events = _events(db_session, uuid.UUID(created["id"]), "tender_submitted")
    assert len(events) == 1
    assert events[0].actor == "test user"
    assert events[0].entity_type == "tender"
    assert events[0].payload["previous_status"] == "open"
    assert events[0].payload["promoted"] == 0
    assert events[0].payload["submitted_at"]


async def test_outcome_change_writes_outcome_set_and_retags_promoted_answers(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    created = await create_tender(app_client, buyer="Meridian NHS Trust")
    tender_id = uuid.UUID(created["id"])

    # No outcome change: no event, and no promotion document is conjured up.
    notes_only = await app_client.patch(
        f"/tenders/{created['id']}", json={"outcome_notes": "Awaiting the award letter"}
    )
    assert notes_only.status_code == 200
    assert _events(db_session, tender_id, "outcome_set") == []
    tender = db_session.get(Tender, tender_id)
    assert retag_promotion_document(db_session, tender) is None

    # Outcome set before any promotion: event written, still no promotion document.
    lost = await app_client.patch(f"/tenders/{created['id']}", json={"outcome": "lost"})
    assert lost.status_code == 200
    events = _events(db_session, tender_id, "outcome_set")
    assert len(events) == 1
    assert events[0].payload["outcome"] == "lost"
    assert events[0].payload["previous_outcome"] == "pending"
    assert events[0].payload["promotion_document_id"] is None
    assert (
        db_session.scalars(
            select(Document).where(Document.storage_path == f"promoted/{tender_id}")
        ).first()
        is None
    )

    # After promotion has happened, a later outcome change re-tags the promoted document.
    db_session.refresh(tender)
    promoted = promotion_document(db_session, tender)
    assert promoted.outcome == "lost"
    won = await app_client.patch(
        f"/tenders/{created['id']}", json={"outcome": "won", "outcome_notes": "Scored 87%"}
    )
    assert won.status_code == 200, won.text
    db_session.refresh(promoted)
    assert promoted.outcome == "won"
    assert promoted.buyer == "Meridian NHS Trust"
    events = _events(db_session, tender_id, "outcome_set")
    assert len(events) == 2
    assert events[-1].payload["outcome"] == "won"
    assert events[-1].payload["previous_outcome"] == "lost"
    assert events[-1].payload["promotion_document_id"] == str(promoted.id)

    # Setting the same outcome again is not a change.
    same = await app_client.patch(f"/tenders/{created['id']}", json={"outcome": "won"})
    assert same.status_code == 200
    assert len(_events(db_session, tender_id, "outcome_set")) == 2


def test_keep_both_after_automatic_supersession_writes_a_reversal_event(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older = make_document(
        db_session, filename="old.docx", doc_kind="iso_27001", effective_date=date(2024, 1, 1)
    )
    newer = make_document(
        db_session, filename="new.docx", doc_kind="iso_27001", effective_date=date(2025, 1, 1)
    )
    item = make_item(db_session, older)
    evaluate_document_supersession(db_session, newer)
    assert older.superseded_by == newer.id
    assert _events(db_session, older.id, "supersession_reversed") == []

    decision = db_session.scalars(select(SupersessionDecision)).one()
    apply_decision(db_session, decision, "keep_both", None, "bid lead")

    db_session.refresh(item)
    assert older.superseded_by is None and item.excluded_from_retrieval is False
    events = _events(db_session, older.id, "supersession_reversed")
    assert len(events) == 1
    assert events[0].actor == "bid lead"
    assert events[0].payload["previously_superseded_by"] == str(newer.id)
    assert events[0].payload["reason"] == "keep_both"
    assert events[0].payload["items_restored"] == 1

    # Deciding keep_both again reverses nothing and writes no second reversal event.
    apply_decision(db_session, decision, "keep_both", None, "bid lead")
    assert len(_events(db_session, older.id, "supersession_reversed")) == 1
