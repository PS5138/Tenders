"""Tenders: creation, list and detail with aggregates, tender documents, re-triage, draft-all,
submission, export and the one-call question list (plan: API surface, Tenders and jobs).

Every endpoint here runs synchronously in FastAPI's threadpool and commits the request's
session itself; the pipeline modules it calls flush only.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import Actor, DbSession, OrgId
from app.api.documents import DocumentWithJob, discard_upload, document_record
from app.api.schemas import (
    CurrentAnswerSummary,
    JobRecord,
    QuestionListItem,
    TenderDetail,
    TenderDocumentSummary,
    TenderListItem,
)
from app.config import get_settings
from app.db import enums as e
from app.db.models import Answer, Document, Event, Job, Question, Tender, Thread, utcnow
from app.jobs import enqueue
from app.review.events import record_event
from app.review.support import summarise_support

router = APIRouter(prefix="/tenders", tags=["tenders"])


# --- Request bodies -------------------------------------------------------------------------------


class TenderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    buyer: str | None = Field(default=None, max_length=256)
    deadline: datetime | None = None
    regime: Literal["procurement_act", "psr", "pcr_2015", "other"] | None = None
    is_framework: bool | None = None


class TenderPatch(BaseModel):
    """Fields are optional so a PATCH sends only what changes. ``outcome`` and ``name`` may be
    omitted but never null: both columns are NOT NULL and a tender with no outcome yet is
    ``pending``. The nullable columns (``buyer``, ``deadline``, ``outcome_notes``, ``regime``,
    ``is_framework``) accept null as a clear. ``status`` archives (``archived``) or restores
    (``open``) a tender; a restored tender that was submitted returns to ``submitted``, and only
    ``POST /tenders/{id}/submit`` submits."""

    name: str | None = Field(default=None, min_length=1, max_length=512)
    buyer: str | None = Field(default=None, max_length=256)
    deadline: datetime | None = None
    status: Literal["open", "archived"] | None = None
    outcome: Literal["pending", "won", "lost", "unknown"] | None = None
    outcome_notes: str | None = None
    regime: Literal["procurement_act", "psr", "pcr_2015", "other"] | None = None
    is_framework: bool | None = None

    @field_validator("name", "status")
    @classmethod
    def _not_null(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("this field cannot be null.")
        return value

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name cannot be blank.")
        return value.strip()

    @field_validator("outcome")
    @classmethod
    def _outcome_not_null(cls, value: str | None) -> str | None:
        # Defaults are not validated, so an omitted outcome still dumps to nothing under
        # exclude_unset; only an explicit null reaches here.
        if value is None:
            raise ValueError(
                "outcome cannot be null; use 'pending' for a tender with no outcome yet."
            )
        return value


class DraftAllRequest(BaseModel):
    include_new: bool = False


class SubmitResponse(BaseModel):
    tender: TenderDetail
    promoted: int


# --- Aggregates -----------------------------------------------------------------------------------


class Aggregates(BaseModel):
    questions_total: int = 0
    questions_approved: int = 0
    words_total: int = 0
    words_approved: int = 0
    c_count: int = 0
    unclassified_mandatory_count: int = 0
    needs_review_count: int = 0


def _aggregates(db: Session, tender_ids: list[uuid.UUID]) -> dict[uuid.UUID, Aggregates]:
    """One grouped query over questions joined to their current answers."""
    if not tender_ids:
        return {}
    approved = Question.status == e.QuestionStatus.APPROVED.value
    stmt = (
        select(
            Question.tender_id,
            func.count(Question.id),
            func.coalesce(func.sum(case((approved, 1), else_=0)), 0),
            func.coalesce(func.sum(Question.word_limit), 0),
            func.coalesce(
                func.sum(case((approved, func.coalesce(Answer.word_count, 0)), else_=0)), 0
            ),
            func.coalesce(
                func.sum(
                    case((Question.compliance_class == e.ComplianceClass.C.value, 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                Question.mandatory.is_(True),
                                Question.compliance_class.is_(None),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(func.sum(case((Question.needs_review.is_(True), 1), else_=0)), 0),
        )
        .select_from(Question)
        .outerjoin(
            Answer, and_(Answer.question_id == Question.id, Answer.is_current.is_(True))
        )
        .where(Question.tender_id.in_(tender_ids))
        .group_by(Question.tender_id)
    )
    result: dict[uuid.UUID, Aggregates] = {tender_id: Aggregates() for tender_id in tender_ids}
    for row in db.execute(stmt):
        result[row[0]] = Aggregates(
            questions_total=int(row[1]),
            questions_approved=int(row[2]),
            words_total=int(row[3]),
            words_approved=int(row[4]),
            c_count=int(row[5]),
            unclassified_mandatory_count=int(row[6]),
            needs_review_count=int(row[7]),
        )
    return result


def _days_remaining(deadline: datetime | None) -> int | None:
    if deadline is None:
        return None
    today: date = datetime.now(UTC).date()
    deadline_date = deadline.astimezone(UTC).date() if deadline.tzinfo else deadline.date()
    return (deadline_date - today).days


def _list_item(tender: Tender, aggregates: Aggregates) -> TenderListItem:
    return TenderListItem(
        id=tender.id,
        name=tender.name,
        buyer=tender.buyer,
        deadline=tender.deadline,
        days_remaining=_days_remaining(tender.deadline),
        status=tender.status,
        outcome=tender.outcome,
        questions_total=aggregates.questions_total,
        questions_approved=aggregates.questions_approved,
        words_total=aggregates.words_total,
        words_approved=aggregates.words_approved,
    )


def _detail(db: Session, tender: Tender) -> TenderDetail:
    aggregates = _aggregates(db, [tender.id])[tender.id]
    documents = db.scalars(
        select(Document).where(Document.tender_id == tender.id).order_by(Document.created_at)
    ).all()
    base = _list_item(tender, aggregates).model_dump()
    return TenderDetail(
        **base,
        outcome_notes=tender.outcome_notes,
        regime=tender.regime,
        is_framework=tender.is_framework,
        submitted_at=tender.submitted_at,
        extract_job_id=tender.extract_job_id,
        triage_job_id=tender.triage_job_id,
        c_count=aggregates.c_count,
        unclassified_mandatory_count=aggregates.unclassified_mandatory_count,
        needs_review_count=aggregates.needs_review_count,
        documents=[TenderDocumentSummary.model_validate(document) for document in documents],
        created_at=tender.created_at,
        updated_at=tender.updated_at,
    )


def _get_tender(db: Session, tender_id: uuid.UUID, org_id: uuid.UUID) -> Tender:
    tender = db.get(Tender, tender_id)
    if tender is None or tender.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tender not found.")
    return tender


# --- Tender CRUD ----------------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TenderDetail)
def create_tender(body: TenderCreate, db: DbSession, org_id: OrgId, actor: Actor) -> TenderDetail:
    tender = Tender(
        org_id=org_id,
        name=body.name.strip(),
        buyer=body.buyer.strip() if body.buyer else None,
        deadline=body.deadline,
        regime=body.regime,
        is_framework=body.is_framework,
        status=e.TenderStatus.OPEN.value,
        outcome=e.TenderOutcome.PENDING.value,
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)
    return _detail(db, tender)


@router.get("", response_model=list[TenderListItem])
def list_tenders(db: DbSession, org_id: OrgId) -> list[TenderListItem]:
    """[{id, name, buyer, deadline, days_remaining, status, outcome, questions_total,
    questions_approved, words_total, words_approved}], newest first."""
    tenders = db.scalars(
        select(Tender).where(Tender.org_id == org_id).order_by(Tender.created_at.desc())
    ).all()
    aggregates = _aggregates(db, [tender.id for tender in tenders])
    return [_list_item(tender, aggregates[tender.id]) for tender in tenders]


@router.get("/{tender_id}", response_model=TenderDetail)
def get_tender(tender_id: uuid.UUID, db: DbSession, org_id: OrgId) -> TenderDetail:
    """Tender row plus aggregates, c_count, unclassified_mandatory_count, needs_review_count,
    the latest job ids and the tender's documents."""
    return _detail(db, _get_tender(db, tender_id, org_id))


@router.patch("/{tender_id}", response_model=TenderDetail)
def patch_tender(
    tender_id: uuid.UUID, body: TenderPatch, db: DbSession, org_id: OrgId, actor: Actor
) -> TenderDetail:
    """outcome, outcome_notes, regime, is_framework. An outcome change writes an
    ``outcome_set`` event and re-tags the promoted-answers document when promotion has
    happened, so the library's tags follow the tender (plan: review pipeline)."""
    tender = _get_tender(db, tender_id, org_id)
    changes = body.model_dump(exclude_unset=True)
    previous_outcome = tender.outcome
    requested_status = changes.pop("status", None)
    if "buyer" in changes and changes["buyer"] is not None:
        changes["buyer"] = changes["buyer"].strip() or None
    for field_name, value in changes.items():
        setattr(tender, field_name, value)
    if requested_status == "archived":
        tender.status = e.TenderStatus.ARCHIVED.value
    elif requested_status == "open":
        tender.status = (
            e.TenderStatus.SUBMITTED.value if tender.submitted_at else e.TenderStatus.OPEN.value
        )
    db.flush()
    if "outcome" in changes and tender.outcome != previous_outcome:
        document = _retag_promotion_document(db, tender)
        record_event(
            db,
            e.EntityType.TENDER,
            tender.id,
            e.EventType.OUTCOME_SET,
            actor,
            {
                "outcome": tender.outcome,
                "previous_outcome": previous_outcome,
                "outcome_notes": tender.outcome_notes,
                "promotion_document_id": str(document.id) if document is not None else None,
            },
            org_id=org_id,
        )
    db.commit()
    db.refresh(tender)
    return _detail(db, tender)


@router.delete("/{tender_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tender(tender_id: uuid.UUID, db: DbSession, org_id: OrgId, actor: Actor) -> Response:
    """Delete a tender that was never submitted, with its documents, questions, answers,
    threads and their events. A submitted tender is a record (its approved answers may be in the
    library) and is archived instead: 409. Refused with 409 while a job or a draft for the
    tender is still running, so nothing writes into rows being removed."""
    tender = _get_tender(db, tender_id, org_id)
    if tender.submitted_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="A submitted tender is kept as a record. Archive it instead.",
        )
    running = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.org_id == org_id,
            Job.status.in_([e.JobStatus.QUEUED.value, e.JobStatus.RUNNING.value]),
            Job.payload["tender_id"].astext == str(tender.id),
        )
    )
    question_ids = list(db.scalars(select(Question.id).where(Question.tender_id == tender.id)))
    drafting = False
    try:
        from app.generate.runner import draft_in_progress as drafting_now

        drafting = any(drafting_now(question_id) for question_id in question_ids)
    except ImportError:
        pass
    if running or drafting:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This tender is still being processed. Try again when processing finishes.",
        )
    answer_ids = (
        list(db.scalars(select(Answer.id).where(Answer.question_id.in_(question_ids))))
        if question_ids
        else []
    )
    documents = list(db.scalars(select(Document).where(Document.tender_id == tender.id)))
    paths = [document.storage_path for document in documents if document.storage_path]
    entity_ids = [tender.id, *question_ids, *answer_ids, *(d.id for d in documents)]
    db.execute(delete(Event).where(Event.org_id == org_id, Event.entity_id.in_(entity_ids)))
    # Documents, sections, questions, answers, evidence, comments, threads and messages go by
    # ON DELETE CASCADE from the tender. A SQL delete, so the ORM does not try to detach
    # already-loaded children by nulling their tender_id first.
    db.execute(delete(Tender).where(Tender.id == tender.id, Tender.org_id == org_id))
    db.commit()
    db.expunge_all()
    root = Path(get_settings().storage_path).resolve()
    for stored in paths:
        target = Path(stored).resolve()
        if target.is_relative_to(root) and target.is_file():
            target.unlink(missing_ok=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Tender documents -----------------------------------------------------------------------------


def _safe_filename(filename: str | None) -> str:
    name = Path(filename or "upload").name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip() or "upload"
    return cleaned[:200]


def _store_upload(
    org_id: uuid.UUID, tender_id: uuid.UUID, document_id: uuid.UUID, upload: UploadFile
) -> str:
    """Store the bytes through owner A's ``app.ingest.storage.save_upload`` (the one place that
    touches the file system for documents); a local writer stands in if it is unavailable."""
    upload.file.seek(0)
    data = upload.file.read()
    try:
        from app.ingest.storage import save_upload
    except ImportError:
        directory = Path(get_settings().storage_path) / "tenders" / str(tender_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{document_id}_{_safe_filename(upload.filename)}"
        target.write_bytes(data)
        return str(target.resolve())
    return save_upload(data, upload.filename or "upload", document_id=document_id, org_id=org_id)


@router.post(
    "/{tender_id}/documents", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentWithJob
)
def upload_tender_document(
    tender_id: uuid.UUID,
    db: DbSession,
    org_id: OrgId,
    actor: Actor,
    tender_doc_kind: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> DocumentWithJob:
    """Multipart with tender_doc_kind. A question pack enqueues extract_questions and records
    it as tenders.extract_job_id; any other kind enqueues the parse-only ingest_document. Always
    returns the document with a top-level ``job_id``, in the same flat shape as
    ``POST /documents``; 409 for a second question pack."""
    tender = _get_tender(db, tender_id, org_id)
    kind = tender_doc_kind.strip().lower()
    if kind not in get_settings().tender_doc_kinds:
        raise HTTPException(
            422,
            detail=f"tender_doc_kind must be one of: {', '.join(get_settings().tender_doc_kinds)}.",
        )
    is_pack = kind == e.TenderDocKind.QUESTION_PACK.value
    if is_pack:
        existing = db.scalars(
            select(Document).where(
                Document.tender_id == tender.id,
                Document.tender_doc_kind == e.TenderDocKind.QUESTION_PACK.value,
            )
        ).first()
        if existing is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="This tender already has a question pack.",
                headers={"X-Document-Id": str(existing.id)},
            )

    document = Document(
        id=uuid.uuid4(),
        org_id=org_id,
        filename=_safe_filename(file.filename),
        storage_path="",
        doc_type=e.DocType.TENDER_DOCUMENT.value,
        tender_id=tender.id,
        tender_doc_kind=kind,
        classification_confirmed=True,
        effective_date=datetime.now(UTC).date(),
        effective_date_source=e.EffectiveDateSource.UPLOAD_TIME.value,
        ingest_status=e.IngestStatus.QUEUED.value,
    )
    document.storage_path = _store_upload(org_id, tender.id, document.id, file)
    try:
        db.add(document)
        db.flush()

        if is_pack:
            job = enqueue(
                db,
                e.JobKind.EXTRACT_QUESTIONS,
                {"tender_id": str(tender.id), "document_id": str(document.id), "actor": actor},
                org_id=org_id,
            )
            tender.extract_job_id = job.id
        else:
            job = enqueue(
                db,
                e.JobKind.INGEST_DOCUMENT,
                {"document_id": str(document.id), "actor": actor},
                total=1,
                org_id=org_id,
            )
        db.commit()
    except Exception:
        # The file was written before the row; a failed persist must not leave it orphaned.
        discard_upload(document.storage_path)
        raise
    db.refresh(document)
    return document_record(db, document, job_id=job.id)


# --- Jobs started from the tender ---------------------------------------------------------------


@router.post(
    "/{tender_id}/retriage", status_code=status.HTTP_202_ACCEPTED, response_model=JobRecord
)
def retriage_tender(
    tender_id: uuid.UUID, db: DbSession, org_id: OrgId, actor: Actor
) -> JobRecord:
    """Returns a triage_tender job; recomputes coverage for not_started questions only."""
    from app.retrieve.triage import retriage

    tender = _get_tender(db, tender_id, org_id)
    job = retriage(db, tender, actor)
    db.commit()
    return JobRecord.model_validate(job)


@router.post(
    "/{tender_id}/draft-all", status_code=status.HTTP_202_ACCEPTED, response_model=JobRecord
)
def draft_all(
    tender_id: uuid.UUID,
    db: DbSession,
    org_id: OrgId,
    actor: Actor,
    body: DraftAllRequest | None = None,
) -> JobRecord:
    """Enqueue draft_all with payload {tender_id, include_new, actor}."""
    tender = _get_tender(db, tender_id, org_id)
    include_new = bool(body.include_new) if body is not None else False
    eligible_coverage = [e.Coverage.COVERED.value, e.Coverage.PARTIAL.value]
    if include_new:
        eligible_coverage.append(e.Coverage.NEW.value)
    total = db.scalar(
        select(func.count(Question.id)).where(
            Question.tender_id == tender.id,
            Question.status == e.QuestionStatus.NOT_STARTED.value,
            Question.response_type != e.ResponseType.PRICING.value,
            Question.coverage.in_(eligible_coverage),
        )
    )
    job = enqueue(
        db,
        e.JobKind.DRAFT_ALL,
        {"tender_id": str(tender.id), "include_new": include_new, "actor": actor},
        total=int(total or 0),
        org_id=org_id,
    )
    db.commit()
    return JobRecord.model_validate(job)


# --- Submission and export ------------------------------------------------------------------------


def _promote_on_submit(db: Session, tender: Tender, actor: str) -> int:
    from app.review.promotion import promote_on_submit

    return int(promote_on_submit(db, tender, actor) or 0)


def _retag_promotion_document(db: Session, tender: Tender) -> Document | None:
    from app.review.promotion import retag_promotion_document

    return retag_promotion_document(db, tender)


@router.post("/{tender_id}/submit", response_model=SubmitResponse)
def submit_tender(
    tender_id: uuid.UUID, db: DbSession, org_id: OrgId, actor: Actor
) -> SubmitResponse:
    """Mark the tender submitted and offer every approved answer for promotion into the
    library (when the promotion module is available)."""
    tender = _get_tender(db, tender_id, org_id)
    if tender.status == e.TenderStatus.SUBMITTED.value:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This tender has already been submitted."
        )
    previous_status = tender.status
    tender.status = e.TenderStatus.SUBMITTED.value
    tender.submitted_at = utcnow()
    db.flush()
    promoted = _promote_on_submit(db, tender, actor)
    record_event(
        db,
        e.EntityType.TENDER,
        tender.id,
        e.EventType.TENDER_SUBMITTED,
        actor,
        {
            "previous_status": previous_status,
            "submitted_at": tender.submitted_at.isoformat(),
            "promoted": promoted,
        },
        org_id=org_id,
    )
    db.commit()
    db.refresh(tender)
    return SubmitResponse(tender=_detail(db, tender), promoted=promoted)


def _run_expiry_sweep(db: Session) -> None:
    from app.review.invalidation import run_expiry_sweep

    run_expiry_sweep(db)


def _export_backend() -> tuple[Callable[..., bytes], type[Exception], dict[str, str], Callable]:
    """(build_export, ExportBlocked, MEDIA_TYPES, export_filename) from ``app.export``; one
    seam so tests can substitute a fake export."""
    from app.export import MEDIA_TYPES, ExportBlocked, build_export, export_filename

    return build_export, ExportBlocked, MEDIA_TYPES, export_filename


@router.get("/{tender_id}/export")
def export_tender(
    tender_id: uuid.UUID,
    db: DbSession,
    org_id: OrgId,
    format: Literal["docx", "xlsx"] = Query(default="docx"),
    mode: Literal["submission", "review"] = Query(default="submission"),
) -> Response:
    """?format=docx|xlsx&mode=submission|review. Runs the expiry sweep and commits it first
    (plan: Export), then builds the file; submission mode returns 409 with
    ``{detail, code: "needs_review", question_ids}`` while any question has needs_review, the
    same flat shape (a string ``detail`` with extra keys beside it) as every other 409."""
    tender = _get_tender(db, tender_id, org_id)
    build_export, blocked, media_types, filename_fn = _export_backend()
    _run_expiry_sweep(db)
    db.commit()
    try:
        content = build_export(db, tender, format=format, mode=mode)
    except blocked as exc:
        question_ids = [str(qid) for qid in getattr(exc, "question_ids", [])]
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": str(exc) or "Export refused while questions need review.",
                "code": "needs_review",
                "question_ids": question_ids,
            },
        )
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    if not callable(filename_fn):
        from app.export import export_filename as filename_fn
    filename = filename_fn(tender, format=format, mode=mode)
    return Response(
        content=content,
        media_type=media_types[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- The one-call question list -------------------------------------------------------------------


def _answer_summary(answer: Answer) -> CurrentAnswerSummary:
    summary = answer.support_summary or summarise_support(answer.segments or [])
    return CurrentAnswerSummary(
        id=answer.id,
        version=answer.version,
        author_type=answer.author_type,
        author_name=answer.author_name,
        word_count=answer.word_count,
        support_summary=summary,
        updated_at=answer.updated_at,
    )


@router.get("/{tender_id}/questions", response_model=list[QuestionListItem])
def list_tender_questions(
    tender_id: uuid.UUID, db: DbSession, org_id: OrgId
) -> list[QuestionListItem]:
    """Every question row with an embedded current_answer summary and thread_id, in one query."""
    tender = _get_tender(db, tender_id, org_id)
    stmt = (
        select(Question, Answer, Thread.id)
        .outerjoin(Answer, and_(Answer.question_id == Question.id, Answer.is_current.is_(True)))
        .outerjoin(Thread, Thread.question_id == Question.id)
        .where(Question.tender_id == tender.id)
        .order_by(Question.order_index, Question.created_at)
    )
    items: list[QuestionListItem] = []
    for question, answer, thread_id in db.execute(stmt):
        record = QuestionListItem.model_validate(question)
        record.thread_id = thread_id
        if answer is not None:
            record.current_answer = _answer_summary(answer)
        items.append(record)
    return items

