"""Engine and session management.

``SessionLocal`` is an unbound sessionmaker configured lazily from settings (or explicitly by
``configure_engine`` in tests). ``get_db`` is the FastAPI request dependency; ``new_session``
is for detached tasks and the worker, which must never share a request's session.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

SessionLocal: sessionmaker[Session] = sessionmaker(class_=Session, expire_on_commit=False)

_engine: Engine | None = None


def configure_engine(url: str | None = None, *, engine: Engine | None = None) -> Engine:
    """Bind ``SessionLocal`` to an engine. Called once at start-up, or by tests to rebind.

    The pool built here is sized for the worker's thread pools: ``triage_tender`` and
    ``draft_all`` each hold one connection per thread for a whole judgement or draft, plus the
    job session's progress commits, so the pool holds the larger concurrency with two to spare
    and a small overflow. A caller that passes its own ``engine`` keeps that engine's sizing.
    """
    global _engine
    if engine is None:
        settings = get_settings()
        pool_size = max(settings.triage_concurrency, settings.draft_all_concurrency) + 2
        engine = create_engine(
            url or settings.database_url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=5,
        )
    if _engine is not None and _engine is not engine:
        _engine.dispose()
    _engine = engine
    SessionLocal.configure(bind=engine)
    return engine


def get_engine() -> Engine:
    if _engine is None:
        return configure_engine()
    return _engine


def new_session() -> Session:
    """A fresh session for detached tasks and the worker. The caller closes it."""
    get_engine()
    return SessionLocal()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed afterwards."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()
