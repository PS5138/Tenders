"""Schema-validated LLM calls.

Every call has a ``name`` (the prompt it serves, such as ``"synthesis"``) so the fake
implementation can be scripted per call and so logs and the harness can attribute cost.

Anthropic SDK 1.x: ``client.messages.parse(..., output_format=Model).parsed_output`` for
structured output and ``client.messages.stream(...).text_stream`` for streaming. No
``thinking`` parameters are set: claude-opus-5 and claude-sonnet-5 run adaptive thinking by
default.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import get_settings

T = TypeVar("T", bound=BaseModel)

# Optional prior turns, ``[{"role": "user" | "assistant", "content": "..."}]``, placed before
# the current ``user`` message so synthesis can see thread history.
History = list[dict[str, str]]


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    def parse(
        self,
        name: str,
        *,
        model: str,
        system: str,
        user: str,
        output_model: type[T],
        max_tokens: int = 16000,
        history: History | None = None,
    ) -> T: ...

    def stream_text(
        self,
        name: str,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 64000,
        history: History | None = None,
    ) -> Iterator[str]: ...


def _messages(user: str, history: History | None) -> list[dict[str, str]]:
    return [*(history or []), {"role": "user", "content": user}]


class AnthropicLLM:
    """Real calls through the Anthropic SDK. The client is created lazily."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            # The SDK falls back to ANTHROPIC_API_KEY when api_key is None.
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def parse(
        self,
        name: str,
        *,
        model: str,
        system: str,
        user: str,
        output_model: type[T],
        max_tokens: int = 16000,
        history: History | None = None,
    ) -> T:
        message = self.client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=_messages(user, history),
            output_format=output_model,
        )
        parsed = message.parsed_output
        if parsed is None:
            raise LLMError(
                f"{name}: the model returned no parsable {output_model.__name__} "
                f"(stop_reason={message.stop_reason})"
            )
        return parsed

    def stream_text(
        self,
        name: str,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 64000,
        history: History | None = None,
    ) -> Iterator[str]:
        with self.client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=_messages(user, history),
        ) as stream:
            yield from stream.text_stream


@dataclass
class FakeCall:
    name: str
    model: str
    system: str
    user: str
    output_model: type[BaseModel] | None
    history: History = field(default_factory=list)


class FakeLLMError(LLMError):
    pass


_MISSING = object()
_CHUNK_RE = re.compile(r"\S+\s*|\s+")


class FakeLLM:
    """Deterministic stand-in selected by ``LLM_PROVIDER=fake``.

    ``register(name, response)`` scripts the reply for a call name. A response may be a
    Pydantic instance, a dict, a JSON string (for ``parse``), a plain string or an iterable of
    strings (for ``stream_text``), or a callable receiving
    ``(system=..., user=..., output_model=..., model=..., history=...)`` and returning one of
    those. Unregistered ``parse`` calls fall back to ``output_model()`` when every field has a
    default. Every call is recorded in ``calls``.
    """

    def __init__(self) -> None:
        self._responses: dict[str, Any] = {}
        self.calls: list[FakeCall] = []

    def register(self, name: str, response_or_callable: Any) -> None:
        self._responses[name] = response_or_callable

    def unregister(self, name: str) -> None:
        self._responses.pop(name, None)

    def reset(self) -> None:
        self._responses.clear()
        self.calls.clear()

    def _resolve(self, name: str, **context: Any) -> Any:
        response = self._responses.get(name, _MISSING)
        if callable(response) and not isinstance(response, BaseModel | type):
            response = response(**context)
        return response

    def parse(
        self,
        name: str,
        *,
        model: str,
        system: str,
        user: str,
        output_model: type[T],
        max_tokens: int = 16000,
        history: History | None = None,
    ) -> T:
        self.calls.append(FakeCall(name, model, system, user, output_model, list(history or [])))
        response = self._resolve(
            name, system=system, user=user, output_model=output_model, model=model, history=history
        )
        if response is _MISSING:
            try:
                return output_model()
            except ValidationError as exc:
                raise FakeLLMError(
                    f"FakeLLM has no response registered for call '{name}' and "
                    f"{output_model.__name__} has required fields; call "
                    f"fake_llm.register('{name}', ...) in the test"
                ) from exc
        if isinstance(response, output_model):
            return response
        if isinstance(response, BaseModel):
            return output_model.model_validate(response.model_dump())
        if isinstance(response, Mapping):
            return output_model.model_validate(dict(response))
        if isinstance(response, str):
            return output_model.model_validate_json(response)
        raise FakeLLMError(
            f"FakeLLM response for '{name}' must be a {output_model.__name__}, a dict or a "
            f"JSON string, not {type(response).__name__}"
        )

    def stream_text(
        self,
        name: str,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 64000,
        history: History | None = None,
    ) -> Iterator[str]:
        self.calls.append(FakeCall(name, model, system, user, None, list(history or [])))
        response = self._resolve(
            name, system=system, user=user, output_model=None, model=model, history=history
        )
        if response is _MISSING:
            raise FakeLLMError(f"FakeLLM has no stream response registered for call '{name}'")
        if isinstance(response, BaseModel):
            response = response.model_dump_json()
        if isinstance(response, str):
            yield from (m.group() for m in _CHUNK_RE.finditer(response))
            return
        if isinstance(response, Iterable):
            yield from (str(chunk) for chunk in response)
            return
        raise FakeLLMError(f"FakeLLM stream response for '{name}' must be text or an iterable")


ProviderFactory = Callable[[], LLMClient]


@lru_cache(maxsize=1)
def get_llm() -> LLMClient:
    """The process-wide client, chosen by ``LLM_PROVIDER``. ``reset_llm()`` rebuilds it."""
    settings = get_settings()
    if settings.llm_provider == "fake":
        if settings.synthetic_demo:
            from app.llm.synthetic import HeuristicFakeLLM

            return HeuristicFakeLLM()
        return FakeLLM()
    if settings.llm_provider == "anthropic":
        return AnthropicLLM(api_key=settings.anthropic_api_key)
    raise LLMError(f"unknown LLM_PROVIDER {settings.llm_provider!r}")


def reset_llm() -> None:
    get_llm.cache_clear()
