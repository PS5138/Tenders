"""The ingest_document job: stage progression, parse-only tender documents, resume, failure,
and the lazy calls into owner B's steps 5 to 9."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocumentSection, Fact, KnowledgeItem
from app.ingest import jobs as ingest_jobs
from app.ingest.classify import PROMPT_NAME as CLASSIFY
from app.ingest.extract import PROMPT_NAME as EXTRACT
from app.ingest.jobs import LIBRARY_STAGES, TENDER_STAGES, enqueue_ingest, resume_point, run_ingest
from app.jobs import get_handler
from app.worker import run_once
from tests.test_ingest_builders import (
    A11_LINES,
    Q11,
    anchors,
    build_pack_xlsx,
    build_reference_docx,
    build_submission_docx,
    make_document,
    parse_and_persist,
    section_starting,
)
from tests.test_supersession_factories import (  # noqa: F401 - fixture import
    InvalidationRecorder,
    invalidations_fixture,
    make_fact,
    make_item,
)


@pytest.fixture
def stage_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every ingest_status written as a stage starts."""
    seen: list[str] = []
    original = ingest_jobs._enter_stage

    def recording(session, document, stage):  # noqa: ANN001
        seen.append(stage)
        original(session, document, stage)

    monkeypatch.setattr(ingest_jobs, "_enter_stage", recording)
    return seen


@pytest.fixture
def owner_b_absent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Make every owner-B step look not-yet-landed and record what the handler asked for."""
    requested: list[tuple[str, str]] = []

    def missing(module: str, name: str):  # noqa: ANN202
        requested.append((module, name))
        return None

    monkeypatch.setattr(ingest_jobs, "_optional", missing)
    return requested


def _script_submission(fake_llm, db_session: Session) -> None:
    fake_llm.register(
        CLASSIFY,
        {"doc_type": "past_submission", "effective_date": "2025-03-14", "buyer": "Barts"},
    )

    def extract(user: str, **_: object) -> dict:
        sections = db_session.scalars(select(DocumentSection)).all()
        s11 = section_starting(sections, Q11)
        answer = "\n".join(A11_LINES)
        q_start, q_end = anchors(Q11)
        a_start, a_end = anchors(answer)
        return {
            "pairs": [
                {
                    "question_section_id": str(s11.id),
                    "answer_section_id": str(s11.id),
                    "question_start_anchor": q_start,
                    "question_end_anchor": q_end,
                    "answer_start_anchor": a_start,
                    "answer_end_anchor": a_end,
                    "question_text_copy": Q11,
                    "answer_text_copy": answer,
                    "topics": ["clinical_safety"],
                    "facts": [
                        {
                            "fact_kind": "clinical_safety_officer",
                            "statement": A11_LINES[1],
                            "value": "Dr Amira Patel",
                        }
                    ],
                }
            ],
            "fragments": [],
        }

    fake_llm.register(EXTRACT, extract)


def test_handler_is_registered() -> None:
    assert get_handler("ingest_document") is ingest_jobs.ingest_document


def test_library_document_runs_every_stage_to_ready(
    db_session: Session, fake_llm, tmp_path: Path, stage_log, owner_b_absent
) -> None:
    path = build_submission_docx(tmp_path / "submission.docx")
    document = make_document(db_session, path)
    _script_submission(fake_llm, db_session)
    job = enqueue_ingest(db_session, document, "test user")
    assert job.total == 5 and job.payload == {"document_id": str(document.id), "actor": "test user"}

    assert run_once(db_session) is True

    assert stage_log == list(LIBRARY_STAGES)
    db_session.refresh(document)
    assert document.ingest_status == "ready" and document.ingest_error is None
    assert document.doc_type == "past_submission" and document.buyer == "Barts"
    assert job.status == "done" and job.done == 5 and job.total == 5
    assert job.results == [], "ingest_document reports the stage through ingest_status"
    assert db_session.scalars(
        select(DocumentSection).where(DocumentSection.document_id == document.id)
    ).first() is not None
    items = db_session.scalars(
        select(KnowledgeItem).where(KnowledgeItem.document_id == document.id)
    ).all()
    assert len(items) == 1 and items[0].text_verified

    # Steps 5 to 9 were requested from owner B's modules in pipeline order and skipped cleanly.
    assert owner_b_absent == [
        ("app.ingest.facts", "persist_facts"),
        ("app.ingest.embed", "embed_items"),
        ("app.ingest.dedup", "deduplicate"),
        ("app.ingest.facts", "evaluate_fact_supersession"),
        ("app.ingest.supersession", "evaluate_document_supersession"),
    ]
    assert [c.name for c in fake_llm.calls] == [CLASSIFY, EXTRACT]


def test_reference_document_is_chunked(
    db_session: Session, fake_llm, tmp_path: Path, stage_log, owner_b_absent
) -> None:
    path = build_reference_docx(tmp_path / "iso.docx")
    document = make_document(db_session, path)
    fake_llm.register(CLASSIFY, {"doc_type": "reference", "doc_kind": "iso_27001"})
    fake_llm.register("chunk_annotate", {"chunks": []})
    enqueue_ingest(db_session, document, "test user")

    assert run_once(db_session) is True

    db_session.refresh(document)
    assert document.ingest_status == "ready"
    assert document.effective_date_source == "upload_time"
    items = db_session.scalars(
        select(KnowledgeItem).where(KnowledgeItem.document_id == document.id)
    ).all()
    assert items and all(i.item_type == "chunk" and i.text_verified for i in items)
    assert stage_log == list(LIBRARY_STAGES)


def test_tender_document_is_parse_only(
    db_session: Session, fake_llm, tmp_path: Path, stage_log, owner_b_absent
) -> None:
    path = build_pack_xlsx(tmp_path / "spec.xlsx")
    document = make_document(
        db_session,
        path,
        doc_type="tender_document",
        tender_doc_kind="specification",
        classification_confirmed=True,
    )
    job = enqueue_ingest(db_session, document, "test user")
    assert job.total == 1

    assert run_once(db_session) is True

    assert stage_log == list(TENDER_STAGES) == ["parsing"]
    db_session.refresh(document)
    assert document.ingest_status == "ready"
    assert job.status == "done" and job.done == 1
    assert fake_llm.calls == [], "no LLM call for a parse-only document"
    assert owner_b_absent == [], "steps 5 to 9 never run for tender documents"
    sections = db_session.scalars(
        select(DocumentSection).where(DocumentSection.document_id == document.id)
    ).all()
    assert len(sections) == 4


def test_resume_starts_at_recorded_stage_after_cleaning(
    db_session: Session, fake_llm, tmp_path: Path, stage_log, owner_b_absent
) -> None:
    path = build_submission_docx(tmp_path / "submission.docx")
    document = make_document(
        db_session,
        path,
        doc_type="past_submission",
        ingest_status="extracting",
        effective_date_source="upload_time",
    )
    parse_and_persist(db_session, document)
    _script_submission(fake_llm, db_session)
    job = enqueue_ingest(db_session, document, "test user")

    run_ingest(db_session, document, job)

    assert stage_log == ["extracting", "embedding", "linking"]
    assert owner_b_absent[0] == ("app.ingest.corrections", "rerun_from_stage")
    assert [c.name for c in fake_llm.calls] == [EXTRACT], "parsing and classifying not re-run"
    assert document.ingest_status == "ready" and job.done == 5

    # Sections were reused, not duplicated.
    count = len(
        db_session.scalars(
            select(DocumentSection).where(DocumentSection.document_id == document.id)
        ).all()
    )
    assert count == len(parse_and_persist(db_session, document))


def test_resume_at_extracting_runs_the_delete_then_re_run_in_the_worker(
    db_session: Session,
    fake_llm,
    fake_embeddings,
    tmp_path: Path,
    stage_log,
    invalidations: InvalidationRecorder,
) -> None:
    """The step 9 clean is the worker's alone: a document left at ``extracting`` (by a PATCH
    of doc_type or a failed attempt) loses its earlier items and facts, with invalidation,
    before the stage re-runs; the request that set the stage deleted nothing."""
    path = build_submission_docx(tmp_path / "submission.docx")
    document = make_document(
        db_session,
        path,
        doc_type="past_submission",
        ingest_status="extracting",
        effective_date_source="upload_time",
        classification_confirmed=True,
    )
    sections = parse_and_persist(db_session, document)
    old_item = make_item(db_session, document, sections[0], question_text="Old question?")
    old_fact = make_fact(db_session, document, sections[0], item=old_item)
    _script_submission(fake_llm, db_session)
    job = enqueue_ingest(db_session, document, "test user")

    run_ingest(db_session, document, job)

    assert stage_log == ["extracting", "embedding", "linking"]
    assert document.ingest_status == "ready" and job.done == 5
    assert db_session.get(KnowledgeItem, old_item.id) is None
    assert db_session.get(Fact, old_fact.id) is None
    assert invalidations.of("knowledge_item", "removed") == [old_item.id]
    assert invalidations.of("fact", "removed") == [old_fact.id]
    items = db_session.scalars(
        select(KnowledgeItem).where(KnowledgeItem.document_id == document.id)
    ).all()
    assert [item.question_text for item in items] == [Q11], "only the re-extracted item remains"
    facts = db_session.scalars(select(Fact).where(Fact.document_id == document.id)).all()
    assert [fact.fact_kind for fact in facts] == ["clinical_safety_officer"]


def test_resume_point_rules() -> None:
    class Doc:
        def __init__(self, status: str) -> None:
            self.ingest_status = status

    assert resume_point(Doc("queued"), LIBRARY_STAGES) == (0, None)
    assert resume_point(Doc("parsing"), LIBRARY_STAGES) == (0, None)
    assert resume_point(Doc("classifying"), LIBRARY_STAGES) == (1, None)
    assert resume_point(Doc("extracting"), LIBRARY_STAGES) == (2, "extracting")
    assert resume_point(Doc("linking"), LIBRARY_STAGES) == (4, "linking")
    assert resume_point(Doc("failed"), LIBRARY_STAGES) == (0, "extracting")
    assert resume_point(Doc("failed"), TENDER_STAGES) == (0, None)


@pytest.mark.integration
def test_end_to_end_with_owner_b_steps(
    db_session: Session, fake_llm, fake_embeddings, tmp_path: Path, stage_log
) -> None:
    """Steps 1 to 8 for real: parse, classify, extract, persist facts, embed, deduplicate."""
    try:
        import app.ingest.dedup  # noqa: F401
        import app.ingest.embed  # noqa: F401
        import app.ingest.facts  # noqa: F401
        import app.ingest.supersession  # noqa: F401
    except ImportError as exc:  # pragma: no cover - owner B not landed yet
        pytest.skip(f"owner B modules not available: {exc}")
    from app.db.models import Fact

    path = build_submission_docx(tmp_path / "submission.docx")
    document = make_document(db_session, path)
    _script_submission(fake_llm, db_session)
    job = enqueue_ingest(db_session, document, "test user")

    assert run_once(db_session) is True

    db_session.refresh(document)
    assert document.ingest_status == "ready", job.error
    assert stage_log == list(LIBRARY_STAGES)
    items = db_session.scalars(
        select(KnowledgeItem).where(KnowledgeItem.document_id == document.id)
    ).all()
    assert len(items) == 1
    item = items[0]
    assert item.answer_embedding is not None and item.question_embedding is not None
    assert item.is_canonical is True and item.canonical_id is None
    facts = db_session.scalars(select(Fact).where(Fact.document_id == document.id)).all()
    assert [f.fact_kind for f in facts] == ["clinical_safety_officer"]
    assert facts[0].knowledge_item_id == item.id and facts[0].section_id == item.section_id
    assert facts[0].effective_date == document.effective_date, "inherited from the document"


def test_missing_file_fails_the_attempt_and_requeues(
    db_session: Session, fake_llm, tmp_path: Path, owner_b_absent
) -> None:
    document = make_document(db_session, tmp_path / "missing.docx")
    job = enqueue_ingest(db_session, document, "test user")

    assert run_once(db_session) is True

    assert job.status == "queued", "below max_attempts the job goes back to the queue"
    assert job.attempts == 1
    assert "missing.docx" in (job.error or "")
    db_session.refresh(document)
    assert document.ingest_status == "parsing", "the stage that was running is recorded"
