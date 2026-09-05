from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CareerSource,
    Company,
    Job,
    JobEvent,
    JobSnapshot,
    SavedCompany,
    SourcePollRun,
)
from app.monitoring.polling import PollingService
from app.monitoring.scheduling import SourceScheduler
from app.monitoring.schemas import FetchResult, NormalizedJob, RawJob

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="Set RUN_INTEGRATION_TESTS=1 with local PostgreSQL and Redis running",
    ),
]


class UnusedHttp:
    async def get(self, _url: str, headers: dict[str, str] | None = None) -> Any:
        del headers
        raise AssertionError("The fake adapter must not perform HTTP")


class MutableAdapter:
    def __init__(self) -> None:
        self.result = result(job_payload())
        self.error: Exception | None = None

    async def fetch_jobs(self, _source: object) -> FetchResult:
        if self.error:
            raise self.error
        return self.result

    def normalize(self, raw: RawJob) -> NormalizedJob:
        return NormalizedJob.model_validate(raw.raw_payload)


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.now
        self.now += timedelta(minutes=1)
        return current


def job_payload(title: str = "Software Engineer Intern") -> dict[str, object]:
    return {
        "external_job_id": "job-1",
        "title": title,
        "location_text": "New York, NY",
        "department": "Engineering",
        "employment_type": "Intern",
        "description_text": "Build reliable systems.",
        "apply_url": "https://jobs.lever.co/acme/job-1/apply",
        "source_posted_at": "2026-08-20T12:00:00Z",
    }


def result(*jobs: dict[str, object], completeness: str = "full") -> FetchResult:
    return FetchResult.model_validate(
        {
            "completeness": completeness,
            "jobs": [
                {"external_job_id": item["external_job_id"], "raw_payload": item} for item in jobs
            ],
            "http_status": 200,
        }
    )


async def saved_source(session: AsyncSession) -> tuple[SavedCompany, CareerSource]:
    company = Company(
        canonical_name="Acme Games",
        normalized_name="acme games",
        official_domain="acme.example",
        official_website_url="https://acme.example/",
        headquarters_country="US",
        verification_status="verified",
    )
    session.add(company)
    await session.flush()
    saved = SavedCompany(company_id=company.id, status="active", notify_current_jobs=False)
    source = CareerSource(
        company_id=company.id,
        provider="lever",
        careers_url="https://jobs.lever.co/acme",
        canonical_source_key="lever:acme",
        provider_config={"site": "acme"},
        status="supported",
        next_poll_at=datetime(2026, 8, 27, 11, 0, tzinfo=UTC),
    )
    session.add_all([saved, source])
    await session.commit()
    return saved, source


async def test_due_source_is_claimed_once_until_lease_expires(
    isolated_session: AsyncSession,
) -> None:
    _, source = await saved_source(isolated_session)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    scheduler = SourceScheduler(isolated_session, clock=lambda: now)

    first = await scheduler.claim_due()
    second = await scheduler.claim_due()

    assert [item[0] for item in first] == [source.id]
    assert second == []

    await scheduler.release_claim(*first[0])
    released = await scheduler.claim_due()
    assert [item[0] for item in released] == [source.id]

    source = await isolated_session.get(CareerSource, source.id)
    assert source is not None
    source.lease_expires_at = now - timedelta(seconds=1)
    await isolated_session.commit()
    recovered = await scheduler.claim_due()
    assert [item[0] for item in recovered] == [source.id]


async def test_poll_lifecycle_is_idempotent_and_closes_only_after_two_full_absences(
    isolated_session: AsyncSession,
) -> None:
    saved, source = await saved_source(isolated_session)
    adapter = MutableAdapter()
    clock = MutableClock()
    service = PollingService(
        isolated_session,
        UnusedHttp(),
        clock=clock,
        adapter_factory=lambda _provider, _http: adapter,
    )

    first = await service.poll_source(source.id)
    saved = await isolated_session.get(SavedCompany, saved.id)
    assert first.status == "succeeded"
    assert first.jobs_created == 1
    assert saved is not None and saved.baseline_completed_at is not None
    assert await isolated_session.scalar(select(func.count()).select_from(JobSnapshot)) == 1
    assert await isolated_session.scalar(select(func.count()).select_from(JobEvent)) == 1

    unchanged = await service.poll_source(source.id)
    assert unchanged.status == "succeeded"
    assert unchanged.jobs_created == unchanged.jobs_updated == unchanged.jobs_closed == 0
    assert await isolated_session.scalar(select(func.count()).select_from(JobSnapshot)) == 1
    assert await isolated_session.scalar(select(func.count()).select_from(JobEvent)) == 1

    adapter.result = result(job_payload("Software Engineer Intern II"))
    changed = await service.poll_source(source.id)
    assert changed.jobs_updated == 1
    assert await isolated_session.scalar(select(func.count()).select_from(JobSnapshot)) == 2
    assert await isolated_session.scalar(select(func.count()).select_from(JobEvent)) == 2

    adapter.result = result()
    await service.poll_source(source.id)
    job = await isolated_session.scalar(select(Job))
    assert job is not None and job.status == "active" and job.consecutive_absences == 1

    adapter.result = result(completeness="partial")
    partial = await service.poll_source(source.id)
    job = await isolated_session.scalar(select(Job))
    assert partial.status == "partial"
    assert job is not None and job.status == "active" and job.consecutive_absences == 1

    adapter.error = TimeoutError("controlled failure")
    failed = await service.poll_source(source.id)
    job = await isolated_session.scalar(select(Job))
    assert failed.status == "failed"
    assert job is not None and job.status == "active" and job.consecutive_absences == 1

    adapter.error = None
    adapter.result = result()
    closed = await service.poll_source(source.id)
    job = await isolated_session.scalar(select(Job))
    assert closed.jobs_closed == 1
    assert job is not None and job.status == "closed" and job.closed_at is not None

    adapter.result = result(job_payload("Software Engineer Intern II"))
    reopened = await service.poll_source(source.id)
    job = await isolated_session.scalar(select(Job))
    events = list(
        (
            await isolated_session.scalars(
                select(JobEvent.event_type).order_by(JobEvent.occurred_at)
            )
        ).all()
    )
    assert reopened.status == "succeeded"
    assert job is not None and job.status == "active" and job.closed_at is None
    assert events == ["discovered", "updated", "closed", "reopened"]
    assert await isolated_session.scalar(select(func.count()).select_from(SourcePollRun)) == 8


async def test_pending_generic_without_server_rendered_jobs_becomes_unsupported(
    isolated_session: AsyncSession,
) -> None:
    _, source = await saved_source(isolated_session)
    source.provider = "generic_html"
    source.status = "pending_resolution"
    source.provider_config = {"rules_version": "generic-html-v1"}
    source.canonical_source_key = "generic_html:acme"
    await isolated_session.commit()
    adapter = MutableAdapter()
    adapter.result = result(completeness="partial")

    run = await PollingService(
        isolated_session,
        UnusedHttp(),
        adapter_factory=lambda _provider, _http: adapter,
    ).poll_source(source.id)
    refreshed = await isolated_session.get(CareerSource, source.id)

    assert run.status == "partial"
    assert refreshed is not None
    assert refreshed.status == "unsupported"
    assert refreshed.next_poll_at is None
    assert refreshed.last_error_code == "NO_SERVER_RENDERED_JOB_LINKS"


async def test_robots_rejection_permanently_stops_generic_polling(
    isolated_session: AsyncSession,
) -> None:
    _, source = await saved_source(isolated_session)
    source.provider = "generic_html"
    source.status = "pending_resolution"
    source.provider_config = {"rules_version": "generic-html-v1"}
    source.canonical_source_key = "generic_html:robots"
    await isolated_session.commit()
    adapter = MutableAdapter()
    adapter.error = ValueError("ROBOTS_DISALLOWED")

    run = await PollingService(
        isolated_session,
        UnusedHttp(),
        adapter_factory=lambda _provider, _http: adapter,
    ).poll_source(source.id)
    refreshed = await isolated_session.get(CareerSource, source.id)

    assert run.status == "failed"
    assert refreshed is not None
    assert refreshed.status == "unsupported"
    assert refreshed.next_poll_at is None


async def test_dns_resolution_failure_keeps_source_retryable(
    isolated_session: AsyncSession,
) -> None:
    _, source = await saved_source(isolated_session)
    adapter = MutableAdapter()
    adapter.error = ValueError("URL_DNS_FAILED")

    run = await PollingService(
        isolated_session,
        UnusedHttp(),
        adapter_factory=lambda _provider, _http: adapter,
    ).poll_source(source.id)
    refreshed = await isolated_session.get(CareerSource, source.id)

    assert run.status == "failed"
    assert run.error_code == "URL_DNS_FAILED"
    assert refreshed is not None
    assert refreshed.status == "supported"
    assert refreshed.next_poll_at is not None
