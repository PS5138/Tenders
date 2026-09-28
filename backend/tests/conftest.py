"""Shared fixtures.

- ``database_url`` / ``engine``: a session-scoped throwaway Postgres 16 with pgvector from the
  ``pgserver`` package, migrated with Alembic and seeded once.
- ``db_session``: a function-scoped session inside a transaction that is rolled back, so
  tests never see each other's writes (commits inside code become savepoints).
- ``app_client``: httpx over the ASGI app with ``X-Actor: test user`` and ``get_db`` bound to
  ``db_session``.
- ``fake_llm`` / ``fake_embeddings``: the fake providers, selected through settings overrides.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import httpx
import pgserver
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]

# The whole suite runs offline: force the fake providers before any app module is imported.
os.environ["LLM_PROVIDER"] = "fake"
os.environ["EMBEDDING_PROVIDER"] = "fake"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("VOYAGE_API_KEY", None)


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Start a temporary Postgres server (pgserver bundles pgvector) and yield its URL."""
    pgdata = tmp_path_factory.mktemp("pgserver") / "data"
    server = pgserver.get_server(pgdata, cleanup_mode="delete")
    server.psql("CREATE DATABASE tenders_test")
    uri = server.get_uri("tenders_test").replace("postgresql://", "postgresql+psycopg://", 1)
    try:
        yield uri
    finally:
        server.cleanup()


@pytest.fixture(scope="session")
def engine(database_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    """Point settings at the test database, run the migrations and the seed once."""
    os.environ["DATABASE_URL"] = database_url
    os.environ["STORAGE_PATH"] = str(tmp_path_factory.mktemp("storage"))

    from app.config import get_settings
    from app.db.seed import seed
    from app.db.session import configure_engine

    get_settings.cache_clear()
    # The suite passes its own engine with SQLAlchemy's default QueuePool sizing (5 + 10):
    # ``configure_engine`` otherwise sizes the pool for the worker's thread pools, and the
    # triage pool-capacity test relies on the default numbers.
    test_engine = configure_engine(engine=create_engine(database_url, pool_pre_ping=True))

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.attributes["configure_logger"] = False
    command.upgrade(alembic_cfg, "head")

    with Session(test_engine) as session:
        seed(session)
        session.commit()

    try:
        yield test_engine
    finally:
        test_engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    """A session whose outer transaction is rolled back after the test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
async def app_client(db_session: Session) -> AsyncIterator[httpx.AsyncClient]:
    from app.db.session import get_db
    from app.main import app

    def override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"X-Actor": "test user"},
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def settings_override(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., object]]:
    """``settings_override(llm_provider="fake", ...)`` sets environment variables and rebuilds
    the cached settings; everything is restored afterwards."""
    from app.config import get_settings

    def apply(**values: object) -> object:
        for key, value in values.items():
            monkeypatch.setenv(key.upper(), str(value))
        get_settings.cache_clear()
        return get_settings()

    try:
        yield apply
    finally:
        get_settings.cache_clear()


@pytest.fixture
def fake_llm(settings_override: Callable[..., object]) -> Iterator[object]:
    from app.llm.client import FakeLLM, get_llm, reset_llm

    settings_override(llm_provider="fake")
    reset_llm()
    llm = get_llm()
    assert isinstance(llm, FakeLLM)
    try:
        yield llm
    finally:
        llm.reset()
        reset_llm()


@pytest.fixture
def fake_embeddings(settings_override: Callable[..., object]) -> Iterator[object]:
    from app.llm.embeddings import FakeEmbedder, get_embedder, reset_embedder

    settings_override(embedding_provider="fake")
    reset_embedder()
    embedder = get_embedder()
    assert isinstance(embedder, FakeEmbedder)
    try:
        yield embedder
    finally:
        reset_embedder()
