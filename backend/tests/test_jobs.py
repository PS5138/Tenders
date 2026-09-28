from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID, get_settings
from app.db.models import Document, Event, Job
from app.jobs import (
    claim_next,
    complete,
    enqueue,
    get_handler,
    handle_failure,
    register,
    registry,
    requeue_stale_on_startup,
    set_progress,
)
from app.worker import run_job, run_once


def _document(session: Session, status: str = "extracting") -> Document:
    document = Document(
        org_id=DEFAULT_ORG_ID,
        filename="submission.docx",
        storage_path="storage/submission.docx",
        doc_type="past_submission",
        ingest_status=status,
    )
    session.add(document)
    session.flush()
    return document


def test_enqueue_and_claim_in_created_order(db_session: Session) -> None:
    now = datetime.now(UTC)
    first = enqueue(db_session, "triage_tender", {"tender_id": "t1", "actor": "test user"})
    second = enqueue(db_session, "triage_tender", {"tender_id": "t2", "actor": "test user"})
    first.created_at = now - timedelta(seconds=10)
    second.created_at = now - timedelta(seconds=5)
    db_session.flush()

    claimed = claim_next(db_session)
    assert claimed is not None and claimed.id == first.id
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert claimed.started_at is not None

    set_progress(db_session, claimed, done=1, total=2, result={"item_id": "q1", "outcome": "new"})
    assert claimed.done == 1 and claimed.total == 2
    assert claimed.results == [{"item_id": "q1", "outcome": "new"}]
    complete(db_session, claimed)
    assert claimed.status == "done" and claimed.finished_at is not None

    assert claim_next(db_session).id == second.id
    assert claim_next(db_session) is None


def test_handle_failure_requeues_below_max_attempts_then_fails(db_session: Session) -> None:
    max_attempts = get_settings().max_attempts
    document = _document(db_session)
    job = enqueue(
        db_session, "ingest_document", {"document_id": str(document.id), "actor": "test user"}
    )

    for attempt in range(1, max_attempts + 1):
        claimed = claim_next(db_session)
        assert claimed is not None and claimed.id == job.id
        assert claimed.attempts == attempt
        handle_failure(db_session, claimed, RuntimeError(f"boom {attempt}"))
        if attempt < max_attempts:
            assert claimed.status == "queued", "requeued below max_attempts"
            assert claimed.attempts == attempt, "a requeue never resets attempts"
            assert f"boom {attempt}" in (claimed.error or "")
        else:
            assert claimed.status == "failed"
            assert claimed.finished_at is not None

    events = db_session.scalars(
        select(Event).where(Event.entity_type == "job", Event.entity_id == job.id)
    ).all()
    assert [event.event_type for event in events] == ["job_failed"]
    assert events[0].actor == "system"
    assert events[0].payload["started_by"] == "test user"
    db_session.refresh(document)
    assert document.ingest_status == "failed"
    assert "boom" in (document.ingest_error or "")


def test_requeue_stale_on_startup(db_session: Session) -> None:
    max_attempts = get_settings().max_attempts
    document = _document(db_session, status="parsing")
    low = enqueue(db_session, "triage_tender", {"tender_id": "t", "actor": "test user"})
    low.status, low.attempts = "running", 1
    exhausted = enqueue(
        db_session,
        "extract_questions",
        {"tender_id": "t", "document_id": str(document.id), "actor": "test user"},
    )
    exhausted.status, exhausted.attempts = "running", max_attempts
    db_session.flush()

    assert requeue_stale_on_startup(db_session) == (1, 1)
    assert low.status == "queued" and low.attempts == 1
    assert exhausted.status == "failed"
    assert "worker died" in (exhausted.error or "")
    db_session.refresh(document)
    assert document.ingest_status == "failed"
    # A second pass finds nothing running.
    assert requeue_stale_on_startup(db_session) == (0, 0)


def test_worker_dispatches_to_registered_handler(db_session: Session) -> None:
    seen: list[uuid.UUID] = []

    @register("draft_all")
    def handler(session: Session, job: Job) -> None:
        seen.append(job.id)
        set_progress(session, job, done=2, total=2)

    try:
        job = enqueue(db_session, "draft_all", {"tender_id": "t", "actor": "test user"})
        assert run_once(db_session) is True
        assert seen == [job.id]
        assert job.status == "done" and job.done == 2
        assert run_once(db_session) is False
    finally:
        # Do not leave the test handler registered for other tests.
        registry._HANDLERS.pop("draft_all", None)
    assert get_handler("draft_all") is None


def test_worker_requeues_a_job_whose_handler_fails_in_a_flush(db_session: Session) -> None:
    """A handler whose flush fails (here a CHECK-constraint violation on commit) leaves the
    session's transaction rolled back and every instance expired, so touching ``job.id`` or
    ``job.attempts`` before ``session.rollback()`` raises ``PendingRollbackError`` from inside
    the worker's except block and the job is stranded at ``running``. The failure rule must
    still apply: ``queued`` below ``max_attempts`` with the error recorded."""

    @register("draft_all")
    def handler(session: Session, job: Job) -> None:
        session.add(
            Job(
                org_id=job.org_id,
                kind="not_a_job_kind",  # violates ck_jobs_kind
                status="queued",
                attempts=0,
                done=0,
                payload={},
                results=[],
            )
        )
        set_progress(session, job, done=1)  # the commit flushes and fails

    try:
        job = enqueue(db_session, "draft_all", {"tender_id": "t", "actor": "test user"})
        claimed = claim_next(db_session)
        assert claimed is not None and claimed.id == job.id
        run_job(db_session, claimed)  # must not raise
        db_session.refresh(job)
        assert job.status == "queued", "requeued below max_attempts, not left running"
        assert job.attempts == 1
        assert "IntegrityError" in (job.error or "")
        assert "ck_jobs_kind" in (job.error or "")
        # The loop picks it up again on the next pass.
        again = claim_next(db_session)
        assert again is not None and again.id == job.id and again.attempts == 2
    finally:
        registry._HANDLERS.pop("draft_all", None)


def test_worker_fails_job_without_handler(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every job kind has a handler once the handler modules are imported, so the registry is
    # emptied for this test to make it independent of import order.
    monkeypatch.setattr(registry, "_HANDLERS", {})
    job = enqueue(db_session, "triage_tender", {"tender_id": "t", "actor": "test user"})
    claimed = claim_next(db_session)
    assert claimed is not None
    run_job(db_session, claimed)
    assert job.status == "failed"
    assert "no handler" in (job.error or "")
