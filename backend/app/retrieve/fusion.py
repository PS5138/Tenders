"""Reciprocal rank fusion and cluster collapse (draft pipeline step 4).

Pure functions over identifiers and scores so the arithmetic can be unit-tested without a
database. ``search.retrieve`` builds the three ranked lists and calls these.

Fusion: ``fused(item) = sum over the lists that contain it of 1 / (k + rank)`` with ranks
1-based and ``k`` from settings (``rrf_k``, 60). Lists that are absent (the topic list for free
text) simply contribute nothing. Collapse: one item per cluster, where the cluster is the
item's ``canonical_id`` or, on a canonical or unclustered item, its own id; the member with the
highest fused score is kept, ties broken by the higher vector score and then by id so the
result is deterministic.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.db.models import KnowledgeItem

LIST_NAMES: tuple[str, ...] = ("lexical", "vector", "topic")


@dataclass
class Candidate:
    """One retrieved item after fusion and collapse.

    ``vector_score`` is the step 3 score (greater of question- and answer-embedding cosine
    similarity to the query vector), computed for every candidate whether or not the item made
    the vector list's top ``candidates_per_list``. Ranks are 1-based positions in each list and
    ``None`` where the item did not appear in that list.
    """

    item: KnowledgeItem
    fused_score: float
    vector_score: float
    lexical_rank: int | None = None
    vector_rank: int | None = None
    topic_rank: int | None = None
    # Filled from the item in __post_init__ when not given; never None afterwards.
    cluster_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if self.cluster_id is None:
            self.cluster_id = cluster_id_for(self.item)

    @property
    def item_id(self) -> uuid.UUID:
        return self.item.id

    def to_detail(self) -> dict[str, Any]:
        """JSON-able summary for ``coverage_detail`` and message ``retrieved_item_ids``."""
        return {
            "item_id": str(self.item.id),
            "cluster_id": str(self.cluster_id),
            "item_type": self.item.item_type,
            "fused_score": round(self.fused_score, 6),
            "vector_score": round(self.vector_score, 6),
            "lexical_rank": self.lexical_rank,
            "vector_rank": self.vector_rank,
            "topic_rank": self.topic_rank,
        }


def cluster_id_for(item: KnowledgeItem) -> uuid.UUID:
    """An item's cluster: its canonical, or itself when it is the canonical or unclustered."""
    return item.canonical_id or item.id


def rrf_scores(
    ranked_lists: Mapping[str, Sequence[uuid.UUID]], k: int
) -> dict[uuid.UUID, float]:
    """Reciprocal rank fusion over the lists present. Ranks are 1-based positions."""
    if k < 0:
        raise ValueError("rrf k must be non-negative")
    fused: dict[uuid.UUID, float] = {}
    for ranked in ranked_lists.values():
        for position, item_id in enumerate(ranked, start=1):
            fused[item_id] = fused.get(item_id, 0.0) + 1.0 / (k + position)
    return fused


def ranks_in(ranked: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Map each id in a ranked list to its 1-based rank."""
    return {item_id: position for position, item_id in enumerate(ranked, start=1)}


def _sort_key(candidate: Candidate) -> tuple[float, float, str]:
    return (-candidate.fused_score, -candidate.vector_score, str(candidate.item.id))


def collapse_clusters(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Keep one candidate per cluster: the highest fused score (then the higher vector score,
    then the smaller id). The result is sorted best first."""
    best: dict[uuid.UUID | None, Candidate] = {}
    for candidate in candidates:
        current = best.get(candidate.cluster_id)
        if current is None or _sort_key(candidate) < _sort_key(current):
            best[candidate.cluster_id] = candidate
    return sorted(best.values(), key=_sort_key)


def fuse_and_collapse(
    ranked_lists: Mapping[str, Sequence[uuid.UUID]],
    items: Mapping[uuid.UUID, KnowledgeItem],
    vector_scores: Mapping[uuid.UUID, float],
    *,
    k: int,
    top_k: int,
) -> list[Candidate]:
    """Fuse the lists present, collapse to one item per cluster and keep ``top_k``.

    ``items`` and ``vector_scores`` must cover every id in the lists; an id without an item row
    is skipped (it was deleted between the list query and the load).
    """
    fused = rrf_scores(ranked_lists, k)
    rank_maps = {name: ranks_in(ranked) for name, ranked in ranked_lists.items()}
    candidates: list[Candidate] = []
    for item_id, score in fused.items():
        item = items.get(item_id)
        if item is None:
            continue
        candidates.append(
            Candidate(
                item=item,
                fused_score=score,
                vector_score=float(vector_scores.get(item_id, 0.0)),
                lexical_rank=rank_maps.get("lexical", {}).get(item_id),
                vector_rank=rank_maps.get("vector", {}).get(item_id),
                topic_rank=rank_maps.get("topic", {}).get(item_id),
            )
        )
    return collapse_clusters(candidates)[:top_k]


def best_vector_score(candidates: Iterable[Candidate]) -> float:
    """``best_vec``: the highest step 3 vector score among the kept candidates, 0.0 if none."""
    return max((candidate.vector_score for candidate in candidates), default=0.0)


__all__ = [
    "LIST_NAMES",
    "Candidate",
    "best_vector_score",
    "cluster_id_for",
    "collapse_clusters",
    "fuse_and_collapse",
    "ranks_in",
    "rrf_scores",
]
