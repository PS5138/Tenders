"""Reciprocal rank fusion arithmetic and cluster collapse, without a database."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.retrieve.fusion import (
    Candidate,
    best_vector_score,
    collapse_clusters,
    fuse_and_collapse,
    rrf_scores,
)

A, B, C, D = (uuid.uuid4() for _ in range(4))


def _item(item_id: uuid.UUID, canonical_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=item_id, canonical_id=canonical_id, item_type="qa_pair")


def test_rrf_hand_computed_example() -> None:
    """Lexical [A, B, C], vector [B, A, D], topic [C, B] with k = 60."""
    fused = rrf_scores({"lexical": [A, B, C], "vector": [B, A, D], "topic": [C, B]}, k=60)

    assert fused[A] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[B] == pytest.approx(1 / 62 + 1 / 61 + 1 / 62)
    assert fused[C] == pytest.approx(1 / 63 + 1 / 61)
    assert fused[D] == pytest.approx(1 / 63)
    assert sorted(fused, key=fused.get, reverse=True) == [B, A, C, D]


def test_rrf_over_two_lists_when_topic_list_is_absent() -> None:
    fused = rrf_scores({"lexical": [A], "vector": [A, B]}, k=60)
    assert fused[A] == pytest.approx(2 / 61)
    assert fused[B] == pytest.approx(1 / 62)
    assert rrf_scores({}, k=60) == {}


def test_collapse_keeps_highest_fused_member_per_cluster() -> None:
    canonical = _item(A)
    variant = _item(B, canonical_id=A)
    other = _item(C)
    kept = collapse_clusters(
        [
            Candidate(item=canonical, fused_score=0.010, vector_score=0.9),
            Candidate(item=variant, fused_score=0.020, vector_score=0.5),
            Candidate(item=other, fused_score=0.015, vector_score=0.1),
        ]
    )
    assert [candidate.item.id for candidate in kept] == [B, C]
    assert kept[0].cluster_id == A, "a variant collapses into its canonical's cluster"
    assert kept[1].cluster_id == C, "an item with no canonical is its own cluster"


def test_collapse_tie_breaks_on_vector_score_then_id() -> None:
    low, high = sorted([A, B], key=str)
    tied_by_vector = collapse_clusters(
        [
            Candidate(item=_item(A), fused_score=0.02, vector_score=0.3),
            Candidate(item=_item(B, canonical_id=A), fused_score=0.02, vector_score=0.7),
        ]
    )
    assert tied_by_vector[0].item.id == B
    tied_by_id = collapse_clusters(
        [
            Candidate(item=_item(high, canonical_id=low), fused_score=0.02, vector_score=0.5),
            Candidate(item=_item(low), fused_score=0.02, vector_score=0.5),
        ]
    )
    assert tied_by_id[0].item.id == low


def test_fuse_and_collapse_assigns_ranks_scores_and_top_k() -> None:
    items = {A: _item(A), B: _item(B, canonical_id=A), C: _item(C), D: _item(D)}
    scores = {A: 0.9, B: 0.8, C: 0.4, D: 0.95}
    candidates = fuse_and_collapse(
        {"lexical": [A, B, C], "vector": [B, A, D], "topic": [C, B]},
        items,
        scores,
        k=60,
        top_k=2,
    )
    # B (cluster A) beats A (cluster A); C is next; D is cut by top_k.
    assert [candidate.item.id for candidate in candidates] == [B, C]
    b, c = candidates
    assert b.lexical_rank == 2 and b.vector_rank == 1 and b.topic_rank == 2
    assert b.vector_score == pytest.approx(0.8)
    assert b.fused_score == pytest.approx(1 / 62 + 1 / 61 + 1 / 62)
    assert c.lexical_rank == 3 and c.vector_rank is None and c.topic_rank == 1
    assert best_vector_score(candidates) == pytest.approx(0.8)


def test_fuse_and_collapse_skips_ids_without_a_loaded_item() -> None:
    candidates = fuse_and_collapse({"vector": [A, B]}, {A: _item(A)}, {A: 0.5}, k=60, top_k=8)
    assert [candidate.item.id for candidate in candidates] == [A]
    assert best_vector_score([]) == 0.0
