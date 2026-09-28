"""Attest and dispute on segments of the current answer version.

Both return ``{answer, question}``: the full updated answer and the question detail.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import Actor, DbSession, OrgId
from app.api.questions import answer_record, question_detail
from app.db.models import Answer, Question
from app.review.actions import ActionNotPermitted, SegmentNotFound, attest, dispute

router = APIRouter(prefix="/answers", tags=["answers"])


class AttestBody(BaseModel):
    note: str | None = None


class DisputeBody(BaseModel):
    note: str = Field(min_length=1)


def _load_answer(session: Session, answer_id: uuid.UUID, org_id: uuid.UUID) -> Answer:
    answer = session.get(Answer, answer_id)
    if answer is None or answer.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Answer not found.")
    return answer


def _respond(session: Session, answer: Answer) -> dict[str, Any]:
    question = session.get(Question, answer.question_id)
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Question not found.")
    return {
        "answer": answer_record(session, answer),
        "question": question_detail(session, question),
    }


@router.post("/{answer_id}/segments/{index}/attest")
def attest_segment(
    answer_id: uuid.UUID,
    index: int,
    db: DbSession,
    org_id: OrgId,
    actor: Actor,
    body: AttestBody | None = None,
) -> Any:
    """``{note?}``; only on an unsupported, weak or human_authored segment of the current
    version (409 otherwise)."""
    answer = _load_answer(db, answer_id, org_id)
    try:
        attest(db, answer, index, actor, body.note if body else None)
    except SegmentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ActionNotPermitted as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return _respond(db, answer)


@router.post("/{answer_id}/segments/{index}/dispute")
def dispute_segment(
    answer_id: uuid.UUID,
    index: int,
    body: DisputeBody,
    db: DbSession,
    org_id: OrgId,
    actor: Actor,
) -> Any:
    """``{note}``; only on a supported segment of the current version (409 otherwise)."""
    answer = _load_answer(db, answer_id, org_id)
    try:
        dispute(db, answer, index, actor, body.note)
    except SegmentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ActionNotPermitted as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return _respond(db, answer)
