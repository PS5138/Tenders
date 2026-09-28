"""Postgres-backed job queue: table access, enqueue, progress and the handler registry.

Interface for module owners::

    from app.jobs import enqueue, register, set_progress

    @register("ingest_document")
    def ingest_document(session, job) -> None:
        ...  # raise to fail the attempt; return to complete

The worker (app/worker.py) claims jobs, dispatches to the registered handler, and applies
the attempts / max_attempts rule on exceptions.
"""

from app.jobs.queue import (
    claim_next,
    complete,
    enqueue,
    fail,
    handle_failure,
    requeue_stale_on_startup,
    set_progress,
)
from app.jobs.registry import JobHandler, get_handler, register, registered_kinds

__all__ = [
    "JobHandler",
    "claim_next",
    "complete",
    "enqueue",
    "fail",
    "get_handler",
    "handle_failure",
    "register",
    "registered_kinds",
    "requeue_stale_on_startup",
    "set_progress",
]
