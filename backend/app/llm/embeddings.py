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


# Live providers cap a request by input count and by total tokens (OpenAI: 2,048 inputs and
# about 300,000 tokens; Voyage: 1,000 inputs and 120,000 tokens for voyage-3), and each input by
# its own token limit (8,191 for text-embedding-3-small). ``embed`` therefore sends batches of at
# most ``batch_size`` texts and cuts each text to ``MAX_EMBED_CHARS`` (about 6,000 tokens). The
# cut applies only to the string embedded; stored text is never shortened.
MAX_EMBED_CHARS = 24_000


class Embedder(Protocol):
    dimension: int
    batch_size: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


_TOKEN_RE = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic hash-based embeddings: unigrams weight 1.0, bigrams weight 0.5."""

    batch_size = 10_000

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
    batch_size = 128

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
    batch_size = 64

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


def get_embedder() -> Embedder:
    """The embedder for the current work: the fake one when the deployment or the current
    organisation is synthetic (``app.llm.scope``), whose library it embedded, otherwise the one
    ``EMBEDDING_PROVIDER`` names."""
    from app.llm.scope import is_synthetic

    if is_synthetic():
        return _synthetic_embedder()
    return _provider_embedder()


@lru_cache(maxsize=1)
def _synthetic_embedder() -> Embedder:
    return FakeEmbedder(get_settings().embedding_dimension)


@lru_cache(maxsize=1)
def _provider_embedder() -> Embedder:
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
    _synthetic_embedder.cache_clear()
    _provider_embedder.cache_clear()


def embed(texts: Sequence[str]) -> list[list[float]]:
    """Embed ``texts`` in as few calls as the provider's limits allow, in order. Every vector
    has ``EMBEDDING_DIMENSION`` entries."""
    embedder = get_embedder()
    inputs = [text[:MAX_EMBED_CHARS] for text in texts]
    vectors: list[list[float]] = []
    size = max(1, getattr(embedder, "batch_size", 128))
    for start in range(0, len(inputs), size):
        vectors.extend(embedder.embed(inputs[start : start + size]))
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
