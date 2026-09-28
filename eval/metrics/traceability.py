"""Traceability metrics (plan: Traceability, "Evaluation").

Per answer and over the run: the share of substantive sentences ``supported``; the share of
cited document spans found at the ``verbatim`` tier and the share found only at the ``near``
tier (from the ``tier`` key support verification leaves on each located source); the counts of
every support status. Two figures need a person: whether the cited span actually supports the
sentence (the twenty-sentence spot check) and the share of ``supported`` sentences a human
disputed. The harness samples twenty (sentence, source) pairs deterministically into
``eval/reports/<run>.spotcheck.json``; ``eval/labels/spot_checks.json`` holds the judged rows,
matched to a run by section, number, sentence and quote. With no matching label both report
``not labelled``.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from operator import itemgetter
from statistics import mean, median
from typing import Any

from eval.metrics.labels import NOT_LABELLED, Labels, spot_check_key
from eval.metrics.records import QuestionRun

SPOT_CHECK_SIZE = 20
DOCUMENT_SOURCE_TYPES = ("knowledge_item", "fact")
SUPPORT_STATUSES = ("supported", "weak", "unsupported", "human_authored", "connective")
_PAIR_ORDER = itemgetter("section", "number", "segment_index", "source_position")


def _located(source: dict[str, Any]) -> bool:
    locator = source.get("locator") or {}
    return locator.get("start") is not None and locator.get("end") is not None


def sentence_source_pairs(runs: Sequence[QuestionRun]) -> list[dict[str, Any]]:
    """Every (substantive sentence, located document source) pair of the run's drafts, in a
    stable order."""
    pairs: list[dict[str, Any]] = []
    for run in runs:
        if not run.drafted:
            continue
        for segment in run.substantive_segments:
            for position, source in enumerate(segment.get("sources") or []):
                if source.get("source_type") not in DOCUMENT_SOURCE_TYPES or not _located(source):
                    continue
                locator = source.get("locator") or {}
                pairs.append(
                    {
                        "section": run.section,
                        "number": run.number,
                        "question_id": str(run.question_id),
                        "answer_id": run.answer_id,
                        "segment_index": int(segment.get("index", 0)),
                        "source_position": position,
                        "sentence": str(segment.get("text") or ""),
                        "support_status": segment.get("support_status"),
                        "source_type": source.get("source_type"),
                        "source_id": source.get("source_id"),
                        "quote": str(source.get("quote") or ""),
                        "tier": source.get("tier"),
                        "document_title": source.get("document_title"),
                        "doc_type": source.get("doc_type"),
                        "locator": {
                            "document_id": locator.get("document_id"),
                            "section_id": locator.get("section_id"),
                            "start": locator.get("start"),
                            "end": locator.get("end"),
                        },
                    }
                )
    pairs.sort(
        key=_PAIR_ORDER
    )
    return pairs


def sample_spot_checks(
    runs: Sequence[QuestionRun], *, size: int = SPOT_CHECK_SIZE, seed: int = 0
) -> list[dict[str, Any]]:
    """Deterministic sample of ``size`` pairs for a person to judge: supported sentences first
    (the question the check answers is whether a verified span really supports its sentence),
    topped up from the rest when there are too few."""
    pairs = sentence_source_pairs(runs)
    supported = [pair for pair in pairs if pair["support_status"] == "supported"]
    others = [pair for pair in pairs if pair["support_status"] != "supported"]
    rng = random.Random(seed)
    chosen = rng.sample(supported, min(size, len(supported)))
    if len(chosen) < size and others:
        chosen.extend(rng.sample(others, min(size - len(chosen), len(others))))
    chosen.sort(
        key=_PAIR_ORDER
    )
    for pair in chosen:
        pair.update({"supports": None, "labelled_by": None, "note": None})
    return chosen


def _share(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def traceability_metrics(runs: Sequence[QuestionRun], labels: Labels) -> dict[str, Any]:
    status_counts = {status: 0 for status in SUPPORT_STATUSES}
    substantive = supported = 0
    per_answer: list[dict[str, Any]] = []
    tiers = {"verbatim": 0, "near": 0, "not_located": 0}
    cited_sources = 0
    for run in runs:
        if not run.drafted:
            continue
        answer_substantive = answer_supported = 0
        for segment in run.segments:
            status = str(segment.get("support_status") or "")
            status_counts[status] = status_counts.get(status, 0) + 1
            if segment.get("kind", "substantive") != "substantive":
                continue
            answer_substantive += 1
            answer_supported += int(status == "supported")
            for source in segment.get("sources") or []:
                if source.get("source_type") not in DOCUMENT_SOURCE_TYPES:
                    continue
                cited_sources += 1
                if not _located(source):
                    tiers["not_located"] += 1
                else:
                    tier = str(source.get("tier") or "verbatim")
                    tiers[tier] = tiers.get(tier, 0) + 1
        substantive += answer_substantive
        supported += answer_supported
        per_answer.append(
            {
                "section": run.section,
                "number": run.number,
                "coverage": run.coverage,
                "substantive": answer_substantive,
                "supported": answer_supported,
                "share": _share(answer_supported, answer_substantive),
                "score": run.support_summary.get("score") if run.support_summary else None,
            }
        )

    shares = [row["share"] for row in per_answer if row["share"] is not None]
    located = tiers["verbatim"] + tiers["near"]

    # Human spot checks: labelled rows matched to this run's pairs.
    pairs = sentence_source_pairs(runs)
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for pair in pairs:
        label = labels.spot_checks.get(
            spot_check_key(pair["section"], pair["number"], pair["sentence"], pair["quote"])
        )
        if label is not None:
            matched.append((pair, label))
    spot_check: Any
    disputed: Any
    if matched:
        agree = sum(1 for _, label in matched if label.get("supports") is True)
        spot_check = {
            "labelled": len(matched),
            "span_supports_sentence": agree,
            "share": _share(agree, len(matched)),
        }
        supported_labelled = [
            (pair, label) for pair, label in matched if pair["support_status"] == "supported"
        ]
        if supported_labelled:
            disagreed = sum(
                1 for _, label in supported_labelled if label.get("supports") is False
            )
            disputed = {
                "supported_sentences_labelled": len(supported_labelled),
                "disputed": disagreed,
                "share": _share(disagreed, len(supported_labelled)),
                "disputed_rows": [
                    {
                        "section": pair["section"],
                        "number": pair["number"],
                        "sentence": pair["sentence"],
                        "note": label.get("note"),
                    }
                    for pair, label in supported_labelled
                    if label.get("supports") is False
                ],
            }
        else:
            disputed = NOT_LABELLED
    else:
        spot_check = NOT_LABELLED
        disputed = NOT_LABELLED

    return {
        "answers": len(per_answer),
        "substantive_sentences": substantive,
        "supported_sentences": supported,
        "supported_share": _share(supported, substantive),
        "per_answer_supported_share": {
            "n": len(shares),
            "mean": round(mean(shares), 4) if shares else None,
            "median": round(median(shares), 4) if shares else None,
            "min": round(min(shares), 4) if shares else None,
            "answers_fully_supported": sum(1 for share in shares if share == 1.0),
        },
        "status_counts": status_counts,
        "span_tiers": {
            "cited_sources": cited_sources,
            "located": located,
            "verbatim": tiers["verbatim"],
            "near": tiers["near"],
            "not_located": tiers["not_located"],
            "verbatim_share_of_cited": _share(tiers["verbatim"], cited_sources),
            "near_share_of_cited": _share(tiers["near"], cited_sources),
            "verbatim_share_of_located": _share(tiers["verbatim"], located),
            "near_share_of_located": _share(tiers["near"], located),
        },
        "spot_check": spot_check,
        "disputed_supported_share": disputed,
        "sentence_source_pairs": len(pairs),
        "per_answer": per_answer,
    }


__all__ = [
    "SPOT_CHECK_SIZE",
    "sample_spot_checks",
    "sentence_source_pairs",
    "traceability_metrics",
]
