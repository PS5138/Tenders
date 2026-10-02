"""Documents and the library document list (plan: API surface, documents and library).

Upload creates the document row and enqueues ``ingest_document``; the list and detail views
add ``item_counts`` by ``item_type`` and ``fact_count``; PATCH and confirm run the step 9
corrections; the unpaired queue and ``POST /documents/{id}/pairs`` let a person fix a pair by
pointing at a span. Endpoints are synchronous and commit at the end of the request.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Actor, DbSession, OrgId
from app.api.schemas import ApiModel, DocumentRecord, SectionRecord
from app.config import get_settings
from app.db.enums import EntityType, EventType, FragmentRole, IngestStatus, ItemType, JobKind
from app.db.models import Document, DocumentSection, Fact, KnowledgeItem, UnpairedFragment
from app.ingest.corrections import (
    LIBRARY_STAGE_COUNT,
    PatchError,
    apply_patch,
)
from app.ingest.corrections import (
    confirm_document as run_confirm,
)
from app.ingest.dedup import deduplicate
from app.ingest.embed import embed_items
from app.jobs import enqueue
from app.review.events import record_event

logger = logging.getLogger(__name__)

# Plain codes: starlette renamed the 422 and 413 constants between versions.
UNPROCESSABLE = 422
TOO_LARGE = 413

# Uploads are read in chunks of this size up to ``Settings.max_upload_bytes``.
UPLOAD_CHUNK_BYTES = 1024 * 1024

router = APIRouter(tags=["documents"])

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


# --- Schemas local to this router -----------------------------------------------------------


class DocumentWithJob(DocumentRecord):
    job_id: uuid.UUID | None = None


class DocumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: str | None = None
    doc_kind: str | None = None
    effective_date: date | None = None
    buyer: str | None = None
    submission_date: date | None = None


class UnpairedItem(ApiModel):
    id: uuid.UUID
    item_type: str
    section_id: uuid.UUID
    answer_start: int
    answer_end: int
    question_section_id: uuid.UUID | None = None
    question_start: int | None = None
    question_end: int | None = None
    question_text: str | None = None
    answer_text: str
    text_verified: bool
    topics: list[str] = Field(default_factory=list)


class UnpairedFragmentRecord(ApiModel):
    id: uuid.UUID
    section_id: uuid.UUID
    role: str
    text: str
    start: int | None = None
    end: int | None = None
    resolved_item_id: uuid.UUID | None = None


class UnpairedResponse(ApiModel):
    document_id: uuid.UUID
    items: list[UnpairedItem]
    fragments: list[UnpairedFragmentRecord]


class PairRequest(BaseModel):
    """Either a fidelity fix (``item_id``) or a manual pairing (``question_fragment_id`` or
    ``question_text``). Confirmation is always by pointing at a span."""

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID | None = None
    question_fragment_id: uuid.UUID | None = None
    question_text: str | None = None
    section_id: uuid.UUID
    answer_start: int = Field(ge=0)
    answer_end: int = Field(gt=0)


class PairResponse(ApiModel):
    item: UnpairedItem
    mode: str
    fragment_id: uuid.UUID | None = None


# --- Helpers --------------------------------------------------------------------------------


def _load_document(db: Session, document_id: uuid.UUID, org_id: uuid.UUID) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return document


def _counts(db: Session, document_ids: list[uuid.UUID]) -> tuple[dict, dict]:
    """(item counts by document and type, fact counts by document)."""
    item_counts: dict[uuid.UUID, dict[str, int]] = {}
    fact_counts: dict[uuid.UUID, int] = {}
    if not document_ids:
        return item_counts, fact_counts
    rows = db.execute(
        select(KnowledgeItem.document_id, KnowledgeItem.item_type, func.count())
        .where(KnowledgeItem.document_id.in_(document_ids))
        .group_by(KnowledgeItem.document_id, KnowledgeItem.item_type)
    ).all()
    for document_id, item_type, count in rows:
        item_counts.setdefault(document_id, {})[item_type] = int(count)
    rows = db.execute(
        select(Fact.document_id, func.count())
        .where(Fact.document_id.in_(document_ids))
        .group_by(Fact.document_id)
    ).all()
    for document_id, count in rows:
        fact_counts[document_id] = int(count)
    return item_counts, fact_counts


def document_record(
    db: Session, document: Document, *, job_id: uuid.UUID | None = None
) -> DocumentWithJob:
    item_counts, fact_counts = _counts(db, [document.id])
    return _record(document, item_counts, fact_counts, job_id=job_id)


def _record(
    document: Document,
    item_counts: dict,
    fact_counts: dict,
    *,
    job_id: uuid.UUID | None = None,
) -> DocumentWithJob:
    counts = {item_type.value: 0 for item_type in ItemType}
    counts.update(item_counts.get(document.id, {}))
    record = DocumentWithJob.model_validate(document)
    record.item_counts = counts
    record.fact_count = fact_counts.get(document.id, 0)
    record.job_id = job_id
    return record


def _safe_filename(filename: str) -> str:
    name = Path(filename or "upload").name
    cleaned = _SAFE_FILENAME.sub("_", name).strip("._") or "upload"
    return cleaned[:200]


def format_upload_limit(limit_bytes: int) -> str:
    """The limit in MiB for the 413 detail: a whole number when it is one, otherwise one
    decimal, never rounded up (1.5 MiB is "1.5", 1.56 MiB is "1.5", 25 MiB is "25"). The
    Next.js proxy's ``uploadLimitMessage`` uses the same rule so the two layers never disagree
    about the number."""
    tenths = limit_bytes * 10 // (1024 * 1024)
    whole, fraction = divmod(tenths, 10)
    return str(whole) if fraction == 0 else f"{whole}.{fraction}"


def upload_limit_message(limit_bytes: int) -> str:
    """The 413 detail. One sentence shared with the Next.js proxy, which refuses an oversized
    upload before it reaches the API in the joined stack; the wording and the number are the
    same whichever layer answers."""
    return f"This file is too large. Uploads are limited to {format_upload_limit(limit_bytes)} MB."


def read_upload(file: UploadFile) -> bytes:
    """Read the uploaded bytes in chunks, stopping at ``Settings.max_upload_bytes``.

    Raises 413 as soon as the limit is passed, so an oversized file is refused before the
    ``SYNTHETIC_DEMO`` checksum, before any storage write and before the document row exists,
    and the whole file is never read into memory first. A file exactly at the limit is
    accepted. Raises 422 for an empty file, likewise before the checksum, any storage write or
    the document row, so a zero-byte drop never leaves a queued document that can only fail
    (or, for a question pack, occupies the tender's one pack slot). Both upload routes read
    through this one function.
    """
    limit = get_settings().max_upload_bytes
    detail = upload_limit_message(limit)
    # The parser's own count, when it has one, saves reading a file that is already known
    # to be too large; acceptance is still decided by the bounded read below.
    if file.size is not None and file.size > limit:
        raise HTTPException(TOO_LARGE, detail=detail)
    file.file.seek(0)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(min(UPLOAD_CHUNK_BYTES, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(TOO_LARGE, detail=detail)
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(UNPROCESSABLE, detail="The uploaded file is empty.")
    return b"".join(chunks)


def store_upload(org_id: uuid.UUID, document_id: uuid.UUID, filename: str, data: bytes) -> str:
    """Write the uploaded bytes under the storage path; returns the stored path as a string.

    Uses owner A's ``app.ingest.storage.save_upload(data, filename, *, document_id, org_id)``
    when it exists so both upload endpoints store files the same way.
    """
    if get_settings().synthetic_demo:
        import hashlib

        from app.config import REPO_ROOT

        digest = hashlib.sha256(data).digest()
        allowed = any(
            hashlib.sha256(candidate.read_bytes()).digest() == digest
            for candidate in (REPO_ROOT / "eval" / "data").glob("*")
            if candidate.suffix in {".docx", ".xlsx", ".pdf"}
        )
        if not allowed:
            raise HTTPException(
                422, "Synthetic demo mode accepts only the supplied synthetic files."
            )
    try:
        from app.ingest.storage import save_upload
    except ImportError:
        save_upload = None
    if save_upload is not None:
        return str(save_upload(data, filename, document_id=document_id, org_id=org_id))
    root = Path(get_settings().storage_path) / str(org_id)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{document_id}_{_safe_filename(filename)}"
    target.write_bytes(data)
    return str(target)


def discard_upload(storage_path: str) -> None:
    """Remove a stored upload whose document row was never persisted, so a failed flush or
    commit leaves no orphan file: the file itself and, when ``save_upload`` gave it a
    per-document directory that is now empty, that directory too. Never raises."""
    try:
        target = Path(storage_path)
        target.unlink(missing_ok=True)
        parent = target.parent
        if parent.name and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:  # pragma: no cover - best effort; the request error is the one to surface
        logger.warning("Could not remove the orphaned upload at %s", storage_path)


def _unpaired_item(item: KnowledgeItem) -> UnpairedItem:
    return UnpairedItem.model_validate(item)


# --- Routes ---------------------------------------------------------------------------------


@router.get("/documents/{document_id}/file")
def original_file(document_id: uuid.UUID, db: DbSession, org_id: OrgId):
    from fastapi.responses import FileResponse

    document = _load_document(db, document_id, org_id)
    root = Path(get_settings().storage_path).resolve()
    target = Path(document.storage_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "Original file not found.")
    return FileResponse(target, filename=document.filename)


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentWithJob)
def upload_document(
    db: DbSession,
    org_id: OrgId,
    actor: Actor,
    file: UploadFile = File(...),
) -> DocumentWithJob:
    """Multipart upload to the library; returns the document and an ingest_document job_id.

    A plain ``def`` like every other non-streaming handler: the file write and the database
    round trips are synchronous, and FastAPI's threadpool keeps them off the event loop that
    drains the draft and reply streams.
    """
    # Bounded read: 413 past max_upload_bytes, 422 for an empty file, both before the
    # checksum, the file write or the row.
    data = read_upload(file)
    filename = file.filename or "upload"
    document_id = uuid.uuid4()
    storage_path = store_upload(org_id, document_id, filename, data)
    document = Document(
        id=document_id,
        org_id=org_id,
        filename=filename,
        storage_path=storage_path,
        ingest_status=IngestStatus.QUEUED.value,
        classification_confirmed=False,
    )
    try:
        db.add(document)
        db.flush()
        job = enqueue(
            db,
            JobKind.INGEST_DOCUMENT,
            {"document_id": str(document.id), "actor": actor},
            total=LIBRARY_STAGE_COUNT,
            org_id=org_id,
        )
        db.commit()
    except Exception:
        # The file was written before the row; a failed persist must not leave it orphaned.
        discard_upload(storage_path)
        raise
    return document_record(db, document, job_id=job.id)


@router.get("/documents", response_model=list[DocumentWithJob])
def list_documents(db: DbSession, org_id: OrgId) -> list[DocumentWithJob]:
    """Library documents only (tender_id null) with item_counts and fact_count."""
    documents = db.scalars(
        select(Document)
        .where(Document.org_id == org_id, Document.tender_id.is_(None))
        .order_by(Document.created_at.desc(), Document.id)
    ).all()
    item_counts, fact_counts = _counts(db, [document.id for document in documents])
    return [_record(document, item_counts, fact_counts) for document in documents]


@router.get("/documents/{document_id}", response_model=DocumentWithJob)
def get_document(document_id: uuid.UUID, db: DbSession, org_id: OrgId) -> DocumentWithJob:
    document = _load_document(db, document_id, org_id)
    return document_record(db, document)


@router.patch("/documents/{document_id}", response_model=DocumentWithJob)
def patch_document(
    document_id: uuid.UUID, body: DocumentPatch, db: DbSession, org_id: OrgId, actor: Actor
) -> DocumentWithJob:
    """Override classification fields; runs the corrections in ingestion step 9."""
    document = _load_document(db, document_id, org_id)
    if document.tender_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Tender documents are not classified and cannot be patched.",
        )
    changes: dict[str, Any] = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(UNPROCESSABLE, detail="No fields to change.")
    if "doc_type" not in changes and document.doc_type is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Classification has not been proposed yet; set doc_type to override it.",
        )
    try:
        job = apply_patch(db, document, changes, actor)
    except PatchError as exc:
        raise HTTPException(UNPROCESSABLE, detail=str(exc)) from exc
    db.commit()
    return document_record(db, document, job_id=job.id if job is not None else None)


@router.post("/documents/{document_id}/confirm", response_model=DocumentWithJob)
def confirm_document(
    document_id: uuid.UUID, db: DbSession, org_id: OrgId, actor: Actor
) -> DocumentWithJob:
    """Sets classification_confirmed and evaluates supersession."""
    document = _load_document(db, document_id, org_id)
    if document.doc_type is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Classification has not been proposed yet."
        )
    run_confirm(db, document, actor)
    db.commit()
    return document_record(db, document)


@router.get("/documents/{document_id}/sections", response_model=list[SectionRecord])
def list_document_sections(
    document_id: uuid.UUID, db: DbSession, org_id: OrgId
) -> list[SectionRecord]:
    _load_document(db, document_id, org_id)
    sections = db.scalars(
        select(DocumentSection)
        .where(DocumentSection.document_id == document_id)
        .order_by(DocumentSection.order_index)
    ).all()
    return [SectionRecord.model_validate(section) for section in sections]


@router.get("/documents/{document_id}/unpaired", response_model=UnpairedResponse)
def list_unpaired(document_id: uuid.UUID, db: DbSession, org_id: OrgId) -> UnpairedResponse:
    """text_verified = false items and unresolved fragments."""
    _load_document(db, document_id, org_id)
    items = db.scalars(
        select(KnowledgeItem)
        .where(KnowledgeItem.document_id == document_id, KnowledgeItem.text_verified.is_(False))
        .order_by(KnowledgeItem.created_at, KnowledgeItem.id)
    ).all()
    fragments = db.scalars(
        select(UnpairedFragment)
        .where(
            UnpairedFragment.document_id == document_id,
            UnpairedFragment.resolved_item_id.is_(None),
        )
        .order_by(UnpairedFragment.created_at, UnpairedFragment.id)
    ).all()
    return UnpairedResponse(
        document_id=document_id,
        items=[_unpaired_item(item) for item in items],
        fragments=[UnpairedFragmentRecord.model_validate(fragment) for fragment in fragments],
    )


@router.post(
    "/documents/{document_id}/pairs", response_model=PairResponse, status_code=status.HTTP_200_OK
)
def fix_pair(
    document_id: uuid.UUID, body: PairRequest, db: DbSession, org_id: OrgId, actor: Actor
) -> PairResponse:
    """Fidelity fix or manual pairing by pointing at a span; writes pair_fixed."""
    document = _load_document(db, document_id, org_id)
    section = db.get(DocumentSection, body.section_id)
    if section is None or section.document_id != document.id:
        raise HTTPException(
            UNPROCESSABLE,
            detail="section_id does not belong to this document.",
        )
    if not (0 <= body.answer_start < body.answer_end <= len(section.text)):
        raise HTTPException(
            UNPROCESSABLE,
            detail="answer_start and answer_end must describe a non-empty span within the section.",
        )
    answer_text = section.text[body.answer_start : body.answer_end]
    fragment: UnpairedFragment | None = None

    if body.item_id is not None:
        mode = "fidelity_fix"
        item = db.get(KnowledgeItem, body.item_id)
        if item is None or item.document_id != document.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="Item not found on this document."
            )
        previous_section_id = item.section_id
        question_located = item.question_start is not None and item.question_end is not None
        if (
            question_located
            and item.question_section_id is None
            and previous_section_id != section.id
        ):
            # A null question_section_id means "the same section as section_id". The question
            # was located in the old answer section, so pin it there before the answer moves;
            # otherwise its offsets would be resolved against the new answer section.
            item.question_section_id = previous_section_id
        elif item.question_section_id == section.id:
            # The answer now lands in the section the question was already pinned to.
            item.question_section_id = None
        item.section_id = section.id
        item.answer_start = body.answer_start
        item.answer_end = body.answer_end
        item.answer_text = answer_text
        item.text_verified = True
        if body.question_text is not None and body.question_text.strip():
            item.question_text = body.question_text.strip()
            item.question_section_id = None
            item.question_start = None
            item.question_end = None
    else:
        mode = "manual_pairing"
        question_text = (body.question_text or "").strip() or None
        if body.question_fragment_id is not None:
            fragment = db.get(UnpairedFragment, body.question_fragment_id)
            if fragment is None or fragment.document_id != document.id:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, detail="Fragment not found on this document."
                )
            if fragment.resolved_item_id is not None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, detail="That fragment has already been paired."
                )
            question_text = question_text or fragment.text
        if question_text is None:
            raise HTTPException(
                UNPROCESSABLE,
                detail="Manual pairing needs question_fragment_id or question_text.",
            )
        item = KnowledgeItem(
            org_id=org_id,
            document_id=document.id,
            section_id=section.id,
            answer_start=body.answer_start,
            answer_end=body.answer_end,
            item_type=ItemType.QA_PAIR.value,
            question_text=question_text,
            answer_text=answer_text,
            text_verified=True,
            topics=[],
        )
        if (
            fragment is not None
            and fragment.role == FragmentRole.QUESTION.value
            and fragment.start is not None
            and fragment.end is not None
        ):
            item.question_section_id = (
                fragment.section_id if fragment.section_id != section.id else None
            )
            item.question_start = fragment.start
            item.question_end = fragment.end
        db.add(item)
        db.flush()
        if fragment is not None:
            fragment.resolved_item_id = item.id
    db.flush()

    # Ingestion steps 5 and 6 for this one item.
    embed_items(db, [item])
    deduplicate(db, org_id, [item])
    record_event(
        db,
        EntityType.KNOWLEDGE_ITEM,
        item.id,
        EventType.PAIR_FIXED,
        actor,
        {
            "document_id": str(document.id),
            "mode": mode,
            "section_id": str(section.id),
            "answer_start": body.answer_start,
            "answer_end": body.answer_end,
            "fragment_id": str(fragment.id) if fragment is not None else None,
        },
        org_id=org_id,
    )
    db.commit()
    return PairResponse(
        item=_unpaired_item(item), mode=mode, fragment_id=fragment.id if fragment else None
    )


__all__ = [
    "DocumentWithJob",
    "discard_upload",
    "document_record",
    "format_upload_limit",
    "read_upload",
    "router",
    "store_upload",
    "upload_limit_message",
]
