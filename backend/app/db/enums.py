"""Enumerations shared by the models, the API schemas and the pipeline modules.

Values are stored as strings guarded by CHECK constraints (see models.py), so adding a value
is a one-line migration rather than an ALTER TYPE. ``review.transitions`` re-exports
``QuestionStatus`` as ``Status`` and ``review.events`` re-exports ``EventType``.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from sqlalchemy import CheckConstraint


class DocType(StrEnum):
    PAST_SUBMISSION = "past_submission"
    REFERENCE = "reference"
    TENDER_DOCUMENT = "tender_document"


class TenderDocKind(StrEnum):
    QUESTION_PACK = "question_pack"
    SPECIFICATION = "specification"
    CLARIFICATION_LOG = "clarification_log"
    CONTRACT_TERMS = "contract_terms"
    OTHER = "other"


class EffectiveDateSource(StrEnum):
    EXTRACTED = "extracted"
    UPLOAD_TIME = "upload_time"
    USER = "user"


class SubmissionOutcome(StrEnum):
    """Outcome recorded on a past submission document."""

    WON = "won"
    LOST = "lost"
    UNKNOWN = "unknown"


class IngestStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    CLASSIFYING = "classifying"
    EXTRACTING = "extracting"
    EMBEDDING = "embedding"
    LINKING = "linking"
    READY = "ready"
    FAILED = "failed"


class ItemType(StrEnum):
    QA_PAIR = "qa_pair"
    CHUNK = "chunk"
    PROMOTED_ANSWER = "promoted_answer"


class FragmentRole(StrEnum):
    QUESTION = "question"
    ANSWER = "answer"
    UNKNOWN = "unknown"


class SupersessionReason(StrEnum):
    AUTO = "auto"
    PENDING_DATE = "pending_date"
    TIE = "tie"
    KEYED_KIND = "keyed_kind"


class SupersessionDecision(StrEnum):
    PENDING = "pending"
    SUPERSEDED = "superseded"
    KEEP_BOTH = "keep_both"


class TenderStatus(StrEnum):
    OPEN = "open"
    SUBMITTED = "submitted"
    ARCHIVED = "archived"


class TenderOutcome(StrEnum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    UNKNOWN = "unknown"


class Regime(StrEnum):
    PROCUREMENT_ACT = "procurement_act"
    PSR = "psr"
    PCR_2015 = "pcr_2015"
    OTHER = "other"


class ResponseType(StrEnum):
    FREE_TEXT = "free_text"
    YES_NO = "yes_no"
    ATTACHMENT = "attachment"
    TABLE = "table"
    PRICING = "pricing"
    OTHER = "other"


class Coverage(StrEnum):
    COVERED = "covered"
    PARTIAL = "partial"
    NEW = "new"
    UNKNOWN = "unknown"


class LabelSource(StrEnum):
    FLOOR = "floor"
    LLM = "llm"


class ComplianceClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class RequirementPriority(StrEnum):
    """MoSCoW priority of a specification requirement, as the buyer states it."""

    MUST = "must"
    SHOULD = "should"
    COULD = "could"


class QuestionStatus(StrEnum):
    """Review state of a question's current answer. Ordinal, in this order."""

    NOT_STARTED = "not_started"
    AI_DRAFT = "ai_draft"
    WRITER_EDITED = "writer_edited"
    SME_VERIFIED = "sme_verified"
    APPROVED = "approved"


class AuthorType(StrEnum):
    AI = "ai"
    USER = "user"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class EventType(StrEnum):
    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    ANSWER_CREATED = "answer_created"
    ANSWER_SAVED_FROM_CHAT = "answer_saved_from_chat"
    VERBATIM_ACCEPTED = "verbatim_accepted"
    COMMENT_ADDED = "comment_added"
    DOCUMENT_SUPERSEDED = "document_superseded"
    SUPERSESSION_DECIDED = "supersession_decided"
    PAIR_FIXED = "pair_fixed"
    FACT_SUPERSEDED = "fact_superseded"
    FACT_EXPIRED = "fact_expired"
    ANSWER_NEEDS_REVIEW = "answer_needs_review"
    ATTESTED = "attested"
    DISPUTED = "disputed"
    GAP_ACKNOWLEDGED = "gap_acknowledged"
    COMPLIANCE_CLASS_SET = "compliance_class_set"
    EVIDENCE_ADDED = "evidence_added"
    JOB_FAILED = "job_failed"
    ANSWER_PROMOTED = "answer_promoted"
    # Added in migration 0002.
    TENDER_SUBMITTED = "tender_submitted"
    OUTCOME_SET = "outcome_set"
    SUPERSESSION_REVERSED = "supersession_reversed"
    # Added in migration 0004.
    REQUIREMENT_RATED = "requirement_rated"


class EntityType(StrEnum):
    """What ``events.entity_id`` points at."""

    QUESTION = "question"
    ANSWER = "answer"
    DOCUMENT = "document"
    TENDER = "tender"
    FACT = "fact"
    JOB = "job"
    THREAD = "thread"
    SUPERSESSION_DECISION = "supersession_decision"
    KNOWLEDGE_ITEM = "knowledge_item"
    # Added in migration 0004.
    REQUIREMENT = "requirement"


class JobKind(StrEnum):
    INGEST_DOCUMENT = "ingest_document"
    EXTRACT_QUESTIONS = "extract_questions"
    TRIAGE_TENDER = "triage_tender"
    DRAFT_ALL = "draft_all"
    # Added in migration 0004.
    EXTRACT_REQUIREMENTS = "extract_requirements"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class SegmentKind(StrEnum):
    SUBSTANTIVE = "substantive"
    CONNECTIVE = "connective"


class SupportStatus(StrEnum):
    """Stored support statuses. ``pending`` exists on the wire only and is never stored."""

    SUPPORTED = "supported"
    WEAK = "weak"
    UNSUPPORTED = "unsupported"
    HUMAN_AUTHORED = "human_authored"
    CONNECTIVE = "connective"


WIRE_PENDING = "pending"


class SourceType(StrEnum):
    KNOWLEDGE_ITEM = "knowledge_item"
    FACT = "fact"
    HUMAN_ATTESTATION = "human_attestation"


class FactChecklistStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    UNVERIFIED = "unverified"


class InvalidationReason(StrEnum):
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    REMOVED = "removed"


class SpanTier(StrEnum):
    VERBATIM = "verbatim"
    NEAR = "near"


def values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def enum_check(column: str, allowed: Iterable[str], name: str) -> CheckConstraint:
    """CHECK constraint restricting ``column`` to ``allowed``. NULL passes, so nullable is fine."""
    quoted = ", ".join(f"'{value}'" for value in allowed)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)
