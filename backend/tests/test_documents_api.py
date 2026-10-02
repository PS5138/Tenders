"""The documents API: upload, list, detail, PATCH, confirm, sections, unpaired queue, pairs."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Document, Event, Job, KnowledgeItem, Tender
from tests.test_supersession_factories import (  # noqa: F401 - fixture import
    ORG,
    InvalidationRecorder,
    invalidations_fixture,
    make_document,
    make_fact,
    make_fragment,
    make_item,
    make_section,
)


async def test_upload_creates_the_document_and_enqueues_ingest(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    response = await app_client.post(
        "/documents",
        files={
            "file": ("submission.docx", b"PK\x03\x04 fake docx bytes", "application/octet-stream")
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["filename"] == "submission.docx"
    assert body["ingest_status"] == "queued"
    assert body["classification_confirmed"] is False
    assert body["doc_type"] is None and body["tender_id"] is None
    assert body["item_counts"] == {"qa_pair": 0, "chunk": 0, "promoted_answer": 0}
    assert body["fact_count"] == 0
    assert body["job_id"]

    document = db_session.get(Document, uuid.UUID(body["id"]))
    assert document is not None
    stored = Path(document.storage_path)
    assert stored.exists() and stored.read_bytes().startswith(b"PK")
    assert str(stored).startswith(str(get_settings().storage_path))

    job = db_session.get(Job, uuid.UUID(body["job_id"]))
    assert job is not None
    assert job.kind == "ingest_document" and job.status == "queued" and job.total == 5
    assert job.payload == {"document_id": body["id"], "actor": "test user"}

    empty = await app_client.post("/documents", files={"file": ("empty.docx", b"")})
    assert empty.status_code == 422
    no_actor = await app_client.post(
        "/documents", files={"file": ("x.docx", b"abc")}, headers={"X-Actor": ""}
    )
    assert no_actor.status_code == 400


def _stored_files() -> set[Path]:
    return {path for path in Path(get_settings().storage_path).rglob("*") if path.is_file()}


async def test_unknown_organisation_is_refused_before_anything_is_written(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    """A well-formed but unknown X-Org-Id is a 404 from the dependency, not a foreign-key
    500 at flush after the file has already been written."""
    before = _stored_files()
    unknown = {"X-Org-Id": str(uuid.uuid4())}
    upload = await app_client.post(
        "/documents", files={"file": ("x.docx", b"PK\x03\x04 bytes")}, headers=unknown
    )
    assert upload.status_code == 404, upload.text
    assert upload.json()["detail"] == "Organisation not found."
    assert _stored_files() == before, "no orphan file for a refused upload"

    # Reads for an unknown organisation are 404 too, not an empty list.
    assert (await app_client.get("/documents", headers=unknown)).status_code == 404
    assert (await app_client.get("/tenders", headers=unknown)).status_code == 404
    malformed = await app_client.get("/documents", headers={"X-Org-Id": "not-a-uuid"})
    assert malformed.status_code == 400

    tender = Tender(org_id=ORG, name="T")
    db_session.add(tender)
    db_session.flush()
    tender_upload = await app_client.post(
        f"/tenders/{tender.id}/documents",
        data={"tender_doc_kind": "specification"},
        files={"file": ("spec.docx", b"PK\x03\x04 bytes")},
        headers=unknown,
    )
    assert tender_upload.status_code == 404
    assert _stored_files() == before


def test_upload_limit_defaults_to_25_mib() -> None:
    from app.config import Settings

    assert Settings().max_upload_bytes == 25 * 1024 * 1024


def test_upload_limit_message_states_the_limit_without_rounding_up() -> None:
    """One sentence and one formatter, shared with the Next.js proxy: whole MiB as a whole
    number, otherwise one decimal, never rounded up."""
    from app.api.documents import format_upload_limit, upload_limit_message

    mib = 1024 * 1024
    assert format_upload_limit(25 * mib) == "25"
    assert format_upload_limit(mib) == "1"
    assert format_upload_limit(mib + mib // 2) == "1.5"
    assert format_upload_limit(int(1.56 * mib)) == "1.5", "floored, not rounded to 1.6"
    assert format_upload_limit(int(2.5 * mib)) == "2.5"
    assert format_upload_limit(1024) == "0", "sub-0.1 MiB limits floor to zero rather than lie"
    assert (
        upload_limit_message(25 * mib)
        == "This file is too large. Uploads are limited to 25 MB."
    )
    assert "too large" in upload_limit_message(mib + mib // 2)


async def test_empty_upload_is_refused_with_its_own_message_even_in_synthetic_demo(
    app_client: httpx.AsyncClient, db_session: Session, settings_override
) -> None:
    """``read_upload`` answers 422 for a zero-byte file before the synthetic checksum gate,
    so the demo stack says the file is empty rather than that it is not a synthetic file."""
    settings_override(synthetic_demo="true")
    before = _stored_files()
    empty = await app_client.post("/documents", files={"file": ("empty.docx", b"")})
    assert empty.status_code == 422, empty.text
    assert empty.json()["detail"] == "The uploaded file is empty."
    assert _stored_files() == before


async def test_upload_over_the_byte_limit_is_refused_before_anything_is_written(
    app_client: httpx.AsyncClient, db_session: Session, settings_override
) -> None:
    """One byte over ``MAX_UPLOAD_BYTES`` is 413 with the limit in MB, and nothing is
    persisted: no file, no document row, no job. A file exactly at the limit is accepted."""
    limit = 1024 * 1024
    settings_override(max_upload_bytes=limit)
    before_files = _stored_files()
    before_documents = db_session.scalar(select(func.count()).select_from(Document))
    before_jobs = db_session.scalar(select(func.count()).select_from(Job))

    too_large = await app_client.post(
        "/documents", files={"file": ("huge.docx", b"x" * (limit + 1), "application/octet-stream")}
    )
    assert too_large.status_code == 413, too_large.text
    assert too_large.json()["detail"] == "This file is too large. Uploads are limited to 1 MB."
    assert _stored_files() == before_files, "no orphan file for a refused upload"
    assert db_session.scalar(select(func.count()).select_from(Document)) == before_documents
    assert db_session.scalar(select(func.count()).select_from(Job)) == before_jobs

    at_limit = await app_client.post(
        "/documents", files={"file": ("fits.docx", b"y" * limit, "application/octet-stream")}
    )
    assert at_limit.status_code == 202, at_limit.text
    document = db_session.get(Document, uuid.UUID(at_limit.json()["id"]))
    assert Path(document.storage_path).stat().st_size == limit


def _multipart_file(boundary: str, filename: str, content: bytes) -> bytes:
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    return head + content + f"\r\n--{boundary}--\r\n".encode()


async def test_oversized_upload_body_is_refused_before_it_is_spooled(
    app_client: httpx.AsyncClient, db_session: Session, settings_override
) -> None:
    """``UploadBodyLimit`` bounds the request body itself, not only the parsed file: an
    honest ``Content-Length`` over the limit is 413 with no body byte read, and a chunked
    body with no ``Content-Length`` is cut off once it passes the limit plus the multipart
    allowance, long before all of it has been consumed. Nothing is written either way."""
    from app.main import MULTIPART_OVERHEAD_BYTES, app

    limit = 1024 * 1024
    settings_override(max_upload_bytes=limit)
    before_files = _stored_files()
    before_documents = db_session.scalar(select(func.count()).select_from(Document))
    tender = Tender(org_id=ORG, name="T")
    db_session.add(tender)
    db_session.flush()

    delivered = 0  # body bytes the application actually pulled from the server

    async def counting(scope, receive, send):  # noqa: ANN001
        async def counted():
            nonlocal delivered
            message = await receive()
            if message["type"] == "http.request":
                delivered += len(message.get("body", b""))
            return message

        await app(scope, counted, send)

    huge = b"x" * (4 * limit)
    expected = "This file is too large. Uploads are limited to 1 MB."
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=counting),
        base_url="http://testserver",
        headers={"X-Actor": "test user"},
    ) as client:
        # httpx declares Content-Length for a multipart upload: refused without reading.
        declared = await client.post(
            f"/tenders/{tender.id}/documents",
            data={"tender_doc_kind": "specification"},
            files={"file": ("huge.docx", huge, "application/octet-stream")},
        )
        assert declared.status_code == 413, declared.text
        assert declared.json()["detail"] == expected
        assert delivered == 0, "an honest Content-Length over the limit costs no body bytes"

        # A streamed body carries Transfer-Encoding: chunked and no Content-Length.
        body = _multipart_file("upload-limit-test", "huge.docx", huge)
        chunk = 64 * 1024

        async def chunks():
            for start in range(0, len(body), chunk):
                yield body[start : start + chunk]

        streamed = await client.post(
            "/documents",
            content=chunks(),
            headers={"Content-Type": "multipart/form-data; boundary=upload-limit-test"},
        )
        assert streamed.status_code == 413, streamed.text
        assert streamed.json()["detail"] == expected
        assert 0 < delivered < len(body), "the request ended before the body was consumed"
        assert delivered <= limit + MULTIPART_OVERHEAD_BYTES + chunk, "cut off within one chunk"

        # The guard sits inside the service-secret check: an unauthenticated caller is 401.
        settings_override(max_upload_bytes=limit, service_secret="x" * 32)
        unauthenticated = await client.post(
            "/documents", files={"file": ("huge.docx", huge, "application/octet-stream")}
        )
        assert unauthenticated.status_code == 401

    assert _stored_files() == before_files
    assert db_session.scalar(select(func.count()).select_from(Document)) == before_documents


def test_discard_upload_removes_the_file_and_its_empty_directory(tmp_path: Path) -> None:
    from app.api.documents import discard_upload

    directory = tmp_path / str(uuid.uuid4())
    directory.mkdir()
    stored = directory / "upload.docx"
    stored.write_bytes(b"PK")
    discard_upload(str(stored))
    assert not stored.exists() and not directory.exists()
    assert tmp_path.exists(), "only the per-document directory goes"

    # A shared directory that still holds other files is left alone; a missing file is fine.
    sibling = tmp_path / "other.docx"
    sibling.write_bytes(b"PK")
    discard_upload(str(tmp_path / "gone.docx"))
    assert sibling.exists() and tmp_path.exists()


async def test_list_returns_library_documents_only_with_counts(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    library = make_document(db_session, filename="library.docx", doc_type="past_submission")
    section = make_section(db_session, library, "Q: How? A: Like this.")
    make_item(db_session, library, section)
    make_item(db_session, library, section)
    make_item(db_session, library, section, item_type="chunk")
    make_fact(db_session, library, section)
    tender = Tender(org_id=ORG, name="A tender")
    db_session.add(tender)
    db_session.flush()
    make_document(
        db_session, filename="pack.xlsx", doc_type="tender_document", tender_id=tender.id
    )

    response = await app_client.get("/documents")
    assert response.status_code == 200, response.text
    rows = {row["filename"]: row for row in response.json()}
    assert "pack.xlsx" not in rows
    assert rows["library.docx"]["item_counts"] == {"qa_pair": 2, "chunk": 1, "promoted_answer": 0}
    assert rows["library.docx"]["fact_count"] == 1
    assert "fixture" in " ".join(rows).lower() or len(rows) >= 2

    detail = await app_client.get(f"/documents/{library.id}")
    assert detail.status_code == 200
    assert detail.json()["item_counts"]["qa_pair"] == 2
    assert detail.json()["job_id"] is None

    missing = await app_client.get(f"/documents/{uuid.uuid4()}")
    assert missing.status_code == 404


async def test_patch_and_confirm(
    app_client: httpx.AsyncClient, db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older = make_document(db_session, filename="iso-2024.pdf", effective_date=date(2024, 1, 1))
    newer = make_document(
        db_session, filename="iso-2025.pdf", effective_date=date(2025, 1, 1), confirmed=False
    )
    unclassified = make_document(db_session, doc_type=None, doc_kind=None, confirmed=False)

    not_yet = await app_client.post(f"/documents/{unclassified.id}/confirm")
    assert not_yet.status_code == 409
    patch_unclassified = await app_client.patch(
        f"/documents/{unclassified.id}", json={"buyer": "x"}
    )
    assert patch_unclassified.status_code == 409

    bad = await app_client.patch(f"/documents/{newer.id}", json={"doc_kind": "nope"})
    assert bad.status_code == 422
    unknown = await app_client.patch(f"/documents/{newer.id}", json={"filename": "x"})
    assert unknown.status_code == 422
    empty = await app_client.patch(f"/documents/{newer.id}", json={})
    assert empty.status_code == 422

    patched = await app_client.patch(
        f"/documents/{newer.id}", json={"effective_date": "2025-03-01"}
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["classification_confirmed"] is True
    assert body["effective_date"] == "2025-03-01"
    assert body["effective_date_source"] == "user"
    assert body["job_id"] is None
    db_session.refresh(older)
    assert older.superseded_by == newer.id

    retype = await app_client.patch(f"/documents/{older.id}", json={"doc_type": "past_submission"})
    assert retype.status_code == 200, retype.text
    assert retype.json()["job_id"]
    assert retype.json()["ingest_status"] == "extracting"
    job = db_session.get(Job, uuid.UUID(retype.json()["job_id"]))
    assert job is not None and job.kind == "ingest_document"

    third = make_document(
        db_session, filename="iso-2026.pdf", effective_date=date(2026, 1, 1), confirmed=False
    )
    confirmed = await app_client.post(f"/documents/{third.id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["classification_confirmed"] is True
    db_session.refresh(newer)
    assert newer.superseded_by == third.id

    tender = Tender(org_id=ORG, name="T")
    db_session.add(tender)
    db_session.flush()
    tender_doc = make_document(db_session, doc_type="tender_document", tender_id=tender.id)
    on_tender_doc = await app_client.patch(f"/documents/{tender_doc.id}", json={"buyer": "x"})
    assert on_tender_doc.status_code == 409


async def test_sections_and_unpaired_queue(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    document = make_document(db_session, doc_type="past_submission")
    first = make_section(db_session, document, "1. Introduction", heading_path=["1"])
    second = make_section(db_session, document, "Q: Why? A: Because.", heading_path=["1", "1.1"])
    verified = make_item(db_session, document, second)
    unverified = make_item(db_session, document, second, text_verified=False)
    fragment = make_fragment(db_session, document, first, role="question")
    resolved = make_fragment(db_session, document, first, role="answer")
    resolved.resolved_item_id = verified.id
    db_session.flush()

    sections = await app_client.get(f"/documents/{document.id}/sections")
    assert sections.status_code == 200
    assert [row["order_index"] for row in sections.json()] == [0, 1]
    assert sections.json()[1]["heading_path"] == ["1", "1.1"]
    assert sections.json()[0]["text"] == "1. Introduction"

    unpaired = await app_client.get(f"/documents/{document.id}/unpaired")
    assert unpaired.status_code == 200, unpaired.text
    body = unpaired.json()
    assert [row["id"] for row in body["items"]] == [str(unverified.id)]
    assert body["items"][0]["text_verified"] is False
    assert [row["id"] for row in body["fragments"]] == [str(fragment.id)]
    assert body["fragments"][0]["role"] == "question"

    assert (await app_client.get(f"/documents/{uuid.uuid4()}/sections")).status_code == 404


async def test_fidelity_fix_re_slices_from_the_section(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    document = make_document(db_session, doc_type="past_submission")
    text = "Question: How is data encrypted? Answer: All data is encrypted at rest with AES-256."
    section = make_section(db_session, document, text)
    item = make_item(
        db_session, document, section, answer_text="retyped answer", text_verified=False,
        with_embeddings=False,
    )
    start = text.index("All data")
    end = len(text)

    response = await app_client.post(
        f"/documents/{document.id}/pairs",
        json={"item_id": str(item.id), "section_id": str(section.id), "answer_start": start,
              "answer_end": end, "question_text": "How is data encrypted?"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "fidelity_fix"
    assert body["item"]["text_verified"] is True
    assert body["item"]["answer_text"] == "All data is encrypted at rest with AES-256."
    assert body["item"]["question_text"] == "How is data encrypted?"

    db_session.refresh(item)
    assert item.text_verified is True
    assert item.answer_text == text[start:end]
    assert item.answer_embedding is not None and item.question_embedding is not None
    assert item.is_canonical is True
    events = db_session.scalars(
        select(Event).where(Event.entity_id == item.id, Event.event_type == "pair_fixed")
    ).all()
    assert len(events) == 1 and events[0].actor == "test user"
    assert events[0].payload["mode"] == "fidelity_fix"

    out_of_range = await app_client.post(
        f"/documents/{document.id}/pairs",
        json={"item_id": str(item.id), "section_id": str(section.id), "answer_start": 5,
              "answer_end": len(text) + 10},
    )
    assert out_of_range.status_code == 422
    other = make_document(db_session, doc_type="past_submission")
    wrong_section = await app_client.post(
        f"/documents/{other.id}/pairs",
        json={"item_id": str(item.id), "section_id": str(section.id), "answer_start": 0,
              "answer_end": 5},
    )
    assert wrong_section.status_code == 422


async def test_fidelity_fix_into_another_section_keeps_the_question_where_it_was_found(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    """A null question_section_id means "same section as section_id". When the fix moves the
    answer to another row, a question that was located in the old row must stay pinned to it,
    so GET /library/items/{id} returns a question_locator that slices the question, not a
    piece of the new answer row."""
    document = make_document(db_session, doc_type="past_submission")
    question = "How do you train new users?"
    question_row = make_section(db_session, document, f"{question} | retyped answer text")
    answer_row = make_section(
        db_session, document, "Every site receives two on-site training days and e-learning."
    )
    item = make_item(
        db_session, document, question_row, question_text=question,
        answer_text="retyped answer", text_verified=False, with_embeddings=False,
    )
    item.question_start, item.question_end = 0, len(question)
    unlocated = make_item(
        db_session, document, question_row, question_text="Who leads onboarding?",
        answer_text="retyped answer", text_verified=False, with_embeddings=False,
    )
    db_session.flush()

    moved = await app_client.post(
        f"/documents/{document.id}/pairs",
        json={"item_id": str(item.id), "section_id": str(answer_row.id), "answer_start": 0,
              "answer_end": len(answer_row.text)},
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()["item"]
    assert body["text_verified"] is True and body["section_id"] == str(answer_row.id)
    assert body["question_section_id"] == str(question_row.id)
    assert (body["question_start"], body["question_end"]) == (0, len(question))

    detail = (await app_client.get(f"/library/items/{item.id}")).json()
    assert detail["locator"]["section_id"] == str(answer_row.id)
    locator = detail["question_locator"]
    assert locator["section_id"] == str(question_row.id)
    assert question_row.text[locator["start"] : locator["end"]] == question

    # With no located question there is nothing to pin: the offsets and the pin stay null.
    other = await app_client.post(
        f"/documents/{document.id}/pairs",
        json={"item_id": str(unlocated.id), "section_id": str(answer_row.id),
              "answer_start": 0, "answer_end": 10},
    )
    assert other.status_code == 200, other.text
    assert other.json()["item"]["question_section_id"] is None
    assert other.json()["item"]["question_start"] is None

    # Moving the answer back into the question's own row normalises the pin to null again.
    start = question_row.text.index("retyped")
    back = await app_client.post(
        f"/documents/{document.id}/pairs",
        json={"item_id": str(item.id), "section_id": str(question_row.id),
              "answer_start": start, "answer_end": len(question_row.text)},
    )
    assert back.status_code == 200, back.text
    assert back.json()["item"]["question_section_id"] is None
    db_session.refresh(item)
    assert item.section_id == question_row.id
    assert (item.question_start, item.question_end) == (0, len(question))
    locator = (await app_client.get(f"/library/items/{item.id}")).json()["question_locator"]
    assert locator["section_id"] == str(question_row.id)


async def test_manual_pairing_creates_the_item_and_resolves_the_fragment(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    document = make_document(db_session, doc_type="past_submission")
    question_section = make_section(db_session, document, "How do you train new users?")
    answer_section = make_section(
        db_session, document, "Every site receives two on-site training days and e-learning."
    )
    fragment = make_fragment(db_session, document, question_section, role="question")

    response = await app_client.post(
        f"/documents/{document.id}/pairs",
        json={"question_fragment_id": str(fragment.id), "section_id": str(answer_section.id),
              "answer_start": 0, "answer_end": len(answer_section.text)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "manual_pairing"
    assert body["fragment_id"] == str(fragment.id)
    item = db_session.get(KnowledgeItem, uuid.UUID(body["item"]["id"]))
    assert item is not None
    assert item.item_type == "qa_pair" and item.text_verified is True
    assert item.question_text == "How do you train new users?"
    assert item.answer_text == answer_section.text
    assert item.question_section_id == question_section.id
    assert (item.question_start, item.question_end) == (0, len(question_section.text))
    assert item.answer_embedding is not None
    db_session.refresh(fragment)
    assert fragment.resolved_item_id == item.id

    again = await app_client.post(
        f"/documents/{document.id}/pairs",
        json={"question_fragment_id": str(fragment.id), "section_id": str(answer_section.id),
              "answer_start": 0, "answer_end": 5},
    )
    assert again.status_code == 409, "a fragment is paired once"

    by_text = await app_client.post(
        f"/documents/{document.id}/pairs",
        json={"question_text": "Who leads onboarding?", "section_id": str(answer_section.id),
              "answer_start": 0, "answer_end": 10},
    )
    assert by_text.status_code == 200
    assert by_text.json()["fragment_id"] is None

    neither = await app_client.post(
        f"/documents/{document.id}/pairs",
        json={"section_id": str(answer_section.id), "answer_start": 0, "answer_end": 10},
    )
    assert neither.status_code == 422

    unpaired = await app_client.get(f"/documents/{document.id}/unpaired")
    assert unpaired.json()["fragments"] == []
