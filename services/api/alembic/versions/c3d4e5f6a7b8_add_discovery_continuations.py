"""add discovery continuations

Revision ID: c3d4e5f6a7b8
Revises: 7b42e8a1c930
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "7b42e8a1c930"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "discovery_runs",
        sa.Column(
            "root_discovery_run_id",
            sa.UUID(),
            nullable=True,
        ),
    )
    op.add_column(
        "discovery_runs",
        sa.Column(
            "continuation_index",
            sa.SmallInteger(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_foreign_key(
        op.f("fk_discovery_runs_root_discovery_run_id_discovery_runs"),
        "discovery_runs",
        "discovery_runs",
        ["root_discovery_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_discovery_runs_root_discovery_run_id"),
        "discovery_runs",
        ["root_discovery_run_id"],
        unique=False,
    )
    op.create_check_constraint(
        "continuation_index_nonnegative",
        "discovery_runs",
        "continuation_index >= 0",
    )
    op.create_unique_constraint(
        "uq_discovery_runs_root_continuation",
        "discovery_runs",
        ["root_discovery_run_id", "continuation_index"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_discovery_runs_root_continuation", "discovery_runs", type_="unique")
    op.drop_constraint(
        op.f("ck_discovery_runs_continuation_index_nonnegative"),
        "discovery_runs",
        type_="check",
    )
    op.drop_index(
        op.f("ix_discovery_runs_root_discovery_run_id"),
        table_name="discovery_runs",
    )
    op.drop_constraint(
        op.f("fk_discovery_runs_root_discovery_run_id_discovery_runs"),
        "discovery_runs",
        type_="foreignkey",
    )
    op.drop_column("discovery_runs", "continuation_index")
    op.drop_column("discovery_runs", "root_discovery_run_id")
