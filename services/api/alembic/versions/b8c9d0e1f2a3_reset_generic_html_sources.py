"""reset generic HTML career sources produced under the job-links-v1 rules

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-31

Generic HTML sources created before careers-page resolution (DESIGN_DOC.md Section 13.7) were
accepted without ever opening the page, and their jobs were extracted by a rule that could not tell
an opening from a navigation link. Because generic results are always ``partial``, the lifecycle
rules in Section 14.4 could never close those rows, so they accumulated permanently and cannot be
repaired in place.

The delete cascades to ``jobs``, ``job_snapshots``, ``job_events``, and ``source_poll_runs``.
``saved_companies`` is untouched: a company keeps its saved state and alert baseline and simply has
no source until one is re-resolved, which the source-repair panel already surfaces.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `replaced_by_source_id` is self-referential with ON DELETE SET NULL, so clear the retired-source
    # audit pointers that reference a row this statement is about to remove.
    op.execute(
        """
        UPDATE career_sources
        SET replaced_by_source_id = NULL
        WHERE replaced_by_source_id IN (
            SELECT id FROM career_sources WHERE provider = 'generic_html'
        )
        """
    )
    op.execute("DELETE FROM career_sources WHERE provider = 'generic_html'")


def downgrade() -> None:
    # Deleted rows cannot be reconstructed, and recreating them would restore known-bad data.
    pass
