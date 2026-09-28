"""The library API: items, item detail with variants and locator, facts, supersession
decisions."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import httpx
from sqlalchemy.orm import Session

from app.db.models import SupersessionDecision
from app.ingest.dedup import deduplicate
from app.ingest.supersession import evaluate_document_supersession
from tests.test_supersession_factories import (  # noqa: F401 - fixture import
    ORG,
    InvalidationRecorder,
    invalidations_fixture,
    make_document,
    make_fact,
    make_item,
    make_section,
)

ANSWER = "We hold ISO 27001 certification covering the platform and its hosting."


async def test_items_list_and_detail(app_client: httpx.AsyncClient, db_session: Session) -> None:
    older_doc = make_document(
        db_session, filename="old.docx", doc_type="past_submission", effective_date=date(2024, 1, 1)
    )
    newer_doc = make_document(
        db_session, filename="new.docx", doc_type="past_submission", effective_date=date(2025, 1, 1)
    )
    section = make_section(
        db_session, newer_doc, "Q: Are you certified? A: " + ANSWER, heading_path=["3", "3.1"]
    )
    older = make_item(db_session, older_doc, answer_text=ANSWER, canonical=False)
    newer = make_item(db_session, newer_doc, section, answer_text=ANSWER, canonical=False)
    newer.question_start, newer.question_end = 3, 21
    unverified = make_item(db_session, newer_doc, section, text_verified=False, canonical=False)
    deduplicate(db_session, ORG, [older, newer])
    make_fact(db_session, newer_doc, section, item=newer)

    listing = await app_client.get("/library/items")
    assert listing.status_code == 200, listing.text
    rows = {row["id"]: row for row in listing.json()}
    assert {str(older.id), str(newer.id), str(unverified.id)} <= set(rows)
    assert rows[str(newer.id)]["document"]["filename"] == "new.docx"
    assert rows[str(newer.id)]["is_canonical"] is True
    assert rows[str(older.id)]["canonical_id"] == str(newer.id)
    assert rows[str(unverified.id)]["text_verified"] is False

    verified_only = await app_client.get("/library/items", params={"text_verified": "true"})
    assert str(unverified.id) not in {row["id"] for row in verified_only.json()}
    by_doc = await app_client.get("/library/items", params={"document_id": str(older_doc.id)})
    assert [row["id"] for row in by_doc.json()] == [str(older.id)]
    canonical = await app_client.get("/library/items", params={"canonical_only": "true"})
    assert str(older.id) not in {row["id"] for row in canonical.json()}

    detail = await app_client.get(f"/library/items/{newer.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["locator"] == {
        "document_id": str(newer_doc.id),
        "section_id": str(section.id),
        "start": newer.answer_start,
        "end": newer.answer_end,
        "page": None,
        "table": None,
        "cell_ref": None,
        "heading_path": ["3", "3.1"],
    }
    assert section.text[body["locator"]["start"] : body["locator"]["end"]] == ANSWER
    assert body["question_locator"]["start"] == 3 and body["question_locator"]["end"] == 21
    assert [variant["id"] for variant in body["variants"]] == [str(older.id)]
    assert body["variants"][0]["document"]["filename"] == "old.docx"
    assert len(body["facts"]) == 1 and body["facts"][0]["is_current"] is True

    from_variant = await app_client.get(f"/library/items/{older.id}")
    assert [variant["id"] for variant in from_variant.json()["variants"]] == [str(newer.id)]
    assert (await app_client.get(f"/library/items/{uuid.uuid4()}")).status_code == 404


async def test_facts_list_with_supersession_chain(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    older_doc = make_document(db_session, filename="iso-2024.pdf", effective_date=date(2024, 1, 1))
    newer_doc = make_document(db_session, filename="iso-2025.pdf", effective_date=date(2025, 1, 1))
    older = make_fact(db_session, older_doc)
    newer = make_fact(db_session, newer_doc)
    older.superseded_by = newer.id
    expired = make_fact(
        db_session,
        newer_doc,
        fact_kind="cyber_essentials_plus",
        expires_on=date.today() - timedelta(days=1),
    )
    keyed = make_fact(
        db_session, newer_doc, fact_kind="insurance_cover", fact_key="cyber", value="£5m"
    )
    db_session.flush()

    response = await app_client.get("/library/facts")
    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()}
    assert rows[str(older.id)]["superseded_by"] == str(newer.id)
    assert rows[str(older.id)]["is_current"] is False
    assert rows[str(newer.id)]["is_current"] is True
    assert rows[str(newer.id)]["document"]["filename"] == "iso-2025.pdf"
    assert rows[str(expired.id)]["is_current"] is False, "past expires_on is not current"
    assert rows[str(keyed.id)]["fact_key"] == "cyber"

    current = await app_client.get("/library/facts", params={"current_only": "true"})
    ids = {row["id"] for row in current.json()}
    assert str(newer.id) in ids and str(older.id) not in ids and str(expired.id) not in ids
    by_kind = await app_client.get("/library/facts", params={"fact_kind": "insurance_cover"})
    assert [row["id"] for row in by_kind.json()] == [str(keyed.id)]

    newer_doc.superseded_by = older_doc.id
    db_session.flush()
    after = await app_client.get("/library/facts", params={"document_id": str(newer_doc.id)})
    assert all(row["is_current"] is False for row in after.json()), (
        "facts of a superseded document are not current"
    )


async def test_supersession_decisions_list_and_resolution(
    app_client: httpx.AsyncClient, db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older = make_document(
        db_session, filename="dspt-2024.pdf", doc_kind="dspt_confirmation",
        effective_date=date(2024, 6, 1),
    )
    newer = make_document(
        db_session, filename="dspt-2025.pdf", doc_kind="dspt_confirmation",
        effective_date=date(2025, 6, 1), source="upload_time",
    )
    older_item = make_item(db_session, older)
    evaluate_document_supersession(db_session, newer)

    listing = await app_client.get("/library/supersession-decisions")
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["decision"] == "pending" and row["reason"] == "keyed_kind"
    assert row["doc_kind"] == "dspt_confirmation"
    docs = {doc["filename"]: doc for doc in (row["document_a"], row["document_b"])}
    assert docs["dspt-2024.pdf"]["effective_date"] == "2024-06-01"
    assert docs["dspt-2024.pdf"]["effective_date_source"] == "extracted"
    assert docs["dspt-2025.pdf"]["effective_date_source"] == "upload_time"
    assert docs["dspt-2025.pdf"]["doc_kind"] == "dspt_confirmation"

    missing_id = await app_client.post(
        f"/library/supersession-decisions/{row['id']}", json={"decision": "superseded"}
    )
    assert missing_id.status_code == 422
    outsider = await app_client.post(
        f"/library/supersession-decisions/{row['id']}",
        json={"decision": "superseded", "superseding_document_id": str(uuid.uuid4())},
    )
    assert outsider.status_code == 422
    bad_choice = await app_client.post(
        f"/library/supersession-decisions/{row['id']}", json={"decision": "maybe"}
    )
    assert bad_choice.status_code == 422
    assert (
        await app_client.post(
            f"/library/supersession-decisions/{uuid.uuid4()}", json={"decision": "keep_both"}
        )
    ).status_code == 404

    decided = await app_client.post(
        f"/library/supersession-decisions/{row['id']}",
        json={"decision": "superseded", "superseding_document_id": str(newer.id)},
    )
    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body["decision"] == "superseded"
    assert body["superseding_document_id"] == str(newer.id)
    assert body["decided_by"] == "test user" and body["decided_at"]
    db_session.refresh(older)
    db_session.refresh(older_item)
    assert older.superseded_by == newer.id
    assert older_item.excluded_from_retrieval is True

    pending = await app_client.get("/library/supersession-decisions")
    assert pending.json() == []
    everything = await app_client.get(
        "/library/supersession-decisions", params={"include_decided": "true"}
    )
    assert [r["id"] for r in everything.json()] == [row["id"]]

    kept = await app_client.post(
        f"/library/supersession-decisions/{row['id']}", json={"decision": "keep_both"}
    )
    assert kept.status_code == 200
    assert kept.json()["decision"] == "keep_both"
    db_session.refresh(older)
    db_session.refresh(older_item)
    assert older.superseded_by is None and older_item.excluded_from_retrieval is False
    decision = db_session.get(SupersessionDecision, uuid.UUID(row["id"]))
    assert decision is not None and decision.decided_by == "test user"
