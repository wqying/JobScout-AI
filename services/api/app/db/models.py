from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


def updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class OwnerProfile(Base):
    __tablename__ = "owner_profile"
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="singleton_key_one"),
        UniqueConstraint("singleton_key", name="uq_owner_profile_singleton"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    singleton_key: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, server_default="US")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class AppSettings(Base):
    __tablename__ = "app_settings"
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="singleton_key_one"),
        CheckConstraint(
            "remote_preference IN ('any', 'remote', 'hybrid', 'onsite')",
            name="remote_preference_values",
        ),
        UniqueConstraint("singleton_key", name="uq_app_settings_singleton"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    singleton_key: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    role_types: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{internship,new_grad,entry_level}"
    )
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    excluded_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    preferred_locations: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    remote_preference: Mapped[str] = mapped_column(Text, nullable=False, server_default="any")
    notify_current_jobs_on_save: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    email_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    notification_email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class IndustryQuery(Base):
    __tablename__ = "industry_queries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')", name="status_values"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    raw_query: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_query: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, server_default="US")
    interpretation_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name="status_values"
        ),
        CheckConstraint("requested_limit BETWEEN 1 AND 20", name="requested_limit_range"),
        CheckConstraint("continuation_index >= 0", name="continuation_index_nonnegative"),
        UniqueConstraint(
            "root_discovery_run_id",
            "continuation_index",
            name="uq_discovery_runs_root_continuation",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    industry_query_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("industry_queries.id", ondelete="SET NULL"), index=True
    )
    root_discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_runs.id", ondelete="CASCADE"), index=True
    )
    continuation_index: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    raw_query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    requested_limit: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="20")
    created_at: Mapped[datetime] = created_at()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            "verification_status IN ('proposed', 'verified', 'rejected')",
            name="verification_status_values",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    official_domain: Mapped[str | None] = mapped_column(Text, unique=True)
    official_website_url: Mapped[str | None] = mapped_column(Text)
    headquarters_country: Mapped[str | None] = mapped_column(String(2))
    description: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(Text, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class CompanyAlias(Base):
    __tablename__ = "company_aliases"
    __table_args__ = (
        CheckConstraint(
            "alias_type IN ('brand', 'former_name', 'subsidiary', 'abbreviation')",
            name="alias_type_values",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        UniqueConstraint("company_id", "normalized_alias"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    alias_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    created_at: Mapped[datetime] = created_at()


class CompanyLegalEntity(Base):
    __tablename__ = "company_legal_entities"
    __table_args__ = (
        CheckConstraint(
            "match_method IN ('exact', 'ai_proposed', 'owner_verified')",
            name="match_method_values",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        UniqueConstraint("company_id", "normalized_legal_name"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_legal_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    match_method: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class CompanyIndustry(Base):
    __tablename__ = "company_industries"
    __table_args__ = (
        CheckConstraint(
            "relevance_score >= 0 AND relevance_score <= 100", name="relevance_score_range"
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    industry_slug: Mapped[str] = mapped_column(Text, primary_key=True)
    industry_label: Mapped[str] = mapped_column(Text, nullable=False)
    relevance_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at()


class CompanyEvidence(Base):
    __tablename__ = "company_evidence"
    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('industry', 'official_identity', 'careers_page', "
            "'internship_program')",
            name="evidence_type_values",
        ),
        CheckConstraint("status IN ('supports', 'conflicts', 'unknown')", name="status_values"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    evidence_type: Mapped[str] = mapped_column(Text, nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_title: Mapped[str | None] = mapped_column(Text)
    exact_quote: Mapped[str | None] = mapped_column(Text)
    source_domain: Mapped[str] = mapped_column(Text, nullable=False)
    is_official_source: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)


class DiscoveryResult(Base):
    __tablename__ = "discovery_results"
    __table_args__ = (
        CheckConstraint("opportunity_score BETWEEN 0 AND 100", name="opportunity_score_range"),
        CheckConstraint("industry_score BETWEEN 0 AND 100", name="industry_score_range"),
        CheckConstraint("sponsorship_score BETWEEN 0 AND 100", name="sponsorship_score_range"),
        CheckConstraint("internship_score BETWEEN 0 AND 100", name="internship_score_range"),
        CheckConstraint(
            "monitorability_score BETWEEN 0 AND 100", name="monitorability_score_range"
        ),
        CheckConstraint(
            "current_openings_score BETWEEN 0 AND 100", name="current_openings_score_range"
        ),
        UniqueConstraint("discovery_run_id", "company_id", name="uq_discovery_results_run_company"),
        UniqueConstraint("discovery_run_id", "rank", name="uq_discovery_results_run_rank"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    discovery_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_runs.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    opportunity_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    industry_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sponsorship_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    internship_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    monitorability_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    current_openings_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = created_at()


class SavedCompany(Base):
    __tablename__ = "saved_companies"
    __table_args__ = (CheckConstraint("status IN ('active', 'paused')", name="status_values"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notify_current_jobs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ImmigrationDatasetImport(Base):
    __tablename__ = "immigration_dataset_imports"
    __table_args__ = (
        CheckConstraint("dataset_type = 'dol_lca'", name="dataset_type_values"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status_values"),
        UniqueConstraint("dataset_type", "fiscal_year", "source_sha256"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    dataset_type: Mapped[str] = mapped_column(Text, nullable=False)
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    record_layout_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    rows_read: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    rows_accepted: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(Text)


class LcaEmployerYearlyStat(Base):
    __tablename__ = "lca_employer_yearly_stats"
    __table_args__ = (
        CheckConstraint("certified_cases >= 0", name="certified_cases_nonnegative"),
        CheckConstraint("certified_workers >= 0", name="certified_workers_nonnegative"),
        CheckConstraint("denied_cases >= 0", name="denied_cases_nonnegative"),
        CheckConstraint("withdrawn_cases >= 0", name="withdrawn_cases_nonnegative"),
        UniqueConstraint("dataset_import_id", "normalized_legal_employer_name"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    dataset_import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("immigration_dataset_imports.id", ondelete="CASCADE"),
        nullable=False,
    )
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    legal_employer_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_legal_employer_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    certified_cases: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    certified_workers: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    denied_cases: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    withdrawn_cases: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    top_occupations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class CareerSource(Base):
    __tablename__ = "career_sources"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('greenhouse', 'lever', 'ashby', 'smartrecruiters', "
            "'generic_html', 'email_alert', 'unsupported')",
            name="provider_values",
        ),
        CheckConstraint(
            "status IN ('pending_resolution', 'supported', 'degraded', 'unsupported', "
            "'paused', 'assisted', 'retired')",
            name="status_values",
        ),
        CheckConstraint("poll_interval_minutes >= 15", name="poll_interval_minimum"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    careers_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_source_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    provider_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    baseline_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notify_current_jobs_on_baseline: Mapped[bool | None] = mapped_column(Boolean)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("career_sources.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class CareerSourceRepair(Base):
    """An expiring, auditable owner confirmation for a replacement careers URL."""

    __tablename__ = "career_source_repairs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('previewed', 'confirmed', 'expired', 'cancelled')",
            name="status_values",
        ),
        CheckConstraint(
            "action IN ('update_in_place', 'create_replacement', 'reuse_existing', 'reactivate')",
            name="action_values",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    replacing_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("career_sources.id", ondelete="CASCADE"), nullable=False
    )
    replacement_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("career_sources.id", ondelete="SET NULL")
    )
    proposed_url: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_source_key: Mapped[str] = mapped_column(Text, nullable=False)
    provider_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    notify_current_jobs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class ReviewReminder(Base):
    """A local reminder whose due instant is computed by the browser's system clock."""

    __tablename__ = "review_reminders"
    __table_args__ = (
        CheckConstraint("status IN ('scheduled', 'checked', 'dismissed')", name="status_values"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    saved_company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("saved_companies.id", ondelete="CASCADE"), nullable=False
    )
    career_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("career_sources.id", ondelete="SET NULL")
    )
    target_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class EmailAlertImport(Base):
    """Metadata and deduplication evidence for one owner-uploaded RFC 822 message."""

    __tablename__ = "email_alert_imports"
    __table_args__ = (
        CheckConstraint("status IN ('imported', 'no_jobs')", name="status_values"),
        UniqueConstraint("message_id", name="uq_email_alert_imports_message_id"),
        UniqueConstraint("content_sha256", name="uq_email_alert_imports_content_sha256"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    career_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("career_sources.id", ondelete="CASCADE"), nullable=False
    )
    poll_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_poll_runs.id", ondelete="SET NULL")
    )
    filename: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text_excerpt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    links_found: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    jobs_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    jobs_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at()


class SourcePollRun(Base):
    __tablename__ = "source_poll_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'partial', 'failed', 'not_modified')",
            name="status_values",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    career_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("career_sources.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    jobs_received: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    jobs_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    jobs_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    jobs_closed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "role_type IN ('internship', 'new_grad', 'entry_level', 'experienced', 'unknown')",
            name="role_type_values",
        ),
        CheckConstraint("status IN ('active', 'closed')", name="status_values"),
        UniqueConstraint("career_source_id", "canonical_job_key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    career_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("career_sources.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    external_job_id: Mapped[str | None] = mapped_column(Text)
    canonical_job_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    location_text: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str | None] = mapped_column(Text)
    role_type: Mapped[str] = mapped_column(Text, nullable=False)
    description_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    apply_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    consecutive_absences: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class JobSnapshot(Base):
    __tablename__ = "job_snapshots"
    __table_args__ = (UniqueConstraint("job_id", "content_hash"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    poll_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_poll_runs.id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('discovered', 'updated', 'closed', 'reopened')",
            name="event_type_values",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    poll_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_poll_runs.id", ondelete="CASCADE"), nullable=False
    )
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        CheckConstraint("channel IN ('in_app', 'email')", name="channel_values"),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'cancelled')",
            name="status_values",
        ),
        CheckConstraint(
            "delivery_adapter IN ('fake', 'resend', 'unavailable')",
            name="delivery_adapter_values",
        ),
        UniqueConstraint("channel", "event_group_key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    event_group_key: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivery_adapter: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InAppNotification(Base):
    __tablename__ = "in_app_notifications"

    id: Mapped[uuid.UUID] = uuid_pk()
    outbox_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notification_outbox.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    action_url: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class AiRun(Base):
    __tablename__ = "ai_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'invalid')",
            name="status_values",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow: Mapped[str] = mapped_column(Text, nullable=False)
    step: Mapped[str] = mapped_column(Text, nullable=False)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_runs.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    redacted_input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    redacted_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_manifest: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text)
