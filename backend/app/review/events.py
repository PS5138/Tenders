"""The audit trail: one function writes every ``events`` row."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.enums import EntityType, EventType
from app.db.models import Event

# Actor recorded for events written by the worker or by pipeline rules with no request behind
# them. Never accepted from a client (see app.api.deps).
SYSTEM_ACTOR = "system"

__all__ = ["SYSTEM_ACTOR", "EntityType", "EventType", "record_event"]


def record_event(
    session: Session,
    entity_type: str | EntityType,
    entity_id: uuid.UUID,
    event_type: str | EventType,
    actor: str,
    payload: dict[str, Any] | None = None,
    *,
    org_id: uuid.UUID | None = None,
) -> Event:
    """Append an event. Flushes but does not commit; the caller owns the transaction.

    ``actor`` is the X-Actor header value for user actions or ``SYSTEM_ACTOR`` otherwise.
    Unknown entity or event types raise ``ValueError`` before anything is written.
    """
    if not actor or not actor.strip():
        raise ValueError("an event needs an actor")
    event = Event(
        org_id=org_id or get_settings().default_org_id,
        entity_type=EntityType(entity_type).value,
        entity_id=entity_id,
        event_type=EventType(event_type).value,
        actor=actor.strip(),
        payload=dict(payload or {}),
    )
    session.add(event)
    session.flush()
    return event
