"""Library items, facts and supersession decisions (plan: API surface, documents and library).

- ``GET /library/items``: flat list with ``text_verified``, ``item_type`` and source document.
- ``GET /library/items/{id}``: the item with its variants (the rest of its cluster) and locator.
- ``GET /library/facts``: facts with their supersession chain and a computed ``is_current``.
- ``GET /library/supersession-decisions``: pending rows with both documents' filename,
  ``doc_kind``, ``effective_date`` and ``effective_date_source``.
- ``POST /library/supersession-decisions/{id}``: resolve a pair by hand.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Actor, DbSession, OrgId
from app.api.schemas import ApiModel, Locator
from app.db.enums import SupersessionDecision as DecisionValue
from app.db.models import Document, DocumentSection, Fact, KnowledgeItem, SupersessionDecision
from app.ingest.supersession import DecisionError, apply_decision

# Plain code: starlette renamed the 422 constant between versions.
UNPROCESSABLE = 422

router = APIRouter(prefix="/library", tags=["library"])


# --- Schemas local to this router -----------------------------------------------------------


class SourceDocument(ApiModel):
    id: uuid.UUID
    filename: str
    doc_type: str | None = None
    doc_kind: str | None = None
    effective_date: date | None = None
    effective_date_source: str | None = None
    superseded_by: uuid.UUID | None = None


class LibraryItem(ApiModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document: SourceDocument
    section_id: uuid.UUID
    item_type: str
    question_text: str | None = None
    answer_text: str
    text_verified: bool
    topics: list[str] = Field(default_factory=list)
    canonical_id: uuid.UUID | None = None
    is_canonical: bool
    excluded_from_retrieval: bool
    created_at: datetime
    updated_at: datetime


class FactRecord(ApiModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document: SourceDocument
    section_id: uuid.UUID
    knowledge_item_id: uuid.UUID | None = None
    fact_kind: str
    fact_key: str | None = None
    statement: str
    value: str
    effective_date: date
    expires_on: date | None = None
    expired_at: datetime | None = None
    superseded_by: uuid.UUID | None = None
    is_current: bool
    created_at: datetime


class LibraryItemDetail(LibraryItem):
    answer_start: int
    answer_end: int
    question_section_id: uuid.UUID | None = None
    question_start: int | None = None
    question_end: int | None = None
    locator: Locator
    question_locator: Locator | None = None
    variants: list[LibraryItem] = Field(default_factory=list)
    facts: list[FactRecord] = Field(default_factory=list)


class DecisionDocument(SourceDocument):
    pass


class DecisionRecord(ApiModel):
    id: uuid.UUID
    doc_kind: str
    reason: str
    decision: str
    document_a: DecisionDocument
    document_b: DecisionDocument
    superseding_document_id: uuid.UUID | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["superseded", "keep_both"]
    superseding_document_id: uuid.UUID | None = None


# --- Helpers --------------------------------------------------------------------------------


def _documents(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, Document]:
    if not ids:
        return {}
    return {doc.id: doc for doc in db.scalars(select(Document).where(Document.id.in_(ids)))}


def _columns(model: type[ApiModel], row: object, *skip: str) -> dict:
    return {
        name: getattr(row, name) for name in model.model_fields if name not in skip
    }


def _item_record(item: KnowledgeItem, document: Document) -> LibraryItem:
    return LibraryItem(
        **_columns(LibraryItem, item, "document"),
        document=SourceDocument.model_validate(document),
    )


def _locator(section: DocumentSection, start: int | None, end: int | None) -> Locator:
    return Locator(
        document_id=section.document_id,
        section_id=section.id,
        start=start,
        end=end,
        page=section.page_start,
        table=section.table_index,
        cell_ref=section.cell_ref,
        heading_path=list(section.heading_path or []),
    )


def fact_is_current(fact: Fact, document: Document | None, today: date | None = None) -> bool:
    """Not superseded, not expired, and its source document is not superseded."""
    today = today or datetime.now(UTC).date()
    if fact.superseded_by is not None or fact.expired_at is not None:
        return False
    if fact.expires_on is not None and fact.expires_on < today:
        return False
    return document is None or document.superseded_by is None


def _fact_record(fact: Fact, document: Document, today: date) -> FactRecord:
    return FactRecord(
        **_columns(FactRecord, fact, "document", "is_current"),
        document=SourceDocument.model_validate(document),
        is_current=fact_is_current(fact, document, today),
    )


def _decision_record(
    row: SupersessionDecision, documents: dict[uuid.UUID, Document]
) -> DecisionRecord:
    doc_a = documents[row.document_a_id]
    doc_b = documents[row.document_b_id]
    return DecisionRecord(
        id=row.id,
        doc_kind=row.doc_kind,
        reason=row.reason,
        decision=row.decision,
        document_a=DecisionDocument.model_validate(doc_a),
        document_b=DecisionDocument.model_validate(doc_b),
        superseding_document_id=row.superseding_document_id,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# --- Routes ---------------------------------------------------------------------------------


@router.get("/items", response_model=list[LibraryItem])
def list_items(
    db: DbSession,
    org_id: OrgId,
    item_type: str | None = Query(default=None),
    document_id: uuid.UUID | None = Query(default=None),
    text_verified: bool | None = Query(default=None),
    canonical_only: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> list[LibraryItem]:
    stmt = select(KnowledgeItem).where(KnowledgeItem.org_id == org_id)
    if item_type is not None:
        stmt = stmt.where(KnowledgeItem.item_type == item_type)
    if document_id is not None:
        stmt = stmt.where(KnowledgeItem.document_id == document_id)
    if text_verified is not None:
        stmt = stmt.where(KnowledgeItem.text_verified.is_(text_verified))
    if canonical_only:
        stmt = stmt.where(KnowledgeItem.is_canonical.is_(True))
    stmt = (
        stmt.order_by(KnowledgeItem.created_at.desc(), KnowledgeItem.id).offset(offset).limit(limit)
    )
    items = db.scalars(stmt).all()
    documents = _documents(db, {item.document_id for item in items})
    return [_item_record(item, documents[item.document_id]) for item in items]


@router.get("/items/{item_id}", response_model=LibraryItemDetail)
def get_item(item_id: uuid.UUID, db: DbSession, org_id: OrgId) -> LibraryItemDetail:
    """The item with its variants and locator."""
    item = db.get(KnowledgeItem, item_id)
    if item is None or item.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Knowledge item not found.")
    cluster_head = item.canonical_id if item.canonical_id is not None else item.id
    members = db.scalars(
        select(KnowledgeItem).where(
            (KnowledgeItem.id == cluster_head) | (KnowledgeItem.canonical_id == cluster_head),
            KnowledgeItem.id != item.id,
        )
    ).all()
    facts = db.scalars(select(Fact).where(Fact.knowledge_item_id == item.id)).all()
    documents = _documents(
        db, {item.document_id, *(member.document_id for member in members)}
    )
    document = documents[item.document_id]
    section = db.get(DocumentSection, item.section_id)
    if section is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The item's section is missing.")
    question_locator: Locator | None = None
    if item.question_start is not None and item.question_end is not None:
        question_section = (
            db.get(DocumentSection, item.question_section_id)
            if item.question_section_id is not None
            else section
        )
        if question_section is not None:
            question_locator = _locator(question_section, item.question_start, item.question_end)
    today = datetime.now(UTC).date()
    base = _item_record(item, document)
    return LibraryItemDetail(
        **base.model_dump(),
        answer_start=item.answer_start,
        answer_end=item.answer_end,
        question_section_id=item.question_section_id,
        question_start=item.question_start,
        question_end=item.question_end,
        locator=_locator(section, item.answer_start, item.answer_end),
        question_locator=question_locator,
        variants=[_item_record(member, documents[member.document_id]) for member in members],
        facts=[_fact_record(fact, document, today) for fact in facts],
    )


@router.get("/facts", response_model=list[FactRecord])
def list_facts(
    db: DbSession,
    org_id: OrgId,
    fact_kind: str | None = Query(default=None),
    fact_key: str | None = Query(default=None),
    document_id: uuid.UUID | None = Query(default=None),
    current_only: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> list[FactRecord]:
    stmt = select(Fact).where(Fact.org_id == org_id)
    if fact_kind is not None:
        stmt = stmt.where(Fact.fact_kind == fact_kind)
    if fact_key is not None:
        stmt = stmt.where(Fact.fact_key == fact_key)
    if document_id is not None:
        stmt = stmt.where(Fact.document_id == document_id)
    stmt = (
        stmt.order_by(Fact.fact_kind, Fact.fact_key, Fact.effective_date.desc(), Fact.id)
        .offset(offset)
        .limit(limit)
    )
    facts = db.scalars(stmt).all()
    documents = _documents(db, {fact.document_id for fact in facts})
    today = datetime.now(UTC).date()
    records = [_fact_record(fact, documents[fact.document_id], today) for fact in facts]
    if current_only:
        records = [record for record in records if record.is_current]
    return records


@router.get("/supersession-decisions", response_model=list[DecisionRecord])
def list_supersession_decisions(
    db: DbSession,
    org_id: OrgId,
    include_decided: bool = Query(default=False),
) -> list[DecisionRecord]:
    """Pending rows with both documents' filename, doc_kind, effective_date and its source."""
    stmt = select(SupersessionDecision).where(SupersessionDecision.org_id == org_id)
    if not include_decided:
        stmt = stmt.where(SupersessionDecision.decision == DecisionValue.PENDING.value)
    rows = db.scalars(stmt.order_by(SupersessionDecision.created_at, SupersessionDecision.id)).all()
    documents = _documents(
        db, {row.document_a_id for row in rows} | {row.document_b_id for row in rows}
    )
    return [_decision_record(row, documents) for row in rows]


@router.post("/supersession-decisions/{decision_id}", response_model=DecisionRecord)
def decide_supersession(
    decision_id: uuid.UUID, body: DecisionRequest, db: DbSession, org_id: OrgId, actor: Actor
) -> DecisionRecord:
    """Body {decision: "superseded", superseding_document_id} or {decision: "keep_both"}."""
    row = db.get(SupersessionDecision, decision_id)
    if row is None or row.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Supersession decision not found.")
    if body.decision == "superseded" and body.superseding_document_id is None:
        raise HTTPException(
            UNPROCESSABLE,
            detail="superseding_document_id is required when the decision is 'superseded'.",
        )
    try:
        apply_decision(db, row, body.decision, body.superseding_document_id, actor)
    except DecisionError as exc:
        raise HTTPException(UNPROCESSABLE, detail=str(exc)) from exc
    db.commit()
    documents = _documents(db, {row.document_a_id, row.document_b_id})
    return _decision_record(row, documents)
