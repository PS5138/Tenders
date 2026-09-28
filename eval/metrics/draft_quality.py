"""Draft quality against the held-out approved answer.

Two figures per drafted question that has a held-out answer: the normalised Levenshtein
distance between the draft text and the held-out answer after the shared normalisation (0 is
identical, 1 is nothing in common; a cheap proxy, lower is better), and an LLM judge score on a
five-point scale from an expert bid reviewer prompt (``eval/prompts/judge.v1.md``) through the
FAST model. Both are stratified by the triage coverage label and by the ground-truth label.
With the fake provider the judge returns a fixed score and the report says so.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field
from rapidfuzz.distance import Levenshtein

from app.config import get_settings
from app.ingest.normalise import normalise
from app.llm import get_llm
from eval.metrics.records import QuestionKey, QuestionRun
from eval.prompts import JUDGE_PROMPT, eval_prompt_version, load_eval_prompt

logger = logging.getLogger(__name__)

FAKE_JUDGE_SCORE = 3
FAKE_JUDGE_NOTE = (
    "fake provider: the judge returned a fixed score of 3 for every draft; this is not a "
    "judgement of quality"
)


class JudgeOutput(BaseModel):
    score: int = Field(default=FAKE_JUDGE_SCORE, ge=1, le=5)
    rationale: str = ""


def normalised_edit_distance(draft: str, reference: str) -> float:
    """Levenshtein distance over the normalised texts divided by the longer length."""
    a, b = normalise(draft or "")[0], normalise(reference or "")[0]
    if not a and not b:
        return 0.0
    return round(float(Levenshtein.normalized_distance(a, b)), 4)


def build_judge_input(run: QuestionRun, reference: str, buyer: str | None) -> str:
    constraints = [f"response type: {run.response_type}"]
    if run.word_limit:
        constraints.append(f"word limit: {run.word_limit} words")
    if buyer:
        constraints.append(f"buyer: {buyer}")
    return "\n\n".join(
        [
            "## question\n" + run.text.strip() + "\n(" + "; ".join(constraints) + ")",
            "## reference\n" + reference.strip(),
            "## draft\n" + (run.answer_text or "").strip(),
        ]
    )


def judge_draft(run: QuestionRun, reference: str, buyer: str | None) -> JudgeOutput:
    """One FAST-model call through the shared client; raises ``LLMError`` on failure."""
    settings = get_settings()
    return get_llm().parse(
        JUDGE_PROMPT,
        model=settings.model_fast,
        system=load_eval_prompt(JUDGE_PROMPT),
        user=build_judge_input(run, reference, buyer),
        output_model=JudgeOutput,
        max_tokens=1000,
    )


def _stratify(rows: Sequence[dict[str, Any]], field: str, by: str) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {}
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        buckets.setdefault(str(row.get(by)), []).append(float(value))
    return {
        key: {"n": len(values), "mean": round(mean(values), 4)}
        for key, values in sorted(buckets.items())
    }


def draft_quality_metrics(
    runs: Sequence[QuestionRun],
    ground_truth: Mapping[QuestionKey, Mapping[str, Any]],
    *,
    buyer: str | None,
    judge: bool,
    fake_provider: bool,
) -> dict[str, Any]:
    settings = get_settings()
    rows: list[dict[str, Any]] = []
    outcomes: dict[str, int] = {}
    judge_failures = 0
    for run in runs:
        outcomes[run.outcome] = outcomes.get(run.outcome, 0) + 1
        truth = ground_truth.get(run.key) or {}
        reference = truth.get("held_out_answer")
        row: dict[str, Any] = {
            "section": run.section,
            "number": run.number,
            "coverage": run.coverage,
            "ground_truth_label": truth.get("label"),
            "outcome": run.outcome,
            "has_held_out_answer": bool(reference),
            "edit_distance": None,
            "judge_score": None,
            "judge_rationale": None,
            "word_count": len((run.answer_text or "").split()) if run.drafted else None,
            "word_limit": run.word_limit,
        }
        if run.drafted and reference:
            row["edit_distance"] = normalised_edit_distance(run.answer_text or "", reference)
            if judge:
                try:
                    verdict = judge_draft(run, reference, buyer)
                    row["judge_score"] = int(verdict.score)
                    row["judge_rationale"] = verdict.rationale.strip() or None
                except Exception as exc:  # noqa: BLE001 - one failed judgement never ends the run
                    judge_failures += 1
                    logger.warning("judge call failed for %s %s: %s", run.section, run.number, exc)
        rows.append(row)

    scored = [row for row in rows if row["edit_distance"] is not None]
    judged = [row for row in rows if row["judge_score"] is not None]
    over_limit = [
        row
        for row in rows
        if row["word_limit"] and row["word_count"] and row["word_count"] > row["word_limit"]
    ]
    judge_block: dict[str, Any] = {
        "enabled": judge,
        "provider": "fake" if fake_provider else settings.llm_provider,
        "model": settings.model_fast,
        "prompt_version": eval_prompt_version(JUDGE_PROMPT),
        "n": len(judged),
        "failures": judge_failures,
        "mean": round(mean(row["judge_score"] for row in judged), 4) if judged else None,
        "distribution": {
            str(score): sum(1 for row in judged if row["judge_score"] == score)
            for score in range(1, 6)
        },
        "by_coverage": _stratify(judged, "judge_score", "coverage"),
        "by_ground_truth": _stratify(judged, "judge_score", "ground_truth_label"),
    }
    if fake_provider:
        judge_block["note"] = FAKE_JUDGE_NOTE
    elif not judge:
        judge_block["note"] = "judge disabled for this run"

    return {
        "outcomes": outcomes,
        "with_held_out_answer": sum(1 for row in rows if row["has_held_out_answer"]),
        "scored": len(scored),
        "edit_distance": {
            "n": len(scored),
            "mean": round(mean(row["edit_distance"] for row in scored), 4) if scored else None,
            "by_coverage": _stratify(scored, "edit_distance", "coverage"),
            "by_ground_truth": _stratify(scored, "edit_distance", "ground_truth_label"),
            "note": (
                "normalised Levenshtein distance after the shared normalisation; "
                "lower is better"
            ),
        },
        "judge": judge_block,
        "word_limit_exceeded": [
            {"section": row["section"], "number": row["number"], "words": row["word_count"],
             "limit": row["word_limit"]}
            for row in over_limit
        ],
        "per_question": rows,
    }


__all__ = [
    "FAKE_JUDGE_NOTE",
    "FAKE_JUDGE_SCORE",
    "JudgeOutput",
    "build_judge_input",
    "draft_quality_metrics",
    "judge_draft",
    "normalised_edit_distance",
]
