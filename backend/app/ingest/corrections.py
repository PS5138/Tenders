"""Ingestion step 9: corrections.

- ``apply_patch`` applies a ``PATCH /documents/{id}`` override: sets ``classification_confirmed``,
  records ``effective_date_source = user`` when the date changed, and then either enqueues a
  re-ingest from the ``extracting`` stage (a ``doc_type`` change; the worker's resume rule
  runs the delete-then-re-run, and the decision rows of the former identity are removed here
  with their partners re-evaluated) or re-evaluates the rules of steps 7 and 8 synchronously
  (any other change), including the former kind and the reversal of an earlier exclusion. A
  date change first moves the date of every fact that inherited the document's former date,
  so the fact gate and the currency ranking see the corrected date.
- ``confirm_document`` sets ``classification_confirmed`` and re-evaluates steps 7 and 8.
- ``rerun_from_stage`` deletes this document's outputs of a stage and the later ones so a
  stage re-run is idempotent: knowledge items and their facts, unpaired fragments and the
  fact supersessions those facts set. Clusters that lost a member re-elect their canonical,
  deleted items and facts go through fact invalidation with reason ``removed``, and sections
  are never deleted. ``supersession_decisions`` rows are kept and reconciled in place by the
  linking stage, so a retry never discards a human decision.

Nothing here commits; the endpoint or the job handler does.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.enums import DocType, EffectiveDateSource, IngestStatus, InvalidationReason, JobKind
from app.db.models import Document, Fact, Job, KnowledgeItem, UnpairedFragment
from app.ingest.dedup import reelect_members
from app.ingest.facts import call_invalidate, evaluate_fact_supersession
from app.ingest.supersession import evaluate_document_supersession, is_supersedable
from app.jobs import enqueue

logger = logging.getLogger(__name__)

# Ingest stages in order; ``ready`` and ``failed`` are not stages.
STAGES: tuple[str, ...] = (
    IngestStatus.PARSING.value,
    IngestStatus.CLASSIFYING.value,
    IngestStatus.EXTRACTING.value,
    IngestStatus.EMBEDDING.value,
    IngestStatus.LINKING.value,
)
# jobs.total for a library document: the number of stages it runs.
LIBRARY_STAGE_COUNT = len(STAGES)

PATCHABLE_FIELDS: frozenset[str] = frozenset(
    {"doc_type", "doc_kind", "effective_date", "buyer", "submission_date"}
)


class PatchError(ValueError):
    """An invalid PATCH body (unknown field or a value outside the configured lists)."""


def _coerce_date(value: Any, field: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise PatchError(f"{field} must be an ISO date.") from exc


def _validated_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    unknown = sorted(set(changes) - PATCHABLE_FIELDS)
    if unknown:
        raise PatchError(f"Unknown field(s): {', '.join(unknown)}.")
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "doc_type":
            if value not in settings.doc_types or value == DocType.TENDER_DOCUMENT.value:
                raise PatchError("doc_type must be 'past_submission' or 'reference'.")
            clean[key] = value
        elif key == "doc_kind":
            if value is not None and value not in settings.doc_kinds:
                raise PatchError(f"doc_kind must be one of: {', '.join(settings.doc_kinds)}.")
            clean[key] = value
        elif key in ("effective_date", "submission_date"):
            clean[key] = _coerce_date(value, key)
        else:
            clean[key] = None if value is None else str(value).strip() or None
    if "effective_date" in clean and clean["effective_date"] is None:
        raise PatchError("effective_date cannot be cleared.")
    return clean


def apply_patch(
    session: Session, document: Document, changes: Mapping[str, Any], actor: str
) -> Job | None:
    """Apply an override and run the step 9 corrections. Returns the re-ingest job, if any."""
    clean = _validated_changes(changes)
    old_doc_type = document.doc_type
    old_date = document.effective_date
    _require_kind_for_reference(document, clean)

    for key, value in clean.items():
        setattr(document, key, value)
    if document.doc_type != DocType.REFERENCE.value and "doc_kind" not in clean:
        document.doc_kind = None
    document.classification_confirmed = True
    date_changed = "effective_date" in clean and clean["effective_date"] != old_date
    if date_changed:
        document.effective_date_source = EffectiveDateSource.USER.value
    session.flush()

    if document.doc_type != old_doc_type:
        # The document is no longer supersedable under its former identity, so every decision
        # row it is party to is removed (reversing the exclusion it caused) and each former
        # partner is re-evaluated for its kind, as the doc_kind path does through the cascade
        # in evaluate_document_supersession. Rows for a new supersedable identity are written
        # by the worker's linking stage, never here, so nothing is applied and then undone.
        # The delete-then-re-run of the extracting stage is the worker's alone: it resumes at
        # the stage recorded in ingest_status and calls rerun_from_stage first.
        if not is_supersedable(document):
            evaluate_document_supersession(session, document)
        document.ingest_status = IngestStatus.EXTRACTING.value
        document.ingest_error = None
        session.flush()
        return enqueue(
            session,
            JobKind.INGEST_DOCUMENT,
            {"document_id": str(document.id), "actor": actor},
            total=LIBRARY_STAGE_COUNT,
            org_id=document.org_id,
        )

    if date_changed:
        _move_inherited_fact_dates(session, document, old_date, clean["effective_date"])
    evaluate_fact_supersession(session, document)
    evaluate_document_supersession(session, document, effective_date_changed=date_changed)
    return None


def _require_kind_for_reference(document: Document, clean: Mapping[str, Any]) -> None:
    """A confirmed reference document without a ``doc_kind`` could never enter step 8, and
    classification never runs again once confirmed, so the PATCH must supply one."""
    doc_type = clean.get("doc_type", document.doc_type)
    if doc_type != DocType.REFERENCE.value:
        return
    if "doc_kind" in clean:
        doc_kind = clean["doc_kind"]
    elif document.doc_type == DocType.REFERENCE.value:
        doc_kind = document.doc_kind
    else:
        doc_kind = None
    if doc_kind is None:
        raise PatchError(
            "A reference document needs a doc_kind; one of: "
            + ", ".join(get_settings().doc_kinds)
            + "."
        )


def _move_inherited_fact_dates(
    session: Session, document: Document, old_date: date | None, new_date: date
) -> None:
    """Carry a date override onto the facts that inherited the document's former date.

    ``persist_facts`` fills a fact's null ``effective_date`` from the document, so a fact
    dated exactly the document's former date is one whose text stated none (the same
    equality the read side uses to rank inherited dates). Without this the gate and the
    currency ranking would keep comparing the stale date after the override.
    """
    if old_date is None or old_date == new_date:
        return
    result = session.execute(
        update(Fact)
        .where(Fact.document_id == document.id, Fact.effective_date == old_date)
        .values(effective_date=new_date)
        .execution_options(synchronize_session="fetch")
    )
    session.flush()
    if result.rowcount:
        logger.info(
            "document %s: moved %d inherited fact date(s) from %s to %s",
            document.id,
            result.rowcount,
            old_date,
            new_date,
        )


def confirm_document(session: Session, document: Document, actor: str) -> None:
    """Confirm the proposed classification and evaluate steps 7 and 8. Flushes only."""
    document.classification_confirmed = True
    session.flush()
    evaluate_fact_supersession(session, document)
    evaluate_document_supersession(session, document)


def _affected_clusters(
    session: Session, items: list[KnowledgeItem]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Cluster key -> ids of members that survive the deletion of ``items``."""
    deleted = {item.id for item in items}
    clusters: dict[uuid.UUID, set[uuid.UUID]] = {}
    for item in items:
        key = item.canonical_id if item.canonical_id is not None else item.id
        clusters.setdefault(key, set())
    for key in list(clusters):
        members = session.scalars(
            select(KnowledgeItem.id).where(
                (KnowledgeItem.id == key) | (KnowledgeItem.canonical_id == key)
            )
        ).all()
        clusters[key] = {member for member in members if member not in deleted}
    return clusters


def rerun_from_stage(session: Session, document: Document, stage: str) -> None:
    """Delete this document's outputs of ``stage`` and every later stage. Flushes only."""
    if stage not in STAGES:
        raise ValueError(f"unknown ingest stage {stage!r}; expected one of {STAGES}")
    session.flush()
    stage_index = STAGES.index(stage)

    # Decision rows are never deleted here. The linking stage reconciles them in place
    # (``evaluate_document_supersession``): a row whose pair is no longer valid is removed with
    # its exclusion reversed, an automatic or pending row is re-evaluated, and a human
    # ``keep_both`` or ``superseded`` stands, as step 8 requires. Deleting them would let a
    # worker retry silently discard a person's decision and rewrite the events it caused.

    if stage_index > STAGES.index(IngestStatus.EXTRACTING.value):
        # Embedding or linking re-run: items and facts stay; step 5 overwrites embeddings, step
        # 6 re-places every item, and steps 7 and 8 recompute fact pointers and decision rows
        # in place, so there is nothing to delete.
        return

    facts = session.scalars(select(Fact).where(Fact.document_id == document.id)).all()
    fact_ids = [fact.id for fact in facts]
    if fact_ids:
        # Fact supersessions these facts set: pointers on other facts that name them.
        session.execute(
            update(Fact)
            .where(Fact.superseded_by.in_(fact_ids))
            .values(superseded_by=None)
            .execution_options(synchronize_session="fetch")
        )

    items = session.scalars(
        select(KnowledgeItem).where(KnowledgeItem.document_id == document.id)
    ).all()
    clusters = _affected_clusters(session, items)

    for fact in facts:
        call_invalidate(session, "fact", fact.id, InvalidationReason.REMOVED)
    for item in items:
        call_invalidate(session, "knowledge_item", item.id, InvalidationReason.REMOVED)

    fragments = session.scalars(
        select(UnpairedFragment).where(UnpairedFragment.document_id == document.id)
    ).all()
    for fact in facts:
        session.delete(fact)
    for fragment in fragments:
        session.delete(fragment)
    session.flush()
    for item in items:
        session.delete(item)
    session.flush()

    for survivors in clusters.values():
        if not survivors:
            continue
        members = session.scalars(
            select(KnowledgeItem).where(KnowledgeItem.id.in_(survivors))
        ).all()
        for member in members:
            session.refresh(member, attribute_names=["canonical_id", "is_canonical"])
        reelect_members(session, members)
    session.flush()
    logger.info(
        "document %s: removed %d item(s), %d fact(s), %d fragment(s) for a re-run from %s",
        document.id,
        len(items),
        len(facts),
        len(fragments),
        stage,
    )
