"""Coverage metrics: the triage distribution, where ``new`` came from, agreement with the
human labels in ``eval/labels/coverage_labels.json`` (``not labelled`` when absent) and, as a
proxy that needs no person, agreement with the ground-truth label (``counterpart`` should be
``covered`` or ``partial``; ``new`` should be ``new``). ``best_vec`` is summarised by
ground-truth label so the coverage floor can be re-based on cosine against the harness.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any

from eval.metrics.labels import NOT_LABELLED, Labels
from eval.metrics.records import QuestionKey, QuestionRun

COVERAGE_ORDER = ("covered", "partial", "new", "unknown")


def _summary(values: Sequence[float]) -> dict[str, Any] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": round(ordered[0], 4),
        "median": round(median(ordered), 4),
        "max": round(ordered[-1], 4),
    }


def coverage_metrics(
    runs: Sequence[QuestionRun],
    ground_truth: Mapping[QuestionKey, Mapping[str, Any]],
    labels: Labels,
    *,
    coverage_floor: float,
) -> dict[str, Any]:
    distribution = {label: 0 for label in COVERAGE_ORDER}
    label_sources = {"floor": 0, "llm": 0, "none": 0}
    new_sources = {"floor": 0, "llm": 0}
    best_vec_by_truth: dict[str, list[float]] = {"counterpart": [], "new": []}
    best_vec_by_coverage: dict[str, list[float]] = {}
    floors_seen: set[float] = set()

    proxy = {
        "counterpart_total": 0,
        "counterpart_covered_or_partial": 0,
        "new_total": 0,
        "new_judged_new": 0,
        "confusion": {},
    }
    human = {"labelled": 0, "agree": 0, "confusion": {}, "disagreements": []}

    for run in runs:
        distribution[run.coverage] = distribution.get(run.coverage, 0) + 1
        source = run.label_source or "none"
        label_sources[source] = label_sources.get(source, 0) + 1
        if run.coverage == "new" and source in new_sources:
            new_sources[source] += 1
        detail_floor = run.coverage_detail.get("coverage_floor")
        if detail_floor is not None:
            floors_seen.add(float(detail_floor))
        if run.best_vec is not None:
            best_vec_by_coverage.setdefault(run.coverage, []).append(run.best_vec)

        truth = ground_truth.get(run.key)
        if truth is not None:
            truth_label = str(truth.get("label"))
            if run.best_vec is not None and truth_label in best_vec_by_truth:
                best_vec_by_truth[truth_label].append(run.best_vec)
            cell = f"{truth_label}->{run.coverage}"
            proxy["confusion"][cell] = proxy["confusion"].get(cell, 0) + 1
            if truth_label == "counterpart":
                proxy["counterpart_total"] += 1
                proxy["counterpart_covered_or_partial"] += int(
                    run.coverage in ("covered", "partial")
                )
            elif truth_label == "new":
                proxy["new_total"] += 1
                proxy["new_judged_new"] += int(run.coverage == "new")

        label = labels.coverage.get(run.key)
        if label is not None:
            human["labelled"] += 1
            expected = str(label.get("coverage"))
            cell = f"{expected}->{run.coverage}"
            human["confusion"][cell] = human["confusion"].get(cell, 0) + 1
            if expected == run.coverage:
                human["agree"] += 1
            else:
                human["disagreements"].append(
                    {
                        "section": run.section,
                        "number": run.number,
                        "human": expected,
                        "triage": run.coverage,
                        "label_source": run.label_source,
                    }
                )

    proxy_total = proxy["counterpart_total"] + proxy["new_total"]
    proxy_agree = proxy["counterpart_covered_or_partial"] + proxy["new_judged_new"]
    human_accuracy: Any
    if human["labelled"]:
        human_accuracy = {
            **human,
            "accuracy": round(human["agree"] / human["labelled"], 4),
        }
    else:
        human_accuracy = NOT_LABELLED

    return {
        "distribution": distribution,
        "unknown_remaining": distribution.get("unknown", 0),
        "label_sources": label_sources,
        "new_by_label_source": new_sources,
        "coverage_floor": coverage_floor,
        "coverage_floor_in_details": sorted(floors_seen),
        "best_vec_by_ground_truth": {
            label: _summary(values) for label, values in best_vec_by_truth.items()
        },
        "best_vec_by_coverage": {
            label: _summary(values) for label, values in best_vec_by_coverage.items()
        },
        "human_accuracy": human_accuracy,
        "ground_truth_proxy": {
            **proxy,
            "agreement": round(proxy_agree / proxy_total, 4) if proxy_total else None,
            "note": (
                "A proxy from the generator's labels, not a human label: counterpart should "
                "triage covered or partial, new should triage new."
            ),
        },
    }


__all__ = ["COVERAGE_ORDER", "coverage_metrics"]
