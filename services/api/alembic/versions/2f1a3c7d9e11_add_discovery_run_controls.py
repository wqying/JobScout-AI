"""add discovery run cache and limit controls

Revision ID: 2f1a3c7d9e11
Revises: 954cafea46eb
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2f1a3c7d9e11"
down_revision: str | None = "954cafea46eb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "discovery_runs",
        sa.Column("cache_hit", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "discovery_runs",
        sa.Column("requested_limit", sa.SmallInteger(), server_default="20", nullable=False),
    )
    op.create_check_constraint(
        "ck_discovery_runs_requested_limit_range",
        "discovery_runs",
        "requested_limit BETWEEN 1 AND 20",
    )


def downgrade() -> None:
    op.drop_constraint("ck_discovery_runs_requested_limit_range", "discovery_runs", type_="check")
    op.drop_column("discovery_runs", "requested_limit")
    op.drop_column("discovery_runs", "cache_hit")
