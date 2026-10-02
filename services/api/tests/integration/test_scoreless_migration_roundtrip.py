"""Exercise the destructive schema change in a database made only for this test."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from app.core.config import get_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="Set RUN_INTEGRATION_TESTS=1 with local PostgreSQL running",
    ),
]

API_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "b8c9d0e1f2a3"


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _alembic(revision: str, database_url: str, *, downgrade: bool = False) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    environment["PYTHONPATH"] = str(API_ROOT)
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade" if downgrade else "upgrade", revision],
        cwd=API_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


async def _seed_legacy_data(database_url: str) -> tuple[UUID, UUID]:
    alpha_id, zulu_id = uuid4(), uuid4()
    query_id, run_id, import_id = uuid4(), uuid4(), uuid4()
    connection = await asyncpg.connect(_asyncpg_dsn(database_url))
    try:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO owner_profile (id, display_name) VALUES ($1, 'Owner')",
                uuid4(),
            )
            await connection.execute("INSERT INTO app_settings (id) VALUES ($1)", uuid4())
            await connection.execute(
                """INSERT INTO immigration_dataset_imports
                   (id, dataset_type, fiscal_year, source_url, source_sha256,
                    record_layout_version, status)
                   VALUES ($1, 'dol_lca', 2025, 'https://dol.gov/test.csv',
                           $2, 'v1', 'succeeded')""",
                import_id,
                "a" * 64,
            )
            await connection.execute(
                """INSERT INTO lca_employer_yearly_stats
                   (id, dataset_import_id, fiscal_year, legal_employer_name,
                    normalized_legal_employer_name, certified_cases)
                   VALUES ($1, $2, 2025, 'Alpha Inc', 'alpha', 3)""",
                uuid4(),
                import_id,
            )
            for company_id, name in ((alpha_id, "Alpha"), (zulu_id, "Zulu")):
                await connection.execute(
                    """INSERT INTO companies
                       (id, canonical_name, normalized_name, verification_status, verified_at)
                       VALUES ($1, $2, $3, 'verified', now())""",
                    company_id,
                    name,
                    name.lower(),
                )
            await connection.execute(
                "INSERT INTO saved_companies (id, company_id, status) VALUES ($1, $2, 'active')",
                uuid4(),
                alpha_id,
            )
            await connection.execute(
                """INSERT INTO industry_queries
                   (id, raw_query, normalized_query, interpretation_json, prompt_version,
                    model_id, status, expires_at)
                   VALUES ($1, 'gaming', 'gaming', '{"slug":"gaming"}'::jsonb,
                           'old', 'fake', 'succeeded', now() + interval '1 day')""",
                query_id,
            )
            await connection.execute(
                """INSERT INTO discovery_runs (id, industry_query_id, raw_query, status)
                   VALUES ($1, $2, 'gaming', 'succeeded')""",
                run_id,
                query_id,
            )
            for company_id, name, gaming_internship, misc_internship, reason in (
                (alpha_id, "Alpha", True, False, "CAREERS_PAGE_LISTING_VERIFIED"),
                (zulu_id, "Zulu", False, True, "CAREERS_SOURCE_STRUCTURED_VERIFIED"),
            ):
                gaming_evidence = {
                    "careers_url": f"https://{name.lower()}.com/careers",
                    "careers_url_status": "evidence_verified",
                    "careers_url_reason": reason,
                    "internship_evidence": gaming_internship,
                    "sources": [{"url": f"https://{name.lower()}.com"}],
                    "monitoring_support": "structured",
                }
                await connection.execute(
                    """INSERT INTO company_industries
                       (company_id, industry_slug, industry_label, relevance_score,
                        evidence_json, created_at)
                       VALUES ($1, 'gaming', 'Gaming', 80, $2::jsonb, now() - interval '1 day')""",
                    company_id,
                    json.dumps(gaming_evidence),
                )
                await connection.execute(
                    """INSERT INTO company_industries
                       (company_id, industry_slug, industry_label, relevance_score,
                        evidence_json, created_at)
                       VALUES ($1, 'other', 'Other', 80, $2::jsonb, now())""",
                    company_id,
                    json.dumps({"internship_evidence": misc_internship}),
                )
                await connection.execute(
                    """INSERT INTO discovery_results
                       (id, discovery_run_id, company_id, rank, opportunity_score,
                        industry_score, sponsorship_score, internship_score,
                        monitorability_score, current_openings_score, explanation)
                       VALUES ($1, $2, $3, $4, 80, 80, 80, 80, 80, 80, 'Legacy result')""",
                    uuid4(),
                    run_id,
                    company_id,
                    2 if name == "Alpha" else 1,
                )
            await connection.execute(
                """INSERT INTO company_aliases
                   (id, company_id, alias, normalized_alias, alias_type, confidence)
                   VALUES ($1, $2, 'Alpha Inc', 'alpha inc', 'brand', 0.75)""",
                uuid4(),
                alpha_id,
            )
            await connection.execute(
                """INSERT INTO company_legal_entities
                   (id, company_id, legal_name, normalized_legal_name, match_method, confidence)
                   VALUES ($1, $2, 'Alpha Inc', 'alpha', 'exact', 0.80)""",
                uuid4(),
                alpha_id,
            )
            await connection.execute(
                """INSERT INTO company_evidence
                   (id, company_id, evidence_type, claim, status, source_url, source_domain,
                    is_official_source, observed_at, confidence)
                   VALUES ($1, $2, 'industry', 'Gaming', 'supports', 'https://alpha.com',
                           'alpha.com', true, now(), 0.90)""",
                uuid4(),
                alpha_id,
            )
    finally:
        await connection.close()
    return alpha_id, zulu_id


async def _assert_upgraded(database_url: str, alpha_id: UUID, zulu_id: UUID) -> None:
    connection = await asyncpg.connect(_asyncpg_dsn(database_url))
    try:
        rows = await connection.fetch(
            """SELECT company.canonical_name, company.verification_status,
                      result.research_source_status, result.internship_research_reported
               FROM discovery_results AS result
               JOIN companies AS company ON company.id = result.company_id
               ORDER BY company.canonical_name"""
        )
        assert [tuple(row.values()) for row in rows] == [
            ("Alpha", "verified", "matched", True),
            ("Zulu", "proposed", "matched", False),
        ]
        gaming = await connection.fetch(
            """SELECT company_id, evidence_json FROM company_industries
               WHERE industry_slug = 'gaming'"""
        )
        by_company = {row["company_id"]: json.loads(row["evidence_json"]) for row in gaming}
        assert by_company[alpha_id]["careers_url_status"] == "page_checked"
        assert by_company[zulu_id]["careers_url_status"] == "research_linked"
        assert all(
            "sources" not in row and "internship_evidence" not in row for row in by_company.values()
        )
        for table, removed in (
            ("discovery_results", {"rank", "opportunity_score"}),
            ("company_industries", {"relevance_score"}),
            ("company_aliases", {"confidence"}),
            ("company_legal_entities", {"confidence"}),
            ("company_evidence", {"confidence"}),
        ):
            columns = {
                row["column_name"]
                for row in await connection.fetch(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = $1""",
                    table,
                )
            }
            assert columns.isdisjoint(removed)
        for table in (
            "owner_profile",
            "app_settings",
            "immigration_dataset_imports",
            "lca_employer_yearly_stats",
        ):
            assert await connection.fetchval(f"SELECT count(*) FROM {table}") == 1
    finally:
        await connection.close()


async def _assert_downgraded(database_url: str) -> None:
    connection = await asyncpg.connect(_asyncpg_dsn(database_url))
    try:
        ranks = await connection.fetch(
            """SELECT company.canonical_name, result.rank, result.opportunity_score
               FROM discovery_results AS result
               JOIN companies AS company ON company.id = result.company_id
               ORDER BY result.rank"""
        )
        assert [tuple(row.values()) for row in ranks] == [
            ("Alpha", 1, 0),
            ("Zulu", 2, 0),
        ]
        assert (
            await connection.fetchval(
                """SELECT count(*) FROM pg_constraint
               WHERE conname = 'uq_discovery_results_run_rank'"""
            )
            == 1
        )
        assert await connection.fetchval("SELECT confidence FROM company_aliases LIMIT 1") == 0
    finally:
        await connection.close()


def test_scoreless_migration_round_trip_preserves_other_data() -> None:
    base_url = make_url(get_settings().database_url)
    database_name = f"jobscout_migration_{uuid4().hex[:12]}"
    admin_url = base_url.set(database="postgres").render_as_string(hide_password=False)
    test_url = base_url.set(database=database_name).render_as_string(hide_password=False)

    async def admin_command(command: str) -> None:
        connection = await asyncpg.connect(_asyncpg_dsn(admin_url))
        try:
            await connection.execute(command)
        finally:
            await connection.close()

    asyncio.run(admin_command(f'CREATE DATABASE "{database_name}"'))
    try:
        _alembic(PREVIOUS_REVISION, test_url)
        alpha_id, zulu_id = asyncio.run(_seed_legacy_data(test_url))
        _alembic("head", test_url)
        asyncio.run(_assert_upgraded(test_url, alpha_id, zulu_id))
        _alembic(PREVIOUS_REVISION, test_url, downgrade=True)
        asyncio.run(_assert_downgraded(test_url))
        _alembic("head", test_url)
        asyncio.run(_assert_upgraded(test_url, alpha_id, zulu_id))
    finally:
        asyncio.run(admin_command(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
