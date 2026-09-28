"""Exception text that is safe to store and show.

``error_detail`` and ``format_error`` turn an arbitrary exception into text that can be kept in
``jobs.error``, ``documents.ingest_error``, a per-item job result or a stream ``error`` event
and shown to the user where the action started, without the noise that some libraries put into
``str(exc)``: a SQLAlchemy statement error otherwise embeds the failing SQL and every bound
parameter (a whole draft), and an Anthropic status error embeds the provider's raw error body.
Shared by the job queue, the ingest and triage handlers and the draft pipeline; the draft
package re-exports both names from ``app.generate.errors``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import StatementError

try:  # the SDK is a runtime dependency; guarded so the fake-only test suite never depends on it
    from anthropic import APIStatusError
except ImportError:  # pragma: no cover
    APIStatusError = None  # type: ignore[assignment,misc]

MAX_ERROR_LENGTH = 1000


def _provider_message(exc: Any) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(body.get("message"), str):
            return body["message"]
    return None


def error_detail(exc: BaseException, *, limit: int = MAX_ERROR_LENGTH) -> str:
    """The exception's own message, without SQL text, bound parameters or raw provider bodies,
    truncated to ``limit`` characters. Empty when the exception carries no message."""
    if isinstance(exc, StatementError):
        # ``str(exc)`` appends ``[SQL: ...] [parameters: ...]``; the driver's message is enough.
        text = str(exc.orig) if exc.orig is not None else "database statement failed"
    elif APIStatusError is not None and isinstance(exc, APIStatusError):
        text = f"model provider returned HTTP {exc.status_code}"
        provider = _provider_message(exc)
        if provider:
            text = f"{text}: {provider}"
    else:
        text = str(exc)
    text = " ".join(text.split())
    return text[:limit]


def format_error(exc: BaseException, *, limit: int = MAX_ERROR_LENGTH) -> str:
    """``Type: message`` for job errors and per-item results, sanitised as ``error_detail``."""
    name = type(exc).__name__
    detail = error_detail(exc, limit=limit)
    text = f"{name}: {detail}" if detail else name
    return text[:limit]


__all__ = ["MAX_ERROR_LENGTH", "error_detail", "format_error"]
