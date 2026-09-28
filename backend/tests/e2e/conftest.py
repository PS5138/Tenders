"""Fixtures for the offline end-to-end test. They extend ``tests/conftest.py``: the shared
``db_session``, ``app_client``, ``settings_override`` and ``fake_embeddings`` fixtures are
used as they are.

- ``heuristic_llm``: swaps ``HeuristicFakeLLM`` in through the same provider switch the
  ``fake_llm`` fixture uses (``LLM_PROVIDER=fake`` plus ``reset_llm()``), by pointing the
  client module's ``FakeLLM`` name at the subclass before the cached client is rebuilt.
- ``job_handlers``: the ingest, extraction and triage handlers registered by importing their
  modules, as the worker's start-up does.
- ``bound_sessions``: the detached runner's ``session_factory`` and the triage pool's
  ``new_session`` bound to the test connection as savepoint sessions, with an inline executor,
  so everything the pipeline commits is visible to the test and rolled back at the end.
- ``run_jobs``: dispatches queued jobs through the worker's ``run_once`` until the queue is
  empty, as the worker process would.
- ``eval_data``: the synthetic files, manifest and ground truth.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from tests.fakes.heuristic_llm import HeuristicFakeLLM

EVAL_DATA_DIR = REPO_ROOT / "eval" / "data"


@pytest.fixture(autouse=True)
def _drafting_routes_registered() -> None:
    """The drafting router is registered by ``main.py``; while that shared file is being
    edited, make sure the streaming routes exist on the app the test drives."""
    from app.api import drafts
    from app.main import app

    paths = {getattr(route, "path", None) for route in app.routes}
    if "/questions/{question_id}/draft" not in paths:
        app.include_router(drafts.router)


@pytest.fixture
def heuristic_llm(
    settings_override: Callable[..., object], monkeypatch: pytest.MonkeyPatch
) -> Iterator[HeuristicFakeLLM]:
    from app.llm import client

    monkeypatch.setattr(client, "FakeLLM", HeuristicFakeLLM)
    settings_override(llm_provider="fake")
    client.reset_llm()
    llm = client.get_llm()
    assert isinstance(llm, HeuristicFakeLLM)
    try:
        yield llm
    finally:
        llm.reset()
        client.reset_llm()


@pytest.fixture
def job_handlers() -> dict[str, Any]:
    """The handlers for the three job kinds this test runs, registered by importing their
    modules exactly as the worker's start-up does through ``app.worker.HANDLER_MODULES``.

    The registrations are left in place: importing registers once per process, and the
    handler modules' own tests rely on that. ``draft_all`` is not imported here because
    ``test_jobs`` registers and removes its own ``draft_all`` handler.
    """
    import app.ingest.jobs as ingest_jobs
    import app.ingest.questions as questions
    import app.retrieve.triage as triage
    from app.jobs import registry

    handlers = {
        "ingest_document": ingest_jobs.ingest_document,
        "extract_questions": questions.extract_questions,
        "triage_tender": triage.triage_tender,
    }
    for kind, handler in handlers.items():
        # Idempotent: the same function the import registered, put back if a test removed it.
        registry._HANDLERS[kind] = handler
    return handlers


class InlineExecutor(Executor):
    """Runs each submission on the calling thread: one connection, one thread at a time."""

    def submit(self, fn, /, *args, **kwargs):  # noqa: ANN001, ANN201
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        return None


@pytest.fixture
def bound_sessions(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[], Session]]:
    from app.generate import runner
    from app.retrieve import triage

    connection = db_session.get_bind()

    def factory() -> Session:
        return Session(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )

    monkeypatch.setattr(runner, "session_factory", factory)
    monkeypatch.setattr(triage, "new_session", factory)
    monkeypatch.setattr(triage, "_make_executor", lambda max_workers: InlineExecutor())
    runner._RUNS.clear()
    try:
        yield factory
    finally:
        runner._RUNS.clear()


@pytest.fixture
def run_jobs(db_session: Session, job_handlers: dict[str, Any]) -> Callable[..., int]:
    """Dispatch queued jobs in ``created_at`` order until the queue is empty; returns how many
    were run. A job that raises follows the worker's retry rule, so the loop always ends."""
    from app.worker import run_once

    def run(limit: int = 200) -> int:
        count = 0
        while count < limit and run_once(db_session):
            count += 1
        db_session.expire_all()
        return count

    return run


@dataclass(frozen=True)
class EvalData:
    directory: Path
    manifest: dict[str, Any]
    ground_truth: dict[str, Any]

    def document(self, role: str, key: str | None = None) -> dict[str, Any]:
        for document in self.manifest["documents"]:
            if document["role"] == role and (key is None or document.get("key") == key):
                return document
        raise KeyError(f"no manifest document with role {role!r} and key {key!r}")

    def path(self, filename: str) -> Path:
        return self.directory / filename


@pytest.fixture(scope="session")
def eval_data() -> EvalData:
    manifest_path = EVAL_DATA_DIR / "manifest.json"
    ground_truth_path = EVAL_DATA_DIR / "ground_truth.json"
    if not manifest_path.exists() or not ground_truth_path.exists():
        pytest.skip("eval/data has not been generated; run eval.generate_synthetic first")
    return EvalData(
        directory=EVAL_DATA_DIR,
        manifest=json.loads(manifest_path.read_text(encoding="utf-8")),
        ground_truth=json.loads(ground_truth_path.read_text(encoding="utf-8")),
    )
