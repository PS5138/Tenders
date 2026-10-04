"""The tenders router: creation, list and detail aggregates, document upload and job
enqueueing, retriage, draft-all, submit, export and the one-call question list."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import tenders as tenders_api
from app.config import DEFAULT_ORG_ID, get_settings
from app.db.models import Answer, Document, Event, Job, Question, Tender, Thread

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Ref", "Question"])
    sheet.append(["Q1", "Describe your clinical safety case."])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def create_tender(client: httpx.AsyncClient, **overrides: object) -> dict:
    body = {
        "name": "Community diagnostics",
        "buyer": "NHS Test ICB",
        "deadline": (datetime.now(UTC) + timedelta(days=10)).isoformat(),
        "regime": "procurement_act",
        "is_framework": False,
    }
    body.update(overrides)
    response = await client.post("/tenders", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def seed_questions(session: Session, tender_id: uuid.UUID) -> list[Question]:
    pack = Document(
        org_id=DEFAULT_ORG_ID,
        filename="pack.xlsx",
        storage_path="/tmp/pack.xlsx",
        doc_type="tender_document",
        tender_id=tender_id,
        tender_doc_kind="question_pack",
        classification_confirmed=True,
        ingest_status="ready",
    )
    session.add(pack)
    session.flush()
    questions = [
        Question(
            org_id=DEFAULT_ORG_ID, tender_id=tender_id, document_id=pack.id, section="1",
            number="1.1", text="Approved one", word_limit=500, order_index=0,
            status="approved", mandatory=True, compliance_class="A",
        ),
        Question(
            org_id=DEFAULT_ORG_ID, tender_id=tender_id, document_id=pack.id, section="1",
            number="1.2", text="Drafted one", word_limit=300, order_index=1,
            status="ai_draft", mandatory=True, compliance_class="C", needs_review=True,
        ),
        Question(
            org_id=DEFAULT_ORG_ID, tender_id=tender_id, document_id=pack.id, section="2",
            number="2.1", text="Not started, mandatory, unclassified", order_index=2,
            mandatory=True,
        ),
        Question(
            org_id=DEFAULT_ORG_ID, tender_id=tender_id, document_id=pack.id, section="2",
            number="2.2", text="Optional one", word_limit=200, order_index=3,
        ),
    ]
    session.add_all(questions)
    session.flush()
    for question in questions:
        session.add(
            Thread(
                org_id=DEFAULT_ORG_ID, tender_id=tender_id, question_id=question.id,
                title=f"{question.section} {question.number}",
            )
        )
    summary = {
        "counts": {"supported": 2, "weak": 0, "unsupported": 0, "human_authored": 0,
                   "connective": 0},
        "substantive": 2, "supported": 2, "needs_attention": 0, "score": 1.0,
    }
    session.add_all(
        [
            Answer(
                org_id=DEFAULT_ORG_ID, question_id=questions[0].id, version=1, author_type="ai",
                text="Old.", word_count=10, is_current=False, support_summary=summary,
            ),
            Answer(
                org_id=DEFAULT_ORG_ID, question_id=questions[0].id, version=2,
                author_type="user", author_name="Ada", text="Approved text here.",
                word_count=120, is_current=True, support_summary=summary,
                segments=[{"index": 0, "paragraph": 0, "text": "Approved text here.",
                           "kind": "substantive", "sources": [],
                           "support_status": "human_authored"}],
            ),
            Answer(
                org_id=DEFAULT_ORG_ID, question_id=questions[1].id, version=1, author_type="ai",
                text="Draft.", word_count=45, is_current=True, support_summary={},
                segments=[{"index": 0, "paragraph": 0, "text": "Draft.", "kind": "substantive",
                           "sources": [], "support_status": "unsupported"}],
            ),
        ]
    )
    session.flush()
    return questions


# --- CRUD, list and detail ---------------------------------------------------------------------


async def test_create_list_and_detail(app_client: httpx.AsyncClient, db_session: Session) -> None:
    created = await create_tender(app_client)
    assert created["status"] == "open" and created["outcome"] == "pending"
    assert created["regime"] == "procurement_act" and created["is_framework"] is False
    assert created["questions_total"] == 0 and created["words_total"] == 0
    assert created["days_remaining"] in (9, 10)
    assert created["documents"] == [] and created["extract_job_id"] is None

    tender_id = uuid.UUID(created["id"])
    seed_questions(db_session, tender_id)

    listed = (await app_client.get("/tenders")).json()
    row = next(item for item in listed if item["id"] == created["id"])
    assert set(row) == {
        "id", "name", "buyer", "deadline", "days_remaining", "status", "outcome",
        "questions_total", "questions_approved", "words_total", "words_approved",
    }
    assert row["questions_total"] == 4
    assert row["questions_approved"] == 1
    assert row["words_total"] == 1000, "sum of word_limit where present"
    assert row["words_approved"] == 120, "current answer word_count on approved questions only"

    detail = (await app_client.get(f"/tenders/{tender_id}")).json()
    assert detail["c_count"] == 1
    assert detail["unclassified_mandatory_count"] == 1
    assert detail["needs_review_count"] == 1
    assert detail["documents"][0]["tender_doc_kind"] == "question_pack"
    assert detail["documents"][0]["ingest_status"] == "ready"

    assert (await app_client.get(f"/tenders/{uuid.uuid4()}")).status_code == 404
    other_org = await app_client.get(
        f"/tenders/{tender_id}", headers={"X-Org-Id": str(uuid.uuid4())}
    )
    assert other_org.status_code == 404


async def test_create_requires_a_name(app_client: httpx.AsyncClient) -> None:
    assert (await app_client.post("/tenders", json={})).status_code == 422
    assert (await app_client.post("/tenders", json={"name": ""})).status_code == 422


async def test_patch_tender(app_client: httpx.AsyncClient) -> None:
    created = await create_tender(app_client)
    response = await app_client.patch(
        f"/tenders/{created['id']}",
        json={"outcome": "won", "outcome_notes": "Scored 87%", "regime": "psr",
              "is_framework": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "won" and body["outcome_notes"] == "Scored 87%"
    assert body["regime"] == "psr" and body["is_framework"] is True
    bad = await app_client.patch(f"/tenders/{created['id']}", json={"outcome": "maybe"})
    assert bad.status_code == 422


async def test_patch_tender_refuses_a_null_outcome(app_client: httpx.AsyncClient) -> None:
    """``tenders.outcome`` is NOT NULL and "no outcome yet" is ``pending``, so an explicit null
    is a 422 rather than an IntegrityError; the nullable fields still accept null as a clear."""
    created = await create_tender(app_client)
    null_outcome = await app_client.patch(f"/tenders/{created['id']}", json={"outcome": None})
    assert null_outcome.status_code == 422, null_outcome.text
    assert "pending" in null_outcome.text
    detail = (await app_client.get(f"/tenders/{created['id']}")).json()
    assert detail["outcome"] == "pending"

    cleared = await app_client.patch(
        f"/tenders/{created['id']}", json={"outcome_notes": None, "regime": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["regime"] is None and cleared.json()["outcome"] == "pending"


# --- Documents -----------------------------------------------------------------------------------


async def test_upload_question_pack_enqueues_extraction(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    created = await create_tender(app_client)
    # Generated once: openpyxl stamps the creation time to the second, so two calls can differ.
    pack = xlsx_bytes()
    files = {"file": ("pack.xlsx", pack, "application/octet-stream")}
    response = await app_client.post(
        f"/tenders/{created['id']}/documents",
        data={"tender_doc_kind": "question_pack"},
        files=files,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    # The same flat shape as POST /documents: the document's fields with job_id beside them.
    document = body
    assert "document" not in body
    assert body["item_counts"] == {"qa_pair": 0, "chunk": 0, "promoted_answer": 0}
    assert body["fact_count"] == 0
    assert document["doc_type"] == "tender_document"
    assert document["tender_id"] == created["id"]
    assert document["tender_doc_kind"] == "question_pack"
    assert document["classification_confirmed"] is True
    assert document["ingest_status"] == "queued"
    assert document["filename"] == "pack.xlsx"
    assert Path(document["storage_path"]).read_bytes() == pack

    job = db_session.get(Job, uuid.UUID(body["job_id"]))
    assert job.kind == "extract_questions"
    assert job.payload == {
        "tender_id": created["id"], "document_id": document["id"], "actor": "test user",
    }
    tender = db_session.get(Tender, uuid.UUID(created["id"]))
    assert tender.extract_job_id == job.id
    detail = (await app_client.get(f"/tenders/{created['id']}")).json()
    assert detail["extract_job_id"] == body["job_id"]
    assert detail["documents"][0]["id"] == document["id"]

    second = await app_client.post(
        f"/tenders/{created['id']}/documents",
        data={"tender_doc_kind": "question_pack"},
        files={"file": ("pack2.xlsx", xlsx_bytes(), "application/octet-stream")},
    )
    assert second.status_code == 409
    assert "question pack" in second.json()["detail"]


async def test_upload_other_tender_document_enqueues_parse_only_ingest(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    created = await create_tender(app_client)
    response = await app_client.post(
        f"/tenders/{created['id']}/documents",
        data={"tender_doc_kind": "specification"},
        files={"file": ("spec.docx", b"not really a docx", DOCX_TYPE)},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    job = db_session.get(Job, uuid.UUID(body["job_id"]))
    assert job.kind == "ingest_document"
    assert job.total == 1, "a parse-only tender document runs one stage"
    assert job.payload == {"document_id": body["id"], "actor": "test user"}
    tender = db_session.get(Tender, uuid.UUID(created["id"]))
    assert tender.extract_job_id is None

    invalid = await app_client.post(
        f"/tenders/{created['id']}/documents",
        data={"tender_doc_kind": "brochure"},
        files={"file": ("x.docx", b"x", DOCX_TYPE)},
    )
    assert invalid.status_code == 422
    missing = await app_client.post(
        f"/tenders/{uuid.uuid4()}/documents",
        data={"tender_doc_kind": "other"},
        files={"file": ("x.docx", b"x", DOCX_TYPE)},
    )
    assert missing.status_code == 404


async def test_upload_tender_document_over_the_byte_limit_is_refused(
    app_client: httpx.AsyncClient, db_session: Session, settings_override
) -> None:
    """The tender route shares the bounded read: one byte over ``MAX_UPLOAD_BYTES`` is 413
    naming the limit in MB and persists nothing; a file at the limit is accepted."""
    limit = 1024 * 1024
    settings_override(max_upload_bytes=limit)
    created = await create_tender(app_client)
    storage = Path(get_settings().storage_path)
    before_files = {path for path in storage.rglob("*") if path.is_file()}
    before_jobs = db_session.scalar(select(func.count()).select_from(Job))

    too_large = await app_client.post(
        f"/tenders/{created['id']}/documents",
        data={"tender_doc_kind": "specification"},
        files={"file": ("huge.docx", b"x" * (limit + 1), DOCX_TYPE)},
    )
    assert too_large.status_code == 413, too_large.text
    assert too_large.json()["detail"] == "This file is too large. Uploads are limited to 1 MB."
    assert {path for path in storage.rglob("*") if path.is_file()} == before_files
    assert db_session.scalar(select(func.count()).select_from(Job)) == before_jobs
    assert (await app_client.get(f"/tenders/{created['id']}")).json()["documents"] == []

    at_limit = await app_client.post(
        f"/tenders/{created['id']}/documents",
        data={"tender_doc_kind": "specification"},
        files={"file": ("fits.docx", b"y" * limit, DOCX_TYPE)},
    )
    assert at_limit.status_code == 202, at_limit.text
    assert Path(at_limit.json()["storage_path"]).stat().st_size == limit


async def test_upload_empty_tender_document_is_refused_before_anything_is_written(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    """A zero-byte upload is 422 from the shared bounded read, for a question pack and for
    any other kind: no file, no document, no job, and the tender's one question-pack slot is
    still free, so the real pack is accepted afterwards."""
    created = await create_tender(app_client)
    storage = Path(get_settings().storage_path)
    before_files = {path for path in storage.rglob("*") if path.is_file()}
    before_jobs = db_session.scalar(select(func.count()).select_from(Job))

    for kind in ("question_pack", "specification"):
        empty = await app_client.post(
            f"/tenders/{created['id']}/documents",
            data={"tender_doc_kind": kind},
            files={"file": ("empty.xlsx", b"", "application/octet-stream")},
        )
        assert empty.status_code == 422, (kind, empty.text)
        assert empty.json()["detail"] == "The uploaded file is empty."

    assert {path for path in storage.rglob("*") if path.is_file()} == before_files
    assert db_session.scalar(select(func.count()).select_from(Job)) == before_jobs
    detail = (await app_client.get(f"/tenders/{created['id']}")).json()
    assert detail["documents"] == []
    assert detail["extract_job_id"] is None

    accepted = await app_client.post(
        f"/tenders/{created['id']}/documents",
        data={"tender_doc_kind": "question_pack"},
        files={"file": ("pack.xlsx", xlsx_bytes(), "application/octet-stream")},
    )
    assert accepted.status_code == 202, accepted.text
    assert (await app_client.get(f"/tenders/{created['id']}")).json()["extract_job_id"]


# --- Jobs started from the tender ----------------------------------------------------------------


async def test_retriage_and_draft_all_return_jobs(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    created = await create_tender(app_client)
    tender_id = uuid.UUID(created["id"])
    questions = seed_questions(db_session, tender_id)
    questions[2].coverage = "covered"
    questions[3].coverage = "new"
    db_session.flush()

    retriage = await app_client.post(f"/tenders/{tender_id}/retriage")
    assert retriage.status_code == 202, retriage.text
    body = retriage.json()
    assert body["kind"] == "triage_tender" and body["status"] == "queued"
    assert body["total"] == 2, "two questions are still not_started"
    assert "payload" not in body
    job = db_session.get(Job, uuid.UUID(body["id"]))
    assert job.payload["scope"] == "not_started" and job.payload["actor"] == "test user"
    assert (await app_client.get(f"/tenders/{tender_id}")).json()["triage_job_id"] == body["id"]

    draft_all = await app_client.post(f"/tenders/{tender_id}/draft-all", json={"include_new": True})
    assert draft_all.status_code == 202, draft_all.text
    body = draft_all.json()
    assert body["kind"] == "draft_all"
    job = db_session.get(Job, uuid.UUID(body["id"]))
    assert job.payload == {"tender_id": str(tender_id), "include_new": True, "actor": "test user"}
    assert job.total == 2, "not_started questions at covered/partial, plus new when included"

    default = await app_client.post(f"/tenders/{tender_id}/draft-all")
    assert default.status_code == 202
    job = db_session.get(Job, uuid.UUID(default.json()["id"]))
    assert job.payload["include_new"] is False and job.total == 1


# --- Submit and export ---------------------------------------------------------------------------


async def test_submit_marks_submitted_once(
    app_client: httpx.AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await create_tender(app_client)
    calls: list[tuple[uuid.UUID, str]] = []

    def fake_promote(db: Session, tender: Tender, actor: str) -> int:
        calls.append((tender.id, actor))
        return 3

    monkeypatch.setattr(tenders_api, "_promote_on_submit", fake_promote)
    response = await app_client.post(f"/tenders/{created['id']}/submit")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tender"]["status"] == "submitted"
    assert body["tender"]["submitted_at"] is not None
    assert body["promoted"] == 3
    assert calls == [(uuid.UUID(created["id"]), "test user")]
    tender = db_session.get(Tender, uuid.UUID(created["id"]))
    assert tender.status == "submitted" and tender.submitted_at is not None

    again = await app_client.post(f"/tenders/{created['id']}/submit")
    assert again.status_code == 409


def _submitted_events(session: Session, tender_id: uuid.UUID) -> list[Event]:
    return list(
        session.scalars(
            select(Event).where(
                Event.entity_id == tender_id, Event.event_type == "tender_submitted"
            )
        )
    )


async def test_submit_never_doubles_as_an_unarchive(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    """A submitted tender that was archived keeps its one submission (409, one event, still
    archived); an archived tender that was never submitted is told to restore it first."""
    created = await create_tender(app_client)
    tender_id = uuid.UUID(created["id"])
    assert (await app_client.post(f"/tenders/{tender_id}/submit")).status_code == 200
    archived = await app_client.patch(f"/tenders/{tender_id}", json={"status": "archived"})
    assert archived.json()["status"] == "archived"

    again = await app_client.post(f"/tenders/{tender_id}/submit")
    assert again.status_code == 409, again.text
    assert "already been submitted" in again.json()["detail"]
    assert len(_submitted_events(db_session, tender_id)) == 1
    tender = db_session.get(Tender, tender_id)
    db_session.refresh(tender)
    assert tender.status == "archived", "submit did not restore the tender"

    never_submitted = await create_tender(app_client, name="Archived, never submitted")
    other_id = uuid.UUID(never_submitted["id"])
    await app_client.patch(f"/tenders/{other_id}", json={"status": "archived"})
    refused = await app_client.post(f"/tenders/{other_id}/submit")
    assert refused.status_code == 409, refused.text
    assert "Restore the tender first" in refused.json()["detail"]
    other = db_session.get(Tender, other_id)
    db_session.refresh(other)
    assert other.status == "archived" and other.submitted_at is None
    assert _submitted_events(db_session, other_id) == []

    restored = await app_client.patch(f"/tenders/{other_id}", json={"status": "open"})
    assert restored.json()["status"] == "open"
    assert (await app_client.post(f"/tenders/{other_id}/submit")).status_code == 200


# --- Delete guards --------------------------------------------------------------------------------


async def test_delete_waits_for_parse_only_ingests_and_streaming_replies(
    app_client: httpx.AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A queued ``ingest_document`` job for one of the tender's documents carries no
    ``tender_id``, so the guard matches on ``document_id``; a reply streaming on one of the
    tender's threads blocks too; with neither, the delete goes through."""
    from app.generate import runner

    created = await create_tender(app_client)
    tender_id = uuid.UUID(created["id"])
    seed_questions(db_session, tender_id)
    upload = await app_client.post(
        f"/tenders/{tender_id}/documents",
        data={"tender_doc_kind": "specification"},
        files={"file": ("spec.docx", b"specification bytes", DOCX_TYPE)},
    )
    assert upload.status_code == 202, upload.text
    job = db_session.get(Job, uuid.UUID(upload.json()["job_id"]))
    assert job.kind == "ingest_document" and "tender_id" not in job.payload

    refused = await app_client.delete(f"/tenders/{tender_id}")
    assert refused.status_code == 409, refused.text
    assert "still being processed" in refused.json()["detail"]
    assert db_session.get(Tender, tender_id) is not None

    job.status = "done"
    db_session.flush()
    thread_id = db_session.scalars(select(Thread.id).where(Thread.tender_id == tender_id)).first()
    assert thread_id is not None
    monkeypatch.setattr(runner, "reply_in_progress", lambda key: str(key) == str(thread_id))
    refused = await app_client.delete(f"/tenders/{tender_id}")
    assert refused.status_code == 409, refused.text
    assert "still being processed" in refused.json()["detail"]
    assert db_session.get(Tender, tender_id) is not None

    monkeypatch.setattr(runner, "reply_in_progress", lambda key: False)
    assert (await app_client.delete(f"/tenders/{tender_id}")).status_code == 204
    assert (await app_client.get(f"/tenders/{tender_id}")).status_code == 404


async def test_export_streams_bytes_and_maps_blocked_to_409(
    app_client: httpx.AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await create_tender(app_client, name="Community diagnostics / 2026")
    order: list[str] = []

    class Blocked(Exception):
        def __init__(self, question_ids: list[uuid.UUID]) -> None:
            super().__init__("Export refused: 1 question needs review.")
            self.question_ids = question_ids

    blocking_id = uuid.uuid4()

    def fake_build(db: Session, tender: Tender, *, format: str, mode: str) -> bytes:
        order.append("build")
        if mode == "submission" and tender.name.endswith("2026"):
            raise Blocked([blocking_id])
        return f"{format}:{mode}".encode()

    def fake_sweep(db: Session) -> None:
        order.append("sweep")

    monkeypatch.setattr(tenders_api, "_run_expiry_sweep", fake_sweep)
    monkeypatch.setattr(
        tenders_api,
        "_export_backend",
        lambda: (fake_build, Blocked, {"docx": DOCX_TYPE}, None),
    )

    blocked = await app_client.get(f"/tenders/{created['id']}/export?format=docx&mode=submission")
    assert blocked.status_code == 409, blocked.text
    # A string detail with the extra keys beside it, like every other 409 in the API.
    assert blocked.json()["question_ids"] == [str(blocking_id)]
    assert "review" in blocked.json()["detail"]
    assert blocked.json()["code"] == "needs_review"
    assert order == ["sweep", "build"], "the expiry sweep runs before the export is built"

    ok = await app_client.get(f"/tenders/{created['id']}/export?format=docx&mode=review")
    assert ok.status_code == 200, ok.text
    assert ok.content == b"docx:review"
    assert ok.headers["content-type"].startswith(DOCX_TYPE)
    assert ok.headers["content-disposition"] == (
        'attachment; filename="Community-diagnostics-2026-review.docx"'
    )

    bad = await app_client.get(f"/tenders/{created['id']}/export?format=pdf")
    assert bad.status_code == 422


@pytest.mark.integration
async def test_export_with_real_export_module(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    pytest.importorskip("app.export.docx")
    created = await create_tender(app_client)
    seed_questions(db_session, uuid.UUID(created["id"]))
    response = await app_client.get(f"/tenders/{created['id']}/export?format=docx&mode=review")
    assert response.status_code == 200, response.text
    assert response.content[:2] == b"PK"
    blocked = await app_client.get(f"/tenders/{created['id']}/export?format=docx&mode=submission")
    assert blocked.status_code == 409, "one seeded question has needs_review"


# --- The one-call question list -------------------------------------------------------------------


async def test_list_tender_questions_embeds_current_answer_and_thread(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    created = await create_tender(app_client)
    tender_id = uuid.UUID(created["id"])
    questions = seed_questions(db_session, tender_id)
    response = await app_client.get(f"/tenders/{tender_id}/questions")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["number"] for row in rows] == ["1.1", "1.2", "2.1", "2.2"]
    threads = {
        thread.question_id: thread.id
        for thread in db_session.scalars(select(Thread).where(Thread.tender_id == tender_id))
    }
    for row, question in zip(rows, questions, strict=True):
        assert row["id"] == str(question.id)
        assert row["thread_id"] == str(threads[question.id])
        for key in ("section", "text", "word_limit", "weighting", "response_type", "mandatory",
                    "coverage", "coverage_detail", "compliance_class", "status", "needs_review",
                    "topics", "gap_acknowledgements"):
            assert key in row

    approved = rows[0]["current_answer"]
    assert approved == {
        "id": approved["id"], "version": 2, "author_type": "user", "author_name": "Ada",
        "word_count": 120,
        "support_summary": {
            "counts": {"supported": 2, "weak": 0, "unsupported": 0, "human_authored": 0,
                       "connective": 0},
            "substantive": 2, "supported": 2, "needs_attention": 0, "score": 1.0,
        },
        "updated_at": approved["updated_at"],
    }
    assert "segments" not in approved
    current = db_session.scalars(
        select(Answer).where(Answer.question_id == questions[0].id, Answer.is_current.is_(True))
    ).one()
    assert approved["id"] == str(current.id), "the current version, not the older one"

    drafted = rows[1]["current_answer"]
    assert drafted["version"] == 1 and drafted["author_type"] == "ai"
    assert drafted["support_summary"]["substantive"] == 1, (
        "an answer with an empty stored summary is summarised from its segments"
    )
    assert rows[2]["current_answer"] is None and rows[3]["current_answer"] is None

    assert (await app_client.get(f"/tenders/{uuid.uuid4()}/questions")).status_code == 404
