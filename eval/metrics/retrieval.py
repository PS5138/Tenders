"""Retrieval recall at eight.

For each held-out question the ground truth labels ``counterpart``, resolve every counterpart
to a knowledge item and its cluster; the question is a hit when any cluster member (the item,
its canonical or a variant) is among the eight candidates synthesis received after fusion and
collapse (``retrieved_item_ids`` recorded on the draft's ``answer_created`` event). Questions
whose counterparts all fail to resolve are reported separately as unresolved and excluded from
the denominator, so a data problem never masquerades as a retrieval miss. The same check over
the triage candidates (``coverage_detail.candidates``) is reported beside it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from eval.metrics.records import QuestionKey, QuestionRun
from eval.metrics.resolution import ItemResolver


def _hit_rank(candidate_ids: Sequence[str], members: set[uuid.UUID]) -> int | None:
    wanted = {str(member) for member in members}
    for rank, candidate in enumerate(candidate_ids, start=1):
        if candidate in wanted:
            return rank
    return None


def recall_at_k(
    runs: Sequence[QuestionRun],
    ground_truth: Mapping[QuestionKey, Mapping[str, Any]],
    resolver: ItemResolver,
    *,
    k: int = 8,
) -> dict[str, Any]:
    runs_by_key = {run.key: run for run in runs}
    per_question: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    methods: dict[str, int] = {}
    hits = total = triage_hits = triage_total = 0
    for key, entry in ground_truth.items():
        if entry.get("label") != "counterpart":
            continue
        run = runs_by_key.get(key)
        members: set[uuid.UUID] = set()
        resolutions: list[dict[str, Any]] = []
        for counterpart in entry.get("counterparts") or []:
            resolution = resolver.resolve(
                str(counterpart.get("submission_filename") or ""),
                str(counterpart.get("question_text") or ""),
            )
            if resolution is None:
                unresolved.append(
                    {
                        "section": key[0],
                        "number": key[1],
                        "submission_filename": counterpart.get("submission_filename"),
                        "question_text": counterpart.get("question_text"),
                    }
                )
                continue
            members |= set(resolution.cluster_member_ids)
            methods[resolution.method] = methods.get(resolution.method, 0) + 1
            resolutions.append(resolution.as_dict())
        if not members:
            continue
        if run is None:
            per_question.append(
                {"section": key[0], "number": key[1], "hit": None, "detail": "not in the pack"}
            )
            continue
        row: dict[str, Any] = {
            "section": key[0],
            "number": key[1],
            "coverage": run.coverage,
            "counterparts_resolved": resolutions,
            "candidates_source": "draft" if run.drafted else "triage",
        }
        candidate_ids = (run.retrieved_item_ids if run.drafted else run.triage_candidate_ids)[:k]
        rank = _hit_rank(candidate_ids, members)
        row["hit"] = rank is not None
        row["hit_rank"] = rank
        row["candidates"] = len(candidate_ids)
        total += 1
        hits += int(rank is not None)
        triage_rank = _hit_rank(run.triage_candidate_ids[:k], members)
        row["triage_hit"] = triage_rank is not None
        triage_total += 1
        triage_hits += int(triage_rank is not None)
        per_question.append(row)

    return {
        "k": k,
        "hits": hits,
        "total": total,
        "value": round(hits / total, 4) if total else None,
        "triage_candidates": {
            "hits": triage_hits,
            "total": triage_total,
            "value": round(triage_hits / triage_total, 4) if triage_total else None,
        },
        "unresolved_counterparts": unresolved,
        "resolution_methods": methods,
        "per_question": per_question,
    }


__all__ = ["recall_at_k"]
