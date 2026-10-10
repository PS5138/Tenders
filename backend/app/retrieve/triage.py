"""Coverage triage: the ``triage_tender`` job and ``retriage`` (plan: Question pack ingestion,
steps 2 to 4).

Every question in scope is judged by owner C's ``coverage_for_question`` in a thread pool
bounded by ``settings.triage_concurrency``. Each worker thread opens its own session through
``app.db.session.new_session``, persists ``coverage`` and ``coverage_detail`` for its question
and commits; the job thread records one result per question (``{item_id, outcome}``) and
done-over-total progress so the board fills in as it goes. The initial run covers questions
still at ``coverage = unknown``; a re-triage covers every question still at ``not_started``.

Two rules keep the job honest under the worker's requeue behaviour. The pool never exceeds the
engine's connection capacity less one, so the job thread's progress commits cannot starve behind
the worker threads (each holds a pooled connection for the whole judgement). And an initial run
reports ``total`` as the tender's question count with ``done`` starting at the number already
labelled, so a requeued attempt that re-covers only the remaining ``unknown`` questions still
shows done-over-total for the pack rather than for the remainder.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import UnboundExecutionError
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from app.config import get_settings
from app.db import enums as e
from app.db.models import Job, Question, Tender
from app.db.session import new_session
from app.errors import format_error
from app.jobs import enqueue, register, set_progress
from app.llm.scope import run_in_context

logger = logging.getLogger(__name__)

SCOPE_UNKNOWN = "unknown"
SCOPE_NOT_STARTED = "not_started"


class CoverageUnavailable(RuntimeError):
    """Raised when owner C's coverage function cannot be imported."""


class CoverageResultLike(Protocol):
    coverage: str
    detail: dict[str, Any]


CoverageFn = Callable[[Session, Question], CoverageResultLike]


def _load_coverage() -> CoverageFn:
    """Owner C's ``coverage_for_question``, imported lazily so this module imports on its own."""
    try:
        from app.retrieve.coverage import coverage_for_question
    except ImportError as exc:
        raise CoverageUnavailable(
            f"Coverage triage is unavailable: app.retrieve.coverage could not be imported ({exc})."
        ) from exc
    return coverage_for_question


def _make_executor(max_workers: int) -> Executor:
    return ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="triage")


def _pool_capacity(session: Session) -> int | None:
    """Connections the job session's engine can hand out at once (``pool_size`` plus
    ``max_overflow`` for a ``QueuePool``); ``None`` when the pool does not bound them."""
    try:
        bind = session.get_bind()
    except UnboundExecutionError:
        return None
    engine: Engine | None
    if isinstance(bind, Connection):
        engine = bind.engine
    elif isinstance(bind, Engine):
        engine = bind
    else:
        engine = None
    pool = getattr(engine, "pool", None)
    if not isinstance(pool, QueuePool):
        return None
    max_overflow = getattr(pool, "_max_overflow", None)
    if max_overflow is None or max_overflow < 0:  # -1 means unlimited overflow
        return None
    return int(pool.size()) + int(max_overflow)


def pool_workers(session: Session, requested: int) -> int:
    """Bound the triage pool by the engine's connection capacity, keeping one connection for the
    job thread's progress commits. Every worker thread holds a pooled connection from loading
    its question through retrieval and the judgement until it commits, so a pool sized at or
    above capacity leaves ``set_progress`` waiting on ``pool_timeout`` and, on a slow batch,
    raising ``TimeoutError`` from the job session."""
    capacity = _pool_capacity(session)
    if capacity is None:
        return max(1, requested)
    return max(1, min(requested, capacity - 1))


def _jsonable(detail: Any) -> dict[str, Any]:
    """``coverage_detail`` is JSONB; coerce UUIDs, dates and the like to strings."""
    if detail is None:
        return {}
    return json.loads(json.dumps(detail, default=str))


@dataclass
class TriageOutcome:
    question_id: uuid.UUID
    coverage: str | None
    error: str | None = None

    def as_result(self) -> dict[str, Any]:
        """The plan's ``[{item_id, outcome: coverage}]`` shape. A question whose judgement raised
        genuinely stays at ``unknown``, so that is its outcome; ``detail`` carries the error."""
        if self.error is not None:
            return {
                "item_id": str(self.question_id),
                "outcome": e.Coverage.UNKNOWN.value,
                "detail": self.error,
            }
        return {"item_id": str(self.question_id), "outcome": self.coverage}


def triage_one(question_id: uuid.UUID, coverage_fn: CoverageFn) -> TriageOutcome:
    """Worker-thread unit: own session, judge, persist coverage and detail, commit."""
    session = new_session()
    try:
        question = session.get(Question, question_id)
        if question is None:
            return TriageOutcome(question_id, None, "question no longer exists")
        result = coverage_fn(session, question)
        coverage = e.Coverage(str(result.coverage)).value
        question.coverage = coverage
        question.coverage_detail = _jsonable(getattr(result, "detail", {}))
        session.commit()
        return TriageOutcome(question_id, coverage)
    except Exception as exc:  # noqa: BLE001 - recorded per item, never raises to the pool
        logger.exception("triage: question %s failed", question_id)
        session.rollback()
        message = format_error(exc)
        _record_failure(session, question_id, message)
        return TriageOutcome(question_id, None, message)
    finally:
        session.close()


def _record_failure(session: Session, question_id: uuid.UUID, message: str) -> None:
    """Leave the reason on the card: ``coverage`` stays ``unknown`` and ``coverage_detail``
    carries ``{"error": ...}`` (never ``label_source``, which the plan types ``floor | llm`` and
    the harness counts). Best effort: a second failure here is logged and swallowed."""
    try:
        question = session.get(Question, question_id)
        if question is None:
            return
        question.coverage_detail = {"error": message}
        session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("triage: could not record the failure on question %s", question_id)
        session.rollback()


def questions_in_scope(session: Session, tender_id: uuid.UUID, scope: str) -> list[Question]:
    stmt = select(Question).where(Question.tender_id == tender_id).order_by(Question.order_index)
    if scope == SCOPE_NOT_STARTED:
        stmt = stmt.where(Question.status == e.QuestionStatus.NOT_STARTED.value)
    else:
        stmt = stmt.where(Question.coverage == e.Coverage.UNKNOWN.value)
    return list(session.scalars(stmt).all())


def count_questions(session: Session, tender_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Question).where(Question.tender_id == tender_id)
    return int(session.scalar(stmt) or 0)


@register(e.JobKind.TRIAGE_TENDER)
def triage_tender(session: Session, job: Job) -> None:
    """Handler for the ``triage_tender`` job. Commits through the job progress functions."""
    payload = job.payload or {}
    try:
        tender_id = uuid.UUID(str(payload["tender_id"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("triage_tender payload needs tender_id") from exc
    if session.get(Tender, tender_id) is None:
        raise ValueError("triage_tender: tender no longer exists")
    scope = str(payload.get("scope") or SCOPE_UNKNOWN)

    coverage_fn = _load_coverage()
    questions = questions_in_scope(session, tender_id, scope)
    question_ids = [question.id for question in questions]
    if scope == SCOPE_UNKNOWN:
        # ``total`` is the pack size. Questions already labelled (by an earlier attempt of this
        # job, whose threads committed before the worker died) count as done from the start,
        # and their results are kept; only the questions being rerun are replaced.
        total = count_questions(session, tender_id)
        already = total - len(question_ids)
        rerun = {str(qid) for qid in question_ids}
        job.results = [r for r in (job.results or []) if r.get("item_id") not in rerun]
    else:
        # A re-triage covers every ``not_started`` question, so a rerun redoes all of them.
        total = len(question_ids)
        already = 0
        job.results = []
    set_progress(session, job, done=already, total=total)
    if not question_ids:
        return

    settings = get_settings()
    outcomes: list[TriageOutcome] = []
    executor = _make_executor(pool_workers(session, settings.triage_concurrency))
    try:
        # Pool threads do not inherit the job's organisation scope (``app.llm.scope``).
        scoped_triage = run_in_context(triage_one)
        futures = [executor.submit(scoped_triage, qid, coverage_fn) for qid in question_ids]
        for done, future in enumerate(as_completed(futures), start=already + 1):
            outcome = future.result()
            outcomes.append(outcome)
            set_progress(session, job, done=done, result=outcome.as_result())
    finally:
        executor.shutdown(wait=True)

    # The worker threads committed on their own sessions; drop stale copies in this one.
    session.expire_all()
    if outcomes and all(outcome.error is not None for outcome in outcomes):
        raise RuntimeError(
            "triage_tender: every question failed; first error: " + str(outcomes[0].error)
        )


def retriage(session: Session, tender: Tender, actor: str) -> Job:
    """Enqueue a ``triage_tender`` run over the tender's ``not_started`` questions and record it
    as the tender's latest triage job. Flushes; the caller commits."""
    total = len(questions_in_scope(session, tender.id, SCOPE_NOT_STARTED))
    job = enqueue(
        session,
        e.JobKind.TRIAGE_TENDER,
        {"tender_id": str(tender.id), "actor": actor, "scope": SCOPE_NOT_STARTED},
        total=total,
        org_id=tender.org_id,
    )
    tender.triage_job_id = job.id
    session.flush()
    return job


__all__ = [
    "SCOPE_NOT_STARTED",
    "SCOPE_UNKNOWN",
    "CoverageUnavailable",
    "TriageOutcome",
    "count_questions",
    "pool_workers",
    "questions_in_scope",
    "retriage",
    "triage_one",
    "triage_tender",
]
