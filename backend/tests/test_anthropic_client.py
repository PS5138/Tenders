"""The Anthropic client against a mocked HTTP transport: the exact requests the SDK sends,
refusal handling and server-side refusal fallbacks. No network and no key are used."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2 as httpx
import pytest
from pydantic import BaseModel

from app.llm.client import FALLBACK_BETA, AnthropicLLM, LLMError


class Verdict(BaseModel):
    label: str


Handler = Callable[[httpx.Request], httpx.Response]


def make_llm(handler: Handler, *, fallbacks: bool = True) -> tuple[AnthropicLLM, list[dict]]:
    sent: list[dict[str, Any]] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append({"body": json.loads(request.content), "beta": request.headers.get(
            "anthropic-beta", ""
        )})
        return handler(request)

    llm = AnthropicLLM(api_key="sk-test", refusal_fallbacks=fallbacks)
    llm._client = anthropic.Anthropic(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(record)),
    )
    return llm, sent


def message(content: list[dict], stop_reason: str = "end_turn", **extra: Any) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        **extra,
    }


def reply(payload: dict, status: int = 200) -> Handler:
    return lambda request: httpx.Response(status, json=payload)


def sse(events: list[dict]) -> Handler:
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return lambda request: httpx.Response(
        200, content=body.encode(), headers={"content-type": "text/event-stream"}
    )


def stream_events(text_parts: list[str], stop_reason: str) -> list[dict]:
    start = message([], stop_reason=None)  # type: ignore[arg-type]
    events: list[dict] = [{"type": "message_start", "message": start}]
    events.append(
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
    )
    for part in text_parts:
        events.append(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": part}}
        )
    events.append({"type": "content_block_stop", "index": 0})
    events.append(
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
         "usage": {"output_tokens": 5}}
    )
    events.append({"type": "message_stop"})
    return events


def test_parse_on_opus_sends_default_fallbacks_with_its_beta() -> None:
    llm, sent = make_llm(reply(message([{"type": "text", "text": '{"label": "covered"}'}])))
    result = llm.parse(
        "coverage_judgement", model="claude-opus-5", system="s", user="u", output_model=Verdict
    )
    assert result == Verdict(label="covered")
    assert sent[0]["body"]["fallbacks"] == "default"
    assert FALLBACK_BETA in sent[0]["beta"]
    assert "output_config" in sent[0]["body"], "structured output goes through output_config"


def test_parse_on_a_model_without_fallbacks_sends_a_plain_request() -> None:
    llm, sent = make_llm(reply(message([{"type": "text", "text": '{"label": "new"}'}])))
    llm.parse("classify", model="claude-sonnet-5", system="s", user="u", output_model=Verdict)
    assert "fallbacks" not in sent[0]["body"]
    assert FALLBACK_BETA not in sent[0]["beta"]


def test_a_refusal_that_survives_the_fallback_is_an_error() -> None:
    refused = message([], stop_reason="refusal", stop_details={
        "type": "refusal", "category": "cyber", "explanation": None,
    })
    llm, _ = make_llm(reply(refused))
    with pytest.raises(LLMError, match="declined.*cyber"):
        llm.parse("synthesis", model="claude-opus-5", system="s", user="u", output_model=Verdict)


def test_a_rejected_fallback_parameter_falls_back_to_a_plain_request_once() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if "fallbacks" in json.loads(request.content):
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": "fallbacks: not permitted here"}})
        return httpx.Response(200, json=message([{"type": "text", "text": '{"label": "x"}'}]))

    llm, sent = make_llm(handler)
    llm.parse("a", model="claude-opus-5", system="s", user="u", output_model=Verdict)
    llm.parse("b", model="claude-opus-5", system="s", user="u", output_model=Verdict)
    assert ["fallbacks" in call["body"] for call in sent] == [True, False, False], (
        "one rejected attempt, then plain requests for the rest of the process"
    )


def test_stream_yields_text_with_fallbacks_on_opus() -> None:
    llm, sent = make_llm(sse(stream_events(['{"type": "seg', 'ment"}\n'], "end_turn")))
    text = "".join(
        llm.stream_text("synthesis", model="claude-opus-5", system="s", user="u")
    )
    assert text == '{"type": "segment"}\n'
    assert sent[0]["body"]["fallbacks"] == "default" and sent[0]["body"]["stream"] is True


def test_a_refused_stream_raises_after_its_partial_output() -> None:
    llm, _ = make_llm(sse(stream_events(["partial "], "refusal")))
    received: list[str] = []
    with pytest.raises(LLMError, match="declined"):
        for chunk in llm.stream_text("synthesis", model="claude-opus-5", system="s", user="u"):
            received.append(chunk)
    assert received == ["partial "], "the caller sees the partial, then the error"
