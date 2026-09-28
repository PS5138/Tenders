"""The ``triage_tender`` job and ``retriage``, with a fake coverage function standing in for
owner C, an inline executor so the pool runs deterministically, and worker-thread sessions
bound to the test transaction."""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Executor, Future
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID
from app.db.models import Document, Event, Job, Question, Tender
from app.jobs import enqueue, set_progress
from app.worker import run_once

# Imported by the autouse fixture below rather than at module level: importing the module
# registers the triage_tender handler, and the existing job-loop tests assume an unregistered
# kind, so registration must happen when these tests run, not when they are collected.
triage: Any = None


@pytest.fixture(autouse=True)
def _load_triage_module() -> None:
    global triage
    from app.retrieve import triage as module

    triage = module


@dataclass
class FakeCoverage:
    coverage: str
    detail: dict[str, Any] = field(default_factory=dict)


class InlineExecutor(Executor):
    """Runs each submission immediately on the calling thread."""

    def __init__(self, max_workers: int) -> None:
        self.max_workers = max_workers

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
def inline_pool(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Inline executor plus worker sessions that join the test's transaction as savepoints."""
    seen: dict[str, Any] = {"max_workers": None}

    def make_executor(max_workers: int) -> Executor:
        seen["max_workers"] = max_workers
        return InlineExecutor(max_workers)

    def savepoint_session() -> Session:
        return Session(
            bind=db_session.get_bind(),
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )

    monkeypatch.setattr(triage, "_make_executor", make_executor)
    monkeypatch.setattr(triage, "new_session", savepoint_session)
    return seen


def fake_coverage_for_question(session: Session, question: Question) -> FakeCoverage:
    text = question.text.lower()
    if "social value" in text:
        return FakeCoverage("new", {"best_vec": 0.21, "label_source": "floor", "candidates": []})
    if "partly" in text:
        return FakeCoverage(
            "partial",
            {
                "best_vec": 0.71,
                "label_source": "llm",
                "candidates": [{"item_id": uuid.uuid4(), "score": 0.71}],
                "gap_summary": "No named clinical safety officer.",
            },
        )
    return FakeCoverage("covered", {"best_vec": 0.88, "label_source": "llm", "candidates": []})


@pytest.fixture
def fake_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(triage, "_load_coverage", lambda: fake_coverage_for_question)


def make_tender_with_questions(session: Session, texts: list[str]) -> tuple[Tender, list[Question]]:
    tender = Tender(org_id=DEFAULT_ORG_ID, name="Triage tender")
    session.add(tender)
    session.flush()
    pack = Document(
        org_id=DEFAULT_ORG_ID,
        filename="pack.xlsx",
        storage_path="/tmp/pack.xlsx",
        doc_type="tender_document",
        tender_id=tender.id,
        tender_doc_kind="question_pack",
        classification_confirmed=True,
        ingest_status="ready",
    )
    session.add(pack)
    session.flush()
    questions = [
        Question(
            org_id=DEFAULT_ORG_ID,
            tender_id=tender.id,
            document_id=pack.id,
            section="1",
            number=str(index),
            text=text,
            order_index=index,
        )
        for index, text in enumerate(texts)
    ]
    session.add_all(questions)
    session.flush()
    return tender, questions


def test_triage_persists_coverage_and_reports_progress(
    db_session: Session, inline_pool: dict[str, Any], fake_coverage, settings_override  # noqa: ANN001
) -> None:
    settings_override(triage_concurrency=4)
    tender, questions = make_tender_with_questions(
        db_session,
        [
            "Describe your clinical safety case.",
            "Outline your social value commitments.",
            "Describe how you partly meet the interoperability standards.",
        ],
    )
    job = enqueue(
        db_session,
        "triage_tender",
        {"tender_id": str(tender.id), "actor": "test user", "scope": "unknown"},
        total=3,
    )
    assert run_once(db_session)
    db_session.refresh(job)
    assert job.status == "done", job.error
    assert job.total == 3 and job.done == 3
    assert inline_pool["max_workers"] == 4, "the pool is bounded by settings.triage_concurrency"

    by_id = {str(question.id): question for question in questions}
    for question in questions:
        db_session.refresh(question)
    assert {r["item_id"]: r["outcome"] for r in job.results} == {
        str(q.id): q.coverage for q in questions
    }
    assert [by_id[r["item_id"]].coverage for r in job.results] == [
        r["outcome"] for r in job.results
    ]
    assert questions[0].coverage == "covered"
    assert questions[1].coverage == "new"
    assert questions[1].coverage_detail["label_source"] == "floor"
    assert questions[2].coverage == "partial"
    assert questions[2].coverage_detail["gap_summary"] == "No named clinical safety officer."
    assert isinstance(questions[2].coverage_detail["candidates"][0]["item_id"], str), (
        "detail is coerced to JSON-safe values"
    )


def test_triage_scope_unknown_skips_already_triaged_questions(
    db_session: Session, inline_pool: dict[str, Any], fake_coverage  # noqa: ANN001
) -> None:
    tender, questions = make_tender_with_questions(
        db_session,
        ["Describe your clinical safety case.", "Outline your social value commitments."],
    )
    questions[0].coverage = "partial"
    db_session.flush()
    job = enqueue(
        db_session, "triage_tender", {"tender_id": str(tender.id), "actor": "test user"}
    )
    assert run_once(db_session)
    db_session.refresh(job)
    assert job.status == "done"
    assert job.total == 2 and job.done == 2, (
        "total is the pack size and an already-labelled question counts as done"
    )
    assert [r["item_id"] for r in job.results] == [str(questions[1].id)]
    db_session.refresh(questions[0])
    assert questions[0].coverage == "partial", "already-triaged questions are left alone"


def test_requeued_initial_triage_keeps_pack_total_and_earlier_results(
    db_session: Session, inline_pool: dict[str, Any], fake_coverage  # noqa: ANN001
) -> None:
    """A worker that died after some threads committed leaves the job requeued with
    ``total``, ``done`` and ``results`` from the first attempt and only the ``unknown``
    questions in scope. The rerun must still report done-over-total for the whole pack."""
    tender, questions = make_tender_with_questions(
        db_session,
        [
            "Describe your clinical safety case.",
            "Outline your social value commitments.",
            "Describe how you partly meet the interoperability standards.",
            "Describe your information governance arrangements.",
        ],
    )
    job = enqueue(
        db_session,
        "triage_tender",
        {"tender_id": str(tender.id), "actor": "test user", "scope": "unknown"},
        total=4,
    )
    # First attempt: two threads committed their labels before the worker died; a third result
    # was recorded for a question that then failed and stayed unknown.
    questions[0].coverage = "covered"
    questions[1].coverage = "new"
    set_progress(
        db_session, job, done=1, result={"item_id": str(questions[0].id), "outcome": "covered"}
    )
    set_progress(
        db_session, job, done=2, result={"item_id": str(questions[1].id), "outcome": "new"}
    )
    set_progress(
        db_session,
        job,
        done=3,
        result={"item_id": str(questions[2].id), "outcome": "unknown", "detail": "boom"},
    )
    job.status = "queued"
    job.attempts = 1
    job.error = "worker restarted during job"
    db_session.flush()

    assert run_once(db_session)
    db_session.refresh(job)
    assert job.status == "done", job.error
    assert job.attempts == 2
    assert job.total == 4 and job.done == 4
    by_id = {r["item_id"]: r for r in job.results}
    assert set(by_id) == {str(q.id) for q in questions}, "earlier results are kept"
    assert by_id[str(questions[0].id)]["outcome"] == "covered"
    assert by_id[str(questions[1].id)]["outcome"] == "new"
    assert by_id[str(questions[2].id)] == {
        "item_id": str(questions[2].id), "outcome": "partial",
    }, "the rerun question's failed result is replaced"
    assert by_id[str(questions[3].id)]["outcome"] == "covered"
    for question in questions:
        db_session.refresh(question)
    assert [q.coverage for q in questions] == ["covered", "new", "partial", "covered"]


def test_retriage_enqueues_for_not_started_only(
    db_session: Session, inline_pool: dict[str, Any], fake_coverage  # noqa: ANN001
) -> None:
    tender, questions = make_tender_with_questions(
        db_session,
        [
            "Describe your clinical safety case.",
            "Outline your social value commitments.",
            "Describe how you partly meet the interoperability standards.",
        ],
    )
    for question in questions:
        question.coverage = "new"
    questions[2].status = "ai_draft"  # fixture set-up only; the app writes status via transition()
    db_session.flush()

    job = triage.retriage(db_session, tender, "test user")
    assert isinstance(job, Job)
    assert job.kind == "triage_tender" and job.status == "queued"
    assert job.payload == {
        "tender_id": str(tender.id), "actor": "test user", "scope": "not_started",
    }
    assert job.total == 2
    assert tender.triage_job_id == job.id

    assert run_once(db_session)
    db_session.refresh(job)
    assert job.status == "done", job.error
    for question in questions:
        db_session.refresh(question)
    assert questions[0].coverage == "covered"
    assert questions[1].coverage == "new"
    assert questions[2].coverage == "new", "a drafted question is not re-triaged"
    assert {r["item_id"] for r in job.results} == {str(questions[0].id), str(questions[1].id)}


def test_per_question_failure_is_recorded_and_job_fails_only_when_all_fail(
    db_session: Session, inline_pool: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def half_broken(session: Session, question: Question) -> FakeCoverage:
        if "broken" in question.text:
            raise RuntimeError("retrieval exploded")
        return FakeCoverage("covered", {"best_vec": 0.9, "label_source": "llm"})

    monkeypatch.setattr(triage, "_load_coverage", lambda: half_broken)
    tender, questions = make_tender_with_questions(
        db_session, ["A fine question.", "A broken question."]
    )
    job = enqueue(db_session, "triage_tender", {"tender_id": str(tender.id), "actor": "t"})
    assert run_once(db_session)
    db_session.refresh(job)
    assert job.status == "done"
    assert job.total == 2 and job.done == 2
    outcomes = {r["item_id"]: r for r in job.results}
    assert outcomes[str(questions[0].id)]["outcome"] == "covered"
    assert outcomes[str(questions[1].id)]["outcome"] == "unknown", (
        "results stay inside the coverage vocabulary: a failed judgement leaves unknown"
    )
    assert "retrieval exploded" in outcomes[str(questions[1].id)]["detail"]
    assert {r["outcome"] for r in job.results} <= {"covered", "partial", "new", "unknown"}
    db_session.refresh(questions[1])
    assert questions[1].coverage == "unknown"
    assert set(questions[1].coverage_detail) == {"error"}, (
        "the reason is on the card and label_source is never written for a failure"
    )
    assert "retrieval exploded" in questions[1].coverage_detail["error"]
    db_session.refresh(questions[0])
    assert questions[0].coverage == "covered" and "error" not in questions[0].coverage_detail

    # Every question failing is a job failure, so the worker's retry rule applies.
    monkeypatch.setattr(
        triage, "_load_coverage", lambda: (lambda s, q: (_ for _ in ()).throw(RuntimeError("x")))
    )
    tender2, _ = make_tender_with_questions(db_session, ["Another question."])
    job2 = enqueue(db_session, "triage_tender", {"tender_id": str(tender2.id), "actor": "t"})
    assert run_once(db_session)
    db_session.refresh(job2)
    assert job2.status == "queued" and "every question failed" in (job2.error or "")


def test_missing_coverage_module_fails_the_attempt(
    db_session: Session, inline_pool: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable():  # noqa: ANN202
        raise triage.CoverageUnavailable("Coverage triage is unavailable.")

    monkeypatch.setattr(triage, "_load_coverage", unavailable)
    tender, _ = make_tender_with_questions(db_session, ["A question."])
    job = enqueue(db_session, "triage_tender", {"tender_id": str(tender.id), "actor": "t"})
    assert run_once(db_session)
    db_session.refresh(job)
    assert job.status == "queued"
    assert "Coverage triage is unavailable" in (job.error or "")


def test_empty_scope_completes_immediately(
    db_session: Session, inline_pool: dict[str, Any], fake_coverage  # noqa: ANN001
) -> None:
    tender = Tender(org_id=DEFAULT_ORG_ID, name="Empty")
    db_session.add(tender)
    db_session.flush()
    job = enqueue(db_session, "triage_tender", {"tender_id": str(tender.id), "actor": "t"})
    assert run_once(db_session)
    db_session.refresh(job)
    assert job.status == "done" and job.total == 0 and job.results == []


def test_pool_workers_leave_one_connection_for_the_job_thread(db_session: Session) -> None:
    """Fifteen threads plus the job session exceed a 5 + 10 default pool; the pool is capped at
    capacity minus one so ``set_progress`` never waits on ``pool_timeout`` behind the workers."""
    engine = db_session.get_bind().engine
    capacity = engine.pool.size() + engine.pool._max_overflow
    assert capacity == 15, "the test engine uses SQLAlchemy's default QueuePool sizing"
    assert triage.pool_workers(db_session, 15) == 14
    assert triage.pool_workers(db_session, 4) == 4
    assert triage.pool_workers(db_session, 0) == 1

    # A session with no bound engine (or an unbounded pool) leaves the request as it is.
    assert triage.pool_workers(Session(), 15) == 15


def test_triage_completes_with_real_threads_against_a_small_pool(
    engine: Engine, settings_override, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    """The real ``ThreadPoolExecutor`` and real per-thread sessions against an engine whose pool
    holds three connections with a short ``pool_timeout``. Each judgement holds its connection
    longer than the timeout, so an uncapped pool of fifteen makes the surplus threads (and the
    job thread's progress commit) time out; capped to capacity minus one, every question is
    labelled and the job ends ``done``. Worker threads cannot see an uncommitted transaction, so
    this test commits real rows and removes them afterwards."""
    settings_override(triage_concurrency=15)
    small = create_engine(engine.url, pool_size=2, max_overflow=1, pool_timeout=0.3)
    monkeypatch.setattr(triage, "new_session", lambda: Session(small, expire_on_commit=False))
    seen: dict[str, Any] = {}

    def make_executor(max_workers: int) -> Executor:
        seen["max_workers"] = max_workers
        return triage.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="triage")

    monkeypatch.setattr(triage, "_make_executor", make_executor)
    threads: set[str] = set()

    def slow_coverage(session: Session, question: Question) -> FakeCoverage:
        threads.add(threading.current_thread().name)
        time.sleep(0.6)
        return FakeCoverage("covered", {"best_vec": 0.9, "label_source": "llm"})

    monkeypatch.setattr(triage, "_load_coverage", lambda: slow_coverage)

    job_session = Session(small, expire_on_commit=False)
    tender_id = job_id = None
    try:
        tender, questions = make_tender_with_questions(
            job_session, [f"Question {index}." for index in range(4)]
        )
        job = enqueue(
            job_session, "triage_tender", {"tender_id": str(tender.id), "actor": "t"}, total=4
        )
        job_session.commit()
        tender_id, job_id = tender.id, job.id

        assert run_once(job_session)
        job_session.refresh(job)
        assert job.status == "done", job.error
        assert job.total == 4 and job.done == 4
        assert all("detail" not in r for r in job.results), "no thread timed out on the pool"
        assert {r["outcome"] for r in job.results} == {"covered"}
        for question in questions:
            job_session.refresh(question)
        assert all(question.coverage == "covered" for question in questions)
        assert seen["max_workers"] == 2, "capacity 3 leaves one connection for the job thread"
        assert threads and all(name.startswith("triage") for name in threads), (
            "the judgements ran on real pool threads"
        )
    finally:
        job_session.rollback()
        with Session(small) as cleanup:
            if tender_id is not None:
                cleanup.execute(delete(Tender).where(Tender.id == tender_id))
            if job_id is not None:
                cleanup.execute(delete(Event).where(Event.entity_id == job_id))
                cleanup.execute(delete(Job).where(Job.id == job_id))
            cleanup.commit()
        job_session.close()
        small.dispose()


@pytest.mark.integration
def test_real_coverage_function_is_callable(db_session: Session) -> None:
    pytest.importorskip("app.retrieve.coverage")
    from app.retrieve.coverage import coverage_for_question

    tender, questions = make_tender_with_questions(db_session, ["Describe your approach."])
    questions[0].topics = ["clinical_safety"]
    from app.llm.embeddings import embed

    questions[0].embedding = embed([questions[0].text])[0]
    db_session.flush()
    result = coverage_for_question(db_session, questions[0])
    assert result.coverage in {"covered", "partial", "new"}
    assert isinstance(result.detail, dict)
    assert db_session.scalars(select(Question).where(Question.id == questions[0].id)).one()
