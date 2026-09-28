"""Fact selection for a draft (draft pipeline step 5).

The facts sent to synthesis are the facts attached to the retrieved items plus, for each
``fact_kind`` whose configured topic is among the question's topics, the single most current
fact per (``fact_kind``, ``fact_key``). A fact is current when it is not superseded, not
expired (``expires_on`` null or not before today, and not marked by the expiry sweep) and its
source document is not superseded. Among current facts sharing a kind and key, one from a
``reference`` document beats one from a ``past_submission``, then the newest
``effective_date`` wins, with every fact whose date was inherited from a document dated at
upload time ranked below the rest. Facts passed over are not returned. For free text (no
topics) only the attached facts are returned. Reads only; never commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import FACT_KINDS
from app.db.enums import DocType, EffectiveDateSource
from app.db.models import Document, Fact, KnowledgeItem


def _today() -> date:
    return datetime.now(UTC).date()


def fact_kinds_for_topics(topics: Iterable[str]) -> list[str]:
    """The configured fact kinds mapped to any of ``topics``, in configuration order."""
    wanted = set(topics)
    return [name for name, kind in FACT_KINDS.items() if kind.topic in wanted]


def _item_ids(candidates: Iterable[object]) -> list[uuid.UUID]:
    ids: dict[uuid.UUID, None] = {}
    for candidate in candidates:
        item = getattr(candidate, "item", candidate)
        item_id = item.id if isinstance(item, KnowledgeItem) else item
        if isinstance(item_id, uuid.UUID):
            ids.setdefault(item_id, None)
    return list(ids)


def attached_facts(session: Session, item_ids: Sequence[uuid.UUID]) -> list[Fact]:
    """Every fact whose ``knowledge_item_id`` is one of ``item_ids``, in item order."""
    if not item_ids:
        return []
    order = {item_id: position for position, item_id in enumerate(item_ids)}
    rows = session.scalars(select(Fact).where(Fact.knowledge_item_id.in_(list(item_ids)))).all()
    return sorted(
        rows,
        key=lambda fact: (
            order.get(fact.knowledge_item_id, len(order)),
            -fact.effective_date.toordinal(),
            str(fact.id),
        ),
    )


def inherited_from_upload_time(fact: Fact, document: Document) -> bool:
    """True when the fact's date came from a document dated at upload time rather than from
    the fact's own text: the document's source is ``upload_time`` and the dates coincide."""
    return (
        document.effective_date_source == EffectiveDateSource.UPLOAD_TIME.value
        and document.effective_date is not None
        and fact.effective_date == document.effective_date
    )


def currency_sort_key(fact: Fact, document: Document) -> tuple[int, int, int, str]:
    """Smaller sorts first: reference before past submission, dated facts before facts whose
    date was inherited from an upload-time document, then the newest effective date, then id."""
    from_reference = 0 if document.doc_type == DocType.REFERENCE.value else 1
    inherited = 1 if inherited_from_upload_time(fact, document) else 0
    return (from_reference, inherited, -fact.effective_date.toordinal(), str(fact.id))


def current_facts_by_kind(
    session: Session, org_id: uuid.UUID, kinds: Sequence[str], *, today: date | None = None
) -> list[tuple[Fact, Document]]:
    """All current facts of ``kinds`` in the organisation, with their source documents."""
    if not kinds:
        return []
    today = today or _today()
    stmt = (
        select(Fact, Document)
        .join(Document, Fact.document_id == Document.id)
        .where(
            Fact.org_id == org_id,
            Fact.fact_kind.in_(list(kinds)),
            Fact.superseded_by.is_(None),
            Fact.expired_at.is_(None),
            or_(Fact.expires_on.is_(None), Fact.expires_on >= today),
            Document.superseded_by.is_(None),
        )
    )
    return [(fact, document) for fact, document in session.execute(stmt).all()]


def most_current_per_key(rows: Iterable[tuple[Fact, Document]]) -> list[Fact]:
    """One fact per (``fact_kind``, ``fact_key``) by ``currency_sort_key``; kinds in
    configuration order, keys alphabetically with the null key first."""
    best: dict[tuple[str, str | None], tuple[tuple[int, int, int, str], Fact]] = {}
    for fact, document in rows:
        key = (fact.fact_kind, fact.fact_key)
        candidate = (currency_sort_key(fact, document), fact)
        current = best.get(key)
        if current is None or candidate[0] < current[0]:
            best[key] = candidate
    kind_order = {name: position for position, name in enumerate(FACT_KINDS)}
    ordered = sorted(
        best.items(),
        key=lambda entry: (kind_order.get(entry[0][0], len(kind_order)), entry[0][1] or ""),
    )
    return [fact for _, (_, fact) in ordered]


def select_facts(
    session: Session,
    org_id: uuid.UUID,
    candidates: Sequence[object],
    topics: Sequence[str] | None,
    *,
    today: date | None = None,
) -> list[Fact]:
    """Step 5: attached facts, then the most current fact per kind and key for the topics.

    ``candidates`` may be ``Candidate`` objects, ``KnowledgeItem`` rows or item ids. Duplicates
    (a topic fact that is also attached) appear once, in the attached position.
    """
    item_ids = _item_ids(candidates)
    selected: list[Fact] = attached_facts(session, item_ids)
    seen = {fact.id for fact in selected}

    kinds = fact_kinds_for_topics(topics or [])
    if kinds:
        rows = current_facts_by_kind(session, org_id, kinds, today=today)
        for fact in most_current_per_key(rows):
            if fact.id not in seen:
                selected.append(fact)
                seen.add(fact.id)
    return selected


__all__ = [
    "attached_facts",
    "currency_sort_key",
    "current_facts_by_kind",
    "fact_kinds_for_topics",
    "inherited_from_upload_time",
    "most_current_per_key",
    "select_facts",
]
