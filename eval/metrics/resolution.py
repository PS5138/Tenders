"""Resolving a ground-truth counterpart to a stored knowledge item and its cluster.

``ground_truth.json`` names a counterpart as ``{submission_filename, question_text}``. The
resolver finds the ``knowledge_items`` row extracted from that document whose question is that
text, in this order:

1. equality of ``question_text`` after the shared normalisation;
2. equality after dropping a leading question number (the synthetic layouts write
   ``"1.1 Describe how..."`` into the document, so the stored question carries the number);
3. ``find_span(counterpart question, item.question_text)``: the counterpart text located
   inside the stored question, the ``verbatim`` tier before the best ``near`` match;
4. ``find_span`` over the document's stored sections, then the item whose question offsets
   cover the span or whose answer sits in that section (the adjacent-cells layout keeps
   question and answer in one row section).

The cluster of an item is its ``canonical_id`` (or itself) plus every item pointing at that
canonical, so recall counts the item, its canonical or any variant. The same resolver serves
the extraction metric (was each manifest pair extracted).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentSection, KnowledgeItem
from app.ingest.normalise import find_span, normalise

_LEADING_NUMBER = re.compile(r"^\s*(?:q(?:uestion)?\s*)?\d+(?:\.\d+)*[.)]?\s+", re.IGNORECASE)

METHOD_EQUALITY = "equality"
METHOD_EQUALITY_NO_NUMBER = "equality_without_number"
METHOD_SPAN_VERBATIM = "find_span:verbatim"
METHOD_SPAN_NEAR = "find_span:near"
METHOD_SECTION = "section_span"
METHODS = (
    METHOD_EQUALITY,
    METHOD_EQUALITY_NO_NUMBER,
    METHOD_SPAN_VERBATIM,
    METHOD_SPAN_NEAR,
    METHOD_SECTION,
)


@dataclass(frozen=True)
class Resolution:
    item_id: uuid.UUID
    cluster_id: uuid.UUID
    cluster_member_ids: frozenset[uuid.UUID]
    method: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": str(self.item_id),
            "cluster_id": str(self.cluster_id),
            "cluster_size": len(self.cluster_member_ids),
            "method": self.method,
        }


def strip_leading_number(text: str) -> str:
    return _LEADING_NUMBER.sub("", text, count=1)


class ItemResolver:
    """Loads the organisation's items once and answers counterpart lookups."""

    def __init__(self, session: Session, documents_by_filename: Mapping[str, Document]) -> None:
        self.session = session
        self.documents = dict(documents_by_filename)
        items = list(session.scalars(select(KnowledgeItem)).all())
        self._items_by_document: dict[uuid.UUID, list[KnowledgeItem]] = {}
        self._members_by_cluster: dict[uuid.UUID, set[uuid.UUID]] = {}
        for item in items:
            self._items_by_document.setdefault(item.document_id, []).append(item)
            cluster = item.canonical_id or item.id
            self._members_by_cluster.setdefault(cluster, set()).add(item.id)
            self._members_by_cluster[cluster].add(cluster)
        self._sections: dict[uuid.UUID, list[DocumentSection]] = {}

    # --- Clusters -------------------------------------------------------------------------------

    def cluster_of(self, item: KnowledgeItem) -> tuple[uuid.UUID, frozenset[uuid.UUID]]:
        cluster = item.canonical_id or item.id
        return cluster, frozenset(self._members_by_cluster.get(cluster, {item.id}))

    def pairs_of(self, filename: str) -> list[KnowledgeItem]:
        document = self.documents.get(filename)
        if document is None:
            return []
        return [
            item
            for item in self._items_by_document.get(document.id, [])
            if item.item_type == "qa_pair"
        ]

    def _sections_of(self, document: Document) -> list[DocumentSection]:
        if document.id not in self._sections:
            self._sections[document.id] = list(
                self.session.scalars(
                    select(DocumentSection)
                    .where(DocumentSection.document_id == document.id)
                    .order_by(DocumentSection.order_index)
                ).all()
            )
        return self._sections[document.id]

    # --- Resolution -----------------------------------------------------------------------------

    def resolve(self, filename: str, question_text: str) -> Resolution | None:
        document = self.documents.get(filename)
        if document is None:
            return None
        pairs = self.pairs_of(filename)
        wanted = normalise(question_text)[0]
        if not wanted:
            return None

        def resolution(item: KnowledgeItem, method: str) -> Resolution:
            cluster, members = self.cluster_of(item)
            return Resolution(item.id, cluster, members, method)

        for item in pairs:
            if normalise(item.question_text or "")[0] == wanted:
                return resolution(item, METHOD_EQUALITY)
        for item in pairs:
            if normalise(strip_leading_number(item.question_text or ""))[0] == wanted:
                return resolution(item, METHOD_EQUALITY_NO_NUMBER)

        best_near: tuple[float, KnowledgeItem] | None = None
        for item in pairs:
            stored = item.question_text or ""
            if not stored.strip():
                continue
            match = find_span(question_text, stored)
            if match is None:
                continue
            if match.tier == "verbatim":
                return resolution(item, METHOD_SPAN_VERBATIM)
            if best_near is None or match.ratio > best_near[0]:
                best_near = (match.ratio, item)
        if best_near is not None:
            return resolution(best_near[1], METHOD_SPAN_NEAR)

        for section in self._sections_of(document):
            match = find_span(question_text, section.text)
            if match is None:
                continue
            for item in pairs:
                question_section = item.question_section_id or item.section_id
                if (
                    question_section == section.id
                    and item.question_start is not None
                    and item.question_end is not None
                    and item.question_start <= match.start < item.question_end
                ):
                    return resolution(item, METHOD_SECTION)
            for item in pairs:
                if item.section_id == section.id:
                    return resolution(item, METHOD_SECTION)
        return None


__all__ = ["METHODS", "ItemResolver", "Resolution", "strip_leading_number"]
