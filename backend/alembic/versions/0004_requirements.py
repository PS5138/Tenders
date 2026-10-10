"""requirements: specification requirements and their compliance class

Compliance class moves to the specification requirements a tender's documents state. This adds
the ``requirements`` table (one row per extracted requirement, with the trace to its section,
the AI suggestion and the human rating), ``tenders.requirements_job_id`` for the latest
``extract_requirements`` run, and three enumerated values guarded by CHECK constraints:
``jobs.kind`` gains ``extract_requirements``, ``events.entity_type`` gains ``requirement`` and
``events.event_type`` gains ``requirement_rated``. As in 0002, each constraint is dropped and
recreated from the enum in code.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10 12:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db import enums as e

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_JOB_KIND = e.JobKind.EXTRACT_REQUIREMENTS.value
NEW_ENTITY_TYPE = e.EntityType.REQUIREMENT.value
NEW_EVENT_TYPE = e.EventType.REQUIREMENT_RATED.value


def _in(column: str, allowed: Sequence[str]) -> str:
    quoted = ", ".join(f"'{value}'" for value in allowed)
    return f"{column} IN ({quoted})"


def _recreate(name: str, table: str, column: str, allowed: Sequence[str]) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, _in(column, allowed))


def upgrade() -> None:
    op.create_table(
        "requirements",
        sa.Column("tender_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("section_id", sa.UUID(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=True),
        sa.Column("end", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("ref", sa.String(length=64), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column(
            "topics",
            postgresql.ARRAY(sa.String(length=64)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("suggested_class", sa.String(length=1), nullable=True),
        sa.Column(
            "suggestion",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("compliance_class", sa.String(length=1), nullable=True),
        sa.Column("compliant_by", sa.Date(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("rated_by", sa.String(length=200), nullable=True),
        sa.Column("rated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in("priority", e.values(e.RequirementPriority)), name="ck_requirements_priority"
        ),
        sa.CheckConstraint(
            _in("suggested_class", e.values(e.ComplianceClass)),
            name="ck_requirements_suggested_class",
        ),
        sa.CheckConstraint(
            _in("compliance_class", e.values(e.ComplianceClass)),
            name="ck_requirements_compliance_class",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"]),
        sa.ForeignKeyConstraint(["section_id"], ["document_sections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tender_id", "key", name="uq_requirements_tender_key"),
    )
    op.create_index(op.f("ix_requirements_org_id"), "requirements", ["org_id"], unique=False)
    op.create_index(
        "ix_requirements_tender_order", "requirements", ["tender_id", "order_index"], unique=False
    )

    op.add_column("tenders", sa.Column("requirements_job_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_tenders_requirements_job_id_jobs",
        "tenders",
        "jobs",
        ["requirements_job_id"],
        ["id"],
        ondelete="SET NULL",
    )

    _recreate("ck_jobs_kind", "jobs", "kind", e.values(e.JobKind))
    _recreate("ck_events_entity_type", "events", "entity_type", e.values(e.EntityType))
    _recreate("ck_events_event_type", "events", "event_type", e.values(e.EventType))


def downgrade() -> None:
    # Rows carrying the new values would violate the old constraints; remove them first.
    op.execute(
        f"DELETE FROM events WHERE event_type = '{NEW_EVENT_TYPE}' "
        f"OR entity_type = '{NEW_ENTITY_TYPE}'"
    )
    op.execute("UPDATE tenders SET requirements_job_id = NULL")
    op.execute(
        "UPDATE jobs SET next_job_id = NULL WHERE next_job_id IN "
        f"(SELECT id FROM jobs WHERE kind = '{NEW_JOB_KIND}')"
    )
    op.execute(f"DELETE FROM jobs WHERE kind = '{NEW_JOB_KIND}'")
    _recreate(
        "ck_events_event_type",
        "events",
        "event_type",
        [value for value in e.values(e.EventType) if value != NEW_EVENT_TYPE],
    )
    _recreate(
        "ck_events_entity_type",
        "events",
        "entity_type",
        [value for value in e.values(e.EntityType) if value != NEW_ENTITY_TYPE],
    )
    _recreate(
        "ck_jobs_kind",
        "jobs",
        "kind",
        [value for value in e.values(e.JobKind) if value != NEW_JOB_KIND],
    )

    op.drop_constraint("fk_tenders_requirements_job_id_jobs", "tenders", type_="foreignkey")
    op.drop_column("tenders", "requirements_job_id")
    op.drop_index("ix_requirements_tender_order", table_name="requirements")
    op.drop_index(op.f("ix_requirements_org_id"), table_name="requirements")
    op.drop_table("requirements")
