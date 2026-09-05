"""record the email adapter used for each delivery

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("notification_outbox", sa.Column("delivery_adapter", sa.Text()))
    op.create_check_constraint(
        "delivery_adapter_values",
        "notification_outbox",
        "delivery_adapter IN ('fake', 'resend', 'unavailable')",
    )
    op.execute(
        "UPDATE notification_outbox "
        "SET delivery_adapter = 'fake' "
        "WHERE channel = 'email' AND provider_message_id LIKE 'fake-email-%'"
    )
    op.execute(
        "UPDATE notification_outbox "
        "SET delivery_adapter = 'resend' "
        "WHERE channel = 'email' "
        "AND provider_message_id IS NOT NULL "
        "AND delivery_adapter IS NULL"
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_notification_outbox_delivery_adapter_values"),
        "notification_outbox",
        type_="check",
    )
    op.drop_column("notification_outbox", "delivery_adapter")
