"""Ingestion step 6: deduplication and canonical election.

For each new pair (or promoted answer) the answer embedding is compared against the existing
canonical items of the organisation through pgvector. At or above ``dedup_threshold`` (cosine
0.90) the item joins that cluster; otherwise it becomes a new canonical item. The canonical of
a cluster is re-elected on every addition or removal: a promoted answer beats an extracted
pair, then the newest source-document ``effective_date`` wins, then the earliest created item.
Chunks are their own single-member clusters and are never compared.

Nothing here commits; the caller owns the transaction.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.enums import ItemType
from app.db.models import Document, KnowledgeItem

logger = logging.getLogger(__name__)

# Item types that take part in clustering. Chunks stand alone.
CLUSTERED_ITEM_TYPES: frozenset[str] = frozenset(
    {ItemType.QA_PAIR.value, ItemType.PROMOTED_ANSWER.value}
)


def _election_key(
    item: KnowledgeItem, effective_dates: dict[uuid.UUID, date | None]
) -> tuple[int, int, datetime, str]:
    """Sort key: promoted answers first, then the newest effective date, then oldest created."""
    promoted = 0 if item.item_type == ItemType.PROMOTED_ANSWER.value else 1
    effective = effective_dates.get(item.document_id) or date.min
    created = item.created_at if item.created_at is not None else datetime.max.replace(tzinfo=UTC)
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (promoted, -effective.toordinal(), created, str(item.id))


def _elect(session: Session, members: Sequence[KnowledgeItem]) -> KnowledgeItem | None:
    """Make the best of ``members`` canonical and attach the rest to it. Flushes."""
    live = [member for member in members if member in session and member.id is not None]
    if not live:
        return None
    document_ids = {member.document_id for member in live}
    effective_dates = {
        document.id: document.effective_date
        for document in session.scalars(select(Document).where(Document.id.in_(document_ids)))
    }
    winner = min(live, key=lambda member: _election_key(member, effective_dates))
    # Detach everyone first so the self-referencing foreign key never points at a variant.
    for member in live:
        if member is not winner:
            member.canonical_id = None
            member.is_canonical = False
    winner.canonical_id = None
    winner.is_canonical = True
    session.flush()
    for member in live:
        if member is not winner:
            member.canonical_id = winner.id
    session.flush()
    return winner


def cluster_members(session: Session, canonical_id: uuid.UUID) -> list[KnowledgeItem]:
    """The canonical item (if it still exists) and every item attached to it.

    When the canonical was just deleted, the database has already set the variants'
    ``canonical_id`` to null (``ON DELETE SET NULL``), so the in-session objects that still
    hold the stale pointer are gathered too.
    """
    members = list(
        session.scalars(
            select(KnowledgeItem).where(
                or_(KnowledgeItem.id == canonical_id, KnowledgeItem.canonical_id == canonical_id)
            )
        )
    )
    seen = {member.id for member in members}
    for obj in list(session.identity_map.values()):
        if (
            isinstance(obj, KnowledgeItem)
            and obj.id not in seen
            and obj.id != canonical_id
            and obj.canonical_id == canonical_id
        ):
            members.append(obj)
            seen.add(obj.id)
    return members


def reelect_canonical(session: Session, canonical_id: uuid.UUID) -> KnowledgeItem | None:
    """Re-elect the canonical of the cluster currently headed by ``canonical_id``.

    Safe to call after the canonical itself was deleted: the surviving variants are gathered
    by their ``canonical_id`` and one of them is promoted. Returns the new canonical.
    """
    session.flush()
    members = cluster_members(session, canonical_id)
    for member in members:
        # A deleted canonical leaves SET NULL pointers in the database; read them back.
        session.refresh(member, attribute_names=["canonical_id", "is_canonical"])
    return _elect(session, members)


def reelect_members(session: Session, members: Iterable[KnowledgeItem]) -> KnowledgeItem | None:
    """Re-elect over an explicit member list (used after deletions by step 9)."""
    return _elect(session, list(members))


def _nearest_canonical(
    session: Session, org_id: uuid.UUID, item: KnowledgeItem
) -> tuple[KnowledgeItem | None, float]:
    """The closest existing canonical item of the organisation and its cosine similarity."""
    distance = KnowledgeItem.answer_embedding.cosine_distance(item.answer_embedding)
    stmt = (
        select(KnowledgeItem, distance.label("distance"))
        .where(
            KnowledgeItem.org_id == org_id,
            KnowledgeItem.is_canonical.is_(True),
            KnowledgeItem.id != item.id,
            KnowledgeItem.item_type.in_(CLUSTERED_ITEM_TYPES),
            KnowledgeItem.answer_embedding.is_not(None),
        )
        .order_by(distance)
        .limit(1)
    )
    row = session.execute(stmt).first()
    if row is None:
        return None, 0.0
    candidate, dist = row
    return candidate, 1.0 - float(dist)


def _detach(session: Session, item: KnowledgeItem) -> uuid.UUID | None:
    """Take ``item`` out of its current cluster; returns the cluster to re-elect afterwards."""
    former = item.canonical_id
    if former is not None:
        item.canonical_id = None
        item.is_canonical = False
        session.flush()
        return former
    if item.is_canonical:
        variants = session.scalars(
            select(KnowledgeItem).where(KnowledgeItem.canonical_id == item.id)
        ).all()
        if variants:
            item.is_canonical = False
            for variant in variants:
                variant.canonical_id = None
            session.flush()
            replacement = _elect(session, variants)
            return replacement.id if replacement is not None else None
    return None


def deduplicate(session: Session, org_id: uuid.UUID, items: Sequence[KnowledgeItem]) -> None:
    """Place every item in ``items`` into a cluster (step 6). Flushes but does not commit.

    Items are processed in order and flushed one by one, so two near-identical items in the
    same batch cluster together. An item already in a cluster is detached first, so a fixed
    pair (``POST /documents/{id}/pairs``) is re-placed on its new embedding.
    """
    threshold = get_settings().dedup_threshold
    for item in items:
        session.flush()
        former_cluster = _detach(session, item)
        if item.item_type not in CLUSTERED_ITEM_TYPES or item.answer_embedding is None:
            item.canonical_id = None
            item.is_canonical = True
            session.flush()
        else:
            candidate, similarity = _nearest_canonical(session, org_id, item)
            if candidate is not None and similarity >= threshold:
                item.canonical_id = candidate.id
                item.is_canonical = False
                session.flush()
                reelect_canonical(session, candidate.id)
                logger.debug(
                    "item %s joined cluster %s (cosine %.3f)", item.id, candidate.id, similarity
                )
            else:
                item.canonical_id = None
                item.is_canonical = True
                session.flush()
        if former_cluster is not None and former_cluster != item.canonical_id:
            reelect_canonical(session, former_cluster)
