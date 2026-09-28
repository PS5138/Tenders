"""Job table access.

``enqueue`` only flushes so it joins the caller's request transaction. The worker-side
functions (``claim_next``, ``set_progress``, ``complete``, ``fail``, ``handle_failure``,
``requeue_stale_on_startup``) commit, because progress must be visible to API pollers.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import enums as e
from app.db.models import Document, Job, utcnow
from app.errors import format_error
from app.review.events import SYSTEM_ACTOR, record_event

_DOCUMENT_JOB_KINDS = {e.JobKind.INGEST_DOCUMENT.value, e.JobKind.EXTRACT_QUESTIONS.value}
_MAX_ERROR_LENGTH = 4000


def enqueue(
    session: Session,
    kind: str | e.JobKind,
    payload: dict[str, Any],
    total: int | None = None,
    *,
    org_id: uuid.UUID | None = None,
) -> Job:
    """Create a queued job. Flushes but does not commit."""
    job = Job(
        org_id=org_id or get_settings().default_org_id,
        kind=e.JobKind(kind).value,
        status=e.JobStatus.QUEUED.value,
        attempts=0,
        total=total,
        done=0,
        payload=dict(payload),
        results=[],
    )
    session.add(job)
    session.flush()
    return job


def claim_next(session: Session) -> Job | None:
    """Claim the oldest queued job: set running, attempts + 1, started_at. Commits."""
    stmt = (
        select(Job)
        .where(Job.status == e.JobStatus.QUEUED.value)
        .order_by(Job.created_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.scalars(stmt).first()
    if job is None:
        session.commit()
        return None
    job.status = e.JobStatus.RUNNING.value
    job.attempts += 1
    job.started_at = utcnow()
    job.finished_at = None
    session.commit()
    return job


def set_progress(
    session: Session,
    job: Job,
    *,
    done: int | None = None,
    total: int | None = None,
    result: dict[str, Any] | None = None,
) -> Job:
    """Update done/total and optionally append one per-item result. Commits."""
    if done is not None:
        job.done = done
    if total is not None:
        job.total = total
    if result is not None:
        job.results = [*(job.results or []), result]
    session.commit()
    return job


def complete(session: Session, job: Job, *, results: list[Any] | None = None) -> Job:
    job.status = e.JobStatus.DONE.value
    job.finished_at = utcnow()
    if results is not None:
        job.results = list(results)
    session.commit()
    return job


def _format_error(error: str | BaseException) -> str:
    """``jobs.error`` and ``documents.ingest_error`` text: the type and a short, sanitised
    message (no SQL, bound parameters or raw provider bodies), never the raw exception."""
    text = error if isinstance(error, str) else format_error(error, limit=_MAX_ERROR_LENGTH)
    return text[:_MAX_ERROR_LENGTH]


def _fail_terminal(session: Session, job: Job, error: str) -> None:
    """Terminal failure: status failed, job_failed event, document ingest_status failed."""
    job.status = e.JobStatus.FAILED.value
    job.error = error
    job.finished_at = utcnow()
    payload = job.payload or {}
    record_event(
        session,
        e.EntityType.JOB,
        job.id,
        e.EventType.JOB_FAILED,
        SYSTEM_ACTOR,
        {
            "kind": job.kind,
            "error": error,
            "attempts": job.attempts,
            "started_by": payload.get("actor"),
        },
        org_id=job.org_id,
    )
    if job.kind in _DOCUMENT_JOB_KINDS and payload.get("document_id"):
        document = session.get(Document, uuid.UUID(str(payload["document_id"])))
        if document is not None:
            document.ingest_status = e.IngestStatus.FAILED.value
            document.ingest_error = error


def fail(session: Session, job: Job, error: str | BaseException) -> Job:
    """Fail a job for good, regardless of attempts. Commits."""
    _fail_terminal(session, job, _format_error(error))
    session.commit()
    return job


def handle_failure(session: Session, job: Job, error: str | BaseException) -> Job:
    """The plan's rule for a run that raised: requeue below max_attempts, else fail. Commits.

    A requeue never resets ``attempts``; the exception text is kept in ``error``.
    """
    message = _format_error(error)
    if job.attempts < get_settings().max_attempts:
        job.status = e.JobStatus.QUEUED.value
        job.error = message
        job.finished_at = None
    else:
        _fail_terminal(session, job, message)
    session.commit()
    return job


def requeue_stale_on_startup(session: Session) -> tuple[int, int]:
    """Handle jobs left ``running`` by a dead worker. Returns (requeued, failed). Commits."""
    max_attempts = get_settings().max_attempts
    stale = session.scalars(select(Job).where(Job.status == e.JobStatus.RUNNING.value)).all()
    requeued = failed = 0
    for job in stale:
        if job.attempts < max_attempts:
            job.status = e.JobStatus.QUEUED.value
            job.error = "worker restarted during job"
            job.finished_at = None
            requeued += 1
        else:
            _fail_terminal(session, job, "worker died during job")
            failed += 1
    session.commit()
    return requeued, failed
