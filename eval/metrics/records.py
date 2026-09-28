"""The per-question record the harness collects and every metric reads."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

OUTCOME_DRAFTED = "drafted"
OUTCOME_SKIPPED_PRICING = "skipped_pricing"
OUTCOME_FAILED = "failed"

QuestionKey = tuple[str, str]


def question_key(section: str, number: str) -> QuestionKey:
    return (str(section).strip(), str(number).strip())


def ground_truth_by_key(ground_truth: Mapping[str, Any]) -> dict[QuestionKey, dict[str, Any]]:
    """``ground_truth.json`` questions keyed by (section, number)."""
    return {
        question_key(entry["section"], entry["number"]): dict(entry)
        for entry in ground_truth.get("questions", [])
    }


@dataclass
class QuestionRun:
    """One pack question after triage and drafting in one toggle configuration."""

    question_id: uuid.UUID
    section: str
    number: str
    text: str
    response_type: str
    mandatory: bool
    word_limit: int | None
    topics: list[str]
    coverage: str
    coverage_detail: dict[str, Any]
    outcome: str = OUTCOME_DRAFTED
    detail: str | None = None
    draft_seconds: float | None = None
    answer_id: str | None = None
    answer_text: str | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    fact_checklist: list[dict[str, Any]] = field(default_factory=list)
    support_summary: dict[str, Any] = field(default_factory=dict)
    retrieved_item_ids: list[str] = field(default_factory=list)
    verbatim_offer_item_id: str | None = None
    model: str | None = None
    prompt_version: str | None = None

    @property
    def key(self) -> QuestionKey:
        return question_key(self.section, self.number)

    @property
    def drafted(self) -> bool:
        return self.outcome == OUTCOME_DRAFTED

    @property
    def label_source(self) -> str | None:
        value = self.coverage_detail.get("label_source")
        return str(value) if value is not None else None

    @property
    def best_vec(self) -> float | None:
        value = self.coverage_detail.get("best_vec")
        return float(value) if value is not None else None

    @property
    def triage_candidate_ids(self) -> list[str]:
        return [
            str(candidate["item_id"])
            for candidate in self.coverage_detail.get("candidates") or []
            if isinstance(candidate, Mapping) and candidate.get("item_id")
        ]

    @property
    def substantive_segments(self) -> list[dict[str, Any]]:
        return [
            segment
            for segment in self.segments
            if segment.get("kind", "substantive") == "substantive"
        ]

    def summary(self) -> dict[str, Any]:
        """The compact row the report prints per question."""
        return {
            "section": self.section,
            "number": self.number,
            "response_type": self.response_type,
            "coverage": self.coverage,
            "label_source": self.label_source,
            "best_vec": self.best_vec,
            "outcome": self.outcome,
            "detail": self.detail,
            "draft_seconds": self.draft_seconds,
            "sentences": len(self.substantive_segments),
            "support_score": self.support_summary.get("score") if self.support_summary else None,
            "gaps": len(self.gaps),
            "verbatim_offer": self.verbatim_offer_item_id is not None,
        }


def drafted_runs(runs: Iterable[QuestionRun]) -> list[QuestionRun]:
    return [run for run in runs if run.drafted]


__all__ = [
    "OUTCOME_DRAFTED",
    "OUTCOME_FAILED",
    "OUTCOME_SKIPPED_PRICING",
    "QuestionKey",
    "QuestionRun",
    "drafted_runs",
    "ground_truth_by_key",
    "question_key",
]
