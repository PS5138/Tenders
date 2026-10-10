"""Provider configuration: blank keys read as unset, the start-up check, and the ``.env`` path."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from app.config import (
    REPO_ROOT,
    ProviderConfigurationError,
    Settings,
    check_provider_configuration,
)


@pytest.fixture
def no_sdk_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check consults the SDKs' alternative variables; make sure the shell has none."""
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


def test_env_file_is_anchored_to_the_repository_root() -> None:
    """A ``.env`` at the repo root is read from any working directory (the README runs the
    API, the worker and alembic from ``backend/``), so the dotenv path must be absolute."""
    env_file = Settings.model_config["env_file"]
    assert isinstance(env_file, str)
    assert Path(env_file).is_absolute()
    assert Path(env_file) == REPO_ROOT / ".env"
    assert (REPO_ROOT / "pyproject.toml").exists(), "REPO_ROOT does not point at the checkout"


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_keys_read_as_unset(settings_override: Callable[..., Settings], blank: str) -> None:
    settings = settings_override(
        anthropic_api_key=blank, openai_api_key=blank, voyage_api_key=blank
    )
    assert settings.anthropic_api_key is None
    assert settings.openai_api_key is None
    assert settings.voyage_api_key is None


def test_real_keys_are_kept(settings_override: Callable[..., Settings]) -> None:
    settings = settings_override(anthropic_api_key=" sk-test ", openai_api_key="k")
    assert settings.anthropic_api_key == " sk-test "
    assert settings.openai_api_key == "k"


def test_check_passes_for_fake_providers(settings_override: Callable[..., Settings]) -> None:
    settings = settings_override(
        llm_provider="fake", embedding_provider="fake", anthropic_api_key="", openai_api_key=""
    )
    check_provider_configuration(settings)  # does not raise


def test_check_raises_for_anthropic_with_empty_key(
    settings_override: Callable[..., Settings], no_sdk_fallbacks: None
) -> None:
    settings = settings_override(
        llm_provider="anthropic", embedding_provider="fake", anthropic_api_key=""
    )
    with pytest.raises(ProviderConfigurationError, match="ANTHROPIC_API_KEY is not set"):
        check_provider_configuration(settings)
    assert issubclass(ProviderConfigurationError, RuntimeError)


def test_check_accepts_anthropic_auth_token_fallback(
    settings_override: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_override(
        llm_provider="anthropic", embedding_provider="fake", anthropic_api_key=""
    )
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    check_provider_configuration(settings)  # the SDK will use the token


def test_check_passes_for_anthropic_with_key(
    settings_override: Callable[..., Settings], no_sdk_fallbacks: None
) -> None:
    settings = settings_override(
        llm_provider="anthropic", embedding_provider="fake", anthropic_api_key="sk-test"
    )
    check_provider_configuration(settings)


@pytest.mark.parametrize(
    ("provider", "variable"),
    [("openai", "OPENAI_API_KEY"), ("voyage", "VOYAGE_API_KEY")],
)
def test_check_raises_for_embedding_provider_without_key(
    settings_override: Callable[..., Settings], provider: str, variable: str
) -> None:
    settings = settings_override(
        llm_provider="fake",
        embedding_provider=provider,
        openai_api_key="",
        voyage_api_key="",
    )
    with pytest.raises(ProviderConfigurationError, match=f"{variable} is not set"):
        check_provider_configuration(settings)


def test_check_builds_no_client(
    settings_override: Callable[..., Settings], no_sdk_fallbacks: None
) -> None:
    """The check must not import or construct an SDK client: it only reads settings."""
    from app.llm.client import _provider_llm, _synthetic_llm, reset_llm
    from app.llm.embeddings import _provider_embedder, _synthetic_embedder, reset_embedder

    settings = settings_override(
        llm_provider="anthropic",
        embedding_provider="openai",
        anthropic_api_key="sk-test",
        openai_api_key="sk-test",
    )
    reset_llm()
    reset_embedder()
    check_provider_configuration(settings)
    for cached in (_provider_llm, _synthetic_llm, _provider_embedder, _synthetic_embedder):
        assert cached.cache_info().currsize == 0


def test_create_app_refuses_a_real_provider_without_its_key(
    settings_override: Callable[..., Settings], no_sdk_fallbacks: None
) -> None:
    """``uvicorn app.main:app`` fails at import with the plain configuration message rather
    than serving an API whose first draft stream ends in an authentication error."""
    from app.main import create_app

    settings_override(llm_provider="anthropic", embedding_provider="fake", anthropic_api_key="")
    with pytest.raises(ProviderConfigurationError, match="ANTHROPIC_API_KEY is not set"):
        create_app()

    settings_override(llm_provider="fake", embedding_provider="openai", openai_api_key="")
    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY is not set"):
        create_app()

    settings_override(llm_provider="fake", embedding_provider="fake")
    app = create_app()
    paths = set(app.openapi()["paths"])  # included routers are flattened here, no database needed
    assert {"/health", "/tenders", "/questions/{question_id}"} <= paths


def test_worker_main_exits_non_zero_on_missing_key(
    settings_override: Callable[..., Settings],
    no_sdk_fallbacks: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``python -m app.worker`` with a blank key stops before touching the database."""
    from app import worker

    settings_override(llm_provider="anthropic", embedding_provider="fake", anthropic_api_key="")

    def _must_not_run() -> None:  # pragma: no cover - reached only when the check is skipped
        raise AssertionError("worker opened a session before the provider check")

    monkeypatch.setattr(worker, "new_session", _must_not_run)
    monkeypatch.setattr(worker, "import_handlers", _must_not_run)
    with pytest.raises(SystemExit) as excinfo:
        worker.main()
    assert excinfo.value.code == 1
    assert os.environ.get("LLM_PROVIDER") == "anthropic"  # the override was in force
