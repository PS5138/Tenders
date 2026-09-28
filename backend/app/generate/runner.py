"""Detached execution of single-question drafts and thread replies (plan: architecture).

``start_draft`` and ``start_reply`` run on the event loop thread: they check the 409 rules
(pricing, displacement without ``confirm_displace``, a run already in progress), register the
run in the module-level registry keyed by question or thread id, and start an ``asyncio`` task
that runs the synchronous pipeline in a worker thread with its own session. The pipeline
pushes stream events onto an ``asyncio.Queue`` through ``loop.call_soon_threadsafe``; the
task serialises the persisted object, commits, emits ``done`` (or rolls back and emits
``error``), and removes its registry entry in a ``finally``. Serialisation runs before the
commit so a failure there is rolled back and ``error`` is accurate: nothing is persisted for a
failed run. ``events(run_key)`` only drains the queue, so a client disconnect stops the
response and nothing else: generation completes and persists regardless.

Both entry points take an optional ``org_id`` (the request's ``X-Org-Id``); when given, a
question or thread of another organisation is ``NotFound``, as every other router answers.

The registry and queues are process-local (one uvicorn worker). Nothing here is a ``jobs``
row.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.enums import ResponseType
from app.db.models import Message, Question, Thread
from app.db.session import new_session
from app.generate import pipeline
from app.generate.errors import Conflict409, DraftError, NotFound, error_detail

logger = logging.getLogger(__name__)

# The factory for a run's own session. Tests substitute one bound to their transaction.
session_factory: Callable[[], Session] = new_session

_END = None  # queue sentinel


@dataclass
class Run:
    key: str
    queue: asyncio.Queue[dict[str, Any] | None]
    task: asyncio.Task[None] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)  # for diagnostics and tests


_RUNS: dict[str, Run] = {}


def question_key(question_id: uuid.UUID | str) -> str:
    return f"question:{question_id}"


def thread_key(thread_id: uuid.UUID | str) -> str:
    return f"thread:{thread_id}"


def draft_in_progress(question_id: uuid.UUID | str) -> bool:
    return question_key(question_id) in _RUNS


def reply_in_progress(thread_id: uuid.UUID | str) -> bool:
    return thread_key(thread_id) in _RUNS


def active_runs() -> list[str]:
    return sorted(_RUNS)


# --- Starting runs ---------------------------------------------------------------------------


def _load_question(
    session: Session, question_id: uuid.UUID, org_id: uuid.UUID | None = None
) -> Question:
    question = session.get(Question, question_id)
    if question is None or (org_id is not None and question.org_id != org_id):
        raise NotFound(f"Question {question_id} does not exist.")
    return question


def _load_thread(session: Session, thread_id: uuid.UUID, org_id: uuid.UUID | None = None) -> Thread:
    thread = session.get(Thread, thread_id)
    if thread is None or (org_id is not None and thread.org_id != org_id):
        raise NotFound(f"Thread {thread_id} does not exist.")
    return thread


def start_draft(
    *,
    question_id: uuid.UUID,
    actor: str,
    instruction: str | None = None,
    confirm_displace: bool = False,
    org_id: uuid.UUID | None = None,
) -> str:
    """Check the 409 rules, register the run and start the detached task. Returns the run key.

    Raises ``NotFound`` for an unknown question (or one outside ``org_id`` when given) and
    ``Conflict409`` (``pricing``, ``displacement`` with ``current_answer``, ``in_progress``).
    Must be called with a running event loop.
    """
    key = question_key(question_id)
    session = session_factory()
    try:
        question = _load_question(session, question_id, org_id)
        if question.response_type == ResponseType.PRICING.value:
            raise Conflict409("pricing", "Pricing questions are never drafted.")
        pipeline.check_displacement(session, question, confirm_displace)
    finally:
        session.close()
    if key in _RUNS:
        raise Conflict409("in_progress", "A draft is already running for this question.")

    def work(session: Session, emit: pipeline.Emit) -> dict[str, Any]:
        question = _load_question(session, question_id)
        answer = pipeline.draft_question(
            session,
            question,
            actor=actor,
            instruction=instruction,
            confirm_displace=confirm_displace,
            emit=emit,
        )
        # Serialise before the commit: a failure here is rolled back with the version, so the
        # terminal ``error`` never contradicts what ``GET /questions/{id}`` then shows.
        persisted = pipeline.serialise_answer(session, answer)
        session.commit()
        return persisted

    return _launch(key, work)


def start_reply(
    *, thread_id: uuid.UUID, content: str, actor: str, org_id: uuid.UUID | None = None
) -> str:
    """As ``start_draft`` for a thread reply. The user message is persisted and committed
    first, inside the task, so it remains when the reply fails.

    Raises ``NotFound`` for an unknown thread (or one outside ``org_id`` when given),
    ``DraftError`` (``empty_message``) and ``Conflict409`` (``pricing`` on a pricing question's
    thread, since pricing questions receive no AI draft in any form; ``in_progress``). The
    pricing check runs before the user message is stored, so nothing is written.
    """
    key = thread_key(thread_id)
    if not content or not content.strip():
        raise DraftError("empty_message", "A message needs some content.")
    session = session_factory()
    try:
        thread = _load_thread(session, thread_id, org_id)
        question = thread.question
        if question is not None and question.response_type == ResponseType.PRICING.value:
            raise Conflict409("pricing", "Pricing questions are never drafted.")
    finally:
        session.close()
    if key in _RUNS:
        raise Conflict409("in_progress", "A reply is already running for this thread.")

    def work(session: Session, emit: pipeline.Emit) -> dict[str, Any]:
        thread = _load_thread(session, thread_id)
        user_message: Message = pipeline.persist_user_message(session, thread, content.strip())
        session.commit()
        session.refresh(thread)
        message = pipeline.reply_in_thread(
            session,
            thread,
            content=content.strip(),
            actor=actor,
            emit=emit,
            user_message=user_message,
        )
        persisted = pipeline.serialise_message(session, message)
        session.commit()
        return persisted

    return _launch(key, work)


# --- The detached task -----------------------------------------------------------------------


def _error_event(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, DraftError):
        code, message = exc.code, exc.message
    elif isinstance(exc, Conflict409):
        code, message = exc.code, exc.message
    else:
        # The exception's own message, without SQL text, bound parameters or raw provider
        # bodies: it is shown to the user where the action started.
        code = type(exc).__name__
        message = error_detail(exc) or "The draft failed."
    return {"type": "error", "code": code, "message": message}


def _run_sync(
    work: Callable[[Session, pipeline.Emit], dict[str, Any]], emit: pipeline.Emit
) -> None:
    """Runs in a worker thread with its own session: the pipeline, the commit, then ``done``
    or ``error``. Never raises."""
    session = session_factory()
    try:
        try:
            persisted = work(session, emit)
        except Exception as exc:  # noqa: BLE001 - every failure becomes an error event
            logger.exception("draft run failed")
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                logger.exception("rollback after a failed draft run also failed")
            emit(_error_event(exc))
            return
        emit({"type": "done", **persisted})
    finally:
        session.close()


def _launch(key: str, work: Callable[[Session, pipeline.Emit], dict[str, Any]]) -> str:
    loop = asyncio.get_running_loop()
    run = Run(key=key, queue=asyncio.Queue())
    _RUNS[key] = run

    def emit(event: dict[str, Any]) -> None:
        run.events.append(event)
        loop.call_soon_threadsafe(run.queue.put_nowait, event)

    async def task() -> None:
        try:
            await loop.run_in_executor(None, _run_sync, work, emit)
        except Exception:  # noqa: BLE001
            logger.exception("draft task failed outside the pipeline")
            run.queue.put_nowait(
                {"type": "error", "code": "runner_failure", "message": "The draft failed."}
            )
        finally:
            run.queue.put_nowait(_END)
            if _RUNS.get(key) is run:
                del _RUNS[key]

    run.task = loop.create_task(task(), name=f"draft-run:{key}")
    return key


def events(run_key: str) -> AsyncIterator[dict[str, Any]]:
    """Drain a run's events until its terminal ``done`` or ``error``. The run is captured when
    this is called, so call it right after ``start_*``; closing the iterator (client
    disconnect) stops draining and nothing else."""
    run = _RUNS.get(run_key)
    if run is None:
        raise KeyError(f"no run registered under {run_key!r}")
    return _drain(run)


async def _drain(run: Run) -> AsyncIterator[dict[str, Any]]:
    while True:
        event = await run.queue.get()
        if event is _END:
            return
        yield event


async def wait_for(run_key: str) -> None:
    """Await a run's task (tests and shutdown). A no-op when the run has already finished."""
    run = _RUNS.get(run_key)
    if run is not None and run.task is not None:
        await run.task


__all__ = [
    "Run",
    "active_runs",
    "draft_in_progress",
    "events",
    "question_key",
    "reply_in_progress",
    "session_factory",
    "start_draft",
    "start_reply",
    "thread_key",
    "wait_for",
]
