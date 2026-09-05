"""remove the unused owner timezone setting

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("app_settings", "timezone")


def downgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column(
            "timezone",
            sa.Text(),
            nullable=False,
            server_default="America/New_York",
        ),
    )
