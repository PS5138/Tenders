"""Minimal service boundary required by the authenticated frontend."""

import uuid

import httpx
import pytest


@pytest.mark.asyncio
async def test_secret_protects_health_schema_and_mutations(app_client, settings_override):
    secret = "test-service-secret-32-characters-long"
    settings_override(service_secret=secret)
    for path in ("/health", "/openapi.json", "/tenders"):
        assert (await app_client.get(path)).status_code == 401
        response = await app_client.get(path, headers={"Authorization": f"Bearer {secret}"})
        assert response.status_code == 200
    assert (await app_client.post("/tenders", json={"name": "test"})).status_code == 401


@pytest.mark.asyncio
async def test_provision_is_idempotent_and_initialises_taxonomy(app_client):
    body = {"id": str(uuid.uuid4()), "name": "Synthetic second organisation"}
    first = await app_client.post("/organisations", json=body)
    second = await app_client.post("/organisations", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == body
    assert (await app_client.get("/tenders", headers={"X-Org-Id": body["id"]})).json() == []
    conflict = await app_client.post("/organisations", json={**body, "name": "Different"})
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_original_download_is_scoped_and_byte_identical(app_client: httpx.AsyncClient):
    content = b"Synthetic original file contents."
    upload = await app_client.post("/documents", files={"file": ("synthetic.txt", content)})
    assert upload.status_code == 202
    document_id = upload.json()["id"]
    download = await app_client.get(f"/documents/{document_id}/file")
    assert download.status_code == 200
    assert download.content == content
    assert "synthetic.txt" in download.headers["content-disposition"]
    other = {"id": str(uuid.uuid4()), "name": "Another synthetic organisation"}
    await app_client.post("/organisations", json=other)
    assert (await app_client.get(
        f"/documents/{document_id}/file", headers={"X-Org-Id": other["id"]}
    )).status_code == 404


@pytest.mark.asyncio
async def test_synthetic_demo_reports_mode_and_refuses_unseen_documents(
    app_client, settings_override
):
    settings_override(synthetic_demo=True)
    health = (await app_client.get("/health")).json()
    assert health["synthetic_demo"] is True
    assert health["llm_provider"] == health["embedding_provider"] == "fake"
    upload = await app_client.post("/documents", files={"file": ("unseen.txt", b"Not a fixture")})
    assert upload.status_code == 422


def test_synthetic_provider_is_explicit(settings_override):
    from app.llm.client import get_llm, reset_llm
    from app.llm.synthetic import HeuristicFakeLLM

    settings_override(synthetic_demo=True)
    reset_llm()
    try:
        assert isinstance(get_llm(), HeuristicFakeLLM)
    finally:
        reset_llm()


@pytest.mark.asyncio
async def test_tender_settings_archive_and_restore(app_client):
    tender = (await app_client.post("/tenders", json={"name": "Synthetic settings"})).json()
    changed = await app_client.patch(
        f"/tenders/{tender['id']}",
        json={
            "name": "  Renamed synthetic  ",
            "buyer": "Synthetic Trust",
            "deadline": "2026-12-01T12:00:00Z",
        },
    )
    assert changed.status_code == 200
    body = changed.json()
    assert (body["name"], body["buyer"], body["deadline"][:10]) == (
        "Renamed synthetic",
        "Synthetic Trust",
        "2026-12-01",
    )
    for bad in ({"name": None}, {"name": "   "}, {"status": "submitted"}, {"status": None}):
        assert (await app_client.patch(f"/tenders/{tender['id']}", json=bad)).status_code == 422
    archived = await app_client.patch(f"/tenders/{tender['id']}", json={"status": "archived"})
    assert archived.json()["status"] == "archived"
    restored = await app_client.patch(f"/tenders/{tender['id']}", json={"status": "open"})
    assert restored.json()["status"] == "open"
    cleared = await app_client.patch(
        f"/tenders/{tender['id']}", json={"deadline": None, "buyer": None}
    )
    assert (cleared.json()["deadline"], cleared.json()["buyer"]) == (None, None)


@pytest.mark.asyncio
async def test_delete_removes_an_unsubmitted_tender_and_is_scoped(app_client, db_session):
    from app.db.models import Document, Question, Tender, Thread

    tender = (await app_client.post("/tenders", json={"name": "Synthetic delete"})).json()
    tender_id = uuid.UUID(tender["id"])
    org = uuid.UUID("00000000-0000-4000-8000-000000000001")
    pack = Document(
        org_id=org,
        tender_id=tender_id,
        filename="synthetic-pack.xlsx",
        storage_path="/nonexistent/synthetic-pack.xlsx",
        doc_type="tender_document",
        tender_doc_kind="question_pack",
        classification_confirmed=True,
        ingest_status="ready",
    )
    db_session.add(pack)
    db_session.flush()
    question = Question(
        org_id=org,
        tender_id=tender_id,
        document_id=pack.id,
        section="1",
        number="1.1",
        text="Synthetic question.",
        order_index=0,
    )
    db_session.add(question)
    db_session.flush()
    db_session.add(
        Thread(org_id=org, tender_id=tender_id, question_id=question.id, title="Synthetic thread")
    )
    db_session.flush()
    other = {"id": str(uuid.uuid4()), "name": "Another synthetic organisation for delete"}
    await app_client.post("/organisations", json=other)
    foreign = await app_client.delete(f"/tenders/{tender_id}", headers={"X-Org-Id": other["id"]})
    assert foreign.status_code == 404
    assert (await app_client.delete(f"/tenders/{tender_id}")).status_code == 204
    assert db_session.get(Tender, tender_id) is None
    db_session.expire_all()
    assert db_session.get(Question, question.id) is None
    assert db_session.get(Document, pack.id) is None
    assert (await app_client.get(f"/tenders/{tender_id}")).status_code == 404


@pytest.mark.asyncio
async def test_delete_refuses_a_submitted_tender(app_client):
    tender = (await app_client.post("/tenders", json={"name": "Synthetic submitted"})).json()
    assert (await app_client.post(f"/tenders/{tender['id']}/submit")).status_code == 200
    refused = await app_client.delete(f"/tenders/{tender['id']}")
    assert refused.status_code == 409
    assert "Archive it instead" in refused.json()["detail"]
    path = f"/tenders/{tender['id']}"
    archived = (await app_client.patch(path, json={"status": "archived"})).json()
    restored = (await app_client.patch(path, json={"status": "open"})).json()
    assert (archived["status"], restored["status"]) == ("archived", "submitted")
