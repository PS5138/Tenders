"""The worker process: a Postgres-backed job loop plus the idle-time fact-expiry sweep.

Run with ``python -m app.worker``. Exactly one worker runs in the hackathon. Handlers are
registered by the owning modules through ``app.jobs.register`` and imported here by name.
"""

from __future__ import annotations

import importlib
import logging
import signal
import threading
import time

from sqlalchemy.orm import Session

from app.config import ProviderConfigurationError, check_provider_configuration, get_settings
from app.db import enums as e
from app.db.models import Job
from app.db.session import new_session
from app.jobs import (
    claim_next,
    complete,
    fail,
    get_handler,
    handle_failure,
    registered_kinds,
    requeue_stale_on_startup,
)
from app.llm.scope import org_scope, synthetic_for_org
from app.review.invalidation import run_expiry_sweep

logger = logging.getLogger("tenders.worker")

# Modules that register job handlers on import. Each is optional until its owner lands it.
HANDLER_MODULES: tuple[str, ...] = (
    "app.ingest.jobs",  # ingest_document
    "app.ingest.questions",  # extract_questions
    "app.retrieve.triage",  # triage_tender
    "app.generate.jobs",  # draft_all
    "app.ingest.requirements",  # extract_requirements
)


def import_handlers() -> list[str]:
    """Import every handler module that exists; report the kinds registered afterwards."""
    for module_name in HANDLER_MODULES:
        try:
            importlib.import_module(module_name)
        except ImportError as exc:
            # Only swallow the absence of the module itself, never an ImportError inside it.
            if exc.name not in (module_name, None):
                raise
            logger.warning("handler module %s not available yet: %s", module_name, exc)
    kinds = registered_kinds()
    logger.info("registered job handlers: %s", ", ".join(kinds) or "none")
    return kinds


def run_job(session: Session, job: Job) -> None:
    """Dispatch one claimed job and apply the failure rule."""
    handler = get_handler(job.kind)
    if handler is None:
        fail(session, job, f"no handler registered for job kind '{job.kind}'")
        return
    # Captured before the handler runs: a flush that fails inside the handler rolls the
    # session's transaction back and expires every instance, so reading ``job.id`` in the
    # except block would itself raise ``PendingRollbackError`` and strand the job at
    # ``running``. Plain values are safe to log; the ORM row is touched only after rollback.
    job_id, kind, attempts = job.id, job.kind, job.attempts
    # The job runs on the providers its organisation calls for (``app.llm.scope``).
    synthetic = synthetic_for_org(session, job.org_id)
    try:
        with org_scope(synthetic):
            handler(session, job)
    except Exception as exc:  # noqa: BLE001 - the loop must survive any handler failure
        logger.exception("job %s (%s) attempt %d raised", job_id, kind, attempts)
        session.rollback()
        handle_failure(session, job, exc)
        return
    if job.status == e.JobStatus.RUNNING.value:
        complete(session, job)


def run_once(session: Session) -> bool:
    """Claim and run one job. Returns False when the queue was empty."""
    job = claim_next(session)
    if job is None:
        return False
    logger.info("running job %s (%s) attempt %d", job.id, job.kind, job.attempts)
    run_job(session, job)
    return True


class WorkerLoop:
    def __init__(self, session: Session, *, stop: threading.Event | None = None) -> None:
        self.session = session
        self.stop = stop or threading.Event()
        self.settings = get_settings()
        self._last_sweep: float | None = None

    def maybe_sweep(self) -> bool:
        """Run the fact-expiry sweep when idle, at most once per configured interval."""
        now = time.monotonic()
        interval = self.settings.expiry_sweep_interval_seconds
        if self._last_sweep is not None and now - self._last_sweep < interval:
            return False
        self._last_sweep = now
        try:
            expired = run_expiry_sweep(self.session)
            self.session.commit()
            logger.info("expiry sweep complete: %s fact(s) expired", expired)
        except Exception:  # noqa: BLE001
            logger.exception("expiry sweep failed")
            self.session.rollback()
        return True

    def run_forever(self) -> None:
        requeued, failed = requeue_stale_on_startup(self.session)
        if requeued or failed:
            logger.info("start-up: requeued %d stale job(s), failed %d", requeued, failed)
        while not self.stop.is_set():
            ran = False
            try:
                ran = run_once(self.session)
            except Exception:  # noqa: BLE001
                logger.exception("worker loop error")
                self.session.rollback()
            if ran:
                continue
            self.maybe_sweep()
            self.stop.wait(self.settings.worker_poll_interval_seconds)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # A missing provider key is a start-up failure here, not a failed job an hour later.
    try:
        check_provider_configuration(settings)
    except ProviderConfigurationError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    import_handlers()
    stop = threading.Event()

    def _request_stop(signum, _frame) -> None:  # noqa: ANN001
        logger.info("received signal %s, stopping after the current job", signum)
        stop.set()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    session = new_session()
    try:
        WorkerLoop(session, stop=stop).run_forever()
    finally:
        session.close()
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
