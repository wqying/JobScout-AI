"""remove discovery scores and retain categorical research state

Revision ID: a1b2c3d4e5f6
Revises: b8c9d0e1f2a3
Create Date: 2026-10-02

The downgrade restores structurally compatible neutral numeric columns and legacy
careers/internship JSON. Historical scores, confidence, and source links cannot be
reconstructed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "discovery_results",
        sa.Column("research_source_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "discovery_results",
        sa.Column("internship_research_reported", sa.Boolean(), nullable=True),
    )
    op.execute(
        """
        UPDATE discovery_results AS result
        SET research_source_status = 'matched',
            internship_research_reported = COALESCE(
                (
                    SELECT CASE
                        WHEN jsonb_typeof(industry.evidence_json->'internship_evidence') = 'boolean'
                            THEN (industry.evidence_json->>'internship_evidence')::boolean
                        ELSE false
                    END
                    FROM company_industries AS industry
                    LEFT JOIN discovery_runs AS run ON run.id = result.discovery_run_id
                    LEFT JOIN industry_queries AS research_query
                        ON research_query.id = run.industry_query_id
                    WHERE industry.company_id = result.company_id
                    ORDER BY
                        CASE WHEN industry.industry_slug = research_query.interpretation_json->>'slug'
                            THEN 0 ELSE 1 END,
                        industry.created_at DESC
                    LIMIT 1
                ),
                false
            )
        """
    )
    op.alter_column("discovery_results", "research_source_status", nullable=False)
    op.alter_column("discovery_results", "internship_research_reported", nullable=False)
    op.create_check_constraint(
        "research_source_status_values",
        "discovery_results",
        "research_source_status IN ('matched', 'unmatched')",
    )

    op.execute(
        """
        UPDATE company_industries
        SET evidence_json = jsonb_set(
            evidence_json - 'sources' - 'internship_evidence',
            '{careers_url_status}',
            to_jsonb(
                CASE
                    WHEN evidence_json->>'careers_url_reason' LIKE 'CAREERS_PAGE_LISTING_%'
                        OR evidence_json->>'careers_url_reason' IN (
                        'CAREERS_PAGE_ATS_DISCOVERED',
                        'CAREERS_PAGE_NO_LISTING_FOUND'
                    ) THEN 'page_checked'
                    WHEN NULLIF(evidence_json->>'careers_url', '') IS NOT NULL
                        THEN 'research_linked'
                    WHEN evidence_json->>'careers_url_status' = 'evidence_verified'
                        THEN 'research_linked'
                    ELSE COALESCE(evidence_json->>'careers_url_status', 'not_found')
                END
            ),
            true
        )
        """
    )
    op.execute(
        """
        UPDATE companies AS company
        SET verification_status = 'proposed', verified_at = NULL
        WHERE company.verification_status = 'verified'
          AND NOT EXISTS (
              SELECT 1 FROM saved_companies AS saved
              WHERE saved.company_id = company.id
          )
        """
    )

    op.drop_constraint("uq_discovery_results_run_rank", "discovery_results", type_="unique")
    for name in (
        "opportunity_score",
        "industry_score",
        "sponsorship_score",
        "internship_score",
        "monitorability_score",
        "current_openings_score",
    ):
        op.drop_constraint(
            op.f(f"ck_discovery_results_{name}_range"),
            "discovery_results",
            type_="check",
        )
    for name in (
        "rank",
        "opportunity_score",
        "industry_score",
        "sponsorship_score",
        "internship_score",
        "monitorability_score",
        "current_openings_score",
    ):
        op.drop_column("discovery_results", name)

    op.drop_constraint(
        op.f("ck_company_industries_relevance_score_range"),
        "company_industries",
        type_="check",
    )
    op.drop_column("company_industries", "relevance_score")

    for table in ("company_aliases", "company_legal_entities", "company_evidence"):
        op.drop_constraint(op.f(f"ck_{table}_confidence_range"), table, type_="check")
        op.drop_column(table, "confidence")


def downgrade() -> None:
    op.execute(
        """
        UPDATE company_industries AS industry
        SET evidence_json = jsonb_set(
            jsonb_set(
                industry.evidence_json,
                '{internship_evidence}',
                to_jsonb(COALESCE(
                    (
                        SELECT result.internship_research_reported
                        FROM discovery_results AS result
                        JOIN discovery_runs AS run ON run.id = result.discovery_run_id
                        JOIN industry_queries AS research_query
                            ON research_query.id = run.industry_query_id
                        WHERE result.company_id = industry.company_id
                          AND research_query.interpretation_json->>'slug' = industry.industry_slug
                        ORDER BY result.created_at DESC
                        LIMIT 1
                    ),
                    false
                )),
                true
            ),
            '{careers_url_status}',
            to_jsonb(
                CASE
                    WHEN industry.evidence_json->>'careers_url_status'
                        IN ('research_linked', 'page_checked') THEN 'evidence_verified'
                    ELSE COALESCE(
                        industry.evidence_json->>'careers_url_status',
                        'not_found'
                    )
                END
            ),
            true
        )
        """
    )
    for table in ("company_aliases", "company_legal_entities", "company_evidence"):
        op.add_column(
            table,
            sa.Column(
                "confidence",
                sa.Numeric(precision=4, scale=3),
                nullable=False,
                server_default="0",
            ),
        )
        op.create_check_constraint("confidence_range", table, "confidence >= 0 AND confidence <= 1")

    op.add_column(
        "company_industries",
        sa.Column("relevance_score", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "relevance_score_range",
        "company_industries",
        "relevance_score >= 0 AND relevance_score <= 100",
    )

    op.add_column("discovery_results", sa.Column("rank", sa.SmallInteger(), nullable=True))
    for name in (
        "opportunity_score",
        "industry_score",
        "sponsorship_score",
        "internship_score",
        "monitorability_score",
        "current_openings_score",
    ):
        op.add_column(
            "discovery_results",
            sa.Column(name, sa.SmallInteger(), nullable=False, server_default="0"),
        )
        op.create_check_constraint(
            f"{name}_range", "discovery_results", f"{name} BETWEEN 0 AND 100"
        )
    op.execute(
        """
        WITH ranked AS (
            SELECT result.id,
                   row_number() OVER (
                       PARTITION BY result.discovery_run_id
                       ORDER BY lower(company.canonical_name), company.id
                   ) AS row_number
            FROM discovery_results AS result
            JOIN companies AS company ON company.id = result.company_id
        )
        UPDATE discovery_results AS result
        SET rank = ranked.row_number
        FROM ranked
        WHERE ranked.id = result.id
        """
    )
    op.alter_column("discovery_results", "rank", nullable=False)
    op.create_unique_constraint(
        "uq_discovery_results_run_rank",
        "discovery_results",
        ["discovery_run_id", "rank"],
    )
    op.drop_constraint(
        op.f("ck_discovery_results_research_source_status_values"),
        "discovery_results",
        type_="check",
    )
    op.drop_column("discovery_results", "internship_research_reported")
    op.drop_column("discovery_results", "research_source_status")
