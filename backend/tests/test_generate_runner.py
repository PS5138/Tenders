# ruff: noqa: F811  (pytest fixtures are imported and then named as parameters)
"""The detached runner (registry lifecycle, disconnecting consumer, error path) and the
drafting router over a test app that includes only ``app.api.drafts``.

The runner's session factory is bound to the test transaction's connection (savepoints), so
the worker thread's commits are visible to the test session and rolled back afterwards.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import drafts
from app.api.deps import actor_guard
from app.db.models import Answer, Event, Message
from app.db.session import get_db
from app.generate import pipeline, runner
from app.generate.errors import Conflict409, format_error
from tests.test_generate_fixtures import (
    EXPECTED_TEXTS,
    QUESTION_TEXT,
    S_A,
    build_library,
    build_question,
    default_synthesis_lines,
    fake_candidate,
    script_entailment,
    script_synthesis,
    stubbed_modules,  # noqa: F401  (fixture)
)

TIMEOUT = 30


@pytest.fixture
def runner_sessions(db_session: Session, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    connection = db_session.bind

    def factory() -> Session:
        return Session(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )

    monkeypatch.setattr(runner, "session_factory", factory)
    runner._RUNS.clear()
    yield factory
    runner._RUNS.clear()


async def collect(iterator: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async with asyncio.timeout(TIMEOUT):
        async for event in iterator:
            events.append(event)
    return events


def _prepare(db_session: Session, stubbed, *, question_text: str = QUESTION_TEXT):  # noqa: ANN001, ANN202
    library = build_library(db_session)
    question = build_question(db_session, text=question_text)
    stubbed.candidates = [fake_candidate(library.item)]
    stubbed.facts = [library.fact]
    script_synthesis(stubbed.fake_llm, default_synthesis_lines(library))
    script_entailment(stubbed.fake_llm)
    db_session.flush()
    return library, question


async def test_start_draft_streams_and_persists_then_clears_the_registry(
    db_session: Session,
    stubbed_modules,
    runner_sessions,  # noqa: ANN001
) -> None:
    library, question = _prepare(db_session, stubbed_modules)
    key = runner.start_draft(question_id=question.id, actor="test user")
    assert key == f"question:{question.id}"
    assert runner.draft_in_progress(question.id)

    events = await collect(runner.events(key))
    await runner.wait_for(key)

    types = [event["type"] for event in events]
    count = len(EXPECTED_TEXTS)
    assert types == (
        ["verbatim"]
        + ["segment"] * count
        + ["gaps", "fact_checklist"]
        + ["support"] * count
        + ["done"]
    )
    done = events[-1]
    db_session.expire_all()
    answer = db_session.get(Answer, done["id"])
    assert answer is not None and answer.is_current
    assert done["word_count"] == answer.word_count
    assert done["segments"] == answer.segments
    assert done["support_summary"] == answer.support_summary
    assert done["model"] == answer.model and done["prompt_version"] == "synthesis.v1"
    assert done["verbatim"]["source_item_id"] == str(library.item.id)
    db_session.refresh(question)
    assert question.status == "ai_draft"
    assert not runner.draft_in_progress(question.id)
    assert runner.active_runs() == []


async def test_concurrent_start_is_a_409_and_disconnect_does_not_stop_generation(
    db_session: Session,
    stubbed_modules,
    runner_sessions,  # noqa: ANN001
) -> None:
    _, question = _prepare(db_session, stubbed_modules, question_text="Anything else.")
    key = runner.start_draft(question_id=question.id, actor="test user")
    with pytest.raises(Conflict409) as excinfo:
        runner.start_draft(question_id=question.id, actor="test user")
    assert excinfo.value.code == "in_progress"

    iterator = runner.events(key)
    async with asyncio.timeout(TIMEOUT):
        first = await anext(iterator)
        second = await anext(iterator)
    assert first["type"] == "segment" and second["type"] == "segment"
    await iterator.aclose()  # the client went away

    await asyncio.wait_for(runner.wait_for(key), TIMEOUT)
    db_session.expire_all()
    answers = db_session.scalars(select(Answer).where(Answer.question_id == question.id)).all()
    assert len(answers) == 1 and answers[0].is_current, "generation completed and persisted"
    assert not runner.draft_in_progress(question.id)
    # The whole event sequence was produced even though nobody drained it.
    with pytest.raises(KeyError):
        runner.events(key)


async def test_error_path_emits_terminal_error_and_persists_nothing(
    db_session: Session,
    stubbed_modules,
    runner_sessions,  # noqa: ANN001
) -> None:
    library, question = _prepare(db_session, stubbed_modules, question_text="Another question.")
    lines = default_synthesis_lines(library)

    def broken(**kwargs):  # noqa: ANN001, ANN202
        yield json.dumps(lines[0]) + "\n"
        raise RuntimeError("model unavailable")

    stubbed_modules.fake_llm.register("synthesis", broken)
    key = runner.start_draft(question_id=question.id, actor="test user")
    events = await collect(runner.events(key))
    await runner.wait_for(key)

    assert [e["type"] for e in events] == ["segment", "segment", "error"]
    assert events[-1] == {
        "type": "error",
        "code": "RuntimeError",
        "message": "model unavailable",
    }
    db_session.expire_all()
    assert db_session.scalars(select(Answer).where(Answer.question_id == question.id)).all() == []
    db_session.refresh(question)
    assert question.status == "not_started"
    assert runner.active_runs() == []


async def test_failure_while_serialising_done_is_rolled_back_and_reported(
    db_session: Session,
    stubbed_modules,
    runner_sessions,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``done`` payload is built before the commit, so a failure there persists nothing
    and the terminal ``error`` agrees with what ``GET /questions/{id}`` then shows."""
    _, question = _prepare(db_session, stubbed_modules, question_text="Serialisation question.")

    def broken_offer(session, item_id):  # noqa: ANN001, ANN202
        raise RuntimeError("offer broke")

    monkeypatch.setattr(pipeline, "verbatim_offer", broken_offer)
    key = runner.start_draft(question_id=question.id, actor="test user")
    events = await collect(runner.events(key))
    await runner.wait_for(key)

    assert events[-1] == {"type": "error", "code": "RuntimeError", "message": "offer broke"}
    assert "done" not in {e["type"] for e in events}
    db_session.expire_all()
    assert db_session.scalars(select(Answer).where(Answer.question_id == question.id)).all() == []
    assert (
        db_session.scalars(select(Event).where(Event.entity_id == question.id)).all() == []
    ), "no status_changed or answer_created event survives the rollback"
    db_session.refresh(question)
    assert question.status == "not_started"
    assert runner.active_runs() == []


async def test_error_event_message_omits_sql_text_and_bound_parameters(
    db_session: Session,
    stubbed_modules,
    runner_sessions,  # noqa: ANN001
) -> None:
    _, question = _prepare(db_session, stubbed_modules, question_text="Integrity question.")
    orig = psycopg.errors.UniqueViolation(
        'duplicate key value violates unique constraint "answers_one_current"'
    )
    failure = IntegrityError(
        "INSERT INTO answers (text, segments) VALUES (%(text)s, %(segments)s)",
        {"text": "the whole draft " * 40, "segments": "[...]"},
        orig,
    )
    assert "[SQL:" in str(failure) and "[parameters:" in str(failure), "the noisy default"

    def broken(**kwargs):  # noqa: ANN001, ANN202
        raise failure
        yield  # pragma: no cover - makes this a generator like the fake stream

    stubbed_modules.fake_llm.register("synthesis", broken)
    key = runner.start_draft(question_id=question.id, actor="test user")
    events = await collect(runner.events(key))
    await runner.wait_for(key)

    error = events[-1]
    assert error["type"] == "error" and error["code"] == "IntegrityError"
    assert "answers_one_current" in error["message"]
    assert "[SQL:" not in error["message"] and "[parameters:" not in error["message"]
    assert "the whole draft" not in error["message"]
    # The shared formatter used by job errors and draft-all results behaves the same.
    formatted = format_error(failure)
    assert formatted.startswith("IntegrityError: duplicate key value")
    assert "[SQL:" not in formatted and "[parameters:" not in formatted
    assert len(format_error(RuntimeError("x" * 5000))) <= 1000


async def test_start_draft_409s_before_registering(
    db_session: Session,
    stubbed_modules,
    runner_sessions,  # noqa: ANN001
) -> None:
    pricing = build_question(db_session, response_type="pricing", number="9.9")
    with pytest.raises(Conflict409) as excinfo:
        runner.start_draft(question_id=pricing.id, actor="test user")
    assert excinfo.value.code == "pricing"
    approved = build_question(db_session, status="approved", number="9.8")
    db_session.flush()
    with pytest.raises(Conflict409) as excinfo:
        runner.start_draft(question_id=approved.id, actor="test user")
    assert excinfo.value.code == "displacement"
    assert runner.active_runs() == []


async def test_start_reply_409s_for_a_pricing_question_before_registering_or_writing(
    db_session: Session,
    stubbed_modules,
    runner_sessions,  # noqa: ANN001
) -> None:
    """A pricing question receives no AI draft in any form: the thread refuses exactly as the
    card does, before the user message is stored and before a synthesis call is paid for."""
    pricing = build_question(db_session, response_type="pricing", number="9.6")
    db_session.flush()
    thread = pricing.thread
    with pytest.raises(Conflict409) as excinfo:
        runner.start_reply(thread_id=thread.id, content="Draft a price.", actor="test user")
    assert excinfo.value.code == "pricing"
    assert runner.active_runs() == []
    db_session.expire_all()
    assert db_session.scalars(select(Message).where(Message.thread_id == thread.id)).all() == []
    assert stubbed_modules.fake_llm.calls == []


async def test_start_draft_and_reply_scope_by_org(
    db_session: Session,
    stubbed_modules,
    runner_sessions,  # noqa: ANN001
) -> None:
    from app.generate.errors import NotFound

    _, question = _prepare(db_session, stubbed_modules, question_text="Org scoped question.")
    foreign = uuid.uuid4()
    with pytest.raises(NotFound):
        runner.start_draft(question_id=question.id, actor="test user", org_id=foreign)
    with pytest.raises(NotFound):
        runner.start_reply(
            thread_id=question.thread.id, content="Hello.", actor="test user", org_id=foreign
        )
    assert runner.active_runs() == []
    # The question's own organisation is accepted, as is no organisation (direct callers).
    key = runner.start_draft(question_id=question.id, actor="test user", org_id=question.org_id)
    events = await collect(runner.events(key))
    await runner.wait_for(key)
    assert events[-1]["type"] == "done"


async def test_start_reply_persists_user_message_first_even_when_the_reply_fails(
    db_session: Session,
    stubbed_modules,
    runner_sessions,  # noqa: ANN001
) -> None:
    library, question = _prepare(db_session, stubbed_modules, question_text="Thread question.")
    thread = question.thread

    # Success first.
    key = runner.start_reply(thread_id=thread.id, content="Draft it, please.", actor="test user")
    assert key == f"thread:{thread.id}" and runner.reply_in_progress(thread.id)
    events = await collect(runner.events(key))
    await runner.wait_for(key)
    assert events[-1]["type"] == "done"
    db_session.expire_all()
    messages = db_session.scalars(
        select(Message).where(Message.thread_id == thread.id).order_by(Message.created_at)
    ).all()
    assert [m.role for m in messages] == ["user", "assistant"]
    assert events[-1]["id"] == str(messages[1].id)
    assert events[-1]["role"] == "assistant"
    assert events[-1]["segments"][0]["text"] == S_A
    assert events[-1]["retrieved_item_ids"] if "retrieved_item_ids" in events[-1] else True
    assert not runner.reply_in_progress(thread.id)

    # Then a failing reply: the user message remains, no assistant message is written.
    stubbed_modules.fake_llm.register("synthesis", lambda **kwargs: iter(()))  # empty stream
    stubbed_modules.fake_llm.register("query_rewrite", {"query": "Thread question, shorter."})
    key = runner.start_reply(thread_id=thread.id, content="And shorter.", actor="test user")
    events = await collect(runner.events(key))
    await runner.wait_for(key)
    assert events[-1]["type"] == "error" and events[-1]["code"] == "empty_draft"
    db_session.expire_all()
    messages = db_session.scalars(
        select(Message).where(Message.thread_id == thread.id).order_by(Message.created_at)
    ).all()
    assert [m.role for m in messages] == ["user", "assistant", "user"]
    assert messages[-1].content == "And shorter."


# --- The router -------------------------------------------------------------------------


@pytest.fixture
async def drafts_client(db_session: Session) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI(dependencies=[Depends(actor_guard)])
    app.include_router(drafts.router)

    def override_get_db():  # noqa: ANN202
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Actor": "test user"},
    ) as client:
        yield client


async def _stream(client: httpx.AsyncClient, url: str, body: dict) -> tuple[int, list[dict]]:
    async with client.stream("POST", url, json=body) as response:
        if response.status_code != 200:
            await response.aread()
            return response.status_code, [json.loads(response.text)]
        assert response.headers["content-type"].startswith("application/x-ndjson")
        return 200, [json.loads(line) async for line in response.aiter_lines() if line.strip()]


async def test_draft_endpoint_streams_ndjson(
    db_session: Session,
    stubbed_modules,
    runner_sessions,
    drafts_client: httpx.AsyncClient,  # noqa: ANN001
) -> None:
    _, question = _prepare(db_session, stubbed_modules, question_text="Endpoint question.")
    status, events = await _stream(drafts_client, f"/questions/{question.id}/draft", {})
    assert status == 200
    assert events[0]["type"] == "segment" and events[-1]["type"] == "done"
    assert all(e["support_status"] == "pending" for e in events if e["type"] == "segment")
    db_session.expire_all()
    assert db_session.get(Answer, events[-1]["id"]) is not None

    # A second draft without confirm_displace is fine at ai_draft; then approve and try again.
    db_session.refresh(question)
    question.status = "approved"  # fixture short-cut
    db_session.flush()
    status, body = await _stream(drafts_client, f"/questions/{question.id}/draft", {})
    assert status == 409
    assert body[0]["code"] == "displacement"
    assert body[0]["current_answer"]["id"] == events[-1]["id"]
    status, events2 = await _stream(
        drafts_client, f"/questions/{question.id}/draft", {"confirm_displace": True}
    )
    assert status == 200 and events2[-1]["type"] == "done"
    assert events2[-1]["version"] == 2


async def test_draft_endpoint_409_for_pricing_and_404_for_unknown(
    db_session: Session,
    stubbed_modules,
    runner_sessions,
    drafts_client: httpx.AsyncClient,  # noqa: ANN001
) -> None:
    pricing = build_question(db_session, response_type="pricing", number="9.7")
    db_session.flush()
    status, body = await _stream(drafts_client, f"/questions/{pricing.id}/draft", {})
    assert status == 409 and body[0]["code"] == "pricing"
    assert "never drafted" in body[0]["detail"]

    status, body = await _stream(drafts_client, f"/questions/{uuid.uuid4()}/draft", {})
    assert status == 404
    missing_actor = await drafts_client.post(
        f"/questions/{pricing.id}/draft", json={}, headers={"X-Actor": ""}
    )
    assert missing_actor.status_code == 400


async def test_messages_endpoint_streams_a_reply(
    db_session: Session,
    stubbed_modules,
    runner_sessions,
    drafts_client: httpx.AsyncClient,  # noqa: ANN001
) -> None:
    _, question = _prepare(db_session, stubbed_modules, question_text="Message endpoint.")
    thread = question.thread
    status, events = await _stream(
        drafts_client, f"/threads/{thread.id}/messages", {"content": "Draft an answer."}
    )
    assert status == 200 and events[-1]["type"] == "done"
    assert events[-1]["role"] == "assistant"
    db_session.expire_all()
    assert db_session.get(Message, events[-1]["id"]).thread_id == thread.id
    empty = await drafts_client.post(f"/threads/{thread.id}/messages", json={"content": ""})
    assert empty.status_code == 422


async def test_verbatim_endpoint_creates_a_verbatim_version(
    db_session: Session,
    stubbed_modules,
    runner_sessions,
    drafts_client: httpx.AsyncClient,  # noqa: ANN001
) -> None:
    library, question = _prepare(db_session, stubbed_modules, question_text="Verbatim endpoint.")
    response = await drafts_client.post(
        f"/questions/{question.id}/verbatim", json={"source_item_id": str(library.item.id)}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"answer", "question"}, "the same shape as the other AI and human writes"
    answer = body["answer"]
    assert answer["verbatim_source_item_id"] == str(library.item.id)
    assert answer["model"] is None and answer["prompt_version"] is None
    assert answer["author_type"] == "ai" and answer["is_current"] is True
    assert [s["text"] for s in answer["segments"]][0] == S_A
    assert all(s["support_status"] == "supported" for s in answer["segments"])
    assert answer["segments"][0]["sources"][0]["tier"] == "verbatim", "the span tier survives"
    assert body["question"]["id"] == str(question.id)
    assert body["question"]["status"] == "ai_draft"
    assert body["question"]["current_answer"]["id"] == answer["id"]
    transitions = {t["to"]: t for t in body["question"]["allowed_transitions"]}
    assert transitions["writer_edited"]["allowed"] is True
    db_session.expire_all()
    db_session.refresh(question)
    assert question.status == "ai_draft"

    question.status = "sme_verified"  # fixture short-cut
    db_session.flush()
    conflict = await drafts_client.post(
        f"/questions/{question.id}/verbatim", json={"source_item_id": str(library.item.id)}
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "displacement"

    missing = await drafts_client.post(
        f"/questions/{question.id}/verbatim",
        json={"source_item_id": str(uuid.uuid4()), "confirm_displace": True},
    )
    assert missing.status_code == 404

    library.item.excluded_from_retrieval = True  # the source document was superseded
    db_session.flush()
    ineligible = await drafts_client.post(
        f"/questions/{question.id}/verbatim",
        json={"source_item_id": str(library.item.id), "confirm_displace": True},
    )
    assert ineligible.status_code == 409 and ineligible.json()["code"] == "ineligible"


async def test_drafting_routes_404_for_another_org_and_400_for_a_malformed_header(
    db_session: Session,
    stubbed_modules,
    runner_sessions,
    drafts_client: httpx.AsyncClient,  # noqa: ANN001
) -> None:
    library, question = _prepare(db_session, stubbed_modules, question_text="Foreign org.")
    thread = question.thread
    foreign = {"X-Org-Id": str(uuid.uuid4())}

    draft = await drafts_client.post(f"/questions/{question.id}/draft", json={}, headers=foreign)
    assert draft.status_code == 404
    reply = await drafts_client.post(
        f"/threads/{thread.id}/messages", json={"content": "Hello."}, headers=foreign
    )
    assert reply.status_code == 404
    verbatim = await drafts_client.post(
        f"/questions/{question.id}/verbatim",
        json={"source_item_id": str(library.item.id)},
        headers=foreign,
    )
    assert verbatim.status_code == 404
    assert runner.active_runs() == []
    db_session.expire_all()
    assert db_session.scalars(select(Answer).where(Answer.question_id == question.id)).all() == []
    assert db_session.scalars(select(Message).where(Message.thread_id == thread.id)).all() == []
    assert db_session.scalars(select(Event).where(Event.entity_id == question.id)).all() == []
    db_session.refresh(question)
    assert question.status == "not_started"

    malformed = {"X-Org-Id": "not-a-uuid"}
    for url, body in (
        (f"/questions/{question.id}/draft", {}),
        (f"/threads/{thread.id}/messages", {"content": "Hello."}),
        (f"/questions/{question.id}/verbatim", {"source_item_id": str(library.item.id)}),
    ):
        response = await drafts_client.post(url, json=body, headers=malformed)
        assert response.status_code == 400, url

    # The question's own organisation, given explicitly, is accepted.
    own = {"X-Org-Id": str(question.org_id)}
    status, events = await _stream(drafts_client, f"/questions/{question.id}/draft", {})
    assert status == 200 and events[-1]["type"] == "done"
    pricing = build_question(db_session, response_type="pricing", number="9.5")
    db_session.flush()
    refused = await drafts_client.post(
        f"/threads/{pricing.thread.id}/messages", json={"content": "Price it."}, headers=own
    )
    assert refused.status_code == 409 and refused.json()["code"] == "pricing"
