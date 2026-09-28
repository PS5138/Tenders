"""Human labels stored under ``eval/labels/`` (plan: they accumulate across runs).

Two files, both optional; see ``eval/labels/README.md`` for their shapes:

- ``coverage_labels.json``: a person's coverage label per pack question, keyed by section and
  number.
- ``spot_checks.json``: the twenty-sentence spot checks, one row per (sentence, source) pair a
  person judged, matched to a run by section, number, the sentence text and the quoted span
  after the shared normalisation, so ids that change between runs do not matter.

A metric that finds no label for the run reports ``NOT_LABELLED``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ingest.normalise import normalise
from eval.metrics.records import QuestionKey, question_key

NOT_LABELLED = "not labelled"
COVERAGE_LABELS_FILE = "coverage_labels.json"
SPOT_CHECKS_FILE = "spot_checks.json"
COVERAGE_VALUES = ("covered", "partial", "new")

SpotCheckKey = tuple[str, str, str, str]


def spot_check_key(section: str, number: str, sentence: str, quote: str) -> SpotCheckKey:
    """How a stored spot-check row is matched to a run's (sentence, source) pair."""
    key = question_key(section, number)
    return (key[0], key[1], normalise(sentence or "")[0], normalise(quote or "")[0])


@dataclass
class Labels:
    labels_dir: Path
    coverage: dict[QuestionKey, dict[str, Any]] = field(default_factory=dict)
    spot_checks: dict[SpotCheckKey, dict[str, Any]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def coverage_path(self) -> Path:
        return self.labels_dir / COVERAGE_LABELS_FILE

    @property
    def spot_checks_path(self) -> Path:
        return self.labels_dir / SPOT_CHECKS_FILE

    def as_dict(self) -> dict[str, Any]:
        return {
            "labels_dir": str(self.labels_dir),
            "coverage_labels": len(self.coverage),
            "spot_checks": len(self.spot_checks),
            "problems": list(self.problems),
        }


def _read_json(path: Path, problems: list[str]) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"{path.name}: could not be read ({exc})")
        return None


def _rows(data: Any, key: str, path: Path, problems: list[str]) -> list[Mapping[str, Any]]:
    rows = data.get(key) if isinstance(data, Mapping) else data
    if rows is None:
        return []
    if not isinstance(rows, list):
        problems.append(f"{path.name}: expected a list under {key!r}")
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def load_labels(labels_dir: Path | str) -> Labels:
    """Read both label files; a missing file simply means no labels of that kind."""
    labels = Labels(labels_dir=Path(labels_dir))
    coverage_data = _read_json(labels.coverage_path, labels.problems)
    for row in _rows(coverage_data, "labels", labels.coverage_path, labels.problems):
        coverage = str(row.get("coverage") or "").strip()
        if coverage not in COVERAGE_VALUES or not row.get("section") or not row.get("number"):
            labels.problems.append(
                f"{COVERAGE_LABELS_FILE}: skipped a row without section, number and a "
                f"coverage in {COVERAGE_VALUES}: {dict(row)!r}"
            )
            continue
        labels.coverage[question_key(str(row["section"]), str(row["number"]))] = dict(row)

    spot_data = _read_json(labels.spot_checks_path, labels.problems)
    for row in _rows(spot_data, "checks", labels.spot_checks_path, labels.problems):
        supports = row.get("supports")
        if not isinstance(supports, bool):
            # An unlabelled sample row copied in as it is; a human has not judged it yet.
            continue
        if not (row.get("section") and row.get("number") and row.get("sentence")):
            labels.problems.append(
                f"{SPOT_CHECKS_FILE}: skipped a row without section, number and sentence"
            )
            continue
        key = spot_check_key(
            str(row["section"]),
            str(row["number"]),
            str(row["sentence"]),
            str(row.get("quote") or ""),
        )
        labels.spot_checks[key] = dict(row)
    return labels


__all__ = [
    "COVERAGE_LABELS_FILE",
    "COVERAGE_VALUES",
    "NOT_LABELLED",
    "SPOT_CHECKS_FILE",
    "Labels",
    "SpotCheckKey",
    "load_labels",
    "spot_check_key",
]
