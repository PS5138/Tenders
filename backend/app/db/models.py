"""SQLAlchemy 2.0 models for every table in the plan's data model.

Every table carries ``id`` (UUID), ``org_id``, ``created_at`` and ``updated_at`` through the
mixins. Enumerated columns are strings with CHECK constraints (see ``enums.py``). Vector
columns take their dimension from settings; offsets are Unicode code-point indices into the
raw stored section ``text``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship

from app.config import DOC_KINDS, FACT_KINDS, get_settings
from app.db import enums as e

EMBEDDING_DIM: int = get_settings().embedding_dimension

_EMPTY_JSON_LIST = sql_text("'[]'::jsonb")
_EMPTY_JSON_OBJECT = sql_text("'{}'::jsonb")
_EMPTY_TEXT_ARRAY = sql_text("'{}'::varchar[]")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class IdTimestampMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


class OrgScopedMixin(IdTimestampMixin):
    @declared_attr
    def org_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True), ForeignKey("organisations.id"), nullable=False, index=True
        )


class Organisation(IdTimestampMixin, Base):
    __tablename__ = "organisations"

    # Every table carries org_id; on the organisation itself it equals id.
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    topic_taxonomy: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_EMPTY_JSON_LIST
    )
    # A demonstration business: its work uses the synthetic providers and its uploads are
    # limited to the supplied synthetic files, whatever the deployment's providers are
    # (``app.llm.scope``).
    synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )


class Document(OrgScopedMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        e.enum_check("doc_type", e.values(e.DocType), "ck_documents_doc_type"),
        e.enum_check("doc_kind", DOC_KINDS, "ck_documents_doc_kind"),
        e.enum_check(
            "tender_doc_kind", e.values(e.TenderDocKind), "ck_documents_tender_doc_kind"
        ),
        e.enum_check(
            "effective_date_source",
            e.values(e.EffectiveDateSource),
            "ck_documents_effective_date_source",
        ),
        e.enum_check("outcome", e.values(e.SubmissionOutcome), "ck_documents_outcome"),
        e.enum_check("ingest_status", e.values(e.IngestStatus), "ck_documents_ingest_status"),
        Index("ix_documents_org_tender", "org_id", "tender_id"),
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String(32))
    doc_kind: Mapped[str | None] = mapped_column(String(64))
    tender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), index=True
    )
    tender_doc_kind: Mapped[str | None] = mapped_column(String(32))
    effective_date: Mapped[date | None] = mapped_column(Date)
    effective_date_source: Mapped[str | None] = mapped_column(String(16))
    classification_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    buyer: Mapped[str | None] = mapped_column(String(256))
    submission_date: Mapped[date | None] = mapped_column(Date)
    outcome: Mapped[str | None] = mapped_column(String(16))
    ingest_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=e.IngestStatus.QUEUED.value,
        server_default=sql_text("'queued'"),
    )
    ingest_error: Mapped[str | None] = mapped_column(Text)

    sections: Mapped[list[DocumentSection]] = relationship(
        back_populates="document",
        order_by="DocumentSection.order_index",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DocumentSection(OrgScopedMixin, Base):
    """One parsed unit of a document, written once at parse time and never edited."""

    __tablename__ = "document_sections"
    __table_args__ = (
        UniqueConstraint("document_id", "order_index", name="uq_document_sections_order"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_EMPTY_JSON_LIST
    )
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    table_index: Mapped[int | None] = mapped_column(Integer)
    cell_ref: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, nullable=False)

    document: Mapped[Document] = relationship(back_populates="sections")


class KnowledgeItem(OrgScopedMixin, Base):
    """A retrievable unit: an extracted pair, a reference chunk or a promoted answer."""

    __tablename__ = "knowledge_items"
    __table_args__ = (
        e.enum_check("item_type", e.values(e.ItemType), "ck_knowledge_items_item_type"),
        Index("ix_knowledge_items_search_tsv", "search_tsv", postgresql_using="gin"),
        Index(
            "ix_knowledge_items_answer_embedding",
            "answer_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"answer_embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_knowledge_items_question_embedding",
            "question_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"question_embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_knowledge_items_eligibility", "org_id", "excluded_from_retrieval", "text_verified"
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_sections.id"), nullable=False, index=True
    )
    answer_start: Mapped[int] = mapped_column(Integer, nullable=False)
    answer_end: Mapped[int] = mapped_column(Integer, nullable=False)
    question_section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_sections.id")
    )
    question_start: Mapped[int | None] = mapped_column(Integer)
    question_end: Mapped[int | None] = mapped_column(Integer)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    question_text: Mapped[str | None] = mapped_column(Text)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    text_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    topics: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default=_EMPTY_TEXT_ARRAY
    )
    canonical_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="SET NULL"), index=True
    )
    is_canonical: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    question_embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM))
    # Nullable so an item can exist between extraction (step 3/4) and embedding (step 5).
    answer_embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM))
    search_tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(question_text, '')), 'A') || "
            "setweight(to_tsvector('english', answer_text), 'B')",
            persisted=True,
        ),
    )
    excluded_from_retrieval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )


class UnpairedFragment(OrgScopedMixin, Base):
    __tablename__ = "unpaired_fragments"
    __table_args__ = (
        e.enum_check("role", e.values(e.FragmentRole), "ck_unpaired_fragments_role"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_sections.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start: Mapped[int | None] = mapped_column(Integer)
    end: Mapped[int | None] = mapped_column(Integer)
    resolved_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="SET NULL")
    )


class Fact(OrgScopedMixin, Base):
    __tablename__ = "facts"
    __table_args__ = (
        e.enum_check("fact_kind", FACT_KINDS.keys(), "ck_facts_fact_kind"),
        Index("ix_facts_kind_key", "org_id", "fact_kind", "fact_key"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_sections.id"), nullable=False
    )
    knowledge_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), index=True
    )
    fact_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_key: Mapped[str | None] = mapped_column(String(256))
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    expires_on: Mapped[date | None] = mapped_column(Date)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id", ondelete="SET NULL")
    )


class SupersessionDecision(OrgScopedMixin, Base):
    """One row per pair of confirmed reference documents of a supersedable kind.

    The pair is stored ordered (``document_a_id < document_b_id``) so the unique constraint
    covers the unordered pair; callers must sort the two ids before writing.
    """

    __tablename__ = "supersession_decisions"
    __table_args__ = (
        UniqueConstraint("document_a_id", "document_b_id", name="uq_supersession_pair"),
        CheckConstraint("document_a_id < document_b_id", name="ck_supersession_pair_ordered"),
        e.enum_check("doc_kind", DOC_KINDS, "ck_supersession_decisions_doc_kind"),
        e.enum_check(
            "reason", e.values(e.SupersessionReason), "ck_supersession_decisions_reason"
        ),
        e.enum_check(
            "decision", e.values(e.SupersessionDecision), "ck_supersession_decisions_decision"
        ),
    )

    doc_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    document_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    document_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=e.SupersessionDecision.PENDING.value,
        server_default=sql_text("'pending'"),
    )
    superseding_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(OrgScopedMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        e.enum_check("kind", e.values(e.JobKind), "ck_jobs_kind"),
        e.enum_check("status", e.values(e.JobStatus), "ck_jobs_status"),
        Index("ix_jobs_status_created", "status", "created_at"),
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=e.JobStatus.QUEUED.value,
        server_default=sql_text("'queued'"),
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    total: Mapped[int | None] = mapped_column(Integer)
    done: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    # Backend-internal, never returned by the API.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_JSON_OBJECT
    )
    results: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_EMPTY_JSON_LIST
    )
    next_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Tender(OrgScopedMixin, Base):
    __tablename__ = "tenders"
    __table_args__ = (
        e.enum_check("status", e.values(e.TenderStatus), "ck_tenders_status"),
        e.enum_check("outcome", e.values(e.TenderOutcome), "ck_tenders_outcome"),
        e.enum_check("regime", e.values(e.Regime), "ck_tenders_regime"),
    )

    name: Mapped[str] = mapped_column(String(512), nullable=False)
    buyer: Mapped[str | None] = mapped_column(String(256))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=e.TenderStatus.OPEN.value,
        server_default=sql_text("'open'"),
    )
    outcome: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=e.TenderOutcome.PENDING.value,
        server_default=sql_text("'pending'"),
    )
    outcome_notes: Mapped[str | None] = mapped_column(Text)
    regime: Mapped[str | None] = mapped_column(String(32))
    is_framework: Mapped[bool | None] = mapped_column(Boolean)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extract_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    triage_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    # The latest extract_requirements run (migration 0004).
    requirements_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )

    documents: Mapped[list[Document]] = relationship(
        primaryjoin="Tender.id == Document.tender_id", order_by="Document.created_at"
    )
    questions: Mapped[list[Question]] = relationship(
        back_populates="tender", order_by="Question.order_index"
    )


class Question(OrgScopedMixin, Base):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("tender_id", "section", "number", name="uq_questions_tender_number"),
        e.enum_check(
            "response_type", e.values(e.ResponseType), "ck_questions_response_type"
        ),
        e.enum_check("coverage", e.values(e.Coverage), "ck_questions_coverage"),
        e.enum_check(
            "compliance_class", e.values(e.ComplianceClass), "ck_questions_compliance_class"
        ),
        e.enum_check("status", e.values(e.QuestionStatus), "ck_questions_status"),
        Index("ix_questions_tender_order", "tender_id", "order_index"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    # The question pack the question was extracted from.
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    section: Mapped[str] = mapped_column(String(256), nullable=False)
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    word_limit: Mapped[int | None] = mapped_column(Integer)
    weighting: Mapped[float | None] = mapped_column(Float)
    response_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=e.ResponseType.FREE_TEXT.value,
        server_default=sql_text("'free_text'"),
    )
    mandatory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    topics: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default=_EMPTY_TEXT_ARRAY
    )
    # The question text embedded once at extraction.
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM))
    coverage: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=e.Coverage.UNKNOWN.value,
        server_default=sql_text("'unknown'"),
    )
    coverage_detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_JSON_OBJECT
    )
    compliance_class: Mapped[str | None] = mapped_column(String(1))
    compliant_by: Mapped[date | None] = mapped_column(Date)
    assignee: Mapped[str | None] = mapped_column(String(200))
    # Written only by review.transitions.transition().
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=e.QuestionStatus.NOT_STARTED.value,
        server_default=sql_text("'not_started'"),
    )
    needs_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    gap_acknowledgements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_EMPTY_JSON_LIST
    )

    tender: Mapped[Tender] = relationship(back_populates="questions")
    answers: Mapped[list[Answer]] = relationship(
        back_populates="question", order_by="Answer.version"
    )
    thread: Mapped[Thread | None] = relationship(back_populates="question", uselist=False)
    evidence: Mapped[list[QuestionEvidence]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    comments: Mapped[list[Comment]] = relationship(
        order_by="Comment.created_at", cascade="all, delete-orphan", passive_deletes=True
    )


class QuestionEvidence(OrgScopedMixin, Base):
    """Attachments and evidence the answer will point the buyer to; distinct from citations."""

    __tablename__ = "question_evidence"
    __table_args__ = (
        UniqueConstraint("question_id", "document_id", name="uq_question_evidence_document"),
    )

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)


class Requirement(OrgScopedMixin, Base):
    """A specification requirement extracted from a tender document (migration 0004).

    The extracted fields (``ref``, ``text``, ``priority``, ``topics``, the locator and the AI
    ``suggested_class``) are rewritten by every ``extract_requirements`` run. The human fields
    (``compliance_class``, ``compliant_by``, ``comment``, ``owner``, ``rated_by``,
    ``rated_at``) are written only by ``PATCH /requirements/{id}``; a run never touches them and
    never deletes a row a person has worked on. ``key`` is the stable identity a re-run upserts
    on: the located span in its section, or a hash of the normalised text when not located.
    ``compliance_class`` is the supplier's rating (A compliant now, B compliant by a date, C
    cannot comply); the interface shows it as Green, Amber and Red, and colours are never
    stored.
    """

    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint("tender_id", "key", name="uq_requirements_tender_key"),
        e.enum_check(
            "priority", e.values(e.RequirementPriority), "ck_requirements_priority"
        ),
        e.enum_check(
            "suggested_class", e.values(e.ComplianceClass), "ck_requirements_suggested_class"
        ),
        e.enum_check(
            "compliance_class", e.values(e.ComplianceClass), "ck_requirements_compliance_class"
        ),
        Index("ix_requirements_tender_order", "tender_id", "order_index"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    # The tender document the requirement was extracted from.
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_sections.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Code-point offsets of the requirement text in the section; null when not located.
    start: Mapped[int | None] = mapped_column(Integer)
    end: Mapped[int | None] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    ref: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str | None] = mapped_column(String(8))
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    topics: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default=_EMPTY_TEXT_ARRAY
    )
    suggested_class: Mapped[str | None] = mapped_column(String(1))
    suggestion: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_JSON_OBJECT
    )
    compliance_class: Mapped[str | None] = mapped_column(String(1))
    compliant_by: Mapped[date | None] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(200))
    rated_by: Mapped[str | None] = mapped_column(String(200))
    rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def has_human_input(self) -> bool:
        """True once a person has rated, commented on or taken ownership of the row."""
        return bool(
            self.compliance_class
            or self.compliant_by
            or (self.comment or "").strip()
            or self.owner
            or self.rated_at
        )


class Answer(OrgScopedMixin, Base):
    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("question_id", "version", name="uq_answers_question_version"),
        # Belt to the code-level invariant: a question has at most one current answer.
        Index(
            "uq_answers_one_current",
            "question_id",
            unique=True,
            postgresql_where=sql_text("is_current"),
        ),
        e.enum_check("author_type", e.values(e.AuthorType), "ck_answers_author_type"),
    )

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    author_type: Mapped[str] = mapped_column(String(8), nullable=False)
    author_name: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The canonical record of what each sentence rests on (see the plan's segment record).
    segments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_EMPTY_JSON_LIST
    )
    gaps: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_EMPTY_JSON_LIST
    )
    fact_checklist: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_EMPTY_JSON_LIST
    )
    verbatim_offer_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="SET NULL")
    )
    verbatim_source_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="SET NULL")
    )
    support_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_JSON_OBJECT
    )
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(128))
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )

    question: Mapped[Question] = relationship(back_populates="answers")


class Thread(OrgScopedMixin, Base):
    __tablename__ = "threads"
    __table_args__ = (UniqueConstraint("question_id", name="uq_threads_question"),)

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    # Null means the tender-level thread.
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)

    question: Mapped[Question | None] = relationship(back_populates="thread")
    messages: Mapped[list[Message]] = relationship(
        back_populates="thread", order_by="Message.created_at"
    )


class Message(OrgScopedMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        e.enum_check("role", e.values(e.MessageRole), "ck_messages_role"),
        Index("ix_messages_thread_created", "thread_id", "created_at"),
    )

    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Assistant-only fields; all null on user messages.
    segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    rewritten_query: Mapped[str | None] = mapped_column(Text)
    retrieved_item_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    support_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    gaps: Mapped[list[str] | None] = mapped_column(JSONB)
    fact_checklist: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    verbatim_offer_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="SET NULL")
    )
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(128))
    answer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("answers.id", ondelete="SET NULL")
    )

    thread: Mapped[Thread] = relationship(back_populates="messages")


class Event(OrgScopedMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        e.enum_check("entity_type", e.values(e.EntityType), "ck_events_entity_type"),
        e.enum_check("event_type", e.values(e.EventType), "ck_events_event_type"),
        Index("ix_events_entity", "entity_type", "entity_id", "created_at"),
    )

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_JSON_OBJECT
    )


class Comment(OrgScopedMixin, Base):
    __tablename__ = "comments"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


__all__ = [
    "EMBEDDING_DIM",
    "Answer",
    "Base",
    "Comment",
    "Document",
    "DocumentSection",
    "Event",
    "Fact",
    "Job",
    "KnowledgeItem",
    "Message",
    "Organisation",
    "Question",
    "QuestionEvidence",
    "Requirement",
    "SupersessionDecision",
    "Tender",
    "Thread",
    "UnpairedFragment",
    "utcnow",
]
