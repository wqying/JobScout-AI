from app.ai.schemas.discovery import NormalizedDiscovery
from app.db import models  # noqa: F401
from app.db.base import Base

EXPECTED_TABLES = {
    "ai_runs",
    "app_settings",
    "career_sources",
    "career_source_repairs",
    "companies",
    "company_aliases",
    "company_evidence",
    "company_industries",
    "company_legal_entities",
    "discovery_results",
    "discovery_runs",
    "email_alert_imports",
    "immigration_dataset_imports",
    "in_app_notifications",
    "industry_queries",
    "job_events",
    "job_snapshots",
    "jobs",
    "lca_employer_yearly_stats",
    "notification_outbox",
    "owner_profile",
    "review_reminders",
    "saved_companies",
    "source_poll_runs",
}


def test_metadata_contains_complete_v1_schema() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_singleton_tables_have_database_constraints() -> None:
    for table_name in ("owner_profile", "app_settings"):
        table = Base.metadata.tables[table_name]
        constraint_names = {constraint.name for constraint in table.constraints}
        assert any(name and "singleton" in name for name in constraint_names)


def test_assisted_source_schema_has_required_columns_and_constraints() -> None:
    source = Base.metadata.tables["career_sources"]
    assert {
        "baseline_completed_at",
        "notify_current_jobs_on_baseline",
        "retired_at",
        "replaced_by_source_id",
    } <= set(source.columns.keys())

    source_checks = " ".join(
        str(constraint.sqltext)
        for constraint in source.constraints
        if hasattr(constraint, "sqltext")
    )
    assert "smartrecruiters" in source_checks
    assert "email_alert" in source_checks
    assert "assisted" in source_checks
    assert "retired" in source_checks

    assert {
        "company_id",
        "replacing_source_id",
        "replacement_source_id",
        "expires_at",
        "confirmed_at",
    } <= set(Base.metadata.tables["career_source_repairs"].columns.keys())
    assert {
        "saved_company_id",
        "career_source_id",
        "due_at",
        "schedule_version",
        "notified_at",
    } <= set(Base.metadata.tables["review_reminders"].columns.keys())
    assert {
        "company_id",
        "career_source_id",
        "poll_run_id",
        "message_id",
        "content_sha256",
    } <= set(Base.metadata.tables["email_alert_imports"].columns.keys())


def test_discovery_schema_is_scoreless_and_keeps_categorical_research_state() -> None:
    results = Base.metadata.tables["discovery_results"]
    assert {
        "research_source_status",
        "internship_research_reported",
    } <= set(results.columns.keys())
    assert {
        "rank",
        "opportunity_score",
        "industry_score",
        "sponsorship_score",
        "internship_score",
        "monitorability_score",
        "current_openings_score",
    }.isdisjoint(results.columns.keys())

    assert "relevance_score" not in Base.metadata.tables["company_industries"].columns
    for table_name in ("company_aliases", "company_legal_entities", "company_evidence"):
        assert "confidence" not in Base.metadata.tables[table_name].columns


def test_ai_website_field_is_required_but_nullable() -> None:
    proposal_schema = NormalizedDiscovery.model_json_schema()["$defs"]["CompanyProposal"]

    assert "official_website_url" in proposal_schema["required"]
    website_options = proposal_schema["properties"]["official_website_url"]["anyOf"]
    assert {option["type"] for option in website_options} == {
        "string",
        "null",
    }
