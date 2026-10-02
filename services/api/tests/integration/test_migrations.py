import os

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Set RUN_INTEGRATION_TESTS=1 with local PostgreSQL and Redis running",
)
async def test_migration_created_core_tables() -> None:
    database_url = get_settings().database_url
    engine = create_async_engine(database_url)

    async with engine.connect() as connection:
        table_names = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_table_names()
        )
        app_settings_columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"] for column in inspect(sync_connection).get_columns("app_settings")
            }
        )
        career_source_columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"] for column in inspect(sync_connection).get_columns("career_sources")
            }
        )
        discovery_result_columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]
                for column in inspect(sync_connection).get_columns("discovery_results")
            }
        )
        company_industry_columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]
                for column in inspect(sync_connection).get_columns("company_industries")
            }
        )
        confidence_columns = await connection.run_sync(
            lambda sync_connection: {
                table_name: {
                    column["name"] for column in inspect(sync_connection).get_columns(table_name)
                }
                for table_name in (
                    "company_aliases",
                    "company_legal_entities",
                    "company_evidence",
                )
            }
        )
        discovery_uniques = await connection.run_sync(
            lambda sync_connection: {
                constraint["name"]
                for constraint in inspect(sync_connection).get_unique_constraints(
                    "discovery_results"
                )
            }
        )

    await engine.dispose()
    assert {
        "owner_profile",
        "companies",
        "jobs",
        "notification_outbox",
        "career_source_repairs",
        "review_reminders",
        "email_alert_imports",
    } <= set(table_names)
    assert "timezone" not in app_settings_columns
    assert {
        "baseline_completed_at",
        "notify_current_jobs_on_baseline",
        "retired_at",
        "replaced_by_source_id",
    } <= career_source_columns
    assert {
        "research_source_status",
        "internship_research_reported",
    } <= discovery_result_columns
    assert {
        "rank",
        "opportunity_score",
        "industry_score",
        "sponsorship_score",
        "internship_score",
        "monitorability_score",
        "current_openings_score",
    }.isdisjoint(discovery_result_columns)
    assert "relevance_score" not in company_industry_columns
    assert all("confidence" not in columns for columns in confidence_columns.values())
    assert "uq_discovery_results_run_rank" not in discovery_uniques
