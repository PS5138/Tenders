"""Hybrid retrieval: the lexical, vector and topic lists, fused and collapsed (draft steps 2-4).

Eligible items are ``excluded_from_retrieval = false and text_verified = true`` in the
organisation. Each list takes ``candidates_per_list`` items; the lists present are fused with
reciprocal rank fusion (``rrf_k``), collapsed to one item per cluster and cut to
``top_k_synthesis``. There is no reranker.

- Lexical: the query is built in Python (lowercase, split on non-alphanumerics, drop
  ``LEXICAL_STOPWORDS``, join with `` or ``) and passed to ``websearch_to_tsquery('english')``;
  ranking is ``ts_rank_cd(search_tsv, query, 1)``. An empty tsquery yields an empty list.
- Vector: one list scoring every eligible item as the greater of its question-embedding and
  answer-embedding cosine similarity to the query vector, in SQL as
  ``1 - least(coalesce(q <=> question_embedding, 2), coalesce(q <=> answer_embedding, 2))``.
- Topic: eligible items whose ``topics`` intersect the question's topics, ranked by the vector
  score. Skipped when there are no topics (free text).

The vector score is computed for every fused candidate, not only the vector list's members,
so ``best_vec`` (the highest vector score among the kept candidates) is well defined.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import KnowledgeItem
from app.retrieve.fusion import Candidate, best_vector_score, fuse_and_collapse

_TOKEN_RE = re.compile(r"[^0-9a-z]+")
_MAX_COSINE_DISTANCE = 2.0
_ENGLISH = literal_column("'english'::regconfig")


@dataclass
class RetrievalResult:
    """What synthesis and triage receive: the fused, collapsed top candidates."""

    candidates: list[Candidate]
    best_vec: float
    lexical_query: str
    lists: dict[str, list[uuid.UUID]] = field(default_factory=dict)

    @property
    def items(self) -> list[KnowledgeItem]:
        return [candidate.item for candidate in self.candidates]

    @property
    def item_ids(self) -> list[uuid.UUID]:
        return [candidate.item.id for candidate in self.candidates]

    def to_detail(self) -> list[dict[str, Any]]:
        return [candidate.to_detail() for candidate in self.candidates]


def build_lexical_query(query_text: str) -> str:
    """Lowercase, split on non-alphanumerics, drop the configured stopwords, OR-join.

    Duplicates are removed in first-seen order so an identifier repeated in a long question
    is not counted twice. The result is the string handed to ``websearch_to_tsquery``; empty
    when nothing survives.
    """
    stopwords = get_settings().lexical_stopwords
    seen: dict[str, None] = {}
    for token in _TOKEN_RE.split(query_text.lower()):
        if token and token not in stopwords:
            seen.setdefault(token, None)
    return " or ".join(seen)


def eligible_items_clause(org_id: uuid.UUID) -> ColumnElement[bool]:
    """The plan's retrieval eligibility for one organisation."""
    return and_(
        KnowledgeItem.org_id == org_id,
        KnowledgeItem.excluded_from_retrieval.is_(False),
        KnowledgeItem.text_verified.is_(True),
    )


def vector_score_expr(query_vector: Sequence[float]) -> ColumnElement[float]:
    """Step 3 score in SQL: the greater cosine similarity of the two embeddings, treating a
    missing embedding as maximally distant."""
    vector = list(query_vector)
    answer_distance = func.coalesce(
        KnowledgeItem.answer_embedding.cosine_distance(vector), _MAX_COSINE_DISTANCE
    )
    question_distance = func.coalesce(
        KnowledgeItem.question_embedding.cosine_distance(vector), _MAX_COSINE_DISTANCE
    )
    return 1.0 - func.least(answer_distance, question_distance)


def _has_an_embedding() -> ColumnElement[bool]:
    return or_(
        KnowledgeItem.answer_embedding.isnot(None), KnowledgeItem.question_embedding.isnot(None)
    )


def lexical_list(
    session: Session, org_id: uuid.UUID, lexical_query: str, limit: int
) -> list[uuid.UUID]:
    """Top ``limit`` eligible items by ``ts_rank_cd`` for the OR-joined query."""
    if not lexical_query.strip():
        return []
    tsquery = func.websearch_to_tsquery(_ENGLISH, lexical_query)
    rank = func.ts_rank_cd(KnowledgeItem.search_tsv, tsquery, 1).label("lexical_rank")
    stmt: Select[Any] = (
        select(KnowledgeItem.id)
        .where(
            eligible_items_clause(org_id),
            func.numnode(tsquery) > 0,
            KnowledgeItem.search_tsv.op("@@")(tsquery),
        )
        .order_by(rank.desc(), KnowledgeItem.id)
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def vector_list(
    session: Session, org_id: uuid.UUID, query_vector: Sequence[float], limit: int
) -> list[tuple[uuid.UUID, float]]:
    """Top ``limit`` eligible items by the step 3 vector score, with their scores."""
    score = vector_score_expr(query_vector).label("vector_score")
    stmt = (
        select(KnowledgeItem.id, score)
        .where(eligible_items_clause(org_id), _has_an_embedding())
        .order_by(score.desc(), KnowledgeItem.id)
        .limit(limit)
    )
    return [(row[0], float(row[1])) for row in session.execute(stmt).all()]


def topic_list(
    session: Session,
    org_id: uuid.UUID,
    query_vector: Sequence[float],
    topics: Sequence[str],
    limit: int,
) -> list[tuple[uuid.UUID, float]]:
    """Eligible items whose topics intersect ``topics``, ranked by the vector score."""
    if not topics:
        return []
    score = vector_score_expr(query_vector).label("vector_score")
    stmt = (
        select(KnowledgeItem.id, score)
        .where(
            eligible_items_clause(org_id),
            _has_an_embedding(),
            KnowledgeItem.topics.overlap(list(topics)),
        )
        .order_by(score.desc(), KnowledgeItem.id)
        .limit(limit)
    )
    return [(row[0], float(row[1])) for row in session.execute(stmt).all()]


def _load_items_with_scores(
    session: Session, ids: Sequence[uuid.UUID], query_vector: Sequence[float]
) -> tuple[dict[uuid.UUID, KnowledgeItem], dict[uuid.UUID, float]]:
    if not ids:
        return {}, {}
    score = vector_score_expr(query_vector).label("vector_score")
    stmt = select(KnowledgeItem, score).where(KnowledgeItem.id.in_(list(ids)))
    items: dict[uuid.UUID, KnowledgeItem] = {}
    scores: dict[uuid.UUID, float] = {}
    for item, value in session.execute(stmt).all():
        items[item.id] = item
        scores[item.id] = float(value) if value is not None else 0.0
    return items, scores


def retrieve(
    session: Session,
    org_id: uuid.UUID,
    *,
    query_text: str,
    query_vector: Sequence[float],
    topics: Sequence[str] | None,
) -> RetrievalResult:
    """Draft pipeline steps 2 to 4 for one query. Reads only; never commits."""
    settings = get_settings()
    per_list = settings.candidates_per_list
    topics = [topic for topic in (topics or []) if topic]

    lexical_query = build_lexical_query(query_text)
    lists: dict[str, list[uuid.UUID]] = {
        "lexical": lexical_list(session, org_id, lexical_query, per_list),
        "vector": [item_id for item_id, _ in vector_list(session, org_id, query_vector, per_list)],
    }
    if topics:
        lists["topic"] = [
            item_id for item_id, _ in topic_list(session, org_id, query_vector, topics, per_list)
        ]

    union: dict[uuid.UUID, None] = {}
    for ranked in lists.values():
        for item_id in ranked:
            union.setdefault(item_id, None)
    items, scores = _load_items_with_scores(session, list(union), query_vector)

    candidates = fuse_and_collapse(
        lists, items, scores, k=settings.rrf_k, top_k=settings.top_k_synthesis
    )
    return RetrievalResult(
        candidates=candidates,
        best_vec=best_vector_score(candidates),
        lexical_query=lexical_query,
        lists=lists,
    )


__all__ = [
    "Candidate",
    "RetrievalResult",
    "build_lexical_query",
    "eligible_items_clause",
    "lexical_list",
    "retrieve",
    "topic_list",
    "vector_list",
    "vector_score_expr",
]
