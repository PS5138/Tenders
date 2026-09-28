"""Latency: per-question draft time (p50, p95) and time to a triaged board for the pack."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from eval.metrics.records import QuestionRun


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (``q`` in 0..100) over ``values``."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return ordered[rank - 1]


def summarise_seconds(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "p50": None, "p95": None, "mean": None, "max": None, "total": 0.0}
    return {
        "n": len(values),
        "p50": round(percentile(values, 50) or 0.0, 3),
        "p95": round(percentile(values, 95) or 0.0, 3),
        "mean": round(sum(values) / len(values), 3),
        "max": round(max(values), 3),
        "total": round(sum(values), 3),
    }


def latency_metrics(
    runs: Sequence[QuestionRun],
    *,
    time_to_triaged_board_seconds: float,
    extract_seconds: float | None,
    triage_seconds: float | None,
    question_count: int,
) -> dict[str, Any]:
    draft_times = [run.draft_seconds for run in runs if run.draft_seconds is not None]
    return {
        "draft_seconds": summarise_seconds(draft_times),
        "time_to_triaged_board_seconds": round(time_to_triaged_board_seconds, 3),
        "extract_questions_seconds": round(extract_seconds, 3) if extract_seconds else None,
        "triage_tender_seconds": round(triage_seconds, 3) if triage_seconds else None,
        "questions": question_count,
        "note": (
            "Wall-clock seconds in the harness process: the jobs run in-process through the "
            "worker's run_once, drafts run synchronously one at a time."
        ),
    }


__all__ = ["latency_metrics", "percentile", "summarise_seconds"]
