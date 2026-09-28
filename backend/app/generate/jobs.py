"""The ``draft_all`` job: the single-question pipeline for every eligible question of a tender,
run inside the worker with a concurrency limit, no stream and no queue.

Eligible: status ``not_started``, coverage ``covered`` or ``partial`` (plus ``new`` when the
payload's ``include_new`` is true), not a pricing question, and no human version. Per-item
outcomes go to ``results`` as ``{item_id, outcome: drafted | skipped | failed, detail}``; a
failure never raises to the job. Each worker thread uses its own session; the job's session
only records progress.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.enums import AuthorType, Coverage, JobKind, QuestionStatus, ResponseType
from app.db.models import Answer, Job, Question
from app.db.session import new_session
from app.generate import pipeline
from app.generate.errors import Conflict409, format_error
from app.jobs import register, set_progress
from app.review.events import SYSTEM_ACTOR

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 4


def concurrency_limit() -> int:
    settings = get_settings()
    return int(settings.draft_all_concurrency)


def eligible_questions(
    session: Session, tender_id: uuid.UUID, *, include_new: bool
) -> list[Question]:
    """Questions draft-all may touch, in ``order_index`` order."""
    coverages = [Coverage.COVERED.value, Coverage.PARTIAL.value]
    if include_new:
        coverages.append(Coverage.NEW.value)
    return list(
        session.scalars(
            select(Question)
            .where(
                Question.tender_id == tender_id,
                Question.status == QuestionStatus.NOT_STARTED.value,
                Question.coverage.in_(coverages),
                Question.response_type != ResponseType.PRICING.value,
            )
            .order_by(Question.order_index)
        )
    )


def has_human_version(session: Session, question_id: uuid.UUID) -> bool:
    return (
        session.scalars(
            select(Answer.id).where(
                Answer.question_id == question_id, Answer.author_type == AuthorType.USER.value
            )
        ).first()
        is not None
    )


def draft_one(session: Session, question_id: uuid.UUID, actor: str) -> dict[str, Any]:
    """Draft one question in ``session`` and commit. Never raises; returns the result row."""
    item_id = str(question_id)
    try:
        question = session.get(Question, question_id)
        if question is None:
            return {"item_id": item_id, "outcome": "skipped", "detail": "question no longer exists"}
        if question.status != QuestionStatus.NOT_STARTED.value:
            return {"item_id": item_id, "outcome": "skipped", "detail": f"status {question.status}"}
        if has_human_version(session, question.id):
            return {"item_id": item_id, "outcome": "skipped", "detail": "has a human version"}
        answer = pipeline.draft_question(session, question, actor=actor, confirm_displace=False)
        session.commit()
        return {"item_id": item_id, "outcome": "drafted", "detail": str(answer.id)}
    except Conflict409 as exc:
        session.rollback()
        return {"item_id": item_id, "outcome": "skipped", "detail": exc.message}
    except Exception as exc:  # noqa: BLE001 - per-item failures are recorded, never raised
        logger.exception("draft_all: question %s failed", question_id)
        session.rollback()
        return {"item_id": item_id, "outcome": "failed", "detail": format_error(exc)}


def run_draft_all(
    session: Session,
    job: Job,
    *,
    session_factory: Callable[[], Session] = new_session,
    concurrency: int | None = None,
) -> None:
    payload = job.payload or {}
    tender_id = uuid.UUID(str(payload["tender_id"]))
    include_new = bool(payload.get("include_new", False))
    actor = str(payload.get("actor") or SYSTEM_ACTOR)
    questions = eligible_questions(session, tender_id, include_new=include_new)
    ids = [question.id for question in questions]
    set_progress(session, job, done=0, total=len(ids))
    if not ids:
        return

    limit = max(1, concurrency if concurrency is not None else concurrency_limit())
    done = 0
    if limit == 1:
        for question_id in ids:
            result = draft_one(session, question_id, actor)
            done += 1
            set_progress(session, job, done=done, result=result)
        return

    def in_own_session(question_id: uuid.UUID) -> dict[str, Any]:
        own = session_factory()
        try:
            return draft_one(own, question_id, actor)
        finally:
            own.close()

    with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="draft-all") as pool:
        for result in pool.map(in_own_session, ids):
            done += 1
            set_progress(session, job, done=done, result=result)


@register(JobKind.DRAFT_ALL)
def draft_all(session: Session, job: Job) -> None:
    run_draft_all(session, job)


__all__ = [
    "DEFAULT_CONCURRENCY",
    "draft_all",
    "draft_one",
    "eligible_questions",
    "has_human_version",
    "run_draft_all",
]
