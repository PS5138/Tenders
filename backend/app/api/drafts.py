"""Drafting endpoints: the two streaming endpoints and accept-verbatim.

- ``POST /questions/{id}/draft`` (stream) body ``{instruction?, confirm_displace?}``.
- ``POST /threads/{id}/messages`` (stream) body ``{content}``.
- ``POST /questions/{id}/verbatim`` body ``{source_item_id, confirm_displace?}``; returns
  ``{answer, question}`` like the other non-streaming AI and human writes, so the client has
  the new status and ``allowed_transitions`` without a second request.

The streams are ``application/x-ndjson`` per ``app.api.streaming``; HTTP 200 is sent as soon
as the request is accepted and anything that fails afterwards arrives as an ``error`` event.
The 409 bodies are ``{detail, code}`` plus ``current_answer`` on a displacement conflict.
Every route is scoped by ``X-Org-Id`` like the rest of the API: a question or thread of
another organisation is a 404.

Registration (main.py, owner of that file): ``drafts.router`` must be included before
``questions.router`` and ``threads.router``, whose stub routes for these paths otherwise match
first; better still, remove those three stubs.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import Actor, DbSession, OrgId
from app.api.questions import answer_record, load_question, question_detail
from app.api.streaming import ndjson_response
from app.generate import runner
from app.generate.errors import Conflict409, DraftError, NotFound
from app.generate.verbatim import accept_verbatim

router = APIRouter(tags=["drafts"])


class DraftRequest(BaseModel):
    instruction: str | None = None
    confirm_displace: bool = False


class MessageRequest(BaseModel):
    content: str = Field(min_length=1)


class VerbatimRequest(BaseModel):
    source_item_id: uuid.UUID
    confirm_displace: bool = False


def conflict(exc: Conflict409) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=exc.body())


@router.post("/questions/{question_id}/draft", response_model=None)
async def draft_question(
    question_id: uuid.UUID, actor: Actor, org_id: OrgId, body: DraftRequest | None = None
) -> StreamingResponse | JSONResponse:
    """Streaming (NDJSON) card draft. 409 past ``ai_draft`` without ``confirm_displace``, 409
    while a draft is already running, 409 (never drafted) for a pricing question; 404 for a
    question outside the request's organisation."""
    body = body or DraftRequest()
    try:
        run_key = runner.start_draft(
            question_id=question_id,
            actor=actor,
            instruction=body.instruction,
            confirm_displace=body.confirm_displace,
            org_id=org_id,
        )
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except Conflict409 as exc:
        return conflict(exc)
    return ndjson_response(runner.events(run_key))


@router.post("/threads/{thread_id}/messages", response_model=None)
async def post_message(
    thread_id: uuid.UUID, body: MessageRequest, actor: Actor, org_id: OrgId
) -> StreamingResponse | JSONResponse:
    """Streaming (NDJSON) thread reply. The user message is persisted first; 409 while a reply
    is already running for the thread, 409 (never drafted) on a pricing question's thread; 404
    for a thread outside the request's organisation."""
    try:
        run_key = runner.start_reply(
            thread_id=thread_id, content=body.content, actor=actor, org_id=org_id
        )
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except Conflict409 as exc:
        return conflict(exc)
    except DraftError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message) from exc
    return ndjson_response(runner.events(run_key))


@router.post(
    "/questions/{question_id}/verbatim",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
)
def accept_verbatim_endpoint(
    question_id: uuid.UUID, body: VerbatimRequest, db: DbSession, actor: Actor, org_id: OrgId
) -> dict[str, Any] | JSONResponse:
    """A new ``ai`` version from the item's verbatim segments: support verification runs
    synchronously, the version becomes current and status moves to ``ai_draft``. Returns
    ``{answer, question}``. 409 codes: ``displacement``, ``in_progress``, ``pricing``,
    ``ineligible`` (an unverified item or one from a superseded document), ``empty``."""
    question = load_question(db, question_id, org_id)
    if runner.draft_in_progress(question.id):
        return conflict(Conflict409("in_progress", "A draft is already running for this question."))
    try:
        answer = accept_verbatim(
            db, question, body.source_item_id, actor, confirm_displace=body.confirm_displace
        )
    except Conflict409 as exc:
        db.rollback()
        return conflict(exc)
    except NotFound as exc:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    db.commit()
    return {"answer": answer_record(db, answer), "question": question_detail(db, question)}
