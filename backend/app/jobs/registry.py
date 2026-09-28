"""Registry mapping job kinds to handler functions. Handlers are registered by the owning
module (ingest, generate) and dispatched by app/worker.py."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.db.enums import JobKind
from app.db.models import Job

JobHandler = Callable[[Session, Job], None]

_HANDLERS: dict[str, JobHandler] = {}


def register(kind: str | JobKind) -> Callable[[JobHandler], JobHandler]:
    """Decorator: ``@register("ingest_document")``. Re-registering a kind replaces the handler."""
    kind_value = JobKind(kind).value

    def decorator(fn: JobHandler) -> JobHandler:
        _HANDLERS[kind_value] = fn
        return fn

    return decorator


def get_handler(kind: str | JobKind) -> JobHandler | None:
    return _HANDLERS.get(JobKind(kind).value)


def registered_kinds() -> list[str]:
    return sorted(_HANDLERS)
