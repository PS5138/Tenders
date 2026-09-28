"""The evaluation harness (``eval/harness.py``) end to end, offline, on ``eval/data``.

The harness runs against a database created for the test on the suite's pgserver (its own
schema, migrated and seeded by the harness itself), with the heuristic fake LLM and the hash
embeddings from the shared fixtures. Two full runs (baseline, then a two-configuration toggle
run) take a few seconds each.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.db.models import Question  # noqa: E402
from app.retrieve.query import get_query_vector  # noqa: E402
from eval import harness  # noqa: E402
from eval.metrics.labels import NOT_LABELLED  # noqa: E402
from eval.toggles import Toggles, apply_toggles  # noqa: E402
from tests.e2e.conftest import EVAL_DATA_DIR, EvalData, eval_data, heuristic_llm  # noqa: E402, F401
from tests.fakes.heuristic_llm import HeuristicFakeLLM  # noqa: E402

FIXED_NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
HARNESS_DATABASE = "tenders_eval_harness"
EVERYTHING_ON = Toggles(topic_list="off", vector_list="answer_only", llm_rerank="on")
EXPECTED_HEADINGS = (
    "SYNTHETIC DATA",
    "## Run",
    "### Providers and models",
    "### Prompt versions",
    "### Thresholds read from `config.py`",
    "## Ingestion of the library",
    "### Supersession",
    "## Extraction",
    "### Retrieval recall at eight",
    "### Coverage",
    "### Draft quality against the held-out answer",
    "### Traceability",
    "### Latency",
    "### Per question",
    "## Human labels and the spot check",
)


@pytest.fixture
def harness_database_url(database_url: str) -> Iterator[str]:
    """A fresh database on the suite's pgserver, dropped afterwards."""
    admin = create_engine(database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{HARNESS_DATABASE}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{HARNESS_DATABASE}"'))
    url = make_url(database_url).set(database=HARNESS_DATABASE)
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{HARNESS_DATABASE}" WITH (FORCE)'))
        admin.dispose()


def _retrieval_originals() -> tuple[object, ...]:
    from app.retrieve import coverage, search

    return (
        search.topic_list,
        search.vector_score_expr,
        search.fuse_and_collapse,
        search.retrieve,
        coverage.retrieve,
    )


def test_full_run_writes_the_report(
    tmp_path: Path,
    harness_database_url: str,
    heuristic_llm: HeuristicFakeLLM,  # noqa: F811 - fixture imported from tests.e2e.conftest
    fake_embeddings,  # noqa: ANN001
    eval_data: EvalData,  # noqa: F811 - fixture imported from tests.e2e.conftest
) -> None:
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    report = harness.run(
        EVAL_DATA_DIR,
        tmp_path / "reports",
        database_url=harness_database_url,
        labels_dir=labels_dir,
        today=FIXED_NOW,
    )
    settings = get_settings()
    truth = eval_data.ground_truth["questions"]
    counterpart_total = sum(1 for entry in truth if entry["label"] == "counterpart")
    pricing_total = sum(1 for entry in truth if entry["response_type"] == "pricing")

    # The three files, the sections and the synthetic-data label.
    assert report.run_id == "20260928T120000Z"
    assert report.markdown_path.exists() and report.json_path.exists()
    assert report.spotcheck_path is not None and report.spotcheck_path.exists()
    markdown = report.markdown_path.read_text(encoding="utf-8")
    for heading in EXPECTED_HEADINGS:
        assert heading in markdown, heading
    assert markdown.startswith("# Evaluation report 20260928T120000Z — synthetic data")
    twin = json.loads(report.json_path.read_text(encoding="utf-8"))
    assert twin["synthetic_data"] is True
    assert twin["configurations"][0]["metrics"] == report.configuration()["metrics"]
    assert twin["providers"]["llm_provider"] == "fake"
    assert twin["providers"]["llm_client"] == "HeuristicFakeLLM"
    assert twin["providers"]["embedding_provider"] == "fake"
    assert twin["models"] == {"main": settings.model_main, "fast": settings.model_fast}
    assert twin["thresholds"]["coverage_floor"] == settings.coverage_floor
    assert f"**{settings.coverage_floor}**" in markdown
    assert {"synthesis.v1", "judge.v1", "rerank.v1"} <= set(twin["prompt_versions"].values())
    assert report.configurations[0]["label"] == "topic_list=on,vector_list=max,llm_rerank=off"
    assert report.configurations[0]["patched"] == []

    # Ingestion: the library documents ready and confirmed, the held-out one never ingested.
    documents = {row["filename"]: row for row in twin["ingestion"]["documents"]}
    assert eval_data.ground_truth["held_out_submission"] not in documents
    assert set(documents) == set(twin["data"]["ingested_documents"])
    assert all(row["ingest_status"] == "ready" for row in documents.values())
    assert all(row["classification_confirmed"] for row in documents.values())
    assert twin["ingestion"]["failures"] == []
    older = eval_data.document("reference", "iso_2024")["filename"]
    newer = eval_data.document("reference", "iso_2025")["filename"]
    assert documents[older]["superseded_by"] == newer, "the supersession gate fired"
    assert twin["ingestion"]["supersession"]["decisions"][0]["decision"] == "superseded"

    # Extraction and fidelity against the target.
    fidelity = twin["extraction"]["fidelity"]
    assert fidelity["target"] == settings.extraction_fidelity_target
    assert fidelity["share"] >= settings.extraction_fidelity_target and fidelity["meets_target"]
    assert twin["extraction"]["totals"]["pairs_present"] == 28
    assert twin["extraction"]["totals"]["extracted_over_present"] >= 0.9

    # Retrieval recall over resolved counterparts; nothing unresolved on the synthetic data.
    retrieval = report.metric("retrieval")
    assert retrieval["unresolved_counterparts"] == []
    assert retrieval["total"] == counterpart_total
    assert retrieval["value"] >= 0.9, retrieval
    assert sum(retrieval["resolution_methods"].values()) >= counterpart_total

    # Coverage: no card left unknown, the floor accounted for, human labels absent.
    coverage = report.metric("coverage")
    assert coverage["distribution"]["unknown"] == 0
    assert sum(coverage["new_by_label_source"].values()) == coverage["distribution"]["new"]
    assert coverage["human_accuracy"] == NOT_LABELLED
    assert coverage["ground_truth_proxy"]["counterpart_total"] == counterpart_total

    # Every question drafted except pricing; the judge is marked as the fake provider.
    quality = report.metric("draft_quality")
    assert quality["outcomes"].get("failed", 0) == 0
    assert quality["outcomes"]["skipped_pricing"] == pricing_total
    assert quality["outcomes"]["drafted"] + pricing_total == len(truth)
    assert quality["scored"] == quality["with_held_out_answer"] > 0
    assert quality["judge"]["provider"] == "fake" and "fake provider" in quality["judge"]["note"]
    assert "fake provider" in markdown
    assert quality["edit_distance"]["by_ground_truth"].keys() >= {"counterpart", "new"}

    # Traceability: verified spans with tiers; the human metrics are not labelled.
    trace = report.metric("traceability")
    assert trace["substantive_sentences"] > 0 and trace["supported_share"] > 0.9
    tiers = trace["span_tiers"]
    assert tiers["verbatim"] + tiers["near"] == tiers["located"] > 0
    assert trace["spot_check"] == NOT_LABELLED
    assert trace["disputed_supported_share"] == NOT_LABELLED
    assert markdown.count(NOT_LABELLED) >= 3

    # The spot-check sample: twenty rows for a person to label.
    sample = json.loads(report.spotcheck_path.read_text(encoding="utf-8"))
    assert len(sample["checks"]) == 20
    assert all(row["supports"] is None for row in sample["checks"])
    assert all(row["locator"]["start"] is not None for row in sample["checks"])
    assert sample["synthetic_data"] is True

    # Latency figures are present.
    latency = report.metric("latency")
    assert latency["draft_seconds"]["n"] == quality["outcomes"]["drafted"]
    assert latency["draft_seconds"]["p50"] is not None
    assert latency["draft_seconds"]["p95"] is not None
    assert latency["time_to_triaged_board_seconds"] > 0
    assert len(report.configurations[0]["questions"]) == len(truth)


def test_toggles_patch_and_unpatch_cleanly(
    tmp_path: Path,
    harness_database_url: str,
    heuristic_llm: HeuristicFakeLLM,  # noqa: F811 - fixture imported from tests.e2e.conftest
    fake_embeddings,  # noqa: ANN001
    eval_data: EvalData,  # noqa: F811 - fixture imported from tests.e2e.conftest
) -> None:
    from app.retrieve import coverage, search

    originals = _retrieval_originals()
    with apply_toggles(EVERYTHING_ON) as applied:
        assert search.topic_list is not originals[0]
        assert search.vector_score_expr is not originals[1]
        assert search.fuse_and_collapse is not originals[2]
        assert search.retrieve is not originals[3]
        assert coverage.retrieve is not originals[4]
        assert search.retrieve is coverage.retrieve, "both names carry the one wrapper"
        assert applied.patched == [
            "app.retrieve.search.topic_list",
            "app.retrieve.search.vector_score_expr",
            "app.retrieve.search.retrieve",
            "app.retrieve.coverage.retrieve",
            "app.retrieve.search.fuse_and_collapse",
        ]
    assert _retrieval_originals() == originals, "restored on exit"

    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    report = harness.run(
        EVAL_DATA_DIR,
        tmp_path / "reports",
        database_url=harness_database_url,
        toggles=[Toggles(), EVERYTHING_ON],
        labels_dir=labels_dir,
        today=FIXED_NOW,
    )
    assert _retrieval_originals() == originals, "restored after the run"

    labels = [configuration["label"] for configuration in report.configurations]
    assert labels == [Toggles().label, EVERYTHING_ON.label]
    markdown = report.markdown_path.read_text(encoding="utf-8")
    assert "## Toggle table" in markdown
    for label in labels:
        assert f"| `{label}` |" in markdown, "one table row per configuration"
        assert f"## Configuration `{label}`" in markdown
    toggled = report.configuration(EVERYTHING_ON.label)
    assert toggled["patched"] and toggled["rerank"]["calls"] > 0
    assert toggled["rerank"]["failures"] == 0
    assert toggled["rerank"]["prompt_version"] == "rerank.v1"
    assert any(call.name == "rerank" for call in heuristic_llm.calls)
    for configuration in report.configurations:
        metrics = configuration["metrics"]
        assert metrics["retrieval"]["unresolved_counterparts"] == []
        assert metrics["coverage"]["distribution"]["unknown"] == 0
        assert metrics["draft_quality"]["outcomes"].get("failed", 0) == 0

    # Retrieval behaves normally afterwards: the topic list is back and eight candidates come
    # out of the unpatched functions against the library the run built.
    engine = create_engine(harness_database_url)
    try:
        with Session(engine) as session:
            question = session.scalars(
                select(Question).where(Question.topics != []).order_by(Question.order_index)
            ).first()
            assert question is not None
            vector = get_query_vector(session, question=question)
            result = search.retrieve(
                session,
                question.org_id,
                query_text=question.text,
                query_vector=vector,
                topics=list(question.topics),
            )
            assert result.lists.get("topic"), "the topic list is built again"
            assert len(result.candidates) == get_settings().top_k_synthesis
    finally:
        engine.dispose()


def test_toggle_parsing_and_matrix() -> None:
    assert Toggles.parse(["topic_list=off", "llm_rerank=on"]) == Toggles(
        topic_list="off", vector_list="max", llm_rerank="on"
    )
    assert Toggles.parse([]) == Toggles() and Toggles().is_baseline
    matrix = Toggles.matrix()
    assert len(matrix) == 8 and matrix[0] == Toggles()
    assert len({toggles.label for toggles in matrix}) == 8
    with pytest.raises(ValueError, match="vector_list"):
        Toggles.parse(["vector_list=maybe"])
    with pytest.raises(ValueError, match="toggle"):
        Toggles.parse(["reranker=on"])
