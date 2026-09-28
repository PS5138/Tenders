"""One ``embed(texts) -> vectors`` function, selected by ``EMBEDDING_PROVIDER``.

Providers: ``openai`` (text-embedding-3-small, 1536) by default, ``voyage`` as the
alternative, ``fake`` for tests. The fake is a normalised bag of hashed unigrams and bigrams,
so texts sharing tokens have a higher cosine similarity and retrieval tests are meaningful.
The vector dimension is ``EMBEDDING_DIMENSION`` for every provider; changing provider is a
migration and a re-embed of the library.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Protocol

from app.config import get_settings


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


_TOKEN_RE = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic hash-based embeddings: unigrams weight 1.0, bigrams weight 0.5."""

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    @staticmethod
    def _slot(token: str, dimension: int) -> tuple[int, float]:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        sign = 1.0 if (value >> 63) & 1 else -1.0
        return value % dimension, sign

    def embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = _TOKEN_RE.findall(text.lower())
        for token in tokens:
            index, sign = self._slot(token, self.dimension)
            vector[index] += sign
        for left, right in zip(tokens, tokens[1:], strict=False):
            index, sign = self._slot(f"{left} {right}", self.dimension)
            vector[index] += 0.5 * sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_one(text) for text in texts]


class OpenAIEmbedder:
    def __init__(self, model: str, dimension: int, api_key: str | None) -> None:
        self.model = model
        self.dimension = dimension
        self._api_key = api_key
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self._api_key)
        return self._client

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(
            model=self.model, input=list(texts), dimensions=self.dimension
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]


class VoyageEmbedder:
    def __init__(self, model: str, dimension: int, api_key: str | None) -> None:
        self.model = model
        self.dimension = dimension
        self._api_key = api_key
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            import voyageai

            self._client = voyageai.Client(api_key=self._api_key)
        return self._client

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        result = self.client.embed(list(texts), model=self.model, output_dimension=self.dimension)
        return [list(vector) for vector in result.embeddings]


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    settings = get_settings()
    dimension = settings.embedding_dimension
    if settings.embedding_provider == "fake":
        return FakeEmbedder(dimension)
    if settings.embedding_provider == "openai":
        return OpenAIEmbedder(settings.embedding_model_openai, dimension, settings.openai_api_key)
    if settings.embedding_provider == "voyage":
        return VoyageEmbedder(settings.embedding_model_voyage, dimension, settings.voyage_api_key)
    raise EmbeddingError(f"unknown EMBEDDING_PROVIDER {settings.embedding_provider!r}")


def reset_embedder() -> None:
    get_embedder.cache_clear()


def embed(texts: Sequence[str]) -> list[list[float]]:
    """Embed ``texts`` in one batched call. Every vector has ``EMBEDDING_DIMENSION`` entries."""
    vectors = get_embedder().embed(list(texts))
    expected = get_settings().embedding_dimension
    for vector in vectors:
        if len(vector) != expected:
            raise EmbeddingError(
                f"embedding provider returned {len(vector)} dimensions, expected {expected}"
            )
    return vectors


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
