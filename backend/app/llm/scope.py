"""Which providers a unit of work uses: the live ones, or the synthetic stand-ins.

Two switches make work synthetic. ``SYNTHETIC_DEMO=true`` makes the whole deployment synthetic
(it requires both providers to be ``fake``). An organisation with ``synthetic = true`` is
synthetic on its own, whatever the providers are, so a demonstration business keeps its
deterministic, keyless behaviour beside businesses that run on Anthropic and OpenAI. Its
library was embedded by the fake embedder, and a live query vector compared with those would
be meaningless, so the two must never mix within one organisation.

The current organisation's flag travels in a context variable, set once per request (by the
app-level ``org_scope_guard`` dependency), per job (by the worker) and carried into every thread
a request or job starts (``run_in_context``). ``get_llm`` and ``get_embedder`` read it through
``is_synthetic``.
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings

_synthetic_org: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "synthetic_org", default=False
)


def is_synthetic() -> bool:
    """True when the current work must use the synthetic providers."""
    return get_settings().synthetic_demo or _synthetic_org.get()


def synthetic_for_org(session: Session, org_id: uuid.UUID) -> bool:
    """The organisation's ``synthetic`` flag (False for an unknown id). Read per request and per
    job rather than cached, so a flag changed by provisioning applies at once."""
    from app.db.models import Organisation

    return bool(
        session.scalar(select(Organisation.synthetic).where(Organisation.id == org_id))
    )


@contextmanager
def org_scope(synthetic: bool) -> Iterator[None]:
    """Run the enclosed work with the given organisation flag."""
    token = _synthetic_org.set(synthetic)
    try:
        yield
    finally:
        _synthetic_org.reset(token)


def set_org_scope(synthetic: bool) -> contextvars.Token[bool]:
    """Set the flag for the rest of the current context (the API middleware's form)."""
    return _synthetic_org.set(synthetic)


def run_in_context[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Bind ``function`` to the current organisation flag, for a thread pool to run.

    Executor threads do not inherit context variables, so without this a synthetic request's
    background work would fall back to the live providers. The flag's value is captured here
    and set inside each call, rather than sharing one copied ``Context``, because a pool runs
    many calls at once and a ``Context`` cannot be entered by two threads together.
    """
    synthetic = _synthetic_org.get()

    def bound(*args: P.args, **kwargs: P.kwargs) -> R:
        with org_scope(synthetic):
            return function(*args, **kwargs)

    return bound


__all__ = [
    "is_synthetic",
    "org_scope",
    "run_in_context",
    "set_org_scope",
    "synthetic_for_org",
]
