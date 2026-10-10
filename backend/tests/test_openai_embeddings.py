"""The OpenAI embedder against a mocked HTTP transport: batching, order and dimensions."""

from __future__ import annotations

import json

import httpx2
import openai
import pytest

from app.llm.embeddings import MAX_EMBED_CHARS, OpenAIEmbedder, embed


def mocked_client(sent: list[dict]) -> openai.OpenAI:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        sent.append(body)
        data = [
            # Out of order on purpose: the embedder must restore the input order by index.
            {"object": "embedding", "index": i, "embedding": [float(len(text))] + [0.0] * 1535}
            for i, text in reversed(list(enumerate(body["input"])))
        ]
        return httpx2.Response(200, json={
            "object": "list", "data": data, "model": body["model"],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        })

    return openai.OpenAI(
        api_key="sk-test", max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def test_embed_batches_in_order_with_the_configured_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict] = []
    embedder = OpenAIEmbedder("text-embedding-3-small", 1536, "sk-test")
    embedder._client = mocked_client(sent)
    monkeypatch.setattr("app.llm.embeddings.get_embedder", lambda: embedder)

    texts = [f"text {i}" for i in range(300)] + ["x" * (MAX_EMBED_CHARS + 500)]
    vectors = embed(texts)

    assert [len(batch["input"]) for batch in sent] == [128, 128, 45]
    assert all(
        batch["dimensions"] == 1536 and batch["model"] == "text-embedding-3-small"
        for batch in sent
    )
    assert [v[0] for v in vectors[:3]] == [6.0, 6.0, 6.0], "order follows the input"
    assert vectors[-1][0] == MAX_EMBED_CHARS, "each input is cut to the embedding limit"
    assert len(vectors) == len(texts) and all(len(v) == 1536 for v in vectors)
