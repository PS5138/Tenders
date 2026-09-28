"""Newline-delimited JSON streaming (the plan's streaming contract).

Streaming endpoints are POST with a JSON body; the response is ``application/x-ndjson``, HTTP
200 as soon as the request is accepted, one JSON object per line with a ``type`` field, and
``done`` or ``error`` always the last line.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

from fastapi.responses import StreamingResponse
from pydantic_core import to_jsonable_python

NDJSON_MEDIA_TYPE = "application/x-ndjson"


def ndjson_line(event: Mapping[str, Any]) -> str:
    """One event as a JSON line. UUIDs, dates and Pydantic models are made JSON-able."""
    if "type" not in event:
        raise ValueError("every stream event needs a 'type'")
    return json.dumps(to_jsonable_python(event), ensure_ascii=False) + "\n"


def ndjson_response(events: AsyncIterator[Mapping[str, Any]]) -> StreamingResponse:
    """Turn an async iterator of event dicts into a streaming NDJSON response."""

    async def body() -> AsyncIterator[bytes]:
        async for event in events:
            yield ndjson_line(event).encode("utf-8")

    return StreamingResponse(
        body(),
        media_type=NDJSON_MEDIA_TYPE,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
