"""Support verification: draft pipeline step 9.

For each document source of each segment, ``find_span`` locates the quoted span inside the
cited item's own slice of its section (for a fact, inside the fact's statement located in its
source section), records ``locator.start`` and ``locator.end`` in section coordinates and
replaces ``quote`` with the section text at those offsets, so the popover and the source pane
show what the document says. A span found in the section but outside the item's slice does
not count. Then one entailment call (the fast model) batched across every located segment
decides ``supported`` or ``weak``. Segments whose span was not found are ``unsupported`` and
keep the candidate source with null offsets. A segment resting on a fact that is no longer
current is never returned to ``supported``. Connective segments stay ``connective``.

``verify_fact_checklist`` gives the checklist entries their status from the same span check
and from ``superseded_by`` / ``expires_on``.

Nothing here commits; the caller owns the transaction.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.enums import (
    FactChecklistStatus,
    SegmentKind,
    SourceType,
    SupportStatus,
)
from app.db.models import Document, DocumentSection, Fact, KnowledgeItem
from app.ingest.normalise import SpanMatch, find_span
from app.llm import get_llm, load_prompt

logger = logging.getLogger(__name__)

ENTAILMENT_PROMPT = "entailment"


# --- Output schema of the entailment call -----------------------------------------------------


class EntailmentVerdict(BaseModel):
    id: int
    verdict: Literal["supported", "weak"]


class EntailmentResult(BaseModel):
    verdicts: list[EntailmentVerdict] = Field(default_factory=list)


# --- Row lookup with a per-call cache -----------------------------------------------------------


@dataclass
class _Rows:
    """Rows a verification pass touches, loaded once each."""

    session: Session
    items: dict[uuid.UUID, KnowledgeItem | None] = field(default_factory=dict)
    facts: dict[uuid.UUID, Fact | None] = field(default_factory=dict)
    sections: dict[uuid.UUID, DocumentSection | None] = field(default_factory=dict)
    documents: dict[uuid.UUID, Document | None] = field(default_factory=dict)

    def item(self, item_id: uuid.UUID) -> KnowledgeItem | None:
        if item_id not in self.items:
            self.items[item_id] = self.session.get(KnowledgeItem, item_id)
        return self.items[item_id]

    def fact(self, fact_id: uuid.UUID) -> Fact | None:
        if fact_id not in self.facts:
            self.facts[fact_id] = self.session.get(Fact, fact_id)
        return self.facts[fact_id]

    def section(self, section_id: uuid.UUID) -> DocumentSection | None:
        if section_id not in self.sections:
            self.sections[section_id] = self.session.get(DocumentSection, section_id)
        return self.sections[section_id]

    def document(self, document_id: uuid.UUID) -> Document | None:
        if document_id not in self.documents:
            self.documents[document_id] = self.session.get(Document, document_id)
        return self.documents[document_id]


# Public name for callers outside this module that locate sources themselves (the requirement
# suggestion in ``app.retrieve.requirements`` verifies its evidence quotes the same way).
SourceRows = _Rows


def _as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


# --- Source records -----------------------------------------------------------------------------


def source_record(
    *,
    source_type: str,
    source_id: uuid.UUID | str,
    quote: str,
    section: DocumentSection,
    document: Document | None,
    start: int | None = None,
    end: int | None = None,
    tier: str | None = None,
) -> dict[str, Any]:
    """The plan's document-source record: locator from the section, title, type, kind and
    effective date from the document. ``start`` and ``end`` are null until verification."""
    record: dict[str, Any] = {
        "source_type": source_type,
        "source_id": str(source_id),
        "quote": quote,
        "locator": {
            "document_id": str(section.document_id),
            "section_id": str(section.id),
            "start": start,
            "end": end,
            "page": section.page_start,
            "table": section.table_index,
            "cell_ref": section.cell_ref,
            "heading_path": list(section.heading_path or []),
        },
        "document_title": document.filename if document is not None else "",
        "doc_type": document.doc_type if document is not None else None,
        "doc_kind": document.doc_kind if document is not None else None,
        "effective_date": (
            document.effective_date.isoformat()
            if document is not None and document.effective_date
            else None
        ),
    }
    if tier is not None:
        record["tier"] = tier
    return record


def fact_is_current(fact: Fact, document: Document | None, today: date | None = None) -> bool:
    """A fact is current when it is not superseded, not expired, and its source document is
    not superseded."""
    today = today or datetime.now(UTC).date()
    if fact.superseded_by is not None or fact.expired_at is not None:
        return False
    if fact.expires_on is not None and fact.expires_on < today:
        return False
    return not (document is not None and document.superseded_by is not None)


def fact_checklist_status(
    fact: Fact, document: Document | None, section: DocumentSection | None
) -> str:
    """``superseded`` | ``expired`` | ``current`` | ``unverified`` for a checklist entry."""
    today = datetime.now(UTC).date()
    if fact.superseded_by is not None or (document is not None and document.superseded_by):
        return FactChecklistStatus.SUPERSEDED.value
    if fact.expired_at is not None or (fact.expires_on is not None and fact.expires_on < today):
        return FactChecklistStatus.EXPIRED.value
    if section is not None and find_span(fact.statement, section.text) is not None:
        return FactChecklistStatus.CURRENT.value
    return FactChecklistStatus.UNVERIFIED.value


# --- Locating one source ------------------------------------------------------------------------


@dataclass
class _Located:
    source: dict[str, Any]
    found: bool
    span_text: str | None
    fact_current: bool | None  # None for knowledge items


def _null_offsets(source: dict[str, Any]) -> dict[str, Any]:
    locator = dict(source.get("locator") or {})
    locator["start"] = None
    locator["end"] = None
    source["locator"] = locator
    source.pop("tier", None)
    return source


def _apply_match(
    source: dict[str, Any], section: DocumentSection, base: int, match: SpanMatch
) -> None:
    start, end = base + match.start, base + match.end
    source["locator"]["start"] = start
    source["locator"]["end"] = end
    source["quote"] = section.text[start:end]
    source["tier"] = match.tier


def _locate_item(rows: _Rows, source: dict[str, Any], item: KnowledgeItem) -> _Located:
    section = rows.section(item.section_id)
    document = rows.document(item.document_id)
    if section is None:
        logger.warning("knowledge item %s cites a missing section %s", item.id, item.section_id)
        return _Located(_null_offsets(source), False, None, None)
    quote = str(source.get("quote") or "")
    record = source_record(
        source_type=SourceType.KNOWLEDGE_ITEM.value,
        source_id=item.id,
        quote=quote,
        section=section,
        document=document,
    )
    item_slice = section.text[item.answer_start : item.answer_end]
    match = find_span(quote, item_slice) if quote.strip() else None
    if match is None:
        return _Located(record, False, None, None)
    _apply_match(record, section, item.answer_start, match)
    return _Located(record, True, record["quote"], None)


def _locate_fact(rows: _Rows, source: dict[str, Any], fact: Fact) -> _Located:
    section = rows.section(fact.section_id)
    document = rows.document(fact.document_id)
    current = fact_is_current(fact, document)
    if section is None:
        logger.warning("fact %s cites a missing section %s", fact.id, fact.section_id)
        return _Located(_null_offsets(source), False, None, current)
    quote = str(source.get("quote") or "")
    record = source_record(
        source_type=SourceType.FACT.value,
        source_id=fact.id,
        quote=quote,
        section=section,
        document=document,
    )
    statement = find_span(fact.statement, section.text)
    if statement is None:
        return _Located(record, False, None, current)
    statement_text = section.text[statement.start : statement.end]
    inner = find_span(quote, statement_text) if quote.strip() else None
    if inner is not None:
        _apply_match(record, section, statement.start, inner)
    else:
        # The fact's statement is the evidence; the model's quote was a paraphrase of it.
        _apply_match(record, section, 0, statement)
    return _Located(record, True, record["quote"], current)


def locate_source(rows: _Rows, source: Mapping[str, Any]) -> _Located:
    """Locate one document source. Unknown ids or missing rows keep the source as given with
    null offsets (the reviewer still sees what the model claimed)."""
    copy: dict[str, Any] = json.loads(json.dumps(dict(source), default=str))
    source_type = copy.get("source_type")
    source_id = _as_uuid(copy.get("source_id"))
    if source_id is None:
        return _Located(_null_offsets(copy), False, None, None)
    if source_type == SourceType.KNOWLEDGE_ITEM.value:
        item = rows.item(source_id)
        if item is None:
            return _Located(_null_offsets(copy), False, None, None)
        return _locate_item(rows, copy, item)
    if source_type == SourceType.FACT.value:
        fact = rows.fact(source_id)
        if fact is None:
            return _Located(_null_offsets(copy), False, None, None)
        return _locate_fact(rows, copy, fact)
    return _Located(copy, False, None, None)


# --- The entailment call ------------------------------------------------------------------------


def _entailment(
    items: list[dict[str, Any]],
) -> dict[int, str]:
    """One fast-model call over every located segment; ``{id: verdict}``. A missing verdict is
    treated as ``weak`` by the caller."""
    if not items:
        return {}
    settings = get_settings()
    result = get_llm().parse(
        ENTAILMENT_PROMPT,
        model=settings.model_fast,
        system=load_prompt(ENTAILMENT_PROMPT),
        user=json.dumps({"items": items}, ensure_ascii=False, indent=1),
        output_model=EntailmentResult,
        max_tokens=8000,
    )
    return {verdict.id: verdict.verdict for verdict in result.verdicts}


# --- Public API ---------------------------------------------------------------------------------


def verify_segments(session: Session, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Step 9 over stored-shape segments. Returns new segment dicts with stored
    ``support_status`` values, verified sources (offsets filled where found, quotes replaced
    with the section text) and ``dispute`` carried through unchanged."""
    rows = _Rows(session)
    verified: list[dict[str, Any]] = []
    entailment_items: list[dict[str, Any]] = []
    pending: dict[int, dict[str, Any]] = {}  # entailment id -> verified segment

    for position, segment in enumerate(segments):
        index = int(segment.get("index", position))
        kind = (
            SegmentKind.CONNECTIVE.value
            if segment.get("kind") == SegmentKind.CONNECTIVE.value
            else SegmentKind.SUBSTANTIVE.value
        )
        text = str(segment.get("text") or "")
        sources_out: list[dict[str, Any]] = []
        located: list[_Located] = []
        has_document_source = False
        attested = False
        for source in segment.get("sources") or []:
            if source.get("source_type") == SourceType.HUMAN_ATTESTATION.value:
                attested = True
                sources_out.append(dict(source))
                continue
            has_document_source = True
            result = locate_source(rows, source)
            sources_out.append(result.source)
            if result.found:
                located.append(result)

        out = {
            "index": index,
            "paragraph": int(segment.get("paragraph", 0) or 0),
            "text": text,
            "kind": kind,
            "sources": sources_out,
            "dispute": segment.get("dispute"),
            "support_status": SupportStatus.UNSUPPORTED.value,
        }
        if kind == SegmentKind.CONNECTIVE.value:
            out["support_status"] = SupportStatus.CONNECTIVE.value
        elif attested:
            out["support_status"] = SupportStatus.SUPPORTED.value
        elif not has_document_source or not located:
            out["support_status"] = SupportStatus.UNSUPPORTED.value
        else:
            entailment_id = len(entailment_items)
            entailment_items.append(
                {
                    "id": entailment_id,
                    "sentence": text,
                    "spans": [
                        {
                            "text": item.span_text,
                            "source_type": item.source["source_type"],
                            "doc_type": item.source.get("doc_type"),
                            "effective_date": item.source.get("effective_date"),
                        }
                        for item in located
                    ],
                }
            )
            pending[entailment_id] = out
            out["_fact_current"] = all(item.fact_current is not False for item in located)
        verified.append(out)

    verdicts = _entailment(entailment_items)
    for entailment_id, out in pending.items():
        verdict = verdicts.get(entailment_id, SupportStatus.WEAK.value)
        if verdict not in (SupportStatus.SUPPORTED.value, SupportStatus.WEAK.value):
            verdict = SupportStatus.WEAK.value
        if not out.pop("_fact_current", True):
            # A sentence resting on a fact that is no longer current never returns to supported.
            verdict = SupportStatus.WEAK.value
        out["support_status"] = verdict
    for out in verified:
        out.pop("_fact_current", None)
    return verified


def verify_fact_checklist(
    session: Session, fact_ids: Iterable[uuid.UUID | str]
) -> list[dict[str, Any]]:
    """Checklist entries ``{fact_id, statement, effective_date, status}`` for the given facts,
    in the order given, unknown ids dropped."""
    rows = _Rows(session)
    entries: list[dict[str, Any]] = []
    seen: set[uuid.UUID] = set()
    for raw in fact_ids:
        fact_id = _as_uuid(raw)
        if fact_id is None or fact_id in seen:
            continue
        seen.add(fact_id)
        fact = rows.fact(fact_id)
        if fact is None:
            logger.warning("fact checklist names an unknown fact %s; dropped", fact_id)
            continue
        document = rows.document(fact.document_id)
        section = rows.section(fact.section_id)
        entries.append(
            {
                "fact_id": str(fact.id),
                "statement": fact.statement,
                "effective_date": fact.effective_date.isoformat() if fact.effective_date else None,
                "status": fact_checklist_status(fact, document, section),
            }
        )
    return entries


__all__ = [
    "ENTAILMENT_PROMPT",
    "EntailmentResult",
    "EntailmentVerdict",
    "SourceRows",
    "fact_checklist_status",
    "fact_is_current",
    "locate_source",
    "source_record",
    "verify_fact_checklist",
    "verify_segments",
]
