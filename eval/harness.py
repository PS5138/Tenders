"""The evaluation harness (plan: "Evaluation harness"; MVP step 6 is opening its latest report).

    python -m eval.harness --data eval/data --out eval/reports [--fake] [--db URL]
                           [--toggle topic_list=off] [--toggle vector_list=answer_only]
                           [--toggle llm_rerank=on] [--matrix] [--labels eval/labels] [--no-judge]

One run, in order:

1. Database. Without ``--db`` a throwaway Postgres 16 with pgvector is started from the
   ``pgserver`` package in a temporary directory (the same server the test suite and
   ``backend/scripts/dev_db.py`` use) and deleted at the end, so the harness never touches a
   real database. Either way the schema is created fresh with ``alembic upgrade head`` and the
   seed (default organisation, fixture document).
2. Library. Every non-held-out synthetic document in ``manifest.json`` (the two ingested past
   submissions and the two reference documents) is stored and ingested through the real
   ``ingest_document`` handler, dispatched by the worker's ``run_once`` in this process (no
   worker process), then confirmed with the same function ``POST /documents/{id}/confirm``
   calls, so the supersession gate fires as it does for a user. The held-out submission is
   never ingested; its answers are the ground truth.
3. Per toggle configuration: a tender is created, the question pack is stored as its
   ``question_pack`` document and ``extract_questions`` then the chained ``triage_tender``
   run through ``run_once``; then EVERY question is drafted, regardless of coverage, through
   the real pipeline (``app.generate.pipeline.draft_question``, the function draft-all calls),
   synchronously and one at a time. Pricing questions are never drafted and are reported as
   skipped. Toggles are monkeypatches of ``app.retrieve`` applied around the tender pass
   (``eval/toggles.py`` says exactly what is patched).
4. Metrics (``eval/metrics``) and the report (``eval/report.py``): ``<run>.md``, its ``.json``
   twin and ``<run>.spotcheck.json`` with twenty (sentence, source) pairs for a person to label.

Providers follow the environment (``LLM_PROVIDER``, ``EMBEDDING_PROVIDER``). With the fake
LLM provider the harness installs the rule-based ``HeuristicFakeLLM`` from
``backend/tests/fakes`` (the plain ``FakeLLM`` cannot synthesise) and registers fixed answers
for its own two prompts: the judge returns a fixed score and the report says "fake provider";
the rerank orders by word overlap so the toggle's plumbing is exercised. Numbers from a fake
provider prove the plumbing, not quality.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import enums as e
from app.db.models import (
    Document,
    Event,
    Fact,
    Job,
    KnowledgeItem,
    Question,
    SupersessionDecision,
    Tender,
)
from app.db.seed import seed
from app.generate import pipeline
from app.generate.errors import Conflict409
from app.ingest.corrections import confirm_document
from app.ingest.jobs import enqueue_ingest
from app.ingest.storage import safe_filename
from app.jobs import enqueue, register
from app.llm import PROMPT_VERSIONS, get_llm, reset_embedder, reset_llm
from app.worker import run_once
from eval import BACKEND_DIR, REPO_ROOT
from eval.metrics.coverage import coverage_metrics
from eval.metrics.draft_quality import FAKE_JUDGE_NOTE, FAKE_JUDGE_SCORE, draft_quality_metrics
from eval.metrics.extraction import extraction_metrics
from eval.metrics.labels import Labels, load_labels
from eval.metrics.latency import latency_metrics
from eval.metrics.records import (
    OUTCOME_FAILED,
    OUTCOME_SKIPPED_PRICING,
    QuestionRun,
    ground_truth_by_key,
)
from eval.metrics.resolution import ItemResolver
from eval.metrics.retrieval import recall_at_k
from eval.metrics.traceability import SPOT_CHECK_SIZE, sample_spot_checks, traceability_metrics
from eval.prompts import EVAL_PROMPT_VERSIONS, JUDGE_PROMPT, RERANK_PROMPT
from eval.report import Report, write_report
from eval.toggles import DEFAULT_RERANK_POOL, Toggles, apply_toggles

logger = logging.getLogger("tenders.eval")

DEFAULT_DATA_DIR = REPO_ROOT / "eval" / "data"
DEFAULT_OUT_DIR = REPO_ROOT / "eval" / "reports"
DEFAULT_LABELS_DIR = REPO_ROOT / "eval" / "labels"
ACTOR = "harness"
THROWAWAY_DATABASE = "tenders_eval"
LIBRARY_ROLES = ("past_submission", "reference")
BENCHMARK_POLICY = (
    "Hackathon numbers come from synthetic data and are labelled as such wherever they are "
    "quoted. Real past tenders join the benchmark only as design partners contribute them under "
    "agreement. No Tandem submission is ever loaded."
)
_JOB_LIMIT = 500


class HarnessError(RuntimeError):
    pass


# --- Providers ------------------------------------------------------------------------------------


def _fake_rerank(**context: Any) -> dict[str, Any]:
    """The fake provider's rerank: candidates ordered by word overlap with the question."""
    from tests.fakes.heuristic_llm import word_share

    payload = json.loads(context["user"])
    question = str(payload.get("question") or "")
    candidates = list(payload.get("candidates") or [])
    ranked = sorted(
        candidates,
        key=lambda candidate: -word_share(
            question, f"{candidate.get('question') or ''} {candidate.get('text') or ''}"
        ),
    )
    return {"ranking": [str(candidate["id"]) for candidate in ranked]}


@contextmanager
def _offline_llm() -> Iterator[Any]:
    """With ``LLM_PROVIDER=fake``, make sure the heuristic fake is the client and register the
    harness's two prompts on it. Restores the client module's ``FakeLLM`` name on exit (the
    cached heuristic instance stays until the next ``reset_llm()``, as the tests expect)."""
    from app.llm import client

    try:
        from tests.fakes.heuristic_llm import HeuristicFakeLLM
    except ImportError as exc:  # pragma: no cover - only without backend/tests on the path
        raise HarnessError(
            "LLM_PROVIDER=fake needs backend/tests/fakes/heuristic_llm.py (the plain fake cannot "
            f"synthesise a draft): {exc}"
        ) from exc

    original_fake = client.FakeLLM
    swapped = False
    llm = get_llm()
    if not isinstance(llm, HeuristicFakeLLM):
        client.FakeLLM = HeuristicFakeLLM
        reset_llm()
        llm = get_llm()
        swapped = True
    llm.register(
        JUDGE_PROMPT, {"score": FAKE_JUDGE_SCORE, "rationale": "fake provider: fixed score"}
    )
    llm.register(RERANK_PROMPT, _fake_rerank)
    try:
        yield llm
    finally:
        if swapped:
            client.FakeLLM = original_fake


def _check_providers(settings: Any) -> None:
    """Refuse to start a run that would fail on the first model call."""
    if settings.llm_provider == "anthropic" and not (
        settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    ):
        raise HarnessError(
            "LLM_PROVIDER is anthropic but no ANTHROPIC_API_KEY is set; pass --fake for the "
            "offline run or set the key"
        )
    if settings.embedding_provider == "openai" and not (
        settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
    ):
        raise HarnessError(
            "EMBEDDING_PROVIDER is openai but no OPENAI_API_KEY is set; pass --fake or set the key"
        )
    if settings.embedding_provider == "voyage" and not (
        settings.voyage_api_key or os.environ.get("VOYAGE_API_KEY")
    ):
        raise HarnessError(
            "EMBEDDING_PROVIDER is voyage but no VOYAGE_API_KEY is set; pass --fake or set the key"
        )


def _provider_info(settings: Any) -> dict[str, Any]:
    if settings.embedding_provider == "fake":
        embedding_model = "fake (normalised bag of hashed unigrams and bigrams)"
    elif settings.embedding_provider == "openai":
        embedding_model = settings.embedding_model_openai
    else:
        embedding_model = settings.embedding_model_voyage
    return {
        "llm_provider": settings.llm_provider,
        "llm_client": type(get_llm()).__name__,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": embedding_model,
        "embedding_dimension": settings.embedding_dimension,
    }


def _thresholds(settings: Any, rerank_pool: int) -> dict[str, Any]:
    return {
        "coverage_floor": settings.coverage_floor,
        "dedup_threshold": settings.dedup_threshold,
        "verbatim_threshold": settings.verbatim_threshold,
        "span_match_min": settings.span_match_min,
        "realign_min": settings.realign_min,
        "extraction_fidelity_target": settings.extraction_fidelity_target,
        "pair_extraction_window_tokens": settings.pair_extraction_window_tokens,
        "chunk_target_tokens": settings.chunk_target_tokens,
        "rrf_k": settings.rrf_k,
        "candidates_per_list": settings.candidates_per_list,
        "top_k_synthesis": settings.top_k_synthesis,
        "coverage_judgement_top": settings.coverage_judgement_top,
        "triage_concurrency": settings.triage_concurrency,
        "rerank_pool": rerank_pool,
    }


def _prompt_versions() -> dict[str, str]:
    versions = {name: f"{name}.{version}" for name, version in PROMPT_VERSIONS.items()}
    versions.update(
        {f"harness:{name}": f"{name}.{version}" for name, version in EVAL_PROMPT_VERSIONS.items()}
    )
    return versions


# --- Database -------------------------------------------------------------------------------------


@dataclass
class HarnessDatabase:
    url: str
    engine: Engine
    session_factory: Callable[[], Session]
    throwaway: bool

    @property
    def description(self) -> str:
        if self.throwaway:
            return "throwaway pgserver instance started for this run and deleted afterwards"
        return "provided with --db (schema migrated to head and seeded; existing rows untouched)"


def migrate_and_seed(engine: Engine) -> None:
    """``alembic upgrade head`` on ``engine``'s connection, then the seed. Idempotent."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["configure_logger"] = False
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with Session(engine) as session:
        seed(session)
        session.commit()


@contextmanager
def open_database(database_url: str | None) -> Iterator[HarnessDatabase]:
    server = None
    pgdata_root: Path | None = None
    if database_url is None:
        import pgserver

        pgdata_root = Path(tempfile.mkdtemp(prefix="tenders-eval-pg-"))
        server = pgserver.get_server(pgdata_root / "data", cleanup_mode="delete")
        server.psql(f"CREATE DATABASE {THROWAWAY_DATABASE}")
        database_url = server.get_uri(THROWAWAY_DATABASE).replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    engine = create_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=25)
    try:
        migrate_and_seed(engine)
        factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
        yield HarnessDatabase(
            url=database_url, engine=engine, session_factory=factory, throwaway=server is not None
        )
    finally:
        engine.dispose()
        if server is not None:
            server.cleanup()
        if pgdata_root is not None:
            shutil.rmtree(pgdata_root, ignore_errors=True)


@contextmanager
def _triage_sessions(factory: Callable[[], Session]) -> Iterator[None]:
    """Point the triage pool's per-thread sessions at the harness engine for the block."""
    from app.retrieve import triage

    original = triage.new_session
    triage.new_session = factory  # type: ignore[assignment]
    try:
        yield
    finally:
        triage.new_session = original  # type: ignore[assignment]


def _register_handlers() -> None:
    """Import the handler modules as the worker does and make sure each kind is registered
    (a test in the same process may have removed one)."""
    import app.ingest.jobs as ingest_jobs
    import app.ingest.questions as questions
    import app.retrieve.triage as triage

    register(e.JobKind.INGEST_DOCUMENT)(ingest_jobs.ingest_document)
    register(e.JobKind.EXTRACT_QUESTIONS)(questions.extract_questions)
    register(e.JobKind.TRIAGE_TENDER)(triage.triage_tender)


def drain_jobs(session: Session, limit: int = _JOB_LIMIT) -> int:
    """Dispatch queued jobs through the worker's ``run_once`` until the queue is empty."""
    count = 0
    while count < limit and run_once(session):
        count += 1
    session.expire_all()
    return count


def _store(uploads_dir: Path, org_id: uuid.UUID, document_id: uuid.UUID, source: Path) -> str:
    target_dir = uploads_dir / str(org_id) / str(document_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_filename(source.name)
    shutil.copyfile(source, target)
    return str(target.resolve())


def _job_seconds(job: Job | None) -> float | None:
    if job is None or job.started_at is None or job.finished_at is None:
        return None
    return (job.finished_at - job.started_at).total_seconds()


# --- Library ingestion ----------------------------------------------------------------------------


@dataclass
class LibraryIngestion:
    documents_by_filename: dict[str, Document]
    rows: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    failures: list[dict[str, Any]] = field(default_factory=list)
    supersession: dict[str, Any] = field(default_factory=dict)


def ingest_library(
    session: Session,
    manifest: Mapping[str, Any],
    data_dir: Path,
    uploads_dir: Path,
    *,
    org_id: uuid.UUID,
    actor: str,
) -> LibraryIngestion:
    """Store, ingest (``ingest_document`` via ``run_once``) and confirm every library document
    the manifest lists, in manifest order; the held-out submission is skipped."""
    started = time.perf_counter()
    documents: dict[str, Document] = {}
    timings: dict[str, float] = {}
    job_ids: dict[str, uuid.UUID] = {}
    for entry in manifest["documents"]:
        if entry.get("role") not in LIBRARY_ROLES:
            continue
        filename = str(entry["filename"])
        source = data_dir / filename
        if not source.exists():
            raise HarnessError(f"manifest names {filename} but it is not in {data_dir}")
        document_id = uuid.uuid4()
        document = Document(
            id=document_id,
            org_id=org_id,
            filename=filename,
            storage_path=_store(uploads_dir, org_id, document_id, source),
            ingest_status=e.IngestStatus.QUEUED.value,
            classification_confirmed=False,
        )
        session.add(document)
        session.flush()
        job = enqueue_ingest(session, document, actor)
        session.commit()
        job_ids[filename] = job.id
        t0 = time.perf_counter()
        drain_jobs(session)
        timings[filename] = time.perf_counter() - t0
        documents[filename] = document
        logger.info(
            "ingested %s -> %s in %.2fs", filename, document.ingest_status, timings[filename]
        )

    # Confirm every classification, as a user would, so supersession is evaluated.
    for filename, document in documents.items():
        session.refresh(document)
        if document.doc_type is None:
            logger.warning("%s has no classification to confirm", filename)
            continue
        confirm_document(session, document, actor)
        session.commit()

    result = LibraryIngestion(documents_by_filename=documents)
    by_id = {document.id: filename for filename, document in documents.items()}
    for filename, document in documents.items():
        session.refresh(document)
        item_rows = session.execute(
            select(KnowledgeItem.item_type, func.count(KnowledgeItem.id))
            .where(KnowledgeItem.document_id == document.id)
            .group_by(KnowledgeItem.item_type)
        ).all()
        facts = session.scalar(
            select(func.count(Fact.id)).where(Fact.document_id == document.id)
        )
        result.rows.append(
            {
                "filename": filename,
                "document_id": str(document.id),
                "role": next(
                    entry["role"]
                    for entry in manifest["documents"]
                    if entry["filename"] == filename
                ),
                "doc_type": document.doc_type,
                "doc_kind": document.doc_kind,
                "effective_date": (
                    document.effective_date.isoformat() if document.effective_date else None
                ),
                "effective_date_source": document.effective_date_source,
                "buyer": document.buyer,
                "ingest_status": document.ingest_status,
                "ingest_error": document.ingest_error,
                "classification_confirmed": document.classification_confirmed,
                "superseded_by": (
                    by_id.get(document.superseded_by) if document.superseded_by else None
                ),
                "items": {str(item_type): int(count) for item_type, count in item_rows},
                "facts": int(facts or 0),
                "seconds": round(timings[filename], 3),
                "job_id": str(job_ids[filename]),
            }
        )
    for job in session.scalars(
        select(Job).where(Job.status == e.JobStatus.FAILED.value).order_by(Job.created_at)
    ).all():
        result.failures.append({"job_id": str(job.id), "kind": job.kind, "error": job.error})

    decisions = session.scalars(select(SupersessionDecision)).all()
    result.supersession = {
        "superseded_documents": [
            {"filename": row["filename"], "superseded_by": row["superseded_by"]}
            for row in result.rows
            if row["superseded_by"]
        ],
        "decisions": [
            {
                "doc_kind": decision.doc_kind,
                "document_a": by_id.get(decision.document_a_id, str(decision.document_a_id)),
                "document_b": by_id.get(decision.document_b_id, str(decision.document_b_id)),
                "reason": decision.reason,
                "decision": decision.decision,
                "superseding": by_id.get(decision.superseding_document_id)
                if decision.superseding_document_id
                else None,
            }
            for decision in decisions
        ],
        "facts_total": int(session.scalar(select(func.count(Fact.id))) or 0),
        "facts_superseded": int(
            session.scalar(select(func.count(Fact.id)).where(Fact.superseded_by.isnot(None))) or 0
        ),
    }
    result.seconds = round(time.perf_counter() - started, 3)
    return result


# --- The tender pass ------------------------------------------------------------------------------


@dataclass
class TenderPass:
    tender_id: uuid.UUID
    buyer: str | None
    questions: list[QuestionRun]
    time_to_board_seconds: float
    extract_seconds: float | None
    triage_seconds: float | None
    jobs_run: int
    failures: list[dict[str, Any]] = field(default_factory=list)


def _retrieved_item_ids(session: Session, question: Question, answer_id: uuid.UUID) -> list[str]:
    """The eight candidates synthesis received, from the draft's ``answer_created`` event."""
    events = session.scalars(
        select(Event)
        .where(
            Event.entity_type == e.EntityType.QUESTION.value,
            Event.entity_id == question.id,
            Event.event_type == e.EventType.ANSWER_CREATED.value,
        )
        .order_by(Event.created_at.desc())
    ).all()
    for event in events:
        payload = event.payload or {}
        if payload.get("answer_id") == str(answer_id):
            return [str(item_id) for item_id in payload.get("retrieved_item_ids") or []]
    return []


def _question_run(question: Question) -> QuestionRun:
    return QuestionRun(
        question_id=question.id,
        section=question.section,
        number=question.number,
        text=question.text,
        response_type=question.response_type,
        mandatory=bool(question.mandatory),
        word_limit=question.word_limit,
        topics=list(question.topics or []),
        coverage=question.coverage,
        coverage_detail=dict(question.coverage_detail or {}),
    )


def draft_every_question(
    session: Session, questions: Sequence[Question], *, actor: str
) -> list[QuestionRun]:
    """The plan's rule for the harness: every question is drafted regardless of coverage;
    pricing questions are never drafted and are recorded as skipped."""
    runs: list[QuestionRun] = []
    for question in questions:
        run = _question_run(question)
        runs.append(run)
        if question.response_type == e.ResponseType.PRICING.value:
            run.outcome = OUTCOME_SKIPPED_PRICING
            run.detail = "pricing questions are never drafted"
            continue
        started = time.perf_counter()
        try:
            answer = pipeline.draft_question(session, question, actor=actor)
            session.commit()
        except Conflict409 as exc:
            session.rollback()
            run.outcome = OUTCOME_FAILED
            run.detail = f"{exc.code}: {exc.message}"
            run.draft_seconds = time.perf_counter() - started
            continue
        except Exception as exc:  # noqa: BLE001 - one failed draft never ends the run
            session.rollback()
            logger.exception("draft failed for %s %s", question.section, question.number)
            run.outcome = OUTCOME_FAILED
            run.detail = f"{type(exc).__name__}: {exc}"[:1000]
            run.draft_seconds = time.perf_counter() - started
            continue
        run.draft_seconds = time.perf_counter() - started
        run.answer_id = str(answer.id)
        run.answer_text = answer.text
        run.segments = list(answer.segments or [])
        run.gaps = list(answer.gaps or [])
        run.fact_checklist = list(answer.fact_checklist or [])
        run.support_summary = dict(answer.support_summary or {})
        run.retrieved_item_ids = _retrieved_item_ids(session, question, answer.id)
        run.verbatim_offer_item_id = (
            str(answer.verbatim_offer_item_id) if answer.verbatim_offer_item_id else None
        )
        run.model = answer.model
        run.prompt_version = answer.prompt_version
    return runs


def run_tender_pass(
    session: Session,
    manifest: Mapping[str, Any],
    data_dir: Path,
    uploads_dir: Path,
    *,
    org_id: uuid.UUID,
    actor: str,
    label: str,
    now: datetime,
) -> TenderPass:
    pack = next(entry for entry in manifest["documents"] if entry.get("role") == "question_pack")
    source = data_dir / str(pack["filename"])
    if not source.exists():
        raise HarnessError(f"question pack {pack['filename']} is not in {data_dir}")
    buyer = (pack.get("expected") or {}).get("buyer")
    tender = Tender(
        org_id=org_id,
        name=f"Harness tender ({label})",
        buyer=buyer,
        deadline=now + timedelta(days=21),
        status=e.TenderStatus.OPEN.value,
        outcome=e.TenderOutcome.PENDING.value,
    )
    session.add(tender)
    session.flush()
    document_id = uuid.uuid4()
    document = Document(
        id=document_id,
        org_id=org_id,
        filename=str(pack["filename"]),
        storage_path=_store(uploads_dir, org_id, document_id, source),
        doc_type=e.DocType.TENDER_DOCUMENT.value,
        tender_id=tender.id,
        tender_doc_kind=e.TenderDocKind.QUESTION_PACK.value,
        classification_confirmed=True,
        effective_date=now.date(),
        effective_date_source=e.EffectiveDateSource.UPLOAD_TIME.value,
        ingest_status=e.IngestStatus.QUEUED.value,
    )
    session.add(document)
    session.flush()
    extract_job = enqueue(
        session,
        e.JobKind.EXTRACT_QUESTIONS,
        {"tender_id": str(tender.id), "document_id": str(document.id), "actor": actor},
        org_id=org_id,
    )
    tender.extract_job_id = extract_job.id
    session.commit()

    t0 = time.perf_counter()
    jobs_run = drain_jobs(session)
    time_to_board = time.perf_counter() - t0
    session.expire_all()
    extract_job = session.get(Job, extract_job.id)
    triage_job = (
        session.get(Job, extract_job.next_job_id)
        if extract_job is not None and extract_job.next_job_id
        else None
    )
    failures = [
        {"job_id": str(job.id), "kind": job.kind, "error": job.error}
        for job in (extract_job, triage_job)
        if job is not None and job.status == e.JobStatus.FAILED.value
    ]
    questions = list(
        session.scalars(
            select(Question).where(Question.tender_id == tender.id).order_by(Question.order_index)
        ).all()
    )
    runs = draft_every_question(session, questions, actor=actor)
    return TenderPass(
        tender_id=tender.id,
        buyer=buyer,
        questions=runs,
        time_to_board_seconds=time_to_board,
        extract_seconds=_job_seconds(extract_job),
        triage_seconds=_job_seconds(triage_job),
        jobs_run=jobs_run,
        failures=failures,
    )


# --- The run --------------------------------------------------------------------------------------


def _clock(today: date | datetime | None) -> datetime:
    if today is None:
        return datetime.now(UTC)
    if isinstance(today, datetime):
        return today if today.tzinfo else today.replace(tzinfo=UTC)
    return datetime.combine(today, dt_time(0, 0), tzinfo=UTC)


def _run_id(out_dir: Path, now: datetime) -> str:
    base = now.strftime("%Y%m%dT%H%M%SZ")
    run_id = base
    suffix = 2
    while (out_dir / f"{run_id}.md").exists() or (out_dir / f"{run_id}.json").exists():
        run_id = f"{base}-{suffix}"
        suffix += 1
    return run_id


def _configurations(toggles: Toggles | Sequence[Toggles] | None) -> list[Toggles]:
    if toggles is None:
        return [Toggles()]
    if isinstance(toggles, Toggles):
        return [toggles]
    configurations = list(toggles)
    if not configurations:
        return [Toggles()]
    seen: dict[str, Toggles] = {}
    for configuration in configurations:
        seen.setdefault(configuration.label, configuration)
    return list(seen.values())


def _resolve_dir(path: Path | str, default: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    if (Path.cwd() / candidate).exists():
        return (Path.cwd() / candidate).resolve()
    if (REPO_ROOT / candidate).exists():
        return (REPO_ROOT / candidate).resolve()
    return (Path.cwd() / candidate).resolve() if candidate != default else default


def _llm_call_counts() -> dict[str, int] | None:
    calls = getattr(get_llm(), "calls", None)
    if calls is None:
        return None
    counts: dict[str, int] = {}
    for call in calls:
        counts[call.name] = counts.get(call.name, 0) + 1
    return counts


def _ground_truth_summary(ground_truth: Mapping[str, Any]) -> dict[str, Any]:
    questions = list(ground_truth.get("questions", []))
    return {
        "counterpart": sum(1 for q in questions if q.get("label") == "counterpart"),
        "new": sum(1 for q in questions if q.get("label") == "new"),
        "with_held_out_answer": sum(1 for q in questions if q.get("held_out_answer")),
        "pricing": sum(1 for q in questions if q.get("response_type") == "pricing"),
    }


def run(
    data_dir: Path | str = DEFAULT_DATA_DIR,
    out_dir: Path | str = DEFAULT_OUT_DIR,
    *,
    database_url: str | None = None,
    toggles: Toggles | Sequence[Toggles] | None = None,
    labels_dir: Path | str = DEFAULT_LABELS_DIR,
    today: date | datetime | None = None,
    judge: bool = True,
    rerank_pool: int = DEFAULT_RERANK_POOL,
    actor: str = ACTOR,
) -> Report:
    """Run the harness once and write the report. See the module docstring for the steps.

    ``toggles`` is one ``Toggles``, a list of them (one tender pass each, one table row each)
    or ``None`` for the baseline. ``today`` fixes the run's clock (report name, tender deadline,
    pack upload date); the pipeline's own date checks use the real clock.
    """
    data_dir = _resolve_dir(data_dir, DEFAULT_DATA_DIR)
    out_dir = Path(out_dir) if Path(out_dir).is_absolute() else (Path.cwd() / out_dir).resolve()
    labels_dir = _resolve_dir(labels_dir, DEFAULT_LABELS_DIR)
    manifest_path = data_dir / "manifest.json"
    ground_truth_path = data_dir / "ground_truth.json"
    if not manifest_path.exists() or not ground_truth_path.exists():
        raise HarnessError(
            f"{data_dir} has no manifest.json and ground_truth.json; run "
            "python -m eval.generate_synthetic --out eval/data first"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    truth_by_key = ground_truth_by_key(ground_truth)
    labels: Labels = load_labels(labels_dir)
    configurations = _configurations(toggles)

    settings = get_settings()
    _check_providers(settings)
    fake_provider = settings.llm_provider == "fake"
    now = _clock(today)
    started = time.perf_counter()
    _register_handlers()

    def _execute() -> Report:
        with (
            open_database(database_url) as db,
            tempfile.TemporaryDirectory(prefix="tenders-eval-uploads-") as uploads,
        ):
            uploads_dir = Path(uploads)
            session = db.session_factory()
            try:
                org_id = settings.default_org_id
                ingestion = ingest_library(
                    session, manifest, data_dir, uploads_dir, org_id=org_id, actor=actor
                )
                resolver = ItemResolver(session, ingestion.documents_by_filename)
                extraction = extraction_metrics(
                    session,
                    manifest["documents"],
                    ingestion.documents_by_filename,
                    resolver,
                    fidelity_target=settings.extraction_fidelity_target,
                )
                configuration_blocks: list[dict[str, Any]] = []
                first_runs: list[QuestionRun] | None = None
                for configuration in configurations:
                    with (
                        apply_toggles(configuration, rerank_pool=rerank_pool) as applied,
                        _triage_sessions(db.session_factory),
                    ):
                        tender_pass = run_tender_pass(
                            session,
                            manifest,
                            data_dir,
                            uploads_dir,
                            org_id=org_id,
                            actor=actor,
                            label=configuration.label,
                            now=now,
                        )
                    runs = tender_pass.questions
                    if first_runs is None:
                        first_runs = runs
                    metrics = {
                        "retrieval": recall_at_k(
                            runs, truth_by_key, resolver, k=settings.top_k_synthesis
                        ),
                        "coverage": coverage_metrics(
                            runs, truth_by_key, labels, coverage_floor=settings.coverage_floor
                        ),
                        "draft_quality": draft_quality_metrics(
                            runs,
                            truth_by_key,
                            buyer=tender_pass.buyer,
                            judge=judge,
                            fake_provider=fake_provider,
                        ),
                        "traceability": traceability_metrics(runs, labels),
                        "latency": latency_metrics(
                            runs,
                            time_to_triaged_board_seconds=tender_pass.time_to_board_seconds,
                            extract_seconds=tender_pass.extract_seconds,
                            triage_seconds=tender_pass.triage_seconds,
                            question_count=len(runs),
                        ),
                    }
                    pack_keys = {run.key for run in runs}
                    configuration_blocks.append(
                        {
                            "label": configuration.label,
                            "toggles": configuration.as_dict(),
                            "baseline": configuration.is_baseline,
                            "patched": list(applied.patched),
                            "rerank": applied.rerank.as_dict() if applied.rerank else None,
                            "tender_id": str(tender_pass.tender_id),
                            "question_count": len(runs),
                            "jobs_run": tender_pass.jobs_run,
                            "job_failures": tender_pass.failures,
                            "pack_rows_missing": sorted(
                                f"{key[0]} {key[1]}" for key in truth_by_key if key not in pack_keys
                            ),
                            "pack_rows_unexpected": sorted(
                                f"{key[0]} {key[1]}" for key in pack_keys if key not in truth_by_key
                            ),
                            "metrics": metrics,
                            "questions": [run.summary() for run in runs],
                        }
                    )
                    logger.info(
                        "configuration %s: recall@8 %s, supported share %s",
                        configuration.label,
                        metrics["retrieval"]["value"],
                        metrics["traceability"]["supported_share"],
                    )
                spot_checks = sample_spot_checks(
                    first_runs or [], size=SPOT_CHECK_SIZE, seed=int(manifest.get("seed") or 0)
                )
            finally:
                session.close()

            duration = time.perf_counter() - started
            data: dict[str, Any] = {
                "synthetic_data": True,
                "benchmark_policy": BENCHMARK_POLICY,
                "run": {
                    "id": _run_id(out_dir, now),
                    "timestamp": now.isoformat(timespec="seconds"),
                    "today": now.date().isoformat(),
                    "clock": "fixed by the today argument" if today is not None else "wall clock",
                    "duration_seconds": round(duration, 3),
                    "data_dir": str(data_dir),
                    "out_dir": str(out_dir),
                    "database": db.description,
                    "actor": actor,
                    "judge_enabled": judge,
                    "llm_calls": _llm_call_counts(),
                    "fake_provider_note": FAKE_JUDGE_NOTE if fake_provider else None,
                },
                "providers": _provider_info(settings),
                "models": {"main": settings.model_main, "fast": settings.model_fast},
                "prompt_versions": _prompt_versions(),
                "thresholds": _thresholds(settings, rerank_pool),
                "data": {
                    "manifest_generated_at": manifest.get("generated_at"),
                    "seed": manifest.get("seed"),
                    "mode": manifest.get("mode"),
                    "supplier": manifest.get("supplier"),
                    "note": manifest.get("note"),
                    "held_out_submission": ground_truth.get("held_out_submission"),
                    "ingested_documents": [
                        entry["filename"]
                        for entry in manifest["documents"]
                        if entry.get("role") in LIBRARY_ROLES
                    ],
                    "question_pack": ground_truth.get("question_pack"),
                    "question_count": len(truth_by_key),
                    "ground_truth": _ground_truth_summary(ground_truth),
                },
                "ingestion": {
                    "documents": ingestion.rows,
                    "seconds": ingestion.seconds,
                    "failures": ingestion.failures,
                    "supersession": ingestion.supersession,
                },
                "extraction": extraction,
                "labels": labels.as_dict(),
                "configurations": configuration_blocks,
            }
            return write_report(
                data,
                out_dir,
                data["run"]["id"],
                spot_checks,
                spot_check_configuration=configurations[0].label,
            )

    if fake_provider:
        with _offline_llm():
            return _execute()
    return _execute()


# --- CLI ------------------------------------------------------------------------------------------


def _force_fake_providers() -> None:
    os.environ["LLM_PROVIDER"] = "fake"
    os.environ["EMBEDDING_PROVIDER"] = "fake"
    get_settings.cache_clear()
    reset_llm()
    reset_embedder()


def _print_summary(report: Report) -> None:
    data = report.data
    print(f"SYNTHETIC DATA. Report: {report.markdown_path}")
    print(f"JSON twin: {report.json_path}")
    print(f"Spot-check sample: {report.spotcheck_path}")
    fidelity = data["extraction"]["fidelity"]
    print(
        f"extraction fidelity {fidelity['items_verified']}/{fidelity['items_total']} "
        f"(target {fidelity['target']}, {'met' if fidelity['meets_target'] else 'NOT met'})"
    )
    for configuration in report.configurations:
        metrics = configuration["metrics"]
        retrieval = metrics["retrieval"]
        distribution = metrics["coverage"]["distribution"]
        trace = metrics["traceability"]
        latency = metrics["latency"]
        print(
            f"[{configuration['label']}] recall@8 {retrieval['hits']}/{retrieval['total']}; "
            f"coverage covered {distribution.get('covered', 0)} / partial "
            f"{distribution.get('partial', 0)} / new {distribution.get('new', 0)}; "
            f"supported {trace['supported_share']}; draft p50 {latency['draft_seconds']['p50']}s "
            f"p95 {latency['draft_seconds']['p95']}s; triaged board "
            f"{latency['time_to_triaged_board_seconds']}s"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.harness", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--data", default=str(DEFAULT_DATA_DIR), help="synthetic data directory")
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR), help="reports directory")
    parser.add_argument("--labels", default=str(DEFAULT_LABELS_DIR), help="human labels directory")
    parser.add_argument(
        "--toggle",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="topic_list=on|off, vector_list=max|answer_only, llm_rerank=on|off; repeatable",
    )
    parser.add_argument(
        "--matrix", action="store_true", help="run every on/off combination of the three toggles"
    )
    parser.add_argument("--db", default=None, help="SQLAlchemy URL; default: throwaway pgserver")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="force LLM_PROVIDER=fake and EMBEDDING_PROVIDER=fake (offline, heuristic fake)",
    )
    parser.add_argument("--no-judge", action="store_true", help="skip the LLM judge calls")
    parser.add_argument(
        "--rerank-pool",
        type=int,
        default=DEFAULT_RERANK_POOL,
        help="candidates shown to the rerank",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.fake:
        _force_fake_providers()
    if args.matrix:
        toggles: list[Toggles] = Toggles.matrix()
        if args.toggle:
            requested = Toggles.parse(args.toggle)
            toggles = [requested, *[t for t in toggles if t != requested]]
    else:
        toggles = [Toggles.parse(args.toggle)] if args.toggle else [Toggles()]
    try:
        report = run(
            args.data,
            args.out,
            database_url=args.db,
            toggles=toggles,
            labels_dir=args.labels,
            judge=not args.no_judge,
            rerank_pool=args.rerank_pool,
        )
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
