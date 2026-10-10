"""Shared request dependencies: the acting user, the organisation and the database session."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Organisation
from app.db.session import get_db
from app.llm.scope import set_org_scope, synthetic_for_org
from app.review.events import SYSTEM_ACTOR

MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ACTOR_HEADER = "X-Actor"
ORG_HEADER = "X-Org-Id"

# The widest value the audit trail can hold: ``events.actor``, ``comments.author``,
# ``answers.author_name`` and ``supersession_decisions.decided_by`` are all ``String(200)``
# in ``app.db.models``. An over-long actor is a client error and is refused here with 400
# rather than failing at the database on the first write.
ACTOR_MAX_LENGTH = 200

DbSession = Annotated[Session, Depends(get_db)]


def _validate_actor(value: str | None) -> str:
    if value is None or not value.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"The {ACTOR_HEADER} header is required on every mutating request.",
        )
    actor = value.strip()
    if len(actor) > ACTOR_MAX_LENGTH:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"The {ACTOR_HEADER} header must be at most {ACTOR_MAX_LENGTH} characters.",
        )
    if actor.lower() == SYSTEM_ACTOR:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"'{SYSTEM_ACTOR}' is reserved for the worker and is not accepted from clients.",
        )
    return actor


async def actor_guard(request: Request) -> None:
    """App-level dependency: every mutating request must carry a valid X-Actor header."""
    if request.method.upper() in MUTATING_METHODS:
        _validate_actor(request.headers.get(ACTOR_HEADER))


async def org_scope_guard(request: Request, db: DbSession) -> None:
    """App-level dependency: run the request on the providers its organisation calls for.

    Async on purpose: FastAPI awaits it in the request's own context, so the flag it sets
    reaches the handler and every task the handler starts (``app.llm.scope``). A malformed or
    unknown X-Org-Id is left to ``get_org_id`` to refuse; it counts as not synthetic here.
    """
    raw = request.headers.get(ORG_HEADER)
    if raw is None or not raw.strip():
        org_id = get_settings().default_org_id
    else:
        try:
            org_id = uuid.UUID(raw.strip())
        except ValueError:
            return
    set_org_scope(synthetic_for_org(db, org_id))


def get_actor(request: Request) -> str:
    """The acting user's display name, for handlers that stamp it into the audit trail."""
    return _validate_actor(request.headers.get(ACTOR_HEADER))


def get_org_id(
    db: DbSession,
    x_org_id: Annotated[str | None, Header(alias=ORG_HEADER)] = None,
) -> uuid.UUID:
    """Organisation from the X-Org-Id header, defaulting to the seeded organisation.

    The organisation must exist: every org-scoped table carries a foreign key to
    ``organisations``, so an unknown id would otherwise pass every read as an empty list and
    fail every write at flush with a foreign-key violation (a 500) after any uploaded file had
    already been written to disk. A primary-key lookup per request is negligible.
    """
    if x_org_id is None or not x_org_id.strip():
        org_id = get_settings().default_org_id
    else:
        try:
            org_id = uuid.UUID(x_org_id.strip())
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail=f"{ORG_HEADER} must be a UUID."
            ) from exc
    if db.get(Organisation, org_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organisation not found.")
    return org_id


Actor = Annotated[str, Depends(get_actor)]
OrgId = Annotated[uuid.UUID, Depends(get_org_id)]
