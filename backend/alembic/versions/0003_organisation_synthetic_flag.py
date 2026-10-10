"""organisations.synthetic: run an organisation on the synthetic providers

A synthetic organisation (a demonstration business) keeps the deterministic, keyless stand-ins
for the language model and the embedder even when the deployment runs on Anthropic and OpenAI,
and its uploads are limited to the supplied synthetic files. See ``app.llm.scope``.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06 12:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organisations",
        sa.Column("synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("organisations", "synthetic")
