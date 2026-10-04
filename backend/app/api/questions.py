"""Questions: detail, patch, evidence, answers, gaps, events, comments.

The draft stream (``POST /questions/{id}/draft``) and accept-verbatim
(``POST /questions/{id}/verbatim``) are owner E's and live in ``app.api.drafts``; they are
deliberately absent here so that router is never shadowed.

This module also holds the serialisers (``answer_record``, ``question_detail``,
``question_list_item``) that ``app.api.answers`` and ``app.api.threads`` reuse.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from pydantic_core import to_jsonable_python
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import ACTOR_MAX_LENGTH, Actor, DbSession, OrgId
from app.api.schemas import (
    AnswerRecord,
    CommentRecord,
    EventRecord,
    QuestionDetail,
    QuestionListItem,
    QuestionRecord,
)
from app.db.enums import ComplianceClass, EntityType, EventType, QuestionStatus
from app.db.models import (
    Answer,
    Comment,
    Document,
    Event,
    KnowledgeItem,
    Question,
    QuestionEvidence,
    Thread,
)
from app.review.actions import GapNotFound, acknowledge_gap
from app.review.events import record_event
from app.review.transitions import (
    TransitionBlocked,
    allowed_transitions,
    current_answer,
    transition,
)
from app.review.versions import EmptyEdit, StaleBaseVersion, save_human_edit

router = APIRouter(prefix="/questions", tags=["questions"])


# --- Serialisers ------------------------------------------------------------------------------


def _verbatim_offer(session: Session, item_id: uuid.UUID | None) -> dict[str, Any] | None:
    """``verbatim: {source_item_id, segments}`` built on read from owner E's splitter over the
    offered item's answer text; None when nothing is offered or the builder is unavailable."""
    if item_id is None:
        return None
    item = session.get(KnowledgeItem, item_id)
    if item is None:
        return None
    try:
        from app.generate.verbatim import build_verbatim_segments
    except ImportError:
        return None
    segments = build_verbatim_segments(session, item)
    return {"source_item_id": str(item.id), "similarity": None, "segments": segments}


def answer_record(session: Session, answer: Answer) -> dict[str, Any]:
    """One item of ``GET /questions/{id}/answers`` (and ``current_answer`` in full)."""
    return AnswerRecord.model_validate(
        {
            "id": answer.id,
            "question_id": answer.question_id,
            "version": answer.version,
            "author_type": answer.author_type,
            "author_name": answer.author_name,
            "text": answer.text,
            "word_count": answer.word_count,
            "segments": answer.segments or [],
            "gaps": answer.gaps or [],
            "fact_checklist": answer.fact_checklist or [],
            "verbatim_offer_item_id": answer.verbatim_offer_item_id,
            "verbatim_source_item_id": answer.verbatim_source_item_id,
            "verbatim": _verbatim_offer(session, answer.verbatim_offer_item_id),
            "support_summary": answer.support_summary or {},
            "model": answer.model,
            "prompt_version": answer.prompt_version,
            "is_current": answer.is_current,
            "created_at": answer.created_at,
            "updated_at": answer.updated_at,
        }
    ).model_dump(mode="json")


def thread_id_for(session: Session, question: Question) -> uuid.UUID | None:
    return session.scalar(select(Thread.id).where(Thread.question_id == question.id))


def _question_row(session: Session, question: Question) -> dict[str, Any]:
    row = {
        column: getattr(question, column)
        for column in QuestionRecord.model_fields
        if column != "thread_id"
    }
    row["thread_id"] = thread_id_for(session, question)
    return row


def question_row(session: Session, question: Question) -> dict[str, Any]:
    """The question row plus ``thread_id``, JSON-ready."""
    return QuestionRecord.model_validate(_question_row(session, question)).model_dump(mode="json")


def draft_in_progress(question: Question) -> bool:
    """From owner E's in-process registry; false when the runner is not available."""
    try:
        from app.generate.runner import draft_in_progress as check
    except ImportError:
        return False
    return bool(check(question.id))


def current_answer_summary(session: Session, question: Question) -> dict[str, Any] | None:
    answer = current_answer(session, question)
    if answer is None:
        return None
    return {
        "id": answer.id,
        "version": answer.version,
        "author_type": answer.author_type,
        "author_name": answer.author_name,
        "word_count": answer.word_count,
        "support_summary": answer.support_summary or {},
        "updated_at": answer.updated_at,
    }


def question_list_item(session: Session, question: Question) -> dict[str, Any]:
    """Row of ``GET /tenders/{id}/questions``: the question plus a current-answer summary."""
    return QuestionListItem.model_validate(
        {
            **_question_row(session, question),
            "current_answer": current_answer_summary(session, question),
        }
    ).model_dump(mode="json")


def _evidence(session: Session, question: Question) -> list[dict[str, Any]]:
    rows = session.execute(
        select(QuestionEvidence, Document.filename)
        .join(Document, Document.id == QuestionEvidence.document_id)
        .where(QuestionEvidence.question_id == question.id)
        .order_by(QuestionEvidence.created_at)
    ).all()
    return [
        {"document_id": evidence.document_id, "filename": filename, "note": evidence.note}
        for evidence, filename in rows
    ]


def _comments(session: Session, question: Question) -> list[Comment]:
    return list(
        session.scalars(
            select(Comment).where(Comment.question_id == question.id).order_by(Comment.created_at)
        ).all()
    )


def question_detail(session: Session, question: Question) -> dict[str, Any]:
    """The full ``GET /questions/{id}`` body."""
    answer = current_answer(session, question)
    return QuestionDetail.model_validate(
        {
            **_question_row(session, question),
            "evidence": _evidence(session, question),
            "comments": [CommentRecord.model_validate(c) for c in _comments(session, question)],
            "current_answer": answer_record(session, answer) if answer is not None else None,
            "draft_in_progress": draft_in_progress(question),
            "allowed_transitions": allowed_transitions(session, question),
        }
    ).model_dump(mode="json")


def load_question(session: Session, question_id: uuid.UUID, org_id: uuid.UUID) -> Question:
    question = session.get(Question, question_id)
    if question is None or question.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Question not found.")
    return question


# --- Request bodies ---------------------------------------------------------------------------


class QuestionPatch(BaseModel):
    # ``questions.assignee`` is String(200) like the actor-bearing columns; refuse here with 422
    # rather than at the database.
    assignee: str | None = Field(default=None, max_length=ACTOR_MAX_LENGTH)
    status: QuestionStatus | None = None
    compliance_class: ComplianceClass | None = None
    compliant_by: date | None = None


class EvidenceBody(BaseModel):
    document_id: uuid.UUID
    note: str | None = None


class HumanEditBody(BaseModel):
    text: str = Field(min_length=1)
    base_version_id: uuid.UUID | None = None


class AcknowledgeBody(BaseModel):
    gap: str = Field(min_length=1)
    note: str | None = None


class CommentBody(BaseModel):
    # Stripped before the length check, so a comment of only spaces is a 422 rather than an
    # empty row in the audit trail.
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


# --- Routes -----------------------------------------------------------------------------------


@router.get("/{question_id}", response_model=QuestionDetail)
def get_question(question_id: uuid.UUID, db: DbSession, org_id: OrgId) -> Any:
    """Question row plus thread_id, evidence, comments, current_answer in full,
    draft_in_progress and allowed_transitions."""
    return question_detail(db, load_question(db, question_id, org_id))


@router.patch("/{question_id}", response_model=QuestionDetail)
def patch_question(
    question_id: uuid.UUID, body: QuestionPatch, db: DbSession, org_id: OrgId, actor: Actor
) -> Any:
    """assignee, status, compliance_class, compliant_by. A status that is not allowed returns
    409 with ``{detail, to, blockers, question}``; the other fields are never gated, so they
    are applied and committed either way and ``question`` is the row as it now stands."""
    question = load_question(db, question_id, org_id)
    provided = body.model_fields_set
    if "assignee" in provided and body.assignee != question.assignee:
        previous = question.assignee
        question.assignee = body.assignee
        record_event(
            db,
            EntityType.QUESTION,
            question.id,
            EventType.ASSIGNED,
            actor,
            {"from": previous, "to": body.assignee},
            org_id=question.org_id,
        )
    if "compliance_class" in provided or "compliant_by" in provided:
        previous_class, previous_by = question.compliance_class, question.compliant_by
        if "compliance_class" in provided:
            question.compliance_class = (
                body.compliance_class.value if body.compliance_class is not None else None
            )
        if "compliant_by" in provided:
            question.compliant_by = body.compliant_by
        if (question.compliance_class, question.compliant_by) != (previous_class, previous_by):
            record_event(
                db,
                EntityType.QUESTION,
                question.id,
                EventType.COMPLIANCE_CLASS_SET,
                actor,
                {
                    "from": {
                        "compliance_class": previous_class,
                        "compliant_by": to_jsonable_python(previous_by),
                    },
                    "to": {
                        "compliance_class": question.compliance_class,
                        "compliant_by": to_jsonable_python(question.compliant_by),
                    },
                },
                org_id=question.org_id,
            )
    if "status" in provided and body.status is not None:
        try:
            transition(db, question, body.status, actor)
        except TransitionBlocked as exc:
            db.commit()
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "detail": str(exc),
                    "to": exc.to,
                    "blockers": exc.blockers,
                    "question": jsonable_encoder(question_detail(db, question)),
                },
            )
    db.commit()
    return question_detail(db, question)


@router.post("/{question_id}/evidence", status_code=201, response_model=QuestionDetail)
def add_evidence(
    question_id: uuid.UUID, body: EvidenceBody, db: DbSession, org_id: OrgId, actor: Actor
) -> Any:
    question = load_question(db, question_id, org_id)
    document = db.get(Document, body.document_id)
    if document is None or document.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found.")
    existing = db.scalars(
        select(QuestionEvidence).where(
            QuestionEvidence.question_id == question.id,
            QuestionEvidence.document_id == document.id,
        )
    ).first()
    if existing is not None:
        existing.note = body.note
    else:
        db.add(
            QuestionEvidence(
                org_id=question.org_id,
                question_id=question.id,
                document_id=document.id,
                note=body.note,
            )
        )
    record_event(
        db,
        EntityType.QUESTION,
        question.id,
        EventType.EVIDENCE_ADDED,
        actor,
        {"document_id": str(document.id), "filename": document.filename, "note": body.note},
        org_id=question.org_id,
    )
    db.commit()
    return question_detail(db, question)


@router.delete("/{question_id}/evidence/{document_id}", status_code=204)
def remove_evidence(
    question_id: uuid.UUID, document_id: uuid.UUID, db: DbSession, org_id: OrgId, actor: Actor
) -> None:
    question = load_question(db, question_id, org_id)
    evidence = db.scalars(
        select(QuestionEvidence).where(
            QuestionEvidence.question_id == question.id,
            QuestionEvidence.document_id == document_id,
        )
    ).first()
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Evidence not found.")
    db.delete(evidence)
    db.commit()


@router.get("/{question_id}/answers", response_model=list[AnswerRecord])
def list_answers(question_id: uuid.UUID, db: DbSession, org_id: OrgId) -> Any:
    question = load_question(db, question_id, org_id)
    answers = db.scalars(
        select(Answer).where(Answer.question_id == question.id).order_by(Answer.version)
    ).all()
    return [answer_record(db, answer) for answer in answers]


@router.post("/{question_id}/answers", status_code=201)
def save_answer(
    question_id: uuid.UUID, body: HumanEditBody, db: DbSession, org_id: OrgId, actor: Actor
) -> Any:
    """Human edit: ``{text, base_version_id}``; re-split, re-align, one batched entailment
    call. Returns ``{answer, question}``; 409 with ``{current_answer}`` on a stale base; 422
    when the authoritative splitter finds no sentence in ``text`` (whitespace only)."""
    question = load_question(db, question_id, org_id)
    try:
        answer = save_human_edit(db, question, body.text, body.base_version_id, actor)
    except EmptyEdit as exc:
        # Raised before anything is written; the row lock is released with the request session.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.detail) from exc
    except StaleBaseVersion as exc:
        # Raised before anything is written, so there is nothing to roll back.
        current = exc.current_answer
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": "base_version_id is not the current version; reload and retry.",
                "current_answer": answer_record(db, current) if current is not None else None,
            },
        )
    db.commit()
    return {"answer": answer_record(db, answer), "question": question_detail(db, question)}


@router.post("/{question_id}/gaps/acknowledge", response_model=QuestionDetail)
def acknowledge(
    question_id: uuid.UUID, body: AcknowledgeBody, db: DbSession, org_id: OrgId, actor: Actor
) -> Any:
    """``{gap, note}``; gap must equal a current gap after normalisation (422 otherwise)."""
    question = load_question(db, question_id, org_id)
    try:
        acknowledge_gap(db, question, body.gap, body.note, actor)
    except GapNotFound as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    db.commit()
    return question_detail(db, question)


@router.get("/{question_id}/events", response_model=list[EventRecord])
def list_events(question_id: uuid.UUID, db: DbSession, org_id: OrgId) -> Any:
    """Events on the question and on its answer versions, newest first."""
    question = load_question(db, question_id, org_id)
    answer_ids = db.scalars(select(Answer.id).where(Answer.question_id == question.id)).all()
    conditions = [
        (Event.entity_type == EntityType.QUESTION.value) & (Event.entity_id == question.id)
    ]
    if answer_ids:
        conditions.append(
            (Event.entity_type == EntityType.ANSWER.value) & Event.entity_id.in_(answer_ids)
        )
    events = db.scalars(
        select(Event).where(or_(*conditions)).order_by(Event.created_at.desc(), Event.id.desc())
    ).all()
    return [EventRecord.model_validate(event) for event in events]


@router.post("/{question_id}/comments", status_code=201, response_model=CommentRecord)
def add_comment(
    question_id: uuid.UUID, body: CommentBody, db: DbSession, org_id: OrgId, actor: Actor
) -> Any:
    question = load_question(db, question_id, org_id)
    comment = Comment(
        org_id=question.org_id, question_id=question.id, author=actor, text=body.text.strip()
    )
    db.add(comment)
    db.flush()
    record_event(
        db,
        EntityType.QUESTION,
        question.id,
        EventType.COMMENT_ADDED,
        actor,
        {"comment_id": str(comment.id), "text": comment.text},
        org_id=question.org_id,
    )
    db.commit()
    return CommentRecord.model_validate(comment)
