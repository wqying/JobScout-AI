"""add assisted source repair, reminders, and email imports

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_career_sources_provider_values"), "career_sources", type_="check")
    op.drop_constraint(op.f("ck_career_sources_status_values"), "career_sources", type_="check")
    op.create_check_constraint(
        "provider_values",
        "career_sources",
        "provider IN ('greenhouse', 'lever', 'ashby', 'smartrecruiters', "
        "'generic_html', 'email_alert', 'unsupported')",
    )
    op.create_check_constraint(
        "status_values",
        "career_sources",
        "status IN ('pending_resolution', 'supported', 'degraded', 'unsupported', "
        "'paused', 'assisted', 'retired')",
    )
    op.add_column("career_sources", sa.Column("baseline_completed_at", sa.DateTime(timezone=True)))
    op.add_column("career_sources", sa.Column("notify_current_jobs_on_baseline", sa.Boolean()))
    op.add_column("career_sources", sa.Column("retired_at", sa.DateTime(timezone=True)))
    op.add_column("career_sources", sa.Column("replaced_by_source_id", sa.UUID()))
    op.create_foreign_key(
        op.f("fk_career_sources_replaced_by_source_id_career_sources"),
        "career_sources",
        "career_sources",
        ["replaced_by_source_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Preserve the old company-wide baseline for sources that existed before this migration.
    op.execute(
        "UPDATE career_sources AS source "
        "SET baseline_completed_at = saved.baseline_completed_at "
        "FROM saved_companies AS saved "
        "WHERE saved.company_id = source.company_id "
        "AND saved.baseline_completed_at IS NOT NULL"
    )

    op.create_table(
        "career_source_repairs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("replacing_source_id", sa.UUID(), nullable=False),
        sa.Column("replacement_source_id", sa.UUID()),
        sa.Column("proposed_url", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("canonical_source_key", sa.Text(), nullable=False),
        sa.Column("provider_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "notify_current_jobs", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('previewed', 'confirmed', 'expired', 'cancelled')",
            name=op.f("ck_career_source_repairs_status_values"),
        ),
        sa.CheckConstraint(
            "action IN ('update_in_place', 'create_replacement', 'reuse_existing', 'reactivate')",
            name=op.f("ck_career_source_repairs_action_values"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_career_source_repairs_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replacing_source_id"],
            ["career_sources.id"],
            name=op.f("fk_career_source_repairs_replacing_source_id_career_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_source_id"],
            ["career_sources.id"],
            name=op.f("fk_career_source_repairs_replacement_source_id_career_sources"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_career_source_repairs")),
    )

    op.create_table(
        "review_reminders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("saved_company_id", sa.UUID(), nullable=False),
        sa.Column("career_source_id", sa.UUID()),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schedule_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("dismissed_at", sa.DateTime(timezone=True)),
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
            "status IN ('scheduled', 'checked', 'dismissed')",
            name=op.f("ck_review_reminders_status_values"),
        ),
        sa.ForeignKeyConstraint(
            ["saved_company_id"],
            ["saved_companies.id"],
            name=op.f("fk_review_reminders_saved_company_id_saved_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["career_source_id"],
            ["career_sources.id"],
            name=op.f("fk_review_reminders_career_source_id_career_sources"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_reminders")),
        sa.UniqueConstraint("target_key", name=op.f("uq_review_reminders_target_key")),
    )
    op.create_index(
        op.f("ix_review_reminders_due_at"), "review_reminders", ["due_at"], unique=False
    )

    op.create_table(
        "email_alert_imports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("career_source_id", sa.UUID(), nullable=False),
        sa.Column("poll_run_id", sa.UUID()),
        sa.Column("filename", sa.Text()),
        sa.Column("message_id", sa.Text()),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.Text()),
        sa.Column("sender", sa.Text()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("text_excerpt", sa.Text(), server_default="", nullable=False),
        sa.Column("links_found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('imported', 'no_jobs')",
            name=op.f("ck_email_alert_imports_status_values"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_email_alert_imports_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["career_source_id"],
            ["career_sources.id"],
            name=op.f("fk_email_alert_imports_career_source_id_career_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["poll_run_id"],
            ["source_poll_runs.id"],
            name=op.f("fk_email_alert_imports_poll_run_id_source_poll_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_alert_imports")),
        sa.UniqueConstraint("message_id", name="uq_email_alert_imports_message_id"),
        sa.UniqueConstraint("content_sha256", name="uq_email_alert_imports_content_sha256"),
    )


def downgrade() -> None:
    op.drop_table("email_alert_imports")
    op.drop_index(op.f("ix_review_reminders_due_at"), table_name="review_reminders")
    op.drop_table("review_reminders")
    op.drop_table("career_source_repairs")
    op.drop_constraint(
        op.f("fk_career_sources_replaced_by_source_id_career_sources"),
        "career_sources",
        type_="foreignkey",
    )
    op.drop_column("career_sources", "replaced_by_source_id")
    op.drop_column("career_sources", "retired_at")
    op.drop_column("career_sources", "notify_current_jobs_on_baseline")
    op.drop_column("career_sources", "baseline_completed_at")
    op.execute("DELETE FROM career_sources WHERE provider IN ('smartrecruiters', 'email_alert')")
    op.execute("UPDATE career_sources SET status = 'paused' WHERE status = 'retired'")
    op.drop_constraint(op.f("ck_career_sources_provider_values"), "career_sources", type_="check")
    op.drop_constraint(op.f("ck_career_sources_status_values"), "career_sources", type_="check")
    op.create_check_constraint(
        "provider_values",
        "career_sources",
        "provider IN ('greenhouse', 'lever', 'ashby', 'generic_html', 'unsupported')",
    )
    op.create_check_constraint(
        "status_values",
        "career_sources",
        "status IN ('pending_resolution', 'supported', 'degraded', 'unsupported', 'paused')",
    )
