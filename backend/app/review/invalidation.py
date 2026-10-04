"""Fact invalidation and the expiry sweep (plan: Traceability, rules).

``invalidate`` is the one function that downgrades segments citing a source that is no longer
current; ``run_expiry_sweep`` expires facts past ``expires_on`` and calls it. Neither writes
``questions.status`` and neither commits; the caller (job handler, worker loop or endpoint)
owns the transaction.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.enums import (
    EventType,
    FactChecklistStatus,
    InvalidationReason,
    SegmentKind,
    SourceType,
    SupportStatus,
)
from app.db.models import Answer, Document, Fact, KnowledgeItem, Question, utcnow
from app.review.events import SYSTEM_ACTOR, EntityType, record_event
from app.review.support import recompute_support

logger = logging.getLogger(__name__)

_INVALIDATABLE = frozenset({SourceType.FACT.value, SourceType.KNOWLEDGE_ITEM.value})

# Checklist status recorded for each reason; "removed" is not a checklist status.
_CHECKLIST_STATUS: dict[str, str] = {
    InvalidationReason.SUPERSEDED.value: FactChecklistStatus.SUPERSEDED.value,
    InvalidationReason.EXPIRED.value: FactChecklistStatus.EXPIRED.value,
    InvalidationReason.REMOVED.value: FactChecklistStatus.UNVERIFIED.value,
}

_CITING_ANSWERS_SQL = text(
    """
    SELECT id FROM answers
    WHERE is_current
      AND jsonb_path_exists(
            segments,
            '$[*].sources[*] ? (@.source_type == $stype && @.source_id == $sid)',
            jsonb_build_object('stype', CAST(:stype AS text), 'sid', CAST(:sid AS text))
          )
    """
)


def _cites(segment: dict[str, Any], source_type: str, source_id: str) -> bool:
    return any(
        source.get("source_type") == source_type and str(source.get("source_id")) == source_id
        for source in (segment.get("sources") or [])
    )


def current_answers_citing(
    session: Session, source_type: str, source_id: uuid.UUID
) -> list[Answer]:
    """Current answer versions with at least one segment citing ``source_id``."""
    session.flush()
    ids = session.scalars(
        _CITING_ANSWERS_SQL.bindparams(stype=source_type, sid=str(source_id))
    ).all()
    if not ids:
        return []
    return list(session.scalars(select(Answer).where(Answer.id.in_(ids))).all())


def invalidate(
    session: Session,
    source_type: str | SourceType,
    source_id: uuid.UUID,
    reason: str | InvalidationReason,
) -> int:
    """For every current answer citing ``source_id``: set those segments ``weak``, update the
    matching fact_checklist status, set ``needs_review``, run the recompute function and write
    one ``answer_needs_review`` event per affected question. Idempotent: segments already
    ``weak`` for that source are left alone and no event is written where nothing changed.
    Never writes status. Older versions and thread messages are left as historical record.

    Returns the number of questions affected.
    """
    stype = SourceType(source_type).value
    if stype not in _INVALIDATABLE:
        raise ValueError(f"cannot invalidate a source of type {stype!r}")
    why = InvalidationReason(reason).value
    sid = str(source_id)
    affected = 0
    for answer in current_answers_citing(session, stype, source_id):
        changed_indices: list[int] = []
        segments: list[dict[str, Any]] = []
        for position, stored in enumerate(answer.segments or []):
            segment = dict(stored)
            if (
                _cites(segment, stype, sid)
                and segment.get("kind", SegmentKind.SUBSTANTIVE.value)
                == SegmentKind.SUBSTANTIVE.value
                and segment.get("support_status") != SupportStatus.WEAK.value
            ):
                segment["support_status"] = SupportStatus.WEAK.value
                changed_indices.append(int(segment.get("index", position)))
            segments.append(segment)

        checklist_changed = False
        checklist: list[dict[str, Any]] = []
        if stype == SourceType.FACT.value:
            wanted = _CHECKLIST_STATUS[why]
            for stored_entry in answer.fact_checklist or []:
                entry = dict(stored_entry)
                if str(entry.get("fact_id")) == sid and entry.get("status") != wanted:
                    entry["status"] = wanted
                    checklist_changed = True
                checklist.append(entry)

        if not changed_indices and not checklist_changed:
            continue
        if changed_indices:
            answer.segments = segments
        if checklist_changed:
            answer.fact_checklist = checklist
        question = session.get(Question, answer.question_id)
        if question is None:
            continue
        question.needs_review = True
        recompute_support(session, answer)
        record_event(
            session,
            EntityType.QUESTION,
            question.id,
            EventType.ANSWER_NEEDS_REVIEW,
            SYSTEM_ACTOR,
            {
                "answer_id": str(answer.id),
                "source_type": stype,
                "source_id": sid,
                "reason": why,
                "segment_indices": changed_indices,
            },
            org_id=question.org_id,
        )
        affected += 1
    session.flush()
    return affected


def stale_fact_reason(
    session: Session, fact_id: uuid.UUID, *, today: date | None = None
) -> str | None:
    """Why a fact is no longer current, or None while it is: ``removed`` when the row is gone,
    ``superseded`` when it or its source document is superseded, ``expired`` when it has
    expired (swept or not). Mirrors the plan's definition of a current fact."""
    fact = session.get(Fact, fact_id)
    if fact is None:
        return InvalidationReason.REMOVED.value
    if fact.superseded_by is not None:
        return InvalidationReason.SUPERSEDED.value
    document = session.get(Document, fact.document_id)
    if document is not None and document.superseded_by is not None:
        return InvalidationReason.SUPERSEDED.value
    day = today or datetime.now(UTC).date()
    if fact.expired_at is not None or (fact.expires_on is not None and fact.expires_on < day):
        return InvalidationReason.EXPIRED.value
    return None


def revalidate_copied_sources(session: Session, answer: Answer) -> int:
    """Bring a version whose segments were copied from an earlier record (a thread message
    saved as an answer) up to date with every invalidation that happened since.

    Run once the version is current. Facts that are no longer current and knowledge items that
    were removed go through ``invalidate``, exactly as they would have had this version been
    current when they changed; fact_checklist entries for facts no segment cites are updated
    in place. Returns the number of sources invalidated.
    """
    fact_ids: set[str] = set()
    item_ids: set[str] = set()
    for segment in answer.segments or []:
        for source in segment.get("sources") or []:
            source_id = source.get("source_id")
            if not source_id:
                continue
            if source.get("source_type") == SourceType.FACT.value:
                fact_ids.add(str(source_id))
            elif source.get("source_type") == SourceType.KNOWLEDGE_ITEM.value:
                item_ids.add(str(source_id))

    checklist: list[dict[str, Any]] = []
    checklist_changed = False
    for stored_entry in answer.fact_checklist or []:
        entry = dict(stored_entry)
        fact_id = entry.get("fact_id")
        if fact_id and str(fact_id) not in fact_ids:
            why = stale_fact_reason(session, uuid.UUID(str(fact_id)))
            if why is not None and entry.get("status") != _CHECKLIST_STATUS[why]:
                entry["status"] = _CHECKLIST_STATUS[why]
                checklist_changed = True
        checklist.append(entry)
    if checklist_changed:
        answer.fact_checklist = checklist
        session.flush()

    invalidated = 0
    for fact_id in sorted(fact_ids):
        why = stale_fact_reason(session, uuid.UUID(fact_id))
        if why is not None:
            invalidate(session, SourceType.FACT, uuid.UUID(fact_id), why)
            invalidated += 1
    for item_id in sorted(item_ids):
        if session.get(KnowledgeItem, uuid.UUID(item_id)) is None:
            invalidate(session, SourceType.KNOWLEDGE_ITEM, uuid.UUID(item_id), "removed")
            invalidated += 1
    return invalidated


def run_expiry_sweep(session: Session, *, today: datetime | None = None) -> int:
    """Expire facts with ``expires_on`` in the past and ``expired_at`` null: set ``expired_at``,
    write a ``fact_expired`` event and call ``invalidate`` with reason ``expired``. A second
    run over the same facts is a no-op.

    Returns the number of facts expired. Called by the worker when idle (at most hourly) and
    synchronously before a submission export. Does not commit.
    """
    now = today or datetime.now(UTC)
    due = session.scalars(
        select(Fact).where(Fact.expires_on < now.date(), Fact.expired_at.is_(None))
    ).all()
    for fact in due:
        fact.expired_at = utcnow()
        record_event(
            session,
            EntityType.FACT,
            fact.id,
            EventType.FACT_EXPIRED,
            SYSTEM_ACTOR,
            {
                "fact_kind": fact.fact_kind,
                "fact_key": fact.fact_key,
                "expires_on": fact.expires_on.isoformat() if fact.expires_on else None,
                "document_id": str(fact.document_id),
            },
            org_id=fact.org_id,
        )
        invalidate(session, SourceType.FACT, fact.id, InvalidationReason.EXPIRED)
    if due:
        logger.info("expiry sweep: %d fact(s) expired", len(due))
    session.flush()
    return len(due)
