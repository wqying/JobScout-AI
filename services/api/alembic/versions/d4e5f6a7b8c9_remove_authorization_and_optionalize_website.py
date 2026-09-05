"""remove authorization evidence and make official website optional

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM company_evidence WHERE evidence_type IN ('cpt', 'work_authorization')")
    op.execute(
        """
        UPDATE company_industries AS industry
        SET evidence_json =
            (industry.evidence_json - 'cpt_evidence_status')
            || jsonb_build_object(
                'sources',
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            source.item
                            || jsonb_build_object(
                                'supports_claims',
                                COALESCE(
                                    (
                                        SELECT jsonb_agg(to_jsonb(claim.value))
                                        FROM jsonb_array_elements_text(
                                            COALESCE(
                                                source.item->'supports_claims',
                                                '[]'::jsonb
                                            )
                                        ) AS claim(value)
                                        WHERE lower(claim.value) NOT IN (
                                            'cpt', 'work_authorization'
                                        )
                                    ),
                                    '[]'::jsonb
                                )
                            )
                        )
                        FROM jsonb_array_elements(
                            COALESCE(industry.evidence_json->'sources', '[]'::jsonb)
                        ) AS source(item)
                    ),
                    '[]'::jsonb
                )
            )
        """
    )
    op.drop_constraint(
        op.f("ck_company_evidence_evidence_type_values"),
        "company_evidence",
        type_="check",
    )
    op.create_check_constraint(
        "evidence_type_values",
        "company_evidence",
        "evidence_type IN ('industry', 'official_identity', 'careers_page', 'internship_program')",
    )
    op.alter_column(
        "companies",
        "official_domain",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.alter_column(
        "companies",
        "official_website_url",
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE companies "
        "SET official_domain = 'manual-' || replace(id::text, '-', '') || '.invalid' "
        "WHERE official_domain IS NULL"
    )
    op.execute(
        "UPDATE companies "
        "SET official_website_url = 'https://' || official_domain "
        "WHERE official_website_url IS NULL"
    )
    op.alter_column(
        "companies",
        "official_website_url",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.alter_column(
        "companies",
        "official_domain",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.drop_constraint(
        op.f("ck_company_evidence_evidence_type_values"),
        "company_evidence",
        type_="check",
    )
    op.create_check_constraint(
        "evidence_type_values",
        "company_evidence",
        "evidence_type IN ('industry', 'official_identity', 'careers_page', "
        "'internship_program', 'cpt', 'work_authorization')",
    )
