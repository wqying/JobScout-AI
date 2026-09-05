"""fix discovery limit constraint name

Revision ID: 7b42e8a1c930
Revises: 2f1a3c7d9e11
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7b42e8a1c930"
down_revision: str | None = "2f1a3c7d9e11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_discovery_runs_ck_discovery_runs_requested_limit_range"),
        "discovery_runs",
        type_="check",
    )
    op.create_check_constraint(
        "requested_limit_range",
        "discovery_runs",
        "requested_limit BETWEEN 1 AND 20",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_discovery_runs_requested_limit_range"),
        "discovery_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_discovery_runs_ck_discovery_runs_requested_limit_range"),
        "discovery_runs",
        "requested_limit BETWEEN 1 AND 20",
    )
