from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CareerSource,
    Job,
    JobEvent,
    JobSnapshot,
    SavedCompany,
    SourcePollRun,
)
from app.monitoring.adapters import adapter_for
from app.monitoring.http import HttpFetcher
from app.monitoring.job_identity import (
    canonical_job_key,
    canonical_payload,
    classify_role,
    content_hash,
    event_dedupe_key,
)
from app.monitoring.schemas import (
    CareerSourceAdapter,
    CareerSourceConfig,
    FetchResult,
    NormalizedJob,
)
from app.notifications.service import AlertCandidate, NotificationService

Clock = Callable[[], datetime]
AdapterFactory = Callable[[str, HttpFetcher], CareerSourceAdapter]


class PollingService:
    def __init__(
        self,
        session: AsyncSession,
        http: HttpFetcher,
        *,
        clock: Clock | None = None,
        adapter_factory: AdapterFactory = adapter_for,
        notifications: NotificationService | None = None,
    ) -> None:
        self.session = session
        self.http = http
        self.clock = clock or (lambda: datetime.now(UTC))
        self.adapter_factory = adapter_factory
        self.notifications = notifications or NotificationService(session, clock=self.clock)

    async def poll_source(self, source_id: UUID, lease_owner: str | None = None) -> SourcePollRun:
        source = await self.session.get(CareerSource, source_id)
        if source is None:
            raise ValueError("CAREER_SOURCE_NOT_FOUND")
        if source.status not in {"pending_resolution", "supported", "degraded"}:
            raise ValueError("CAREER_SOURCE_NOT_POLLABLE")
        now = self.clock()
        if lease_owner is not None and (
            source.lease_owner != lease_owner
            or source.lease_expires_at is None
            or source.lease_expires_at <= now
        ):
            raise ValueError("CAREER_SOURCE_LEASE_LOST")

        run = SourcePollRun(
            career_source_id=source.id,
            status="running",
            started_at=now,
        )
        self.session.add(run)
        await self.session.commit()

        try:
            adapter = self.adapter_factory(source.provider, self.http)
            result = await adapter.fetch_jobs(
                CareerSourceConfig(
                    careers_url=source.careers_url,
                    provider_config=source.provider_config,
                    etag=source.etag,
                    last_modified=source.last_modified,
                )
            )
            await self._apply_result(source, run, adapter, result)
        except Exception as exc:
            await self.session.rollback()
            source = await self.session.get(CareerSource, source_id)
            refreshed_run = await self.session.get(SourcePollRun, run.id)
            assert source is not None and refreshed_run is not None
            run = refreshed_run
            await self._record_failure(source, run, exc)
        return run

    async def _apply_result(
        self,
        source: CareerSource,
        run: SourcePollRun,
        adapter: CareerSourceAdapter,
        result: FetchResult,
    ) -> None:
        now = self.clock()
        # Read the source baseline before it is completed below. Replacement sources receive their
        # own quiet first poll even when another source already established the company's baseline.
        saved = await self.session.scalar(
            select(SavedCompany).where(SavedCompany.company_id == source.company_id)
        )
        is_baseline_poll = source.baseline_completed_at is None
        alert_candidates: list[AlertCandidate] = []
        run.http_status = result.http_status
        source.etag = result.response_etag or source.etag
        source.last_modified = result.response_last_modified or source.last_modified
        source.last_success_at = now
        source.last_error_code = None
        source.consecutive_failures = 0
        source.lease_owner = None
        source.lease_expires_at = None
        source.next_poll_at = next_successful_poll(source, now)
        if source.status == "degraded":
            source.status = "supported"

        if source.status == "pending_resolution" and source.provider == "generic_html":
            if result.jobs:
                source.status = "supported"
            else:
                source.status = "unsupported"
                source.next_poll_at = None
                source.last_error_code = "NO_SERVER_RENDERED_JOB_LINKS"

        if result.not_modified:
            run.status = "not_modified"
            run.finished_at = now
            self._complete_baseline(source, saved, now)
            await self.session.commit()
            return

        normalized: list[tuple[NormalizedJob, dict[str, Any]]] = []
        had_invalid_job = False
        for raw in result.jobs:
            try:
                normalized.append((adapter.normalize(raw), raw.raw_payload))
            except (TypeError, ValueError):
                had_invalid_job = True
        completeness = "partial" if had_invalid_job else result.completeness
        run.jobs_received = len(result.jobs)
        existing = {
            job.canonical_job_key: job
            for job in (
                await self.session.scalars(select(Job).where(Job.career_source_id == source.id))
            ).all()
        }
        seen_keys: set[str] = set()
        for job_data, raw_payload in normalized:
            key = canonical_job_key(source.provider, source.company_id, job_data)
            if key in seen_keys:
                completeness = "partial"
                continue
            seen_keys.add(key)
            job = existing.get(key)
            digest = content_hash(job_data)
            if job is None:
                job = Job(
                    career_source_id=source.id,
                    company_id=source.company_id,
                    external_job_id=job_data.external_job_id,
                    canonical_job_key=key,
                    title=job_data.title,
                    location_text=job_data.location_text,
                    department=job_data.department,
                    employment_type=job_data.employment_type,
                    role_type=classify_role(job_data.title, job_data.description_text),
                    description_text=job_data.description_text,
                    apply_url=str(job_data.apply_url),
                    source_posted_at=job_data.source_posted_at,
                    first_seen_at=now,
                    last_seen_at=now,
                    status="active",
                    current_content_hash=digest,
                    consecutive_absences=0,
                )
                self.session.add(job)
                await self.session.flush()
                await self._add_snapshot(job, run, job_data, raw_payload, digest, now)
                self._add_event(job, run, "discovered", {"content_hash": digest}, now)
                alert_candidates.append(AlertCandidate(job=job, event_type="discovered"))
                run.jobs_created += 1
                continue

            was_closed = job.status == "closed"
            changed = job.current_content_hash != digest
            job.last_seen_at = now
            job.consecutive_absences = 0
            if changed:
                self._update_job(job, job_data, digest)
                await self._add_snapshot(job, run, job_data, raw_payload, digest, now)
                run.jobs_updated += 1
            if was_closed:
                job.status = "active"
                job.closed_at = None
                self._add_event(job, run, "reopened", {"content_hash": digest}, now)
                alert_candidates.append(AlertCandidate(job=job, event_type="reopened"))
            elif changed:
                self._add_event(job, run, "updated", {"content_hash": digest}, now)

        if completeness == "full":
            for key, job in existing.items():
                if key in seen_keys or job.status != "active":
                    continue
                job.consecutive_absences += 1
                if job.consecutive_absences >= 2:
                    job.status = "closed"
                    job.closed_at = now
                    self._add_event(job, run, "closed", {"consecutive_absences": 2}, now)
                    run.jobs_closed += 1

        run.status = "succeeded" if completeness == "full" else "partial"
        run.finished_at = now
        # Outbox rows join this transaction: if the poll rolls back, no alert survives it.
        await self.notifications.enqueue_job_alerts(
            source=source,
            poll_run_id=run.id,
            candidates=alert_candidates,
            is_baseline_poll=is_baseline_poll,
        )
        self._complete_baseline(source, saved, now)
        await self.session.commit()

    async def _record_failure(
        self, source: CareerSource, run: SourcePollRun, error: Exception
    ) -> None:
        now = self.clock()
        previous_failures = source.consecutive_failures
        source.consecutive_failures += 1
        source.last_error_code = error_code(error)
        if is_permanent_source_error(source.last_error_code):
            source.status = "unsupported"
            source.next_poll_at = None
        else:
            source.next_poll_at = now + failure_backoff(source.consecutive_failures)
        source.lease_owner = None
        source.lease_expires_at = None
        if source.consecutive_failures >= 10 and source.status != "unsupported":
            source.status = "degraded"
        run.status = "failed"
        run.finished_at = now
        run.error_code = source.last_error_code
        run.error_detail = safe_error_detail(error)
        if previous_failures < 10 <= source.consecutive_failures and source.status == "degraded":
            await self.notifications.enqueue_source_degraded_alert(
                source=source,
                poll_run_id=run.id,
            )
        await self.session.commit()

    @staticmethod
    def _complete_baseline(source: CareerSource, saved: SavedCompany | None, now: datetime) -> None:
        if source.baseline_completed_at is None:
            source.baseline_completed_at = now
        if saved is not None and saved.baseline_completed_at is None:
            saved.baseline_completed_at = now

    async def _add_snapshot(
        self,
        job: Job,
        run: SourcePollRun,
        data: NormalizedJob,
        raw_payload: dict[str, Any],
        digest: str,
        now: datetime,
    ) -> None:
        existing = await self.session.scalar(
            select(JobSnapshot.id).where(
                JobSnapshot.job_id == job.id,
                JobSnapshot.content_hash == digest,
            )
        )
        if existing is None:
            self.session.add(
                JobSnapshot(
                    job_id=job.id,
                    poll_run_id=run.id,
                    content_hash=digest,
                    normalized_payload=canonical_payload(data),
                    raw_payload=raw_payload,
                    observed_at=now,
                )
            )

    def _add_event(
        self,
        job: Job,
        run: SourcePollRun,
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> None:
        self.session.add(
            JobEvent(
                job_id=job.id,
                event_type=event_type,
                poll_run_id=run.id,
                event_payload=payload,
                occurred_at=now,
                dedupe_key=event_dedupe_key(job.id, event_type, run.id),
            )
        )

    @staticmethod
    def _update_job(job: Job, data: NormalizedJob, digest: str) -> None:
        job.external_job_id = data.external_job_id
        job.title = data.title
        job.location_text = data.location_text
        job.department = data.department
        job.employment_type = data.employment_type
        job.role_type = classify_role(data.title, data.description_text)
        job.description_text = data.description_text
        job.apply_url = str(data.apply_url)
        job.source_posted_at = data.source_posted_at
        job.current_content_hash = digest


def stable_jitter_minutes(source_id: UUID) -> int:
    return int(hashlib.sha256(str(source_id).encode()).hexdigest()[:8], 16) % 11


def next_successful_poll(source: CareerSource, now: datetime) -> datetime:
    return now + timedelta(minutes=source.poll_interval_minutes + stable_jitter_minutes(source.id))


def failure_backoff(failures: int) -> timedelta:
    return timedelta(hours=min(2 ** max(failures - 1, 0), 24))


def error_code(error: Exception) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "SOURCE_TIMEOUT"
    if isinstance(error, httpx.NetworkError):
        return "SOURCE_NETWORK_ERROR"
    text = str(error)
    if text and len(text) <= 80 and text.replace("_", "").isalnum():
        return text.upper()
    return "SOURCE_POLL_FAILED"


def safe_error_detail(error: Exception) -> str:
    return str(error)[:500] or type(error).__name__


def is_permanent_source_error(code: str) -> bool:
    if code in {"ROBOTS_DISALLOWED", "ROBOTS_TXT_HTTP_401", "ROBOTS_TXT_HTTP_403"}:
        return True
    # A resolver failure can recover without changing the configured URL. Other URL-policy
    # failures are deterministic validation failures and still stop polling.
    return code.startswith("URL_") and code != "URL_DNS_FAILED"
