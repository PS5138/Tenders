"""Ingestion step 7: persist facts and evaluate fact supersession.

``persist_facts`` stores the raw facts an extraction step returned (owner A's
``app.ingest.types.RawFact``: ``fact_kind``, ``fact_key``, ``statement``, ``value``,
``effective_date``, ``expires_on``, ``section_id``, ``knowledge_item_id``). A null effective
date is inherited from the document, ``fact_kind`` is validated against the configured list
and ``fact_key`` follows the kind's ``key_rule`` (null for kinds with one current holder).

``evaluate_fact_supersession`` implements the fact gate: a fact supersedes another only when
``fact_kind`` and ``fact_key`` match, both source documents share a ``doc_type``, both are
``classification_confirmed``, neither has ``effective_date_source = upload_time`` and the
effective dates differ; the later-dated fact supersedes regardless of upload order. It
recomputes ``superseded_by`` in place, so a pointer the rule no longer supports is cleared
(which restores no segment) and a newly superseded fact goes through fact invalidation.

Nothing here commits.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.enums import EffectiveDateSource, EntityType, EventType, InvalidationReason
from app.db.models import Document, Fact, KnowledgeItem
from app.ingest.normalise import normalise
from app.review import invalidation as review_invalidation
from app.review.events import SYSTEM_ACTOR, record_event

logger = logging.getLogger(__name__)

# Mirrors ``facts.fact_key``'s ``String(256)``; a longer model-returned key would fail the flush
# of the whole extracting stage.
_FACT_KEY_MAX = 256


def call_invalidate(
    session: Session, source_type: str, source_id: uuid.UUID, reason: str | InvalidationReason
) -> None:
    """Call the review module's fact-invalidation function.

    Until the review owner lands it the stub raises ``NotImplementedError``; that is logged
    and ingestion carries on, because supersession must not fail on a missing downstream.
    """
    try:
        review_invalidation.invalidate(session, source_type, source_id, str(reason))
    except NotImplementedError:
        logger.warning(
            "fact invalidation not available yet: %s %s (%s) not propagated to answers",
            source_type,
            source_id,
            reason,
        )


def _get(raw: Any, name: str, default: Any = None) -> Any:
    if isinstance(raw, dict):
        return raw.get(name, default)
    return getattr(raw, name, default)


def normalise_fact_key(fact_kind: str, fact_key: str | None) -> str | None:
    """Apply the kind's ``key_rule``: null for unkeyed kinds, whitespace-collapsed and cut to
    the column's length otherwise (Python slices by code point, as ``varchar(256)`` counts)."""
    kind = get_settings().fact_kinds.get(fact_kind)
    if kind is None or not kind.keyed:
        return None
    if fact_key is None:
        return None
    key = " ".join(str(fact_key).split())
    if len(key) > _FACT_KEY_MAX:
        logger.warning(
            "%s fact key of %d characters truncated to %d", fact_kind, len(key), _FACT_KEY_MAX
        )
        key = key[:_FACT_KEY_MAX].rstrip()
    return key or None


def _comparable_key(fact_key: str | None) -> str:
    return normalise(fact_key)[0] if fact_key else ""


def _coerce_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def persist_facts(session: Session, document: Document, raw_facts: Iterable[Any]) -> list[Fact]:
    """Persist ``raw_facts`` for ``document``. Flushes but does not commit.

    Facts with an unknown ``fact_kind`` or no locatable section are skipped with a warning.
    """
    settings = get_settings()
    document_date = document.effective_date or (
        document.created_at.date() if document.created_at else datetime.now(UTC).date()
    )
    facts: list[Fact] = []
    for raw in raw_facts:
        fact_kind = _get(raw, "fact_kind")
        if fact_kind not in settings.fact_kinds:
            logger.warning(
                "skipping fact with unknown kind %r on document %s", fact_kind, document.id
            )
            continue
        section_id = _get(raw, "section_id")
        item_id = _get(raw, "knowledge_item_id")
        if section_id is None and item_id is not None:
            item = session.get(KnowledgeItem, item_id)
            section_id = item.section_id if item is not None else None
        if section_id is None:
            logger.warning(
                "skipping %s fact without a section on document %s", fact_kind, document.id
            )
            continue
        statement = str(_get(raw, "statement") or "").strip()
        value = _get(raw, "value")
        fact = Fact(
            org_id=document.org_id,
            document_id=document.id,
            section_id=uuid.UUID(str(section_id)),
            knowledge_item_id=uuid.UUID(str(item_id)) if item_id is not None else None,
            fact_kind=fact_kind,
            fact_key=normalise_fact_key(fact_kind, _get(raw, "fact_key")),
            statement=statement or str(value or ""),
            value="" if value is None else str(value),
            effective_date=_coerce_date(_get(raw, "effective_date")) or document_date,
            expires_on=_coerce_date(_get(raw, "expires_on")),
        )
        session.add(fact)
        facts.append(fact)
    session.flush()
    return facts


def gate_allows(fact_a: Fact, doc_a: Document, fact_b: Fact, doc_b: Document) -> bool:
    """The fact gate between two facts of the same kind and key."""
    if doc_a.id == doc_b.id:
        return False
    if doc_a.doc_type is None or doc_a.doc_type != doc_b.doc_type:
        return False
    if not (doc_a.classification_confirmed and doc_b.classification_confirmed):
        return False
    upload = EffectiveDateSource.UPLOAD_TIME.value
    if doc_a.effective_date_source == upload or doc_b.effective_date_source == upload:
        return False
    return fact_a.effective_date != fact_b.effective_date


def _recency(fact: Fact) -> tuple[date, datetime]:
    return fact.effective_date, fact.created_at or datetime.min.replace(tzinfo=UTC)


def _successor(
    fact: Fact, group: Sequence[Fact], documents: dict[uuid.UUID, Document]
) -> Fact | None:
    """The fact that supersedes ``fact`` under the gate: the latest-dated eligible successor."""
    doc = documents[fact.document_id]
    best: Fact | None = None
    for other in group:
        if other.id == fact.id or other.effective_date <= fact.effective_date:
            continue
        if not gate_allows(fact, doc, other, documents[other.document_id]):
            continue
        if best is None or _recency(other) > _recency(best):
            best = other
    return best


def evaluate_fact_supersession(session: Session, document: Document) -> None:
    """Recompute ``superseded_by`` for every fact group ``document`` takes part in.

    A group is one (``fact_kind``, comparable ``fact_key``) across the organisation. Within a
    group every fact's pointer is recomputed: a new pointer goes through fact invalidation
    (reason ``superseded``) and writes a ``fact_superseded`` event; a pointer the rule no
    longer supports is cleared. Idempotent. Flushes but does not commit.
    """
    session.flush()
    own = session.scalars(select(Fact).where(Fact.document_id == document.id)).all()
    if not own:
        return
    kinds = {fact.fact_kind for fact in own}
    keys = {(fact.fact_kind, _comparable_key(fact.fact_key)) for fact in own}
    candidates = session.scalars(
        select(Fact).where(Fact.org_id == document.org_id, Fact.fact_kind.in_(kinds))
    ).all()
    groups: dict[tuple[str, str], list[Fact]] = defaultdict(list)
    for fact in candidates:
        group_key = (fact.fact_kind, _comparable_key(fact.fact_key))
        if group_key in keys:
            groups[group_key].append(fact)
    document_ids = {fact.document_id for facts in groups.values() for fact in facts}
    documents = {
        doc.id: doc
        for doc in session.scalars(select(Document).where(Document.id.in_(document_ids)))
    }
    documents[document.id] = document

    for group in groups.values():
        for fact in group:
            successor = _successor(fact, group, documents)
            new_pointer = successor.id if successor is not None else None
            old_pointer = fact.superseded_by
            if new_pointer == old_pointer:
                continue
            fact.superseded_by = new_pointer
            if new_pointer is None:
                logger.info("fact %s: supersession pointer cleared by re-evaluation", fact.id)
                continue
            session.flush()
            if old_pointer is None:
                record_event(
                    session,
                    EntityType.FACT,
                    fact.id,
                    EventType.FACT_SUPERSEDED,
                    SYSTEM_ACTOR,
                    {
                        "superseded_by": str(new_pointer),
                        "document_id": str(fact.document_id),
                        "superseding_document_id": str(successor.document_id),
                        "fact_kind": fact.fact_kind,
                        "fact_key": fact.fact_key,
                    },
                    org_id=fact.org_id,
                )
                call_invalidate(session, "fact", fact.id, InvalidationReason.SUPERSEDED)
    session.flush()


def current_facts_of_document(session: Session, document_id: uuid.UUID) -> list[Fact]:
    """Facts of a document that are neither superseded nor expired."""
    today = datetime.now(UTC).date()
    facts = session.scalars(
        select(Fact).where(
            Fact.document_id == document_id,
            Fact.superseded_by.is_(None),
            Fact.expired_at.is_(None),
        )
    ).all()
    return [fact for fact in facts if fact.expires_on is None or fact.expires_on >= today]
