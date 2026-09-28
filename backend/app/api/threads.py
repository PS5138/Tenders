"""Threads and messages: create a tender-level thread, read a thread, save a message as an
answer.

The reply stream (``POST /threads/{id}/messages``) is owner E's and lives in
``app.api.drafts``; it is deliberately absent here so that router is never shadowed.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Actor, DbSession, OrgId
from app.api.questions import _verbatim_offer, answer_record, question_detail
from app.api.schemas import ThreadRecord
from app.db.models import Message, Question, Tender, Thread
from app.generate.errors import Conflict409
from app.review.messages import MessageNotSaveable, save_message_as_answer
from app.review.versions import DisplacementRequired

router = APIRouter(tags=["threads"])


class CreateThreadBody(BaseModel):
    tender_id: uuid.UUID
    title: str | None = None


class SaveAsAnswerBody(BaseModel):
    confirm_displace: bool = False


def reply_in_progress(thread: Thread) -> bool:
    """From owner E's in-process registry; false when the runner is not available."""
    try:
        from app.generate.runner import reply_in_progress as check
    except ImportError:
        return False
    return bool(check(thread.id))


def _message_record(session: Session, message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "segments": message.segments,
        "support_summary": message.support_summary,
        "gaps": message.gaps,
        "fact_checklist": message.fact_checklist,
        "verbatim": _verbatim_offer(session, message.verbatim_offer_item_id),
        "model": message.model,
        "prompt_version": message.prompt_version,
        "answer_id": message.answer_id,
        "created_at": message.created_at,
    }


def thread_record(session: Session, thread: Thread) -> dict[str, Any]:
    """The ``GET /threads/{id}`` body."""
    messages = session.scalars(
        select(Message)
        .where(Message.thread_id == thread.id)
        .order_by(Message.created_at, Message.id)
    ).all()
    return ThreadRecord.model_validate(
        {
            "id": thread.id,
            "tender_id": thread.tender_id,
            "question_id": thread.question_id,
            "title": thread.title,
            "reply_in_progress": reply_in_progress(thread),
            "messages": [_message_record(session, message) for message in messages],
        }
    ).model_dump(mode="json")


def _load_thread(session: Session, thread_id: uuid.UUID, org_id: uuid.UUID) -> Thread:
    thread = session.get(Thread, thread_id)
    if thread is None or thread.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Thread not found.")
    return thread


@router.post("/threads", status_code=201, response_model=ThreadRecord)
def create_thread(body: CreateThreadBody, db: DbSession, org_id: OrgId, actor: Actor) -> Any:
    """Tender-level threads only; question threads are created at extraction."""
    tender = db.get(Tender, body.tender_id)
    if tender is None or tender.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tender not found.")
    thread = Thread(
        org_id=tender.org_id,
        tender_id=tender.id,
        question_id=None,
        title=(body.title or "").strip() or f"{tender.name}: tender thread",
    )
    db.add(thread)
    db.commit()
    return thread_record(db, thread)


@router.get("/threads/{thread_id}", response_model=ThreadRecord)
def get_thread(thread_id: uuid.UUID, db: DbSession, org_id: OrgId) -> Any:
    return thread_record(db, _load_thread(db, thread_id, org_id))


@router.post("/messages/{message_id}/save-as-answer", status_code=201)
def save_as_answer(
    message_id: uuid.UUID,
    db: DbSession,
    org_id: OrgId,
    actor: Actor,
    body: SaveAsAnswerBody | None = None,
) -> Any:
    """``{confirm_displace?}``; 409 on a question past ``ai_draft`` without the flag, with the
    same ``{detail, code: "displacement", current_answer}`` body as the card draft and
    accept-verbatim so the interface's one confirmation dialog handles all three. Returns
    ``{answer, question}``."""
    message = db.get(Message, message_id)
    if message is None or message.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Message not found.")
    confirm = body.confirm_displace if body else False
    try:
        answer = save_message_as_answer(db, message, actor, confirm)
    except DisplacementRequired as exc:
        # Raised before anything is written, so there is nothing to roll back.
        current = exc.current_answer
        conflict = Conflict409(
            "displacement",
            str(exc),
            current_answer=answer_record(db, current) if current is not None else None,
        ).body()
        conflict["status"] = exc.question.status
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict)
    except MessageNotSaveable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    question = db.get(Question, answer.question_id)
    return {"answer": answer_record(db, answer), "question": question_detail(db, question)}
