"""Shared types for the ingestion pipeline (steps 1 to 4) and the LLM output schemas they use.

``RawFact`` is the contract shape handed to ``app.ingest.facts.persist_facts`` (owner B):
one dated or expiring claim, already attached to the section it was stated in and to the
knowledge item it came from.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import KnowledgeItem, UnpairedFragment

# --- Parsing (step 1) -------------------------------------------------------------------------


@dataclass
class ParsedSection:
    """One section at the plan's granularity, before it is persisted.

    ``cell_ref`` is ``Sheet1!5`` for a spreadsheet row (the sheet title and the Excel row
    number) or ``T2:R5`` for a docx table row (1-based table index within the document and
    1-based row within that table, counting the header row as row 1).
    """

    order_index: int
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    table_index: int | None
    cell_ref: str | None
    text: str


# --- Facts (steps 3, 4 and 7) -----------------------------------------------------------------


class RawFact(BaseModel):
    """A fact as extracted, before ``persist_facts`` turns it into a ``facts`` row.

    ``statement`` is one sentence copied from the section so ``find_span`` can locate it;
    ``value`` holds the number, status or name; the dates are those the text states and are
    null when it states none. ``effective_date`` is filled from the document by owner B.
    """

    fact_kind: str
    fact_key: str | None = None
    statement: str
    value: str
    effective_date: date | None = None
    expires_on: date | None = None
    section_id: uuid.UUID
    knowledge_item_id: uuid.UUID | None = None


@dataclass
class FailedWindow:
    """One extraction window whose LLM call failed. The other windows are still attempted, and
    ``extract_pairs`` then raises ``ExtractionFailed`` naming every failed window."""

    index: int
    section_ids: list[uuid.UUID]
    error: str


@dataclass
class ExtractionResult:
    """Output of ``extract_pairs`` (step 3). Items and fragments are persisted and flushed.
    ``failed_windows`` records windows whose LLM call failed; when any window failed
    ``extract_pairs`` raises ``ExtractionFailed`` instead of returning, so the worker's retry
    rule applies to the stage."""

    items: list[KnowledgeItem] = field(default_factory=list)
    fragments: list[UnpairedFragment] = field(default_factory=list)
    raw_facts: list[RawFact] = field(default_factory=list)
    failed_windows: list[FailedWindow] = field(default_factory=list)


@dataclass
class ChunkResult:
    """Output of ``chunk_reference`` (step 4). Items are persisted and flushed."""

    items: list[KnowledgeItem] = field(default_factory=list)
    raw_facts: list[RawFact] = field(default_factory=list)


# --- LLM output schemas -----------------------------------------------------------------------


class ClassificationOutput(BaseModel):
    """Step 2. A library upload is never a tender document, so only two types are offered."""

    doc_type: Literal["past_submission", "reference"]
    doc_kind: str | None = Field(
        default=None,
        description="Reference documents only: one of the configured document kinds.",
    )
    effective_date: date | None = Field(
        default=None, description="The date the document itself states; null if it states none."
    )
    buyer: str | None = Field(
        default=None, description="Past submissions only: the buying organisation."
    )
    submission_date: date | None = Field(
        default=None, description="Past submissions only: the date the response was submitted."
    )
    rationale: str = Field(default="", description="One sentence on what decided the type.")


class ExtractedFact(BaseModel):
    """A fact as the model states it, without the section it came from."""

    fact_kind: str
    fact_key: str | None = None
    statement: str
    value: str
    effective_date: date | None = None
    expires_on: date | None = None


class ExtractedPair(BaseModel):
    """One question-answer pair located by anchors (step 3)."""

    question_section_id: str
    answer_section_id: str
    question_start_anchor: str
    question_end_anchor: str
    answer_start_anchor: str
    answer_end_anchor: str
    question_text_copy: str
    answer_text_copy: str
    topics: list[str] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)


class ExtractedFragment(BaseModel):
    """A question or answer the model could not pair with a counterpart."""

    section_id: str
    role: Literal["question", "answer", "unknown"] = "unknown"
    text: str


class PairExtractionOutput(BaseModel):
    pairs: list[ExtractedPair] = Field(default_factory=list)
    fragments: list[ExtractedFragment] = Field(default_factory=list)


class ChunkAnnotation(BaseModel):
    """Topics and facts for one chunk (step 4)."""

    chunk_id: str
    topics: list[str] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)


class ChunkAnnotationOutput(BaseModel):
    chunks: list[ChunkAnnotation] = Field(default_factory=list)


__all__ = [
    "ChunkAnnotation",
    "ChunkAnnotationOutput",
    "ChunkResult",
    "ClassificationOutput",
    "ExtractedFact",
    "ExtractedFragment",
    "ExtractedPair",
    "ExtractionResult",
    "FailedWindow",
    "PairExtractionOutput",
    "ParsedSection",
    "RawFact",
]
