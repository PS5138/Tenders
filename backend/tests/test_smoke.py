"""Smoke tests: the app imports, health works, migrations and seed applied, the fixture answer
validates against the segment schema with every locator resolving, and the fixture stream
yields the right event sequence."""

from __future__ import annotations

import json
import time
import uuid

import httpx
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import ACTOR_MAX_LENGTH
from app.api.schemas import FixtureAnswerResponse, StoredAnswerRecord
from app.config import DEFAULT_ORG_ID, FIXTURE_DOCUMENT_ID, get_settings
from app.db.models import Document, DocumentSection, Organisation
from app.jobs import enqueue
from app.llm.client import get_llm
from app.llm.embeddings import cosine_similarity, embed

STORED_STATUSES = {"supported", "weak", "unsupported", "human_authored", "connective"}


async def test_health(app_client: httpx.AsyncClient) -> None:
    response = await app_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "llm_provider": "fake", "embedding_provider": "fake",
        "service_secret_enabled": False, "synthetic_demo": False,
    }


def test_migrations_applied_and_vector_extension_present(db_session: Session) -> None:
    version = db_session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version
    extension = db_session.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).scalar_one_or_none()
    assert extension is not None


def test_seed_created_organisation_and_fixture_document(db_session: Session) -> None:
    organisation = db_session.get(Organisation, DEFAULT_ORG_ID)
    assert organisation is not None
    assert "clinical_safety" in organisation.topic_taxonomy

    document = db_session.get(Document, FIXTURE_DOCUMENT_ID)
    assert document is not None
    assert document.doc_type == "reference"
    assert document.doc_kind == "other"
    assert document.classification_confirmed is True
    assert document.effective_date_source == "user"
    assert document.ingest_status == "ready"

    sections = db_session.scalars(
        select(DocumentSection).where(DocumentSection.document_id == FIXTURE_DOCUMENT_ID)
    ).all()
    assert 6 <= len(sections) <= 8
    assert any(section.table_index is not None for section in sections)
    assert any(section.cell_ref for section in sections)


async def test_fixture_answer_satisfies_the_plan(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    response = await app_client.get("/fixtures/answer")
    assert response.status_code == 200, response.text
    body = response.json()

    payload = FixtureAnswerResponse.model_validate(body)
    # The stored schema forbids `pending`: it is a wire status only.
    answer = StoredAnswerRecord.model_validate(body["answer"])

    statuses = {segment.support_status for segment in answer.segments}
    assert statuses == STORED_STATUSES
    assert [segment.index for segment in answer.segments] == list(range(len(answer.segments)))
    assert any(len(segment.sources) >= 2 for segment in answer.segments)
    assert any(
        source.source_type == "fact" for segment in answer.segments for source in segment.sources
    )
    assert any(
        source.source_type == "human_attestation"
        for segment in answer.segments
        for source in segment.sources
    )
    disputed = [segment for segment in answer.segments if segment.dispute is not None]
    assert len(disputed) == 1
    assert disputed[0].support_status == "unsupported"
    assert disputed[0].sources, "a disputed segment keeps its sources for context"

    assert answer.gaps
    acknowledged = {ack.gap for ack in payload.question.gap_acknowledgements}
    assert acknowledged & set(answer.gaps)
    assert answer.fact_checklist
    assert answer.verbatim is not None and answer.verbatim.segments
    assert answer.verbatim_offer_item_id == answer.verbatim.source_item_id
    assert answer.support_summary.substantive > answer.support_summary.supported > 0
    assert answer.prompt_version == "synthesis.v2"
    assert answer.model == get_settings().model_main

    # Every locator resolves to a seeded fixture section and every located quote is exact.
    located = 0
    for segment in [*answer.segments, *answer.verbatim.segments]:
        for source in segment.sources:
            if source.source_type == "human_attestation":
                continue
            section = db_session.get(DocumentSection, source.locator.section_id)
            assert section is not None, f"section {source.locator.section_id} missing"
            assert section.document_id == source.locator.document_id == FIXTURE_DOCUMENT_ID
            if source.locator.start is not None:
                assert section.text[source.locator.start : source.locator.end] == source.quote
                located += 1
            else:
                assert source.locator.end is None
    assert located > 0
    assert any(
        source.source_type != "human_attestation" and source.locator.start is None
        for segment in answer.segments
        for source in segment.sources
    ), "an unsupported segment keeps its candidate source with null offsets"

    assert payload.question.current_answer is not None
    assert payload.question.current_answer.id == answer.id
    assert payload.question.status == "ai_draft"
    assert payload.question.needs_review is True
    assert payload.thread.messages[-1].answer_id == answer.id
    assert payload.thread.messages[-1].segments is not None


async def test_fixture_locator_opens_through_sections_endpoint(
    app_client: httpx.AsyncClient,
) -> None:
    answer = (await app_client.get("/fixtures/answer")).json()["answer"]
    source = next(
        source
        for segment in answer["segments"]
        for source in segment["sources"]
        if source["source_type"] != "human_attestation" and source["locator"]["start"] is not None
    )
    locator = source["locator"]
    response = await app_client.get(
        f"/sections/{locator['section_id']}",
        params={"start": locator["start"], "end": locator["end"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["highlight"] == {"start": locator["start"], "end": locator["end"]}
    assert body["section"]["text"][locator["start"] : locator["end"]] == source["quote"]
    assert body["document"]["id"] == locator["document_id"]

    out_of_range = await app_client.get(
        f"/sections/{locator['section_id']}", params={"start": 0, "end": 10_000}
    )
    assert out_of_range.json()["highlight"] is None

    missing = await app_client.get(f"/sections/{uuid.uuid4()}")
    assert missing.status_code == 404


async def _collect_events(client: httpx.AsyncClient, url: str) -> list[dict]:
    async with client.stream("POST", url, json={}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        return [json.loads(line) async for line in response.aiter_lines() if line.strip()]


async def test_fixture_stream_event_sequence(app_client: httpx.AsyncClient) -> None:
    answer = (await app_client.get("/fixtures/answer")).json()["answer"]
    events = await _collect_events(app_client, "/fixtures/draft?delay_ms=0")
    count = len(answer["segments"])

    assert [event["type"] for event in events] == (
        ["verbatim"] + ["segment"] * count + ["gaps", "fact_checklist"] + ["support"] * count
        + ["done"]
    )
    assert events[0]["source_item_id"] == answer["verbatim_offer_item_id"]
    assert events[0]["segments"]

    segments = [event for event in events if event["type"] == "segment"]
    assert [segment["index"] for segment in segments] == list(range(count))
    assert all(segment["support_status"] == "pending" for segment in segments)
    assert all(
        source["locator"]["start"] is None and source["locator"]["end"] is None
        for segment in segments
        for source in segment["sources"]
    )
    assert [segment["text"] for segment in segments] == [s["text"] for s in answer["segments"]]

    gaps = next(event for event in events if event["type"] == "gaps")
    assert gaps["gaps"] == answer["gaps"]
    checklist = next(event for event in events if event["type"] == "fact_checklist")
    assert checklist["fact_checklist"] == answer["fact_checklist"]

    supports = [event for event in events if event["type"] == "support"]
    assert [support["index"] for support in supports] == list(range(count))
    assert [support["support_status"] for support in supports] == [
        segment["support_status"] for segment in answer["segments"]
    ]
    assert [support["sources"] for support in supports] == [
        segment["sources"] for segment in answer["segments"]
    ]

    done = events[-1]
    assert done["id"] == answer["id"]
    assert done["segments"] == answer["segments"]
    assert done["support_summary"] == answer["support_summary"]
    assert done["word_count"] == answer["word_count"]


async def test_fixture_stream_fail_after(app_client: httpx.AsyncClient) -> None:
    events = await _collect_events(app_client, "/fixtures/draft?fail_after=3&delay_ms=0")
    assert [event["type"] for event in events] == [
        "verbatim", "segment", "segment", "segment", "error",
    ]
    assert events[-1]["code"] == "fixture_failure"
    assert events[-1]["message"]


async def test_fixture_stream_default_pacing(app_client: httpx.AsyncClient) -> None:
    started = time.monotonic()
    events = await _collect_events(app_client, "/fixtures/draft?fail_after=1")
    elapsed = time.monotonic() - started
    assert [event["type"] for event in events] == ["verbatim", "segment", "error"]
    assert elapsed >= 0.25, "two gaps of about 150 ms are expected by default"


async def test_mutating_requests_require_an_actor(app_client: httpx.AsyncClient) -> None:
    missing = await app_client.post("/tenders", headers={"X-Actor": ""})
    assert missing.status_code == 400
    reserved = await app_client.post("/tenders", headers={"X-Actor": "system"})
    assert reserved.status_code == 400
    read_without_actor = await app_client.get("/health", headers={"X-Actor": ""})
    assert read_without_actor.status_code == 200

    # An actor wider than the audit-trail columns is refused up front with 400, not at the
    # database with a 500 on the first write that stamps it.
    too_long = await app_client.post(
        "/tenders", json={"name": "T"}, headers={"X-Actor": "a" * (ACTOR_MAX_LENGTH + 1)}
    )
    assert too_long.status_code == 400, too_long.text
    assert str(ACTOR_MAX_LENGTH) in too_long.json()["detail"]

    at_limit = "b" * ACTOR_MAX_LENGTH
    created = await app_client.post("/tenders", json={"name": "T"}, headers={"X-Actor": at_limit})
    assert created.status_code == 201, created.text
    # ... and one at the limit is stamped into an event without error.
    stamped = await app_client.patch(
        f"/tenders/{created.json()['id']}", json={"outcome": "won"}, headers={"X-Actor": at_limit}
    )
    assert stamped.status_code == 200, stamped.text


async def test_cors_lets_the_browser_front_end_call_the_api(app_client: httpx.AsyncClient) -> None:
    """The Next.js front end runs on another origin and sends X-Actor on every write, which
    forces a preflight; the middleware must answer it and mark ordinary responses."""
    origin = "http://localhost:3000"
    preflight = await app_client.options(
        "/tenders",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-actor,x-org-id,content-type",
        },
    )
    assert preflight.status_code == 200, preflight.text
    assert preflight.headers["access-control-allow-origin"] == origin
    allowed_headers = preflight.headers["access-control-allow-headers"].lower()
    assert "x-actor" in allowed_headers and "x-org-id" in allowed_headers
    assert "content-type" in allowed_headers
    assert "POST" in preflight.headers["access-control-allow-methods"]

    # The 400 for a missing actor still carries the CORS headers, so the body is readable.
    missing_actor = await app_client.post(
        "/tenders", json={"name": "T"}, headers={"Origin": origin, "X-Actor": ""}
    )
    assert missing_actor.status_code == 400
    assert missing_actor.headers["access-control-allow-origin"] == origin

    # Headers the plan returns only as headers are exposed to scripts.
    read = await app_client.get("/health", headers={"Origin": origin})
    assert read.headers["access-control-allow-origin"] == origin
    exposed = read.headers["access-control-expose-headers"].lower()
    assert "x-document-id" in exposed and "content-disposition" in exposed

    # Origins are an explicit list, never a wildcard.
    other = await app_client.options(
        "/tenders",
        headers={"Origin": "http://elsewhere.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in other.headers
    assert other.status_code == 400


async def test_cors_origins_come_from_settings(settings_override) -> None:  # noqa: ANN001
    """``CORS_ORIGINS`` is a comma-separated list on ``Settings``; the app built from it admits
    exactly those origins and no longer the development default."""
    from app.main import cors_origins, create_app

    settings = settings_override(cors_origins=" https://bids.example, http://localhost:3001 ,")
    assert settings.cors_origins.strip()
    assert cors_origins() == ["https://bids.example", "http://localhost:3001"]

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for origin in ("https://bids.example", "http://localhost:3001"):
            preflight = await client.options(
                "/tenders",
                headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
            )
            assert preflight.status_code == 200, preflight.text
            assert preflight.headers["access-control-allow-origin"] == origin
            read = await client.get("/health", headers={"Origin": origin})
            assert read.headers["access-control-allow-origin"] == origin

        dropped = await client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert dropped.status_code == 200
        assert "access-control-allow-origin" not in dropped.headers

    settings_override(cors_origins="")
    assert cors_origins() == ["http://localhost:3000"], "an empty setting keeps the default"


async def test_get_job(app_client: httpx.AsyncClient, db_session: Session) -> None:
    job = enqueue(
        db_session, "triage_tender", {"tender_id": str(uuid.uuid4()), "actor": "test user"}, total=3
    )
    response = await app_client.get(f"/jobs/{job.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(job.id)
    assert body["kind"] == "triage_tender"
    assert body["status"] == "queued"
    assert body["total"] == 3
    assert body["done"] == 0
    assert body["results"] == []
    assert "payload" not in body

    missing = await app_client.get(f"/jobs/{uuid.uuid4()}")
    assert missing.status_code == 404


def test_fake_llm_and_embeddings(fake_llm, fake_embeddings) -> None:  # noqa: ANN001
    class Classification(BaseModel):
        doc_type: str
        confidence: float = 1.0

    fake_llm.register("classify_document", {"doc_type": "reference"})
    result = get_llm().parse(
        "classify_document",
        model=get_settings().model_fast,
        system="classify",
        user="some text",
        output_model=Classification,
    )
    assert result.doc_type == "reference"
    assert fake_llm.calls[0].name == "classify_document"

    fake_llm.register("synthesis", '{"text": "Hello world."}\n')
    streamed = "".join(get_llm().stream_text("synthesis", model="m", system="s", user="u"))
    assert json.loads(streamed) == {"text": "Hello world."}

    vectors = embed(
        ["clinical safety case for the care record", "clinical safety officer", "public liability"]
    )
    assert all(len(vector) == get_settings().embedding_dimension for vector in vectors)
    assert cosine_similarity(vectors[0], vectors[1]) > cosine_similarity(vectors[0], vectors[2])
    assert embed(["clinical safety"]) == embed(["clinical safety"]), "deterministic"
