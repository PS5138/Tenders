"""Retrieval toggles for the evaluation harness (plan: Evaluation harness, "Toggles").

Nothing under ``backend/app`` changes for a toggle. ``apply_toggles`` replaces module
attributes of ``app.retrieve.search`` (and one name in ``app.retrieve.coverage``) for the
duration of a ``with`` block and restores every one of them afterwards, whatever happens
inside. Exactly what is patched, per toggle:

- ``topic_list=off``: ``app.retrieve.search.topic_list`` becomes a function that returns
  ``[]``. ``retrieve`` still records an empty ``topic`` entry in ``lists`` when the question
  has topics; reciprocal rank fusion ignores an empty list, so the lexical and vector lists
  alone are fused. Nothing else changes.
- ``vector_list=answer_only``: ``app.retrieve.search.vector_score_expr`` becomes the
  answer-embedding cosine alone, ``1 - coalesce(answer_embedding <=> q, 2)``, instead of the
  greater of the question- and answer-embedding cosines. ``vector_list``, ``topic_list`` and
  ``_load_items_with_scores`` all read that module attribute at call time, so the vector
  list's ranking, the topic list's ranking, every candidate's ``vector_score`` and therefore
  ``best_vec`` (and the coverage floor decision) follow the same definition. The verbatim
  offer in ``app.generate.pipeline`` compares question embeddings directly and is not
  affected.
- ``llm_rerank=on``: two attributes. ``app.retrieve.search.retrieve`` and
  ``app.retrieve.coverage.retrieve`` (coverage binds the name at import, so both must be
  swapped) become a wrapper that notes the query text in a thread-local and calls the
  original; ``app.retrieve.search.fuse_and_collapse`` becomes a wrapper that calls the
  original with ``top_k`` raised to ``rerank_pool`` (default 20), makes one schema-validated
  FAST-model call over that fused, collapsed pool (prompt ``eval/prompts/rerank.v1.md``,
  output ``{"ranking": [ids]}``) and returns the model's order cut to the original ``top_k``
  (eight). Ids the model leaves out follow the ones it returned in fused order; ids it
  invents are ignored; a failed or empty call falls back to the fused order. The thread-local
  makes the wrapper safe under the triage thread pool. ``app.generate.pipeline`` imports
  ``retrieve`` lazily inside ``retrieve_candidates`` and so picks the wrapper up too.

The product path is untouched: the plan keeps the rerank as a harness-only toggle, and the
toggle exists so the question can be answered with numbers.
"""

from __future__ import annotations

import functools
import itertools
import json
import logging
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from eval.prompts import RERANK_PROMPT, eval_prompt_version, load_eval_prompt

logger = logging.getLogger(__name__)

TOGGLE_VALUES: dict[str, tuple[str, ...]] = {
    "topic_list": ("on", "off"),
    "vector_list": ("max", "answer_only"),
    "llm_rerank": ("off", "on"),
}
DEFAULT_RERANK_POOL = 20
_RERANK_TEXT_LIMIT = 1200


class ToggleError(ValueError):
    pass


@dataclass(frozen=True)
class Toggles:
    """One retrieval configuration. Defaults are the product's own behaviour."""

    topic_list: str = "on"
    vector_list: str = "max"
    llm_rerank: str = "off"

    def __post_init__(self) -> None:
        for name, allowed in TOGGLE_VALUES.items():
            value = getattr(self, name)
            if value not in allowed:
                raise ToggleError(
                    f"toggle {name} must be one of {', '.join(allowed)}; got {value!r}"
                )

    @property
    def label(self) -> str:
        return ",".join(f"{name}={getattr(self, name)}" for name in TOGGLE_VALUES)

    @property
    def is_baseline(self) -> bool:
        return self == Toggles()

    def as_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in TOGGLE_VALUES}

    @classmethod
    def parse(cls, pairs: Iterable[str]) -> Toggles:
        """``["topic_list=off", "llm_rerank=on"]`` -> ``Toggles``; unspecified toggles keep
        their defaults."""
        values: dict[str, str] = {}
        for pair in pairs:
            name, separator, value = pair.partition("=")
            name, value = name.strip(), value.strip()
            if not separator or name not in TOGGLE_VALUES:
                raise ToggleError(
                    f"expected --toggle <name>=<value> with name one of "
                    f"{', '.join(TOGGLE_VALUES)}; got {pair!r}"
                )
            values[name] = value
        return cls(**values)

    @classmethod
    def matrix(cls) -> list[Toggles]:
        """Every combination of the three toggles, the baseline first."""
        names = list(TOGGLE_VALUES)
        combinations = [
            cls(**dict(zip(names, values, strict=True)))
            for values in itertools.product(*(TOGGLE_VALUES[name] for name in names))
        ]
        return sorted(combinations, key=lambda toggles: (not toggles.is_baseline, toggles.label))


# --- The rerank call ------------------------------------------------------------------------------


class RerankOutput(BaseModel):
    """The model's order, best first. An empty default keeps the fused order, which is also
    what the plain fake provider returns."""

    ranking: list[str] = Field(default_factory=list)


@dataclass
class RerankStats:
    """What the rerank toggle did during a run, for the report."""

    calls: int = 0
    failures: int = 0
    reordered: int = 0
    pool: int = DEFAULT_RERANK_POOL
    model: str | None = None
    prompt_version: str = field(default_factory=lambda: eval_prompt_version(RERANK_PROMPT))

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "reordered": self.reordered,
            "pool": self.pool,
            "model": self.model,
            "prompt_version": self.prompt_version,
        }


def _excerpt(text: str | None, limit: int = _RERANK_TEXT_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_rerank_input(query_text: str, candidates: Sequence[Any]) -> str:
    """The JSON user message: the question and the fused pool with identifiers."""
    payload = {
        "question": query_text,
        "candidates": [
            {
                "id": str(candidate.item.id),
                "type": candidate.item.item_type,
                "question": candidate.item.question_text or None,
                "text": _excerpt(candidate.item.answer_text),
            }
            for candidate in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def rerank_candidates(query_text: str, candidates: Sequence[Any]) -> list[str]:
    """One FAST-model call; returns the ids in the model's order (unvalidated)."""
    from app.config import get_settings
    from app.llm import get_llm

    settings = get_settings()
    result = get_llm().parse(
        RERANK_PROMPT,
        model=settings.model_fast,
        system=load_eval_prompt(RERANK_PROMPT),
        user=build_rerank_input(query_text, candidates),
        output_model=RerankOutput,
        max_tokens=2000,
    )
    return [str(item_id).strip() for item_id in result.ranking]


def apply_ranking(candidates: Sequence[Any], ranking: Sequence[str]) -> list[Any]:
    """Candidates in the model's order, unknown ids ignored, omitted ids appended in their
    fused order."""
    by_id = {str(candidate.item.id): candidate for candidate in candidates}
    ordered: list[Any] = []
    seen: set[str] = set()
    for item_id in ranking:
        candidate = by_id.get(item_id)
        if candidate is not None and item_id not in seen:
            ordered.append(candidate)
            seen.add(item_id)
    ordered.extend(candidate for candidate in candidates if str(candidate.item.id) not in seen)
    return ordered


# --- Patches --------------------------------------------------------------------------------------


def _topic_list_off(
    session: Any, org_id: Any, query_vector: Sequence[float], topics: Sequence[str], limit: int
) -> list[tuple[Any, float]]:
    return []


def _answer_only_vector_score_expr(query_vector: Sequence[float]) -> Any:
    from sqlalchemy import func

    from app.db.models import KnowledgeItem

    distance = func.coalesce(
        KnowledgeItem.answer_embedding.cosine_distance(list(query_vector)), 2.0
    )
    return 1.0 - distance


def _make_retrieve_wrapper(original: Callable[..., Any], context: threading.local) -> Any:
    @functools.wraps(original)
    def retrieve(
        session: Any, org_id: Any, *, query_text: str, query_vector: Any, topics: Any
    ) -> Any:
        context.query_text = query_text
        try:
            return original(
                session, org_id, query_text=query_text, query_vector=query_vector, topics=topics
            )
        finally:
            context.query_text = None

    return retrieve


def _make_fuse_wrapper(
    original: Callable[..., list[Any]], context: threading.local, stats: RerankStats
) -> Any:
    @functools.wraps(original)
    def fuse_and_collapse(
        ranked_lists: Mapping[Any, Sequence[Any]],
        items: Mapping[Any, Any],
        vector_scores: Mapping[Any, float],
        *,
        k: int,
        top_k: int,
    ) -> list[Any]:
        pool = original(ranked_lists, items, vector_scores, k=k, top_k=max(top_k, stats.pool))
        query_text = getattr(context, "query_text", None)
        if not query_text or len(pool) <= 1:
            return pool[:top_k]
        stats.calls += 1
        try:
            ranking = rerank_candidates(query_text, pool)
        except Exception as exc:  # noqa: BLE001 - a failed rerank must not fail the run
            stats.failures += 1
            logger.warning("rerank call failed (%s: %s); keeping the fused order", type(exc), exc)
            return pool[:top_k]
        reordered = apply_ranking(pool, ranking)
        before = [candidate.item.id for candidate in pool[:top_k]]
        after = [candidate.item.id for candidate in reordered[:top_k]]
        if before != after:
            stats.reordered += 1
        return reordered[:top_k]

    return fuse_and_collapse


@dataclass
class AppliedToggles:
    toggles: Toggles
    rerank: RerankStats | None
    patched: list[str]


@contextmanager
def apply_toggles(
    toggles: Toggles, *, rerank_pool: int = DEFAULT_RERANK_POOL
) -> Iterator[AppliedToggles]:
    """Patch ``app.retrieve`` for ``toggles`` and restore it on exit."""
    from app.retrieve import coverage, search

    originals: list[tuple[Any, str, Any]] = []

    def patch(module: Any, name: str, value: Any) -> None:
        originals.append((module, name, getattr(module, name)))
        setattr(module, name, value)

    applied = AppliedToggles(toggles=toggles, rerank=None, patched=[])
    try:
        if toggles.topic_list == "off":
            patch(search, "topic_list", _topic_list_off)
            applied.patched.append("app.retrieve.search.topic_list")
        if toggles.vector_list == "answer_only":
            patch(search, "vector_score_expr", _answer_only_vector_score_expr)
            applied.patched.append("app.retrieve.search.vector_score_expr")
        if toggles.llm_rerank == "on":
            from app.config import get_settings

            context = threading.local()
            stats = RerankStats(pool=rerank_pool, model=get_settings().model_fast)
            wrapper = _make_retrieve_wrapper(search.retrieve, context)
            patch(search, "retrieve", wrapper)
            patch(coverage, "retrieve", wrapper)
            fuse_wrapper = _make_fuse_wrapper(search.fuse_and_collapse, context, stats)
            patch(search, "fuse_and_collapse", fuse_wrapper)
            applied.rerank = stats
            applied.patched.extend(
                [
                    "app.retrieve.search.retrieve",
                    "app.retrieve.coverage.retrieve",
                    "app.retrieve.search.fuse_and_collapse",
                ]
            )
        yield applied
    finally:
        for module, name, value in reversed(originals):
            setattr(module, name, value)


__all__ = [
    "DEFAULT_RERANK_POOL",
    "TOGGLE_VALUES",
    "AppliedToggles",
    "RerankOutput",
    "RerankStats",
    "ToggleError",
    "Toggles",
    "apply_ranking",
    "apply_toggles",
    "build_rerank_input",
    "rerank_candidates",
]
