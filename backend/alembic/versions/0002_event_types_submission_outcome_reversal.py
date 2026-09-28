"""event types: tender_submitted, outcome_set, supersession_reversed

Event types are strings guarded by a CHECK constraint (see app.db.enums.enum_check), so adding
a value is a drop-and-recreate of ``ck_events_event_type`` from the enum in code. The three new
values record a tender being marked submitted, a tender outcome being set or changed, and a
document exclusion being lifted (keep_both after an automatic supersession, a date change that
flips the direction, a kind change or a stage re-run).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28 18:40:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db import enums as e

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_EVENT_TYPES: tuple[str, ...] = (
    e.EventType.TENDER_SUBMITTED.value,
    e.EventType.OUTCOME_SET.value,
    e.EventType.SUPERSESSION_REVERSED.value,
)
CONSTRAINT = "ck_events_event_type"


def _check_sql(allowed: Sequence[str]) -> str:
    quoted = ", ".join(f"'{value}'" for value in allowed)
    return f"event_type IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "events", type_="check")
    op.create_check_constraint(CONSTRAINT, "events", _check_sql(e.values(e.EventType)))


def downgrade() -> None:
    # Rows carrying the new types would violate the old constraint; remove them first.
    quoted = ", ".join(f"'{value}'" for value in NEW_EVENT_TYPES)
    op.execute(f"DELETE FROM events WHERE event_type IN ({quoted})")
    op.drop_constraint(CONSTRAINT, "events", type_="check")
    previous = [value for value in e.values(e.EventType) if value not in NEW_EVENT_TYPES]
    op.create_check_constraint(CONSTRAINT, "events", _check_sql(previous))
