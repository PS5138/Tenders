"""Ingestion step 8: document supersession.

Applies to reference documents whose ``doc_kind`` is in ``supersedable_doc_kinds``. When a
document is ``classification_confirmed`` and another non-superseded, confirmed reference
document of the same kind exists, the pair's ``supersession_decisions`` row is written or
updated and evaluated:

- kinds in ``keyed_doc_kinds`` never supersede automatically (``pending`` / ``keyed_kind``);
- otherwise, when neither date is ``upload_time`` and the dates differ, ``superseded`` /
  ``auto``: the older document gets ``superseded_by``, its knowledge items are excluded from
  retrieval, its remaining current facts go through fact invalidation with reason
  ``superseded`` and a ``document_superseded`` event is recorded;
- otherwise ``pending`` with reason ``pending_date`` (an upload-time date) or ``tie``.

Re-evaluation never overturns a ``keep_both`` or a human ``superseded`` unless a document's
``effective_date`` changed after ``decided_at``; the corrections module says so through
``effective_date_changed``. No row exists while either member is unconfirmed. Nothing here
commits.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.enums import (
    DocType,
    EffectiveDateSource,
    EntityType,
    EventType,
    InvalidationReason,
)
from app.db.enums import (
    SupersessionDecision as Decision,
)
from app.db.enums import (
    SupersessionReason as Reason,
)
from app.db.models import Document, KnowledgeItem, SupersessionDecision
from app.ingest.facts import call_invalidate, current_facts_of_document
from app.review.events import SYSTEM_ACTOR, record_event

logger = logging.getLogger(__name__)


class DecisionError(ValueError):
    """An invalid decision request (unknown choice, or a superseding document outside the pair)."""


def _now() -> datetime:
    return datetime.now(UTC)


def is_supersedable(document: Document) -> bool:
    settings = get_settings()
    return (
        document.doc_type == DocType.REFERENCE.value
        and document.doc_kind is not None
        and document.doc_kind in settings.supersedable_doc_kinds
        and document.classification_confirmed
    )


def is_human_decision(row: SupersessionDecision) -> bool:
    return row.decision != Decision.PENDING.value and row.decided_by is not None


def rows_for_document(session: Session, document_id: uuid.UUID) -> list[SupersessionDecision]:
    return list(
        session.scalars(
            select(SupersessionDecision).where(
                or_(
                    SupersessionDecision.document_a_id == document_id,
                    SupersessionDecision.document_b_id == document_id,
                )
            )
        )
    )


def other_document_id(row: SupersessionDecision, document_id: uuid.UUID) -> uuid.UUID:
    return row.document_b_id if row.document_a_id == document_id else row.document_a_id


def _pair_valid(row: SupersessionDecision, doc_a: Document, doc_b: Document) -> bool:
    return (
        is_supersedable(doc_a)
        and is_supersedable(doc_b)
        and doc_a.doc_kind == doc_b.doc_kind == row.doc_kind
    )


# --- Effects --------------------------------------------------------------------------------


def _exclude_items(session: Session, document_id: uuid.UUID, excluded: bool) -> int:
    result = session.execute(
        update(KnowledgeItem)
        .where(
            KnowledgeItem.document_id == document_id,
            KnowledgeItem.excluded_from_retrieval.is_(not excluded),
        )
        .values(excluded_from_retrieval=excluded)
        .execution_options(synchronize_session="fetch")
    )
    return result.rowcount or 0


def apply_supersession_effects(
    session: Session,
    older: Document,
    newer: Document,
    *,
    actor: str,
    row: SupersessionDecision | None,
) -> None:
    """The step 8 effects on ``older``: pointer, exclusion, fact invalidation, event."""
    superseder = session.get(Document, older.superseded_by) if older.superseded_by else None
    if superseder is None or superseder.id == newer.id or (
        superseder.effective_date is not None
        and newer.effective_date is not None
        and newer.effective_date >= superseder.effective_date
    ):
        older.superseded_by = newer.id
    excluded = _exclude_items(session, older.id, True)
    invalidated = 0
    for fact in current_facts_of_document(session, older.id):
        call_invalidate(session, "fact", fact.id, InvalidationReason.SUPERSEDED)
        invalidated += 1
    session.flush()
    record_event(
        session,
        EntityType.DOCUMENT,
        older.id,
        EventType.DOCUMENT_SUPERSEDED,
        actor,
        {
            "superseded_by": str(newer.id),
            "doc_kind": older.doc_kind,
            "decision_id": str(row.id) if row is not None else None,
            "reason": row.reason if row is not None else None,
            "items_excluded": excluded,
            "facts_invalidated": invalidated,
        },
        org_id=older.org_id,
    )
    logger.info("document %s superseded by %s", older.id, newer.id)


def _other_superseder(
    session: Session, superseded: Document, excluding: uuid.UUID
) -> Document | None:
    """Another decided row that still supersedes ``superseded``, if any."""
    for row in rows_for_document(session, superseded.id):
        if (
            row.decision == Decision.SUPERSEDED.value
            and row.superseding_document_id is not None
            and row.superseding_document_id != superseded.id
            and row.superseding_document_id != excluding
        ):
            return session.get(Document, row.superseding_document_id)
    return None


def reverse_supersession_effects(
    session: Session,
    superseded: Document,
    by: Document,
    *,
    actor: str = SYSTEM_ACTOR,
    reason: str | None = None,
) -> None:
    """Undo an exclusion that a decision no longer supports. Restores no answer segment.

    When the exclusion is lifted a ``supersession_reversed`` event is written on the document
    (``actor`` is the deciding user for a manual keep_both, ``system`` for rule-driven
    reversals); when another decided row still supersedes the document only the pointer moves.
    """
    if superseded.superseded_by != by.id:
        return
    replacement = _other_superseder(session, superseded, by.id)
    if replacement is not None:
        superseded.superseded_by = replacement.id
        session.flush()
        return
    superseded.superseded_by = None
    restored = _exclude_items(session, superseded.id, False)
    session.flush()
    record_event(
        session,
        EntityType.DOCUMENT,
        superseded.id,
        EventType.SUPERSESSION_REVERSED,
        actor,
        {
            "previously_superseded_by": str(by.id),
            "doc_kind": superseded.doc_kind,
            "reason": reason,
            "items_restored": restored,
        },
        org_id=superseded.org_id,
    )
    logger.info("document %s no longer superseded by %s", superseded.id, by.id)


# --- Evaluation -----------------------------------------------------------------------------


def _automatic_outcome(
    doc_a: Document, doc_b: Document
) -> tuple[str, str, Document | None, Document | None]:
    """``(decision, reason, older, newer)`` the rules give for the pair."""
    settings = get_settings()
    if doc_a.doc_kind in settings.keyed_doc_kinds:
        return Decision.PENDING.value, Reason.KEYED_KIND.value, None, None
    upload = EffectiveDateSource.UPLOAD_TIME.value
    if (
        doc_a.effective_date_source == upload
        or doc_b.effective_date_source == upload
        or doc_a.effective_date is None
        or doc_b.effective_date is None
    ):
        return Decision.PENDING.value, Reason.PENDING_DATE.value, None, None
    if doc_a.effective_date == doc_b.effective_date:
        return Decision.PENDING.value, Reason.TIE.value, None, None
    older, newer = (
        (doc_a, doc_b) if doc_a.effective_date < doc_b.effective_date else (doc_b, doc_a)
    )
    return Decision.SUPERSEDED.value, Reason.AUTO.value, older, newer


def _reset_human_decision(
    session: Session, row: SupersessionDecision, doc_a: Document, doc_b: Document
) -> None:
    if row.decision == Decision.SUPERSEDED.value and row.superseding_document_id is not None:
        newer = doc_a if row.superseding_document_id == doc_a.id else doc_b
        older = doc_b if newer is doc_a else doc_a
        reverse_supersession_effects(session, older, newer, reason="date_changed")
    row.decision = Decision.PENDING.value
    row.superseding_document_id = None
    row.decided_by = None
    row.decided_at = None
    session.flush()


def evaluate_row(
    session: Session,
    row: SupersessionDecision,
    doc_a: Document,
    doc_b: Document,
    *,
    overturn_human: bool = False,
) -> None:
    """Evaluate one pair row in place, applying or reversing effects as the outcome changes."""
    if is_human_decision(row):
        if not overturn_human:
            return
        _reset_human_decision(session, row, doc_a, doc_b)

    decision, reason, older, newer = _automatic_outcome(doc_a, doc_b)
    previously_auto = row.decision == Decision.SUPERSEDED.value and row.decided_by is None
    if previously_auto and row.superseding_document_id is not None:
        same_direction = (
            decision == Decision.SUPERSEDED.value
            and newer is not None
            and row.superseding_document_id == newer.id
        )
        if same_direction:
            # Already applied; keep the exclusion in force and write nothing new.
            assert older is not None
            _exclude_items(session, older.id, True)
            if older.superseded_by is None:
                older.superseded_by = newer.id
            row.reason = reason
            session.flush()
            return
        previous_newer = doc_a if row.superseding_document_id == doc_a.id else doc_b
        previous_older = doc_b if previous_newer is doc_a else doc_a
        reverse_supersession_effects(
            session, previous_older, previous_newer, reason="re_evaluated"
        )

    row.decision = decision
    row.reason = reason
    row.decided_by = None
    if decision == Decision.SUPERSEDED.value:
        assert older is not None and newer is not None
        row.superseding_document_id = newer.id
        row.decided_at = _now()
        session.flush()
        apply_supersession_effects(session, older, newer, actor=SYSTEM_ACTOR, row=row)
    else:
        row.superseding_document_id = None
        row.decided_at = None
    session.flush()


def remove_decision_row(
    session: Session, row: SupersessionDecision, *, reverse: bool = True
) -> None:
    """Delete a pair row, reversing the exclusion it caused when ``reverse`` is set."""
    if reverse and row.decision == Decision.SUPERSEDED.value and row.superseding_document_id:
        newer = session.get(Document, row.superseding_document_id)
        older_id = other_document_id(row, row.superseding_document_id)
        older = session.get(Document, older_id)
        if newer is not None and older is not None:
            reverse_supersession_effects(session, older, newer, reason="decision_removed")
    session.delete(row)
    session.flush()


def _partners(session: Session, document: Document) -> list[Document]:
    """Confirmed, non-superseded reference documents of the same kind, without a row yet."""
    existing = {
        other_document_id(row, document.id) for row in rows_for_document(session, document.id)
    }
    stmt = select(Document).where(
        Document.org_id == document.org_id,
        Document.id != document.id,
        Document.doc_type == DocType.REFERENCE.value,
        Document.doc_kind == document.doc_kind,
        Document.classification_confirmed.is_(True),
        Document.superseded_by.is_(None),
    )
    return [doc for doc in session.scalars(stmt) if doc.id not in existing]


def _create_row(session: Session, doc_a: Document, doc_b: Document) -> SupersessionDecision:
    # The models' CHECK wants document_a_id < document_b_id; UUIDs compare as 128-bit ints in
    # both Python and Postgres, so sorting here satisfies it.
    first, second = sorted((doc_a, doc_b), key=lambda doc: doc.id)
    row = SupersessionDecision(
        org_id=doc_a.org_id,
        doc_kind=doc_a.doc_kind or "",
        document_a_id=first.id,
        document_b_id=second.id,
        reason=Reason.PENDING_DATE.value,
        decision=Decision.PENDING.value,
    )
    session.add(row)
    session.flush()
    return row


def evaluate_document_supersession(
    session: Session,
    document: Document,
    *,
    effective_date_changed: bool = False,
    _cascade: bool = True,
) -> None:
    """Write, update and evaluate every pair row ``document`` belongs to (step 8).

    Rows whose pair is no longer valid (a member unconfirmed, no longer a reference document,
    or of a different kind) are removed and their exclusion reversed; the former partner is
    then re-evaluated for its own kind. ``effective_date_changed`` lets a human decision be
    overturned, per the plan's rule. Flushes but does not commit.
    """
    session.flush()
    stale_partners: list[Document] = []
    for row in rows_for_document(session, document.id):
        other = session.get(Document, other_document_id(row, document.id))
        if other is None:
            remove_decision_row(session, row, reverse=False)
            continue
        if not _pair_valid(row, document, other):
            remove_decision_row(session, row, reverse=True)
            stale_partners.append(other)
            continue
        doc_a = document if row.document_a_id == document.id else other
        doc_b = other if doc_a is document else document
        evaluate_row(session, row, doc_a, doc_b, overturn_human=effective_date_changed)

    if is_supersedable(document) and document.superseded_by is None:
        for partner in _partners(session, document):
            row = _create_row(session, document, partner)
            doc_a = document if row.document_a_id == document.id else partner
            doc_b = partner if doc_a is document else document
            evaluate_row(session, row, doc_a, doc_b)
            if document.superseded_by is not None:
                break

    if document.superseded_by is not None:
        # A superseded document's items are always excluded, including items an extracting
        # re-run created after a human decision that this evaluation leaves standing.
        _exclude_items(session, document.id, True)

    if _cascade:
        for partner in stale_partners:
            evaluate_document_supersession(session, partner, _cascade=False)
    session.flush()


# --- The decisions endpoint -----------------------------------------------------------------


def apply_decision(
    session: Session,
    decision: SupersessionDecision,
    choice: str,
    superseding_document_id: uuid.UUID | None,
    actor: str,
) -> None:
    """Resolve a pending (or re-decide a decided) pair by hand. Flushes but does not commit.

    ``superseded`` applies the step 8 effects to the other document; ``keep_both`` reverses an
    earlier automatic exclusion. Both write a ``supersession_decided`` event.
    """
    doc_a = session.get(Document, decision.document_a_id)
    doc_b = session.get(Document, decision.document_b_id)
    if doc_a is None or doc_b is None:
        raise DecisionError("One of the documents in this pair no longer exists.")

    if choice == Decision.SUPERSEDED.value:
        if superseding_document_id not in (doc_a.id, doc_b.id):
            raise DecisionError("superseding_document_id must be one of the pair's documents.")
        newer = doc_a if superseding_document_id == doc_a.id else doc_b
        older = doc_b if newer is doc_a else doc_a
        if (
            decision.decision == Decision.SUPERSEDED.value
            and decision.superseding_document_id not in (None, newer.id)
        ):
            reverse_supersession_effects(
                session, newer, older, actor=actor, reason="decision_changed"
            )
        decision.decision = Decision.SUPERSEDED.value
        decision.superseding_document_id = newer.id
        decision.decided_by = actor
        decision.decided_at = _now()
        session.flush()
        if older.superseded_by != newer.id:
            apply_supersession_effects(session, older, newer, actor=actor, row=decision)
        else:
            _exclude_items(session, older.id, True)
    elif choice == Decision.KEEP_BOTH.value:
        if decision.decision == Decision.SUPERSEDED.value and decision.superseding_document_id:
            newer = doc_a if decision.superseding_document_id == doc_a.id else doc_b
            older = doc_b if newer is doc_a else doc_a
            reverse_supersession_effects(session, older, newer, actor=actor, reason="keep_both")
        decision.decision = Decision.KEEP_BOTH.value
        decision.superseding_document_id = None
        decision.decided_by = actor
        decision.decided_at = _now()
    else:
        raise DecisionError("decision must be 'superseded' or 'keep_both'.")
    session.flush()
    record_event(
        session,
        EntityType.SUPERSESSION_DECISION,
        decision.id,
        EventType.SUPERSESSION_DECIDED,
        actor,
        {
            "decision": decision.decision,
            "reason": decision.reason,
            "doc_kind": decision.doc_kind,
            "document_a_id": str(decision.document_a_id),
            "document_b_id": str(decision.document_b_id),
            "superseding_document_id": (
                str(decision.superseding_document_id) if decision.superseding_document_id else None
            ),
        },
        org_id=decision.org_id,
    )
