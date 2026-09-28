"""Errors raised by the draft pipeline, the runner and the verbatim path.

``Conflict409`` carries the body the API returns for the plan's 409 cases (displacement without
``confirm_displace``, a run already in progress, a pricing question, an ineligible or empty
verbatim source). ``DraftError`` is a pipeline failure that becomes a terminal ``error`` stream
event with nothing persisted.

``error_detail`` and ``format_error`` (re-exported from ``app.errors``, where the job queue and
the ingest and triage handlers also read them) turn an arbitrary exception into text that can
be shown to the user where the action started, without the noise that some libraries put into
``str(exc)``: a SQLAlchemy statement error otherwise embeds the failing SQL and every bound
parameter (a whole draft), and an Anthropic status error embeds the provider's raw error body.
"""

from __future__ import annotations

from typing import Any

from app.errors import MAX_ERROR_LENGTH, error_detail, format_error


class Conflict409(Exception):
    """A request the plan answers with HTTP 409.

    ``code`` is one of ``displacement``, ``in_progress``, ``pricing``, ``ineligible`` (a
    verbatim source that is unverified or from a superseded document) or ``empty`` (a verbatim
    source whose answer slice holds no sentences). ``current_answer`` is the serialised current
    version on a displacement conflict, so the client can show what would be displaced; it is
    absent otherwise.
    """

    def __init__(
        self, code: str, message: str, *, current_answer: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.current_answer = current_answer

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"detail": self.message, "code": self.code}
        if self.current_answer is not None:
            body["current_answer"] = self.current_answer
        return body


class DraftError(RuntimeError):
    """A pipeline failure. ``code`` is a short machine-readable identifier for the error event."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotFound(LookupError):
    """A referenced row (question, thread, knowledge item) does not exist."""

    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.message = what


__all__ = [
    "MAX_ERROR_LENGTH",
    "Conflict409",
    "DraftError",
    "NotFound",
    "error_detail",
    "format_error",
]
