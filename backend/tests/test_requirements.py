"""Specification requirements: the ``extract_requirements`` job (windows, trace, overlap
collapse, idempotent re-runs that keep the human fields), the AI suggestion (floor, verified
evidence), the triggers that enqueue the scan, a synthetic organisation running the whole job on
the heuristic double, and the API (list, rating, re-scan, tender counts, org scoping, delete)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID, Settings
from app.db.models import (
    Document,
    DocumentSection,
    Event,
    Job,
    KnowledgeItem,
    Organisation,
    Requirement,
    Tender,
)
from app.ingest.extract import estimate_tokens
from app.jobs import enqueue
from app.llm import embed
from app.llm.client import reset_llm
from app.llm.embeddings import reset_embedder
from app.llm.scope import org_scope
from app.worker import run_job, run_once

# Imported by the autouse fixture rather than at module level, so the handler is registered
# when these tests run, not when they are collected (see test_triage_jobs.py).
ingest_requirements: Any = None
suggestions: Any = None


@pytest.fixture(autouse=True)
def _load_modules() -> None:
    global ingest_requirements, suggestions
    import app.ingest.jobs  # noqa: F401 - registers ingest_document
    from app.ingest import requirements as extraction
    from app.retrieve import requirements as suggestion

    ingest_requirements = extraction
    suggestions = suggestion


class InlineExecutor(Executor):
    def __init__(self, max_workers: int = 1) -> None:
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
def inline_pool(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The suggestion pool runs inline, each unit on a savepoint session of the test's
    transaction."""

    def savepoint_session() -> Session:
        return Session(
            bind=db_session.get_bind(),
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )

    monkeypatch.setattr(suggestions, "_make_executor", lambda max_workers: InlineExecutor())
    monkeypatch.setattr(suggestions, "new_session", savepoint_session)


# --- Builders ---------------------------------------------------------------------------------


def make_tender(session: Session, org_id: uuid.UUID = DEFAULT_ORG_ID) -> Tender:
    tender = Tender(org_id=org_id, name="Clinical messaging", buyer="NHS Test ICB")
    session.add(tender)
    session.flush()
    return tender


def make_tender_document(
    session: Session,
    tender: Tender,
    *,
    kind: str = "specification",
    filename: str = "specification.docx",
    status: str = "ready",
) -> Document:
    document = Document(
        org_id=tender.org_id,
        filename=filename,
        storage_path=f"/tmp/{uuid.uuid4()}",
        doc_type="tender_document",
        tender_id=tender.id,
        tender_doc_kind=kind,
        classification_confirmed=True,
        ingest_status=status,
    )
    session.add(document)
    session.flush()
    return document


def add_section(
    session: Session, document: Document, body: str, order_index: int, heading: str = ""
) -> DocumentSection:
    section = DocumentSection(
        org_id=document.org_id,
        document_id=document.id,
        order_index=order_index,
        heading_path=[heading] if heading else [],
        text=body,
    )
    session.add(section)
    session.flush()
    return section


def add_library_item(session: Session, org_id: uuid.UUID, answer: str) -> KnowledgeItem:
    document = Document(
        org_id=org_id,
        filename="security-policy.docx",
        storage_path=f"/tmp/{uuid.uuid4()}",
        doc_type="reference",
        doc_kind="information_security_policy",
        classification_confirmed=True,
        effective_date=date(2025, 3, 1),
        effective_date_source="extracted",
        ingest_status="ready",
    )
    session.add(document)
    session.flush()
    section = add_section(session, document, f"Hosting\n{answer}", 0, "Hosting")
    start = section.text.index(answer)
    item = KnowledgeItem(
        org_id=org_id,
        document_id=document.id,
        section_id=section.id,
        answer_start=start,
        answer_end=start + len(answer),
        item_type="chunk",
        answer_text=answer,
        text_verified=True,
        topics=["information_security"],
        is_canonical=True,
        answer_embedding=embed([answer])[0],
    )
    session.add(item)
    session.flush()
    return item


def make_requirement(
    session: Session, tender: Tender, section: DocumentSection, body: str, **fields: Any
) -> Requirement:
    start = section.text.find(body)
    located = start >= 0
    requirement = Requirement(
        org_id=tender.org_id,
        tender_id=tender.id,
        document_id=section.document_id,
        section_id=section.id,
        start=start if located else None,
        end=start + len(body) if located else None,
        key=f"{section.id}:{start}-{start + len(body)}" if located else f"{section.id}:t:x",
        text=body,
        order_index=fields.pop("order_index", 0),
        topics=fields.pop("topics", []),
        suggestion=fields.pop("suggestion", {}),
        **fields,
    )
    session.add(requirement)
    session.flush()
    return requirement


def dispatch(session: Session, job: Job) -> Job:
    """Claim this job the way the worker would and run it through the registry."""
    job.status = "running"
    job.attempts += 1
    session.flush()
    run_job(session, job)
    session.refresh(job)
    return job


def run_scan(session: Session, tender: Tender) -> Job:
    """Enqueue a requirement scan and dispatch it as the worker would."""
    return dispatch(session, ingest_requirements.request_requirements_scan(session, tender, "x"))


def rows_of(session: Session, tender: Tender) -> list[Requirement]:
    session.expire_all()
    return list(
        session.scalars(
            select(Requirement)
            .where(Requirement.tender_id == tender.id)
            .order_by(Requirement.order_index)
        ).all()
    )


S1 = "1 Hosting\nThe supplier must host all patient data in UK data centres."
S2 = "2 Availability\nThe service must achieve 99.9% availability each month."
S3 = "3 Support\nThe supplier should provide a service desk from 8am to 6pm."
R1 = "The supplier must host all patient data in UK data centres."
R2 = "The service must achieve 99.9% availability each month."
R3 = "The supplier should provide a service desk from 8am to 6pm."
_SECTION_ID = re.compile(r"^<<< section id=(\S+) \|", re.MULTILINE)


@pytest.fixture
def specification(db_session: Session) -> tuple[Tender, list[DocumentSection]]:
    tender = make_tender(db_session)
    document = make_tender_document(db_session, tender)
    sections = [
        add_section(db_session, document, body, index, body.split("\n")[0])
        for index, body in enumerate((S1, S2, S3))
    ]
    return tender, sections


def scripted(
    responses: dict[uuid.UUID, list[dict[str, Any]]], calls: list[list[str]]
) -> Callable[..., dict[str, Any]]:
    """An extraction double: every section in the window contributes its scripted entries."""

    def respond(*, user: str, **_: object) -> dict[str, Any]:
        ids = _SECTION_ID.findall(user)
        calls.append(ids)
        found: list[dict[str, Any]] = []
        for section_id in ids:
            found.extend(responses.get(uuid.UUID(section_id), []))
        return {"requirements": found}

    return respond


def two_windows(settings_override: Callable[..., Settings]) -> None:
    """Windows of [S1, S2] and [S2, S3]: S2 is the overlap."""
    cost = [estimate_tokens(body) + 30 for body in (S1, S2, S3)]
    settings_override(pair_extraction_window_tokens=cost[0] + cost[1])


# --- Schema -------------------------------------------------------------------------------------


def test_head_schema_carries_the_requirement_values(db_session: Session) -> None:
    def constraint(name: str) -> str:
        return db_session.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
            {"name": name},
        ).scalar_one()

    assert "'extract_requirements'" in constraint("ck_jobs_kind")
    assert "'requirement'" in constraint("ck_events_entity_type")
    assert "'requirement_rated'" in constraint("ck_events_event_type")
    columns = set(
        db_session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'tenders'")
        ).scalars()
    )
    assert "requirements_job_id" in columns


# --- Extraction -----------------------------------------------------------------------------------


def test_extraction_locates_spans_and_collapses_overlap_duplicates(
    db_session: Session,
    specification,  # noqa: ANN001
    fake_llm,  # noqa: ANN001
    fake_embeddings,  # noqa: ANN001
    inline_pool,  # noqa: ANN001
    settings_override: Callable[..., Settings],
) -> None:
    tender, (s1, s2, s3) = specification
    two_windows(settings_override)
    calls: list[list[str]] = []
    fake_llm.register(
        "extract_requirements",
        scripted(
            {
                s1.id: [
                    {
                        "section_id": str(s1.id),
                        "ref": "1",
                        "text": R1,
                        "priority": "Mandatory",
                        "topics": ["information_security", "not_a_topic"],
                    },
                    # The section is unknown and the text is nowhere: dropped.
                    {"section_id": str(uuid.uuid4()), "text": "Bidders must use the portal."},
                ],
                # Returned by both windows: one row.
                s2.id: [{"section_id": str(s2.id), "ref": "2", "text": R2, "priority": "M"}],
                s3.id: [
                    {"section_id": str(s3.id), "ref": "3", "text": R3, "priority": "Desirable"},
                    # The named section exists but the text is not in it: kept, unlocated.
                    {"section_id": str(s3.id), "text": "Training must be delivered on site."},
                ],
            },
            calls,
        ),
    )

    job = run_scan(db_session, tender)

    assert job.status == "done", job.error
    assert calls == [[str(s1.id), str(s2.id)], [str(s2.id), str(s3.id)]]
    window_results = [r for r in job.results if "sections" in r]
    assert [r["outcome"] for r in window_results] == ["extracted", "extracted"]
    assert [r["detail"] for r in window_results] == [2, 2], "the overlap duplicate is skipped"
    rows = rows_of(db_session, tender)
    assert [row.text for row in rows] == [R1, R2, R3, "Training must be delivered on site."]
    assert [row.order_index for row in rows] == [0, 1, 2, 3]
    for row, section, body in zip(rows[:3], (s1, s2, s3), (R1, R2, R3), strict=True):
        assert row.section_id == section.id
        assert section.text[row.start : row.end] == body, "the trace lands on the buyer's words"
    unlocated = rows[3]
    assert unlocated.section_id == s3.id and unlocated.start is None and unlocated.end is None
    assert [row.priority for row in rows] == ["must", "must", "should", None]
    assert [row.ref for row in rows] == ["1", "2", "3", None]
    assert rows[0].topics == ["information_security"], "unknown taxonomy ids are dropped"
    assert job.total == 2 + 4 and job.done == 6, "windows, then one suggestion per requirement"
    tender_row = db_session.get(Tender, tender.id)
    assert tender_row.requirements_job_id == job.id


def test_empty_library_hits_the_floor_without_a_judgement_call(
    db_session: Session,
    specification,  # noqa: ANN001
    fake_llm,  # noqa: ANN001
    fake_embeddings,  # noqa: ANN001
    inline_pool,  # noqa: ANN001
) -> None:
    tender, (s1, _, _) = specification
    fake_llm.register(
        "extract_requirements",
        {"requirements": [{"section_id": str(s1.id), "text": R1, "priority": "must"}]},
    )

    job = run_scan(db_session, tender)

    assert job.status == "done", job.error
    assert [call.name for call in fake_llm.calls] == ["extract_requirements"]
    (row,) = rows_of(db_session, tender)
    assert row.suggested_class is None, "absence of evidence is never Red"
    assert row.suggestion["label_source"] == "floor"
    assert row.suggestion["evidence"] == []
    assert row.compliance_class is None
    assert job.results[-1] == {"item_id": str(row.id), "outcome": "none"}


def test_rerun_keeps_human_fields_and_rated_rows_and_drops_untouched_ones(
    db_session: Session,
    specification,  # noqa: ANN001
    fake_llm,  # noqa: ANN001
    fake_embeddings,  # noqa: ANN001
    inline_pool,  # noqa: ANN001
) -> None:
    tender, (s1, s2, s3) = specification
    first = {
        s1.id: [{"section_id": str(s1.id), "text": R1, "priority": "must"}],
        s2.id: [{"section_id": str(s2.id), "text": R2, "priority": "must"}],
        s3.id: [{"section_id": str(s3.id), "text": R3, "priority": "should"}],
    }
    fake_llm.register("extract_requirements", scripted(first, []))
    run_scan(db_session, tender)
    hosting, availability, support = rows_of(db_session, tender)
    hosting.compliance_class = "B"
    hosting.compliant_by = date(2027, 1, 31)
    hosting.comment = "UK region goes live in January."
    hosting.owner = "Sam Patel"
    hosting.rated_by = "Sam Patel"
    hosting.rated_at = hosting.created_at
    db_session.flush()
    ids = (hosting.id, availability.id, support.id)

    # The second run: S1's priority changes, S2 is no longer found, S3 is copied without its
    # final full stop (a different span that overlaps the old one), plus a new requirement.
    second = {
        s1.id: [{"section_id": str(s1.id), "text": R1, "priority": "should"}],
        s3.id: [
            {"section_id": str(s3.id), "text": R3.rstrip("."), "priority": "should"},
            {"section_id": str(s3.id), "text": "3 Support", "priority": None},
        ],
    }
    fake_llm.register("extract_requirements", scripted(second, []))
    job = run_scan(db_session, tender)

    assert job.status == "done", job.error
    rows = rows_of(db_session, tender)
    by_id = {row.id: row for row in rows}
    assert ids[1] not in by_id, "an untouched requirement the run no longer finds is removed"
    kept = by_id[ids[0]]
    assert kept.priority == "should", "extracted fields are refreshed"
    assert (kept.compliance_class, kept.compliant_by, kept.comment, kept.owner, kept.rated_by) == (
        "B",
        date(2027, 1, 31),
        "UK region goes live in January.",
        "Sam Patel",
        "Sam Patel",
    )
    assert ids[2] in by_id, "an overlapping span updates the same row"
    assert by_id[ids[2]].text == R3.rstrip(".")
    assert len(rows) == 3 and len({row.key for row in rows}) == 3

    # A rated requirement survives even when the next run finds nothing at all.
    fake_llm.register("extract_requirements", {"requirements": []})
    run_scan(db_session, tender)
    assert [row.id for row in rows_of(db_session, tender)] == [ids[0]]


def test_a_failed_window_deletes_nothing(
    db_session: Session,
    specification,  # noqa: ANN001
    fake_llm,  # noqa: ANN001
    fake_embeddings,  # noqa: ANN001
    inline_pool,  # noqa: ANN001
    settings_override: Callable[..., Settings],
) -> None:
    tender, (s1, s2, s3) = specification
    two_windows(settings_override)
    fake_llm.register(
        "extract_requirements",
        scripted({s3.id: [{"section_id": str(s3.id), "text": R3}]}, []),
    )
    run_scan(db_session, tender)
    (support,) = rows_of(db_session, tender)

    def flaky(*, user: str, **_: object) -> dict[str, Any]:
        if str(s3.id) in user:
            raise RuntimeError("model unavailable")
        return {"requirements": [{"section_id": str(s1.id), "text": R1}]}

    fake_llm.register("extract_requirements", flaky)
    job = run_scan(db_session, tender)

    assert job.status == "done", job.error
    window_results = [r for r in job.results if "sections" in r]
    assert [r["outcome"] for r in window_results] == ["extracted", "failed"]
    texts = [row.text for row in rows_of(db_session, tender)]
    assert texts == [R1, R3], "the requirement in the failed window's section is kept"
    assert support.id in {row.id for row in rows_of(db_session, tender)}


def test_priority_wording_maps_to_moscow() -> None:
    normalise = ingest_requirements.normalise_priority
    assert [normalise(v) for v in ("Must", "SHALL", "Essential", "mandatory requirement")] == [
        "must"
    ] * 4
    assert [normalise(v) for v in ("should", "Desirable", "S")] == ["should"] * 3
    assert [normalise(v) for v in ("could", "Optional", "nice to have")] == ["could"] * 3
    assert [normalise(v) for v in (None, "", "n/a", "see note")] == [None] * 4


# --- The suggestion -------------------------------------------------------------------------------


HOSTING_ANSWER = (
    "All patient data is hosted in UK data centres certified to ISO 27001. "
    "Our hosting has been UK-only since 2019."
)


def test_judgement_keeps_only_verified_evidence(
    db_session: Session,
    specification,  # noqa: ANN001
    fake_llm,  # noqa: ANN001
    fake_embeddings,  # noqa: ANN001
    settings_override: Callable[..., Settings],
) -> None:
    settings_override(coverage_floor=0.1)
    tender, (s1, _, _) = specification
    item = add_library_item(db_session, DEFAULT_ORG_ID, HOSTING_ANSWER)
    requirement = make_requirement(db_session, tender, s1, R1, topics=["information_security"])
    good = "All patient data is hosted in UK data centres certified to ISO 27001."
    fake_llm.register(
        "requirement_judgement",
        {
            "suggested_class": "A",
            "rationale": "The library states UK hosting.",
            "evidence": [
                {"source": "C1", "quote": good},
                {"source": "C1", "quote": "We operate a data centre on the Moon."},
                {"source": "C9", "quote": good},
            ],
        },
    )

    result = suggestions.suggest_for_requirement(db_session, requirement, embed([R1])[0])

    assert result.suggested_class == "A"
    assert result.suggestion["label_source"] == "llm"
    assert result.suggestion["rationale"] == "The library states UK hosting."
    (evidence,) = result.suggestion["evidence"]
    assert evidence["source_type"] == "knowledge_item"
    assert evidence["source_id"] == str(item.id)
    section = db_session.get(DocumentSection, item.section_id)
    locator = evidence["locator"]
    assert section.text[locator["start"] : locator["end"]] == good
    assert evidence["quote"] == good
    (call,) = [c for c in fake_llm.calls if c.name == "requirement_judgement"]
    assert R1 in call.user and HOSTING_ANSWER[:40] in call.user


def test_a_suggestion_with_no_verified_evidence_is_withdrawn(
    db_session: Session,
    specification,  # noqa: ANN001
    fake_llm,  # noqa: ANN001
    fake_embeddings,  # noqa: ANN001
    settings_override: Callable[..., Settings],
) -> None:
    settings_override(coverage_floor=0.1)
    tender, (s1, _, _) = specification
    add_library_item(db_session, DEFAULT_ORG_ID, HOSTING_ANSWER)
    requirement = make_requirement(db_session, tender, s1, R1)
    fake_llm.register(
        "requirement_judgement",
        {
            "suggested_class": "C",
            "rationale": "Not hosted in the UK.",
            "evidence": [{"source": "C1", "quote": "We host everything in Antarctica."}],
        },
    )

    result = suggestions.suggest_for_requirement(db_session, requirement, embed([R1])[0])

    assert result.suggested_class is None
    assert result.suggestion["withdrawn_class"] == "C"
    assert result.suggestion["evidence"] == []


# --- Triggers ---------------------------------------------------------------------------------


def _queued_scans(session: Session, tender: Tender) -> list[Job]:
    return list(
        session.scalars(
            select(Job).where(
                Job.kind == "extract_requirements",
                Job.status == "queued",
                Job.payload["tender_id"].astext == str(tender.id),
            )
        ).all()
    )


def test_a_parse_only_tender_document_requests_one_scan(
    db_session: Session, tmp_path: Path, fake_llm  # noqa: ANN001
) -> None:
    from app.ingest.jobs import enqueue_ingest
    from tests.test_ingest_builders import build_pack_xlsx, make_document

    tender = make_tender(db_session)
    first = make_document(
        db_session,
        build_pack_xlsx(tmp_path / "spec.xlsx"),
        doc_type="tender_document",
        tender_doc_kind="specification",
        tender_id=tender.id,
        classification_confirmed=True,
    )
    dispatch(db_session, enqueue_ingest(db_session, first, "Jo Bloggs"))
    db_session.refresh(first)
    assert first.ingest_status == "ready"
    (scan,) = _queued_scans(db_session, tender)
    assert scan.payload == {"tender_id": str(tender.id), "actor": "Jo Bloggs"}
    assert db_session.get(Tender, tender.id).requirements_job_id == scan.id

    second = make_document(
        db_session,
        build_pack_xlsx(tmp_path / "terms.xlsx"),
        doc_type="tender_document",
        tender_doc_kind="contract_terms",
        tender_id=tender.id,
        classification_confirmed=True,
    )
    ingest = dispatch(db_session, enqueue_ingest(db_session, second, "Jo Bloggs"))
    assert ingest.status == "done", ingest.error
    assert [job.id for job in _queued_scans(db_session, tender)] == [scan.id], "one is enough"


def test_extract_questions_requests_the_scan_without_duplicating_a_queued_one(
    db_session: Session,
    tmp_path: Path,
    fake_llm,  # noqa: ANN001
    fake_embeddings,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingest import questions as q
    from tests import test_questions_extract as helpers

    monkeypatch.setattr(
        q, "_load_parser", lambda: (helpers.parse_stand_in, helpers.persist_stand_in)
    )
    fake_llm.register("extract_questions", helpers.scripted_extraction)
    tender = helpers.make_tender(db_session)
    pack = helpers.make_pack(db_session, tender, helpers.build_xlsx_pack(tmp_path / "pack.xlsx"))
    job = helpers.enqueue_extract(db_session, tender, pack)
    helpers.run(db_session, job)
    db_session.refresh(tender)
    (scan,) = _queued_scans(db_session, tender)
    assert tender.requirements_job_id == scan.id
    assert scan.payload == {"tender_id": str(tender.id), "actor": "test user"}

    # A second run of the extraction while the scan is still queued adds no second scan.
    job.status = "queued"
    db_session.flush()
    helpers.run(db_session, job)
    assert [j.id for j in _queued_scans(db_session, tender)] == [scan.id]


# --- A synthetic organisation ---------------------------------------------------------------


@pytest.fixture
def live_providers(settings_override: Callable[..., Settings]) -> Iterator[None]:
    """Anthropic and OpenAI selected with placeholder keys: a call to either would fail."""
    settings_override(
        llm_provider="anthropic",
        embedding_provider="openai",
        anthropic_api_key="sk-test",
        openai_api_key="sk-test",
        synthetic_demo="false",
        coverage_floor="0.2",
    )
    reset_llm()
    reset_embedder()
    yield
    reset_llm()
    reset_embedder()


def test_a_synthetic_organisation_runs_the_whole_scan_on_the_heuristic_double(
    db_session: Session, live_providers: None, inline_pool  # noqa: ANN001
) -> None:
    from app.llm.client import _synthetic_llm

    org_id = uuid.uuid4()
    db_session.add(
        Organisation(id=org_id, org_id=org_id, name="Example Health", synthetic=True)
    )
    db_session.flush()
    tender = make_tender(db_session, org_id)
    document = make_tender_document(db_session, tender)
    add_section(
        db_session,
        document,
        "Specification\n3.1 The supplier must hold ISO/IEC 27001 certification for its "
        "hosting environment.\nThe buyer is an integrated care board.",
        0,
        "Specification",
    )
    with org_scope(True):
        add_library_item(
            db_session,
            org_id,
            "We hold ISO/IEC 27001 certification for our hosting environment. "
            "It was renewed in March 2025.",
        )
    job = enqueue(
        db_session, "extract_requirements", {"tender_id": str(tender.id), "actor": "Ana"},
        org_id=org_id,
    )

    assert run_once(db_session) is True

    db_session.refresh(job)
    assert job.status == "done", job.error
    (row,) = rows_of(db_session, tender)
    assert row.ref == "3.1"
    assert row.text == (
        "The supplier must hold ISO/IEC 27001 certification for its hosting environment."
    )
    assert row.priority == "must"
    assert row.suggested_class == "A"
    assert row.compliance_class is None, "a suggestion never counts until accepted"
    (evidence,) = row.suggestion["evidence"]
    assert evidence["quote"].startswith("We hold ISO/IEC 27001 certification")
    names = [call.name for call in _synthetic_llm().calls]
    assert names == ["extract_requirements", "requirement_judgement"]


# --- API ----------------------------------------------------------------------------------------


@pytest.fixture
def rated_tender(db_session: Session) -> tuple[Tender, list[Requirement]]:
    tender = make_tender(db_session)
    document = make_tender_document(db_session, tender, filename="spec.docx")
    section = add_section(db_session, document, f"{S1}\n{R2}\n{R3}", 0, "Requirements")
    rows = [
        make_requirement(db_session, tender, section, R1, order_index=0, compliance_class="A"),
        make_requirement(db_session, tender, section, R2, order_index=1, compliance_class="C"),
        make_requirement(
            db_session,
            tender,
            section,
            R3,
            order_index=2,
            suggested_class="A",
            suggestion={"label_source": "llm", "rationale": "Service desk hours match."},
        ),
    ]
    return tender, rows


async def test_list_returns_rows_in_order_with_summary_and_locator(
    app_client: httpx.AsyncClient, rated_tender  # noqa: ANN001
) -> None:
    tender, rows = rated_tender
    response = await app_client.get(f"/tenders/{tender.id}/requirements")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"] == {
        "green": 1,
        "amber": 0,
        "red": 1,
        "unrated": 1,
        "total": 3,
        "suggested_unrated": 1,
    }
    assert body["job"] is None
    assert [r["id"] for r in body["requirements"]] == [str(row.id) for row in rows]
    third = body["requirements"][2]
    assert third["document_filename"] == "spec.docx"
    assert third["tender_doc_kind"] == "specification"
    assert third["suggested_class"] == "A" and third["compliance_class"] is None
    assert third["locator"]["section_id"] == str(rows[2].section_id)
    assert (third["locator"]["start"], third["locator"]["end"]) == (rows[2].start, rows[2].end)


async def test_patch_rates_stamps_and_records_an_event(
    app_client: httpx.AsyncClient, db_session: Session, rated_tender  # noqa: ANN001
) -> None:
    tender, rows = rated_tender
    target = rows[2]
    response = await app_client.patch(
        f"/requirements/{target.id}",
        json={"compliance_class": "A", "comment": "  Confirmed with the service team. "},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["compliance_class"] == "A"
    assert body["comment"] == "Confirmed with the service team."
    assert body["rated_by"] == "test user" and body["rated_at"]
    events = db_session.scalars(select(Event).where(Event.entity_id == target.id)).all()
    (event,) = events
    assert (event.entity_type, event.event_type, event.actor) == (
        "requirement",
        "requirement_rated",
        "test user",
    )
    assert event.payload["from"] is None and event.payload["to"] == "A"
    assert event.payload["accepted_suggestion"] is True

    amber = await app_client.patch(
        f"/requirements/{target.id}", json={"compliance_class": "B", "compliant_by": "2027-03-31"}
    )
    assert amber.json()["compliant_by"] == "2027-03-31"
    red = await app_client.patch(f"/requirements/{target.id}", json={"compliance_class": "C"})
    assert red.json()["compliant_by"] is None, "a date belongs to Amber only"

    # Owner and comment alone are not a rating: no event, no new stamp.
    before = len(db_session.scalars(select(Event).where(Event.entity_id == target.id)).all())
    owner = await app_client.patch(f"/requirements/{target.id}", json={"owner": "Priya Shah"})
    assert owner.json()["owner"] == "Priya Shah"
    after = len(db_session.scalars(select(Event).where(Event.entity_id == target.id)).all())
    assert after == before

    cleared = await app_client.patch(
        f"/requirements/{target.id}", json={"compliance_class": None}
    )
    assert cleared.json()["compliance_class"] is None
    assert cleared.json()["rated_by"] is None and cleared.json()["rated_at"] is None

    invalid = await app_client.patch(f"/requirements/{target.id}", json={"compliance_class": "D"})
    assert invalid.status_code == 422
    missing_actor = await app_client.patch(
        f"/requirements/{target.id}", json={"owner": "x"}, headers={"X-Actor": ""}
    )
    assert missing_actor.status_code == 400


async def test_requirements_are_scoped_to_the_organisation(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    other = uuid.uuid4()
    db_session.add(Organisation(id=other, org_id=other, name="Another business"))
    db_session.flush()
    tender = make_tender(db_session, other)
    document = make_tender_document(db_session, tender)
    section = add_section(db_session, document, S1, 0)
    requirement = make_requirement(db_session, tender, section, R1)

    patch = await app_client.patch(f"/requirements/{requirement.id}", json={"owner": "Sam"})
    assert patch.status_code == 404
    listing = await app_client.get(f"/tenders/{tender.id}/requirements")
    assert listing.status_code == 404
    rescan = await app_client.post(f"/tenders/{tender.id}/requirements/rescan")
    assert rescan.status_code == 404
    own = await app_client.get(
        f"/tenders/{tender.id}/requirements", headers={"X-Org-Id": str(other)}
    )
    assert own.status_code == 200 and own.json()["summary"]["total"] == 1


async def test_rescan_enqueues_once_and_the_tender_reports_counts(
    app_client: httpx.AsyncClient, db_session: Session, rated_tender  # noqa: ANN001
) -> None:
    tender, _ = rated_tender
    first = await app_client.post(f"/tenders/{tender.id}/requirements/rescan")
    assert first.status_code == 202, first.text
    second = await app_client.post(f"/tenders/{tender.id}/requirements/rescan")
    assert second.json()["job_id"] == first.json()["job_id"], "one queued scan is enough"
    job = db_session.get(Job, uuid.UUID(first.json()["job_id"]))
    assert job.kind == "extract_requirements"
    assert job.payload == {"tender_id": str(tender.id), "actor": "test user"}

    detail = (await app_client.get(f"/tenders/{tender.id}")).json()
    assert detail["requirement_counts"] == {
        "green": 1,
        "amber": 0,
        "red": 1,
        "unrated": 1,
        "total": 3,
    }
    assert detail["requirements_job_id"] == first.json()["job_id"]
    assert "c_count" in detail and "unclassified_mandatory_count" in detail
    listing = (await app_client.get(f"/tenders/{tender.id}/requirements")).json()
    assert listing["job"] == first.json()["job_id"]


async def test_delete_waits_for_the_scan_and_removes_requirements_and_their_events(
    app_client: httpx.AsyncClient, db_session: Session, rated_tender  # noqa: ANN001
) -> None:
    tender, rows = rated_tender
    rated = await app_client.patch(f"/requirements/{rows[2].id}", json={"compliance_class": "B"})
    assert rated.status_code == 200
    scan = await app_client.post(f"/tenders/{tender.id}/requirements/rescan")
    job = db_session.get(Job, uuid.UUID(scan.json()["job_id"]))

    refused = await app_client.delete(f"/tenders/{tender.id}")
    assert refused.status_code == 409, refused.text
    assert "still being processed" in refused.json()["detail"]

    job.status = "done"
    db_session.flush()
    assert (await app_client.delete(f"/tenders/{tender.id}")).status_code == 204
    remaining = db_session.scalars(
        select(Requirement).where(Requirement.id.in_([row.id for row in rows]))
    ).all()
    assert remaining == []
    events = db_session.scalars(select(Event).where(Event.entity_id == rows[2].id)).all()
    assert events == []
