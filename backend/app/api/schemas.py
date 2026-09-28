"""Pydantic response models for the shapes the plan specifies.

Offsets are Unicode code-point indices into the stored section ``text``. ``pending`` is a
support status on the wire only; ``StoredSegmentRecord`` and ``StoredAnswerRecord`` forbid it
and are what persistence and the fixture tests validate against.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SupportStatusStored = Literal["supported", "weak", "unsupported", "human_authored", "connective"]
SupportStatusWire = Literal[
    "supported", "weak", "unsupported", "human_authored", "connective", "pending"
]
SegmentKind = Literal["substantive", "connective"]
StreamEventType = Literal[
    "verbatim", "segment", "gaps", "fact_checklist", "support", "done", "error"
]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Segments and sources -------------------------------------------------------------------


class Locator(ApiModel):
    document_id: uuid.UUID
    section_id: uuid.UUID
    start: int | None = None
    end: int | None = None
    page: int | None = None
    table: int | None = None
    cell_ref: str | None = None
    heading_path: list[str] = Field(default_factory=list)


class DocumentSource(ApiModel):
    source_type: Literal["knowledge_item", "fact"]
    source_id: uuid.UUID
    quote: str
    locator: Locator
    document_title: str
    doc_type: str | None = None
    doc_kind: str | None = None
    effective_date: date | None = None
    # Span tier recorded by support verification: exact match or windowed alignment.
    tier: Literal["verbatim", "near"] | None = None


class HumanAttestationSource(ApiModel):
    source_type: Literal["human_attestation"]
    attested_by: str
    note: str | None = None
    at: datetime


Source = Annotated[DocumentSource | HumanAttestationSource, Field(discriminator="source_type")]


class Dispute(ApiModel):
    disputed_by: str
    note: str
    at: datetime


class SegmentRecord(ApiModel):
    index: int
    paragraph: int
    text: str
    kind: SegmentKind
    sources: list[Source] = Field(default_factory=list)
    dispute: Dispute | None = None
    support_status: SupportStatusWire


class StoredSegmentRecord(SegmentRecord):
    support_status: SupportStatusStored


class FactChecklistEntry(ApiModel):
    fact_id: uuid.UUID
    statement: str
    effective_date: date | None = None
    status: Literal["current", "superseded", "expired", "unverified"]


class SupportSummary(ApiModel):
    counts: dict[str, int]
    substantive: int
    supported: int
    needs_attention: int
    score: float | None = None  # evidence coverage, not correctness


class VerbatimOffer(ApiModel):
    source_item_id: uuid.UUID
    similarity: float | None = None
    segments: list[SegmentRecord]


# --- Answers --------------------------------------------------------------------------------


class AnswerRecord(ApiModel):
    id: uuid.UUID
    question_id: uuid.UUID
    version: int
    author_type: Literal["ai", "user"]
    author_name: str | None = None
    text: str
    word_count: int
    segments: list[SegmentRecord]
    gaps: list[str]
    fact_checklist: list[FactChecklistEntry]
    verbatim_offer_item_id: uuid.UUID | None = None
    verbatim_source_item_id: uuid.UUID | None = None
    verbatim: VerbatimOffer | None = None
    support_summary: SupportSummary
    model: str | None = None
    prompt_version: str | None = None
    is_current: bool
    created_at: datetime
    updated_at: datetime


class StoredAnswerRecord(AnswerRecord):
    segments: list[StoredSegmentRecord]


class CurrentAnswerSummary(ApiModel):
    id: uuid.UUID
    version: int
    author_type: Literal["ai", "user"]
    author_name: str | None = None
    word_count: int
    support_summary: SupportSummary
    updated_at: datetime


# --- Jobs -----------------------------------------------------------------------------------


class JobRecord(ApiModel):
    id: uuid.UUID
    kind: str
    status: Literal["queued", "running", "done", "failed"]
    attempts: int
    done: int
    total: int | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    next_job_id: uuid.UUID | None = None
    results: list[Any] = Field(default_factory=list)


# --- Questions ------------------------------------------------------------------------------


class Blocker(ApiModel):
    kind: Literal["segment", "gap", "needs_review", "no_answer", "system_only"]
    index: int | None = None
    gap: str | None = None


class AllowedTransition(ApiModel):
    to: str
    allowed: bool
    blockers: list[Blocker] = Field(default_factory=list)


class EvidenceRecord(ApiModel):
    document_id: uuid.UUID
    filename: str
    note: str | None = None


class CommentRecord(ApiModel):
    id: uuid.UUID
    author: str
    text: str
    created_at: datetime


class GapAcknowledgement(ApiModel):
    gap: str
    acknowledged_by: str
    note: str | None = None
    at: datetime


class QuestionRecord(ApiModel):
    id: uuid.UUID
    org_id: uuid.UUID
    tender_id: uuid.UUID
    document_id: uuid.UUID
    section: str
    number: str
    text: str
    word_limit: int | None = None
    weighting: float | None = None
    response_type: Literal["free_text", "yes_no", "attachment", "table", "pricing", "other"]
    mandatory: bool
    order_index: int
    topics: list[str]
    coverage: Literal["covered", "partial", "new", "unknown"]
    coverage_detail: dict[str, Any] = Field(default_factory=dict)
    compliance_class: Literal["A", "B", "C"] | None = None
    compliant_by: date | None = None
    assignee: str | None = None
    status: Literal["not_started", "ai_draft", "writer_edited", "sme_verified", "approved"]
    needs_review: bool
    gap_acknowledgements: list[GapAcknowledgement] = Field(default_factory=list)
    thread_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class QuestionListItem(QuestionRecord):
    """Row of GET /tenders/{id}/questions: the question plus a summary of its current answer."""

    current_answer: CurrentAnswerSummary | None = None


class QuestionDetail(QuestionRecord):
    """GET /questions/{id}."""

    evidence: list[EvidenceRecord] = Field(default_factory=list)
    comments: list[CommentRecord] = Field(default_factory=list)
    current_answer: AnswerRecord | None = None
    draft_in_progress: bool = False
    allowed_transitions: list[AllowedTransition] = Field(default_factory=list)


# --- Tenders --------------------------------------------------------------------------------


class TenderListItem(ApiModel):
    id: uuid.UUID
    name: str
    buyer: str | None = None
    deadline: datetime | None = None
    days_remaining: int | None = None
    status: Literal["open", "submitted", "archived"]
    outcome: Literal["pending", "won", "lost", "unknown"]
    questions_total: int
    questions_approved: int
    words_total: int
    words_approved: int


class TenderDocumentSummary(ApiModel):
    id: uuid.UUID
    filename: str
    tender_doc_kind: str | None = None
    ingest_status: str
    ingest_error: str | None = None


class TenderDetail(TenderListItem):
    outcome_notes: str | None = None
    regime: str | None = None
    is_framework: bool | None = None
    submitted_at: datetime | None = None
    extract_job_id: uuid.UUID | None = None
    triage_job_id: uuid.UUID | None = None
    c_count: int
    unclassified_mandatory_count: int
    needs_review_count: int
    documents: list[TenderDocumentSummary] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# --- Threads --------------------------------------------------------------------------------


class ThreadMessage(ApiModel):
    id: uuid.UUID
    role: Literal["user", "assistant", "system"]
    content: str
    segments: list[SegmentRecord] | None = None
    support_summary: SupportSummary | None = None
    gaps: list[str] | None = None
    fact_checklist: list[FactChecklistEntry] | None = None
    verbatim: VerbatimOffer | None = None
    model: str | None = None
    prompt_version: str | None = None
    answer_id: uuid.UUID | None = None
    created_at: datetime


class ThreadRecord(ApiModel):
    id: uuid.UUID
    tender_id: uuid.UUID
    question_id: uuid.UUID | None = None
    title: str
    reply_in_progress: bool = False
    messages: list[ThreadMessage] = Field(default_factory=list)


# --- Events, sections, documents ------------------------------------------------------------


class EventRecord(ApiModel):
    id: uuid.UUID
    event_type: str
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class SectionRecord(ApiModel):
    id: uuid.UUID
    document_id: uuid.UUID
    order_index: int
    heading_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    table_index: int | None = None
    cell_ref: str | None = None
    text: str


class SectionDocument(ApiModel):
    id: uuid.UUID
    filename: str
    doc_type: str | None = None
    doc_kind: str | None = None
    tender_doc_kind: str | None = None
    effective_date: date | None = None
    superseded_by: uuid.UUID | None = None


class Highlight(ApiModel):
    start: int
    end: int


class SectionResponse(ApiModel):
    section: SectionRecord
    document: SectionDocument
    highlight: Highlight | None = None


class DocumentRecord(ApiModel):
    """Every ``documents`` column plus item counts and fact count."""

    id: uuid.UUID
    org_id: uuid.UUID
    filename: str
    storage_path: str
    doc_type: str | None = None
    doc_kind: str | None = None
    tender_id: uuid.UUID | None = None
    tender_doc_kind: str | None = None
    effective_date: date | None = None
    effective_date_source: str | None = None
    classification_confirmed: bool
    superseded_by: uuid.UUID | None = None
    buyer: str | None = None
    submission_date: date | None = None
    outcome: str | None = None
    ingest_status: str
    ingest_error: str | None = None
    created_at: datetime
    updated_at: datetime
    item_counts: dict[str, int] = Field(default_factory=dict)
    fact_count: int = 0


# --- Fixtures and misc ----------------------------------------------------------------------


class FixtureAnswerResponse(ApiModel):
    question: QuestionDetail
    answer: AnswerRecord
    thread: ThreadRecord


class HealthResponse(ApiModel):
    status: Literal["ok"]


class ErrorDetail(ApiModel):
    detail: str
