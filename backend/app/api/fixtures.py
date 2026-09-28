"""Fixtures for the front end: a fully populated answer and a replay of the streaming contract.

``POST /fixtures/draft`` is the reference implementation of the stream: ``verbatim``, then
``segment`` events (support_status ``pending``, offsets null), ``gaps``, ``fact_checklist``,
one ``support`` per segment with verified sources, and ``done`` with the full answer. With
``?fail_after=N`` it emits N segments and then ``error``. Neither endpoint persists anything.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import DbSession
from app.api.fixture_data import FixtureBundle, FixtureError, build_fixture
from app.api.schemas import FixtureAnswerResponse
from app.api.streaming import ndjson_response

router = APIRouter(prefix="/fixtures", tags=["fixtures"])

DEFAULT_DELAY_MS = 150


class DraftRequest(BaseModel):
    instruction: str | None = None
    confirm_displace: bool = False


def _bundle(db) -> FixtureBundle:  # noqa: ANN001
    try:
        return build_fixture(db)
    except FixtureError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get("/answer", response_model=FixtureAnswerResponse)
def fixture_answer(db: DbSession) -> FixtureAnswerResponse:
    """{question, answer, thread} in the shapes of GET /questions/{id}, one item of
    GET /questions/{id}/answers and GET /threads/{id}."""
    return _bundle(db).response()


async def _events(
    bundle: FixtureBundle, fail_after: int | None, delay: float
) -> AsyncIterator[dict[str, Any]]:
    async def pause() -> None:
        if delay > 0:
            await asyncio.sleep(delay)

    answer = bundle.answer_json()
    yield {"type": "verbatim", **bundle.verbatim_event}

    segments = bundle.stream_segments
    if fail_after is not None:
        segments = segments[:fail_after]
    for segment in segments:
        await pause()
        yield {"type": "segment", **segment}
    if fail_after is not None:
        await pause()
        yield {
            "type": "error",
            "code": "fixture_failure",
            "message": f"Fixture failure requested after {len(segments)} segment(s).",
        }
        return

    await pause()
    yield {"type": "gaps", "gaps": answer["gaps"]}
    await pause()
    yield {"type": "fact_checklist", "fact_checklist": answer["fact_checklist"]}
    for segment in answer["segments"]:
        await pause()
        yield {
            "type": "support",
            "index": segment["index"],
            "support_status": segment["support_status"],
            "sources": segment["sources"],
        }
    await pause()
    yield {"type": "done", **answer}


@router.post("/draft")
async def fixture_draft(
    db: DbSession,
    body: DraftRequest | None = None,
    fail_after: int | None = Query(
        default=None, ge=0, description="Emit this many segments, then an error event."
    ),
    delay_ms: int = Query(
        default=DEFAULT_DELAY_MS, ge=0, le=5000, description="Gap between events."
    ),
) -> StreamingResponse:
    """Replay the fixture answer over the streaming contract (application/x-ndjson)."""
    bundle = _bundle(db)
    return ndjson_response(_events(bundle, fail_after, delay_ms / 1000))
