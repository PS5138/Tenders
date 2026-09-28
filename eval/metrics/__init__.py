"""Metric calculations for the evaluation harness (plan: Evaluation harness, "Metrics").

Each module takes plain records the harness collected during a run (``records.QuestionRun``)
plus the ground truth and the stored human labels, and returns a JSON-able dict. Metrics that
need human labels return the string ``labels.NOT_LABELLED`` when no label applies to the run,
never a zero.
"""

from eval.metrics.labels import NOT_LABELLED, Labels, load_labels
from eval.metrics.records import QuestionRun, ground_truth_by_key

__all__ = ["NOT_LABELLED", "Labels", "QuestionRun", "ground_truth_by_key", "load_labels"]
