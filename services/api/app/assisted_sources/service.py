from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Literal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.assisted_sources.eml import ParsedEmailAlert, parse_eml
from app.assisted_sources.schemas import (
    EmailAlertImportListResponse,
    EmailAlertImportResponse,
    ReviewReminderListResponse,
    ReviewReminderResponse,
    SourceRepairResponse,
)
from app.companies.normalization import normalize_https_url
from app.db.models import (
    CareerSource,
    CareerSourceRepair,
    Company,
    EmailAlertImport,
    Job,
    JobEvent,
    JobSnapshot,
    NotificationOutbox,
    ReviewReminder,
    SavedCompany,
    SourcePollRun,
)
from app.monitoring.job_identity import (
    canonical_job_key,
    canonical_payload,
    classify_role,
    content_hash,
    event_dedupe_key,
)
from app.monitoring.provider_detection import ProviderDetection, detect_provider
from app.monitoring.schemas import NormalizedJob
from app.notifications.schemas import ReviewReminderPayload
from app.notifications.service import AlertCandidate, NotificationService

Clock = Callable[[], datetime]
RepairAction = Literal["update_in_place", "create_replacement", "reuse_existing", "reactivate"]

STRUCTURED_PROVIDERS = {"greenhouse", "lever", "ashby", "smartrecruiters"}
REPAIR_PREVIEW_TTL = timedelta(minutes=15)
MAX_FILENAME_CHARS = 255


class SourceRepairService:
    """Preview and confirm source replacement without probing a live provider."""

    def __init__(self, session: AsyncSession, *, clock: Clock | None = None) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(UTC))

    async def preview(
        self,
        *,
        company_id: UUID,
        source_id: UUID,
        careers_url: str,
        notify_current_jobs: bool,
    ) -> SourceRepairResponse:
        now = self.clock()
        source = await self._validated_source(company_id, source_id)
        detection, normalized_url = self._supported_detection(careers_url)
        await self._ensure_target_not_owned_by_another_company(company_id, detection)
        action = await self._choose_action(source, detection)

        existing_preview = await self.session.scalar(
            select(CareerSourceRepair)
            .where(
                CareerSourceRepair.company_id == company_id,
                CareerSourceRepair.replacing_source_id == source_id,
                CareerSourceRepair.canonical_source_key == detection.canonical_source_key,
                CareerSourceRepair.notify_current_jobs == notify_current_jobs,
                CareerSourceRepair.status == "previewed",
                CareerSourceRepair.expires_at > now,
                CareerSourceRepair.proposed_url == normalized_url,
            )
            .order_by(CareerSourceRepair.created_at.desc())
            .limit(1)
        )
        if existing_preview is not None:
            return self._repair_response(existing_preview)

        repair = CareerSourceRepair(
            company_id=company_id,
            replacing_source_id=source_id,
            proposed_url=normalized_url,
            provider=detection.provider,
            canonical_source_key=detection.canonical_source_key,
            provider_config=detection.provider_config,
            action=action,
            notify_current_jobs=notify_current_jobs,
            status="previewed",
            expires_at=now + REPAIR_PREVIEW_TTL,
        )
        self.session.add(repair)
        await self.session.commit()
        await self.session.refresh(repair)
        return self._repair_response(repair)

    async def confirm(self, repair_id: UUID) -> SourceRepairResponse:
        repair = await self.session.scalar(
            select(CareerSourceRepair).where(CareerSourceRepair.id == repair_id).with_for_update()
        )
        if repair is None:
            raise AppError(
                "SOURCE_REPAIR_NOT_FOUND", "Source repair preview not found.", status_code=404
            )
        if repair.status == "confirmed":
            await self.session.commit()
            return self._repair_response(repair)
        if repair.status != "previewed":
            raise AppError(
                "SOURCE_REPAIR_NOT_CONFIRMABLE",
                "This source repair preview can no longer be confirmed.",
                status_code=409,
            )

        now = self.clock()
        if repair.expires_at <= now:
            repair.status = "expired"
            await self.session.commit()
            raise AppError(
                "SOURCE_REPAIR_PREVIEW_EXPIRED",
                "Preview the replacement URL again before confirming it.",
                status_code=409,
            )

        source = await self._validated_source(repair.company_id, repair.replacing_source_id)
        detection, normalized_url = self._supported_detection(repair.proposed_url)
        if (
            detection.provider != repair.provider
            or detection.canonical_source_key != repair.canonical_source_key
            or detection.provider_config != repair.provider_config
            or normalized_url != repair.proposed_url
        ):
            raise AppError(
                "SOURCE_REPAIR_PREVIEW_STALE",
                "Provider detection changed. Preview this URL again before confirming it.",
                status_code=409,
            )
        await self._ensure_target_not_owned_by_another_company(repair.company_id, detection)
        action = await self._choose_action(source, detection)
        replacement = await self._apply_replacement(
            source=source,
            detection=detection,
            normalized_url=normalized_url,
            action=action,
            notify_current_jobs=repair.notify_current_jobs,
            now=now,
        )
        repair.action = action
        repair.status = "confirmed"
        repair.replacement_source_id = replacement.id
        repair.confirmed_at = now
        await self.session.commit()
        await self.session.refresh(repair)
        return self._repair_response(repair)

    async def _validated_source(self, company_id: UUID, source_id: UUID) -> CareerSource:
        saved = await self.session.scalar(
            select(SavedCompany).where(SavedCompany.company_id == company_id)
        )
        if saved is None:
            raise AppError(
                "SAVED_COMPANY_NOT_FOUND",
                "Save the company before repairing its monitoring source.",
                status_code=404,
            )
        source = await self.session.get(CareerSource, source_id)
        if source is None or source.company_id != company_id:
            raise AppError(
                "CAREER_SOURCE_NOT_FOUND",
                "Career source not found for this saved company.",
                status_code=404,
            )
        if source.provider == "email_alert":
            raise AppError(
                "SOURCE_REPAIR_NOT_APPLICABLE",
                "Uploaded-email sources are assisted observations, not monitoring URLs.",
                status_code=409,
            )
        return source

    @staticmethod
    def _supported_detection(careers_url: str) -> tuple[ProviderDetection, str]:
        normalized_url = normalize_https_url(careers_url)
        detection = detect_provider(normalized_url, allow_pending_generic=False)
        if detection.provider not in STRUCTURED_PROVIDERS or detection.status != "supported":
            raise AppError(
                "SUPPORTED_SOURCE_REQUIRED",
                "Enter a direct URL for a supported structured job board.",
                status_code=422,
                details={"supported_providers": sorted(STRUCTURED_PROVIDERS)},
            )
        return detection, normalized_url

    async def _ensure_target_not_owned_by_another_company(
        self, company_id: UUID, detection: ProviderDetection
    ) -> None:
        existing = await self.session.scalar(
            select(CareerSource).where(
                CareerSource.canonical_source_key == detection.canonical_source_key
            )
        )
        if existing is not None and existing.company_id != company_id:
            raise AppError(
                "SOURCE_ALREADY_OWNED",
                "This job board is already attached to another company.",
                status_code=409,
            )

    async def _choose_action(
        self, source: CareerSource, detection: ProviderDetection
    ) -> RepairAction:
        existing = await self.session.scalar(
            select(CareerSource).where(
                CareerSource.canonical_source_key == detection.canonical_source_key
            )
        )
        if existing is not None:
            return "reactivate" if existing.id == source.id else "reuse_existing"
        history_count = await self.session.scalar(
            select(func.count())
            .select_from(SourcePollRun)
            .where(SourcePollRun.career_source_id == source.id)
        )
        job_count = await self.session.scalar(
            select(func.count()).select_from(Job).where(Job.career_source_id == source.id)
        )
        if int(history_count or 0) or int(job_count or 0):
            return "create_replacement"
        return "update_in_place"

    async def _apply_replacement(
        self,
        *,
        source: CareerSource,
        detection: ProviderDetection,
        normalized_url: str,
        action: RepairAction,
        notify_current_jobs: bool,
        now: datetime,
    ) -> CareerSource:
        if action in {"reactivate", "reuse_existing"}:
            replacement = await self.session.scalar(
                select(CareerSource).where(
                    CareerSource.canonical_source_key == detection.canonical_source_key
                )
            )
            assert replacement is not None
            replacement.careers_url = normalized_url
            replacement.provider = detection.provider
            replacement.provider_config = detection.provider_config
            self._schedule_replacement(replacement, notify_current_jobs, now)
        elif action == "update_in_place":
            replacement = source
            replacement.provider = detection.provider
            replacement.careers_url = normalized_url
            replacement.canonical_source_key = detection.canonical_source_key
            replacement.provider_config = detection.provider_config
            self._schedule_replacement(replacement, notify_current_jobs, now)
        else:
            replacement = CareerSource(
                company_id=source.company_id,
                provider=detection.provider,
                careers_url=normalized_url,
                canonical_source_key=detection.canonical_source_key,
                provider_config=detection.provider_config,
                status="supported",
                next_poll_at=now,
                notify_current_jobs_on_baseline=notify_current_jobs,
            )
            self.session.add(replacement)
            await self.session.flush()

        if replacement.id != source.id:
            source.status = "retired"
            source.next_poll_at = None
            source.lease_owner = None
            source.lease_expires_at = None
            source.retired_at = now
            source.replaced_by_source_id = replacement.id
        return replacement

    @staticmethod
    def _schedule_replacement(
        source: CareerSource, notify_current_jobs: bool, now: datetime
    ) -> None:
        source.status = "supported"
        source.next_poll_at = now
        source.lease_owner = None
        source.lease_expires_at = None
        source.etag = None
        source.last_modified = None
        source.consecutive_failures = 0
        source.last_error_code = None
        source.baseline_completed_at = None
        source.notify_current_jobs_on_baseline = notify_current_jobs
        source.retired_at = None
        source.replaced_by_source_id = None

    @staticmethod
    def _repair_response(repair: CareerSourceRepair) -> SourceRepairResponse:
        return SourceRepairResponse(
            id=repair.id,
            company_id=repair.company_id,
            replacing_source_id=repair.replacing_source_id,
            replacement_source_id=repair.replacement_source_id,
            normalized_url=repair.proposed_url,
            provider=repair.provider,
            canonical_source_key=repair.canonical_source_key,
            action=repair.action,
            notify_current_jobs=repair.notify_current_jobs,
            status=repair.status,
            expires_at=repair.expires_at,
            confirmed_at=repair.confirmed_at,
        )


class ReviewReminderService:
    def __init__(self, session: AsyncSession, *, clock: Clock | None = None) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(UTC))

    async def schedule(
        self,
        *,
        saved_company_id: UUID,
        career_source_id: UUID | None,
        due_at: datetime,
    ) -> ReviewReminderResponse:
        saved = await self.session.get(SavedCompany, saved_company_id)
        if saved is None:
            raise AppError("SAVED_COMPANY_NOT_FOUND", "Saved company not found.", status_code=404)
        source = await self._validate_source(saved, career_source_id)
        target_key = self._target_key(saved.id, source.id if source else None)
        reminder = await self.session.scalar(
            select(ReviewReminder).where(ReviewReminder.target_key == target_key)
        )
        now = self.clock()
        if reminder is None:
            reminder = ReviewReminder(
                saved_company_id=saved.id,
                career_source_id=source.id if source else None,
                target_key=target_key,
                due_at=due_at.astimezone(UTC),
                schedule_version=1,
                status="scheduled",
            )
            self.session.add(reminder)
        else:
            reminder.due_at = due_at.astimezone(UTC)
            reminder.schedule_version += 1
            reminder.status = "scheduled"
            reminder.notified_at = None
            reminder.checked_at = None
            reminder.dismissed_at = None
            reminder.updated_at = now
        await self.session.commit()
        await self.session.refresh(reminder)
        return await self._response(reminder)

    async def list(self, *, status: str | None = None) -> ReviewReminderListResponse:
        statement = select(ReviewReminder).order_by(ReviewReminder.due_at, ReviewReminder.id)
        if status is not None:
            statement = statement.where(ReviewReminder.status == status)
        reminders = list((await self.session.scalars(statement)).all())
        return ReviewReminderListResponse(
            items=[await self._response(reminder) for reminder in reminders]
        )

    async def due(self, *, as_of: datetime | None = None) -> ReviewReminderListResponse:
        cutoff = (as_of or self.clock()).astimezone(UTC)
        reminders = list(
            (
                await self.session.scalars(
                    select(ReviewReminder)
                    .where(
                        ReviewReminder.status == "scheduled",
                        ReviewReminder.due_at <= cutoff,
                    )
                    .order_by(ReviewReminder.due_at, ReviewReminder.id)
                )
            ).all()
        )
        return ReviewReminderListResponse(
            items=[await self._response(reminder) for reminder in reminders]
        )

    async def checked(self, reminder_id: UUID) -> ReviewReminderResponse:
        reminder = await self._get(reminder_id)
        now = self.clock()
        reminder.status = "checked"
        reminder.checked_at = now
        reminder.dismissed_at = None
        reminder.updated_at = now
        await self.session.commit()
        return await self._response(reminder)

    async def reschedule(self, reminder_id: UUID, due_at: datetime) -> ReviewReminderResponse:
        reminder = await self._get(reminder_id)
        reminder.due_at = due_at.astimezone(UTC)
        reminder.schedule_version += 1
        reminder.status = "scheduled"
        reminder.notified_at = None
        reminder.checked_at = None
        reminder.dismissed_at = None
        reminder.updated_at = self.clock()
        await self.session.commit()
        return await self._response(reminder)

    async def dismiss(self, reminder_id: UUID) -> ReviewReminderResponse:
        reminder = await self._get(reminder_id)
        now = self.clock()
        reminder.status = "dismissed"
        reminder.dismissed_at = now
        reminder.updated_at = now
        await self.session.commit()
        return await self._response(reminder)

    async def enqueue_due_notifications(self, limit: int = 100) -> int:
        """Atomically enqueue one in-app outbox row for each due schedule version."""

        now = self.clock()
        reminders = list(
            (
                await self.session.scalars(
                    select(ReviewReminder)
                    .where(
                        ReviewReminder.status == "scheduled",
                        ReviewReminder.due_at <= now,
                        ReviewReminder.notified_at.is_(None),
                    )
                    .order_by(ReviewReminder.due_at, ReviewReminder.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        created = 0
        for reminder in reminders:
            saved = await self.session.get(SavedCompany, reminder.saved_company_id)
            company = await self.session.get(Company, saved.company_id) if saved else None
            if saved is None or company is None:
                continue
            group_key = f"review-reminder:{reminder.id}:{reminder.schedule_version}"
            duplicate = await self.session.scalar(
                select(NotificationOutbox.id).where(
                    NotificationOutbox.channel == "in_app",
                    NotificationOutbox.event_group_key == group_key,
                )
            )
            payload = ReviewReminderPayload(
                reminder_id=reminder.id,
                company_id=company.id,
                company_name=company.canonical_name,
                career_source_id=reminder.career_source_id,
                title=f"Review {company.canonical_name} careers",
                body="This source needs a manual careers-page review.",
                action_path=f"/companies/{company.id}",
                due_at=reminder.due_at,
            )
            if duplicate is None:
                self.session.add(
                    NotificationOutbox(
                        channel="in_app",
                        event_group_key=group_key,
                        payload=payload.model_dump(mode="json"),
                        status="pending",
                        next_attempt_at=now,
                    )
                )
                created += 1
            reminder.notified_at = now
            reminder.updated_at = now
        await self.session.commit()
        return created

    async def _validate_source(
        self, saved: SavedCompany, source_id: UUID | None
    ) -> CareerSource | None:
        if source_id is None:
            return None
        source = await self.session.get(CareerSource, source_id)
        if source is None or source.company_id != saved.company_id:
            raise AppError(
                "CAREER_SOURCE_NOT_FOUND",
                "Career source not found for this saved company.",
                status_code=404,
            )
        return source

    async def _get(self, reminder_id: UUID) -> ReviewReminder:
        reminder = await self.session.get(ReviewReminder, reminder_id)
        if reminder is None:
            raise AppError(
                "REVIEW_REMINDER_NOT_FOUND", "Review reminder not found.", status_code=404
            )
        return reminder

    async def _response(self, reminder: ReviewReminder) -> ReviewReminderResponse:
        saved = await self.session.get(SavedCompany, reminder.saved_company_id)
        assert saved is not None
        company = await self.session.get(Company, saved.company_id)
        assert company is not None
        source = (
            await self.session.get(CareerSource, reminder.career_source_id)
            if reminder.career_source_id
            else None
        )
        return ReviewReminderResponse(
            id=reminder.id,
            saved_company_id=reminder.saved_company_id,
            company_id=company.id,
            company_name=company.canonical_name,
            career_source_id=reminder.career_source_id,
            careers_url=(
                source.careers_url
                if source is not None and source.provider != "email_alert"
                else None
            ),
            due_at=reminder.due_at,
            schedule_version=reminder.schedule_version,
            status=reminder.status,
            notified_at=reminder.notified_at,
            checked_at=reminder.checked_at,
            dismissed_at=reminder.dismissed_at,
        )

    @staticmethod
    def _target_key(saved_company_id: UUID, source_id: UUID | None) -> str:
        target = f"source:{source_id}" if source_id else "company"
        return f"saved:{saved_company_id}:{target}"


class EmailAlertImportService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Clock | None = None,
        notifications: NotificationService | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(UTC))
        self.notifications = notifications or NotificationService(session, clock=self.clock)

    async def import_message(
        self, *, company_id: UUID, raw: bytes, filename: str | None
    ) -> EmailAlertImportResponse:
        parsed = parse_eml(raw)
        # Lock the company to serialize duplicate checks and source creation for this local owner.
        company = await self.session.scalar(
            select(Company).where(Company.id == company_id).with_for_update()
        )
        if company is None:
            raise AppError("COMPANY_NOT_FOUND", "Company not found.", status_code=404)
        saved = await self.session.scalar(
            select(SavedCompany).where(SavedCompany.company_id == company_id)
        )
        if saved is None:
            raise AppError(
                "SAVED_COMPANY_NOT_FOUND",
                "Save the company before importing one of its job-alert emails.",
                status_code=409,
            )
        duplicate = await self._find_duplicate(parsed)
        if duplicate is not None:
            if duplicate.company_id != company.id:
                raise AppError(
                    "EMAIL_ALREADY_IMPORTED_FOR_ANOTHER_COMPANY",
                    "This employer alert was already imported for another saved company.",
                    status_code=409,
                )
            await self.session.commit()
            return self._import_response(duplicate, duplicate=True)

        now = self.clock()
        source = await self._email_source(company, now)
        run = SourcePollRun(
            career_source_id=source.id,
            status="running",
            started_at=now,
        )
        self.session.add(run)
        await self.session.flush()
        imported = EmailAlertImport(
            company_id=company.id,
            career_source_id=source.id,
            poll_run_id=run.id,
            filename=self._safe_filename(filename),
            message_id=parsed.message_id,
            content_sha256=parsed.content_sha256,
            subject=parsed.subject,
            sender=parsed.sender,
            sent_at=parsed.sent_at,
            text_excerpt=parsed.text[:5000],
            links_found=parsed.links_found,
            status="imported" if parsed.candidates else "no_jobs",
        )
        self.session.add(imported)
        await self.session.flush()

        existing = {
            job.canonical_job_key: job
            for job in (
                await self.session.scalars(select(Job).where(Job.career_source_id == source.id))
            ).all()
        }
        alert_candidates: list[AlertCandidate] = []
        for candidate in parsed.candidates:
            normalized = NormalizedJob(
                external_job_id=hashlib.sha256(candidate.apply_url.encode()).hexdigest(),
                title=candidate.title,
                description_text="",
                apply_url=candidate.apply_url,
                source_posted_at=parsed.sent_at,
            )
            key = canonical_job_key(source.provider, company.id, normalized)
            digest = content_hash(normalized)
            job = existing.get(key)
            raw_payload: dict[str, object] = {
                "email_alert_import_id": str(imported.id),
                "message_id": parsed.message_id,
                "subject": parsed.subject,
                "sender": parsed.sender,
                "apply_url": candidate.apply_url,
                "title": candidate.title,
            }
            if job is None:
                job = Job(
                    career_source_id=source.id,
                    company_id=company.id,
                    external_job_id=normalized.external_job_id,
                    canonical_job_key=key,
                    title=normalized.title,
                    location_text=None,
                    department=None,
                    employment_type=None,
                    role_type=classify_role(normalized.title),
                    description_text="",
                    apply_url=str(normalized.apply_url),
                    source_posted_at=normalized.source_posted_at,
                    first_seen_at=now,
                    last_seen_at=now,
                    status="active",
                    current_content_hash=digest,
                    consecutive_absences=0,
                )
                self.session.add(job)
                await self.session.flush()
                self._add_snapshot(job, run, normalized, raw_payload, digest, now)
                self._add_event(job, run, "discovered", imported.id, now)
                alert_candidates.append(AlertCandidate(job=job, event_type="discovered"))
                imported.jobs_created += 1
                continue

            was_closed = job.status == "closed"
            changed = job.current_content_hash != digest
            job.last_seen_at = now
            job.consecutive_absences = 0
            if changed:
                job.external_job_id = normalized.external_job_id
                job.title = normalized.title
                job.role_type = classify_role(normalized.title)
                job.apply_url = str(normalized.apply_url)
                job.source_posted_at = normalized.source_posted_at
                job.current_content_hash = digest
                self._add_snapshot(job, run, normalized, raw_payload, digest, now)
                imported.jobs_updated += 1
                self._add_event(job, run, "updated", imported.id, now)
            if was_closed:
                job.status = "active"
                job.closed_at = None
                self._add_event(job, run, "reopened", imported.id, now)
                alert_candidates.append(AlertCandidate(job=job, event_type="reopened"))

        run.status = "partial"
        run.jobs_received = len(parsed.candidates)
        run.jobs_created = imported.jobs_created
        run.jobs_updated = imported.jobs_updated
        run.jobs_closed = 0
        run.finished_at = now
        source.last_success_at = now
        source.last_error_code = None
        await self.notifications.enqueue_job_alerts(
            source=source,
            poll_run_id=run.id,
            candidates=alert_candidates,
            is_baseline_poll=False,
        )
        await self.session.commit()
        await self.session.refresh(imported)
        return self._import_response(imported, duplicate=False)

    async def list_for_company(
        self, company_id: UUID, *, limit: int = 50
    ) -> EmailAlertImportListResponse:
        company = await self.session.get(Company, company_id)
        if company is None:
            raise AppError("COMPANY_NOT_FOUND", "Company not found.", status_code=404)
        imports = list(
            (
                await self.session.scalars(
                    select(EmailAlertImport)
                    .where(EmailAlertImport.company_id == company_id)
                    .order_by(EmailAlertImport.created_at.desc(), EmailAlertImport.id.desc())
                    .limit(limit)
                )
            ).all()
        )
        return EmailAlertImportListResponse(
            items=[self._import_response(item, duplicate=False) for item in imports]
        )

    async def _find_duplicate(self, parsed: ParsedEmailAlert) -> EmailAlertImport | None:
        predicates = [EmailAlertImport.content_sha256 == parsed.content_sha256]
        if parsed.message_id:
            predicates.append(EmailAlertImport.message_id == parsed.message_id)
        duplicate: EmailAlertImport | None = await self.session.scalar(
            select(EmailAlertImport)
            .where(or_(*predicates))
            .order_by(EmailAlertImport.created_at)
            .limit(1)
        )
        return duplicate

    async def _email_source(self, company: Company, now: datetime) -> CareerSource:
        key = f"email_alert:{company.id}"
        source = await self.session.scalar(
            select(CareerSource).where(CareerSource.canonical_source_key == key)
        )
        if source is not None:
            return source
        source = CareerSource(
            company_id=company.id,
            provider="email_alert",
            careers_url=f"jobscout-local://email-alert/{company.id}",
            canonical_source_key=key,
            provider_config={"ingestion": "owner_uploaded_rfc822", "completeness": "partial"},
            status="assisted",
            next_poll_at=None,
            baseline_completed_at=now,
        )
        self.session.add(source)
        await self.session.flush()
        return source

    def _add_snapshot(
        self,
        job: Job,
        run: SourcePollRun,
        normalized: NormalizedJob,
        raw_payload: dict[str, object],
        digest: str,
        now: datetime,
    ) -> None:
        self.session.add(
            JobSnapshot(
                job_id=job.id,
                poll_run_id=run.id,
                content_hash=digest,
                normalized_payload=canonical_payload(normalized),
                raw_payload=raw_payload,
                observed_at=now,
            )
        )

    def _add_event(
        self,
        job: Job,
        run: SourcePollRun,
        event_type: str,
        import_id: UUID,
        now: datetime,
    ) -> None:
        self.session.add(
            JobEvent(
                job_id=job.id,
                event_type=event_type,
                poll_run_id=run.id,
                event_payload={
                    "observation": "email_alert",
                    "completeness": "partial",
                    "email_alert_import_id": str(import_id),
                },
                occurred_at=now,
                dedupe_key=event_dedupe_key(job.id, event_type, run.id),
            )
        )

    @staticmethod
    def _safe_filename(filename: str | None) -> str | None:
        if not filename:
            return None
        return PurePath(filename.strip()).name[:MAX_FILENAME_CHARS] or None

    @staticmethod
    def _import_response(
        imported: EmailAlertImport, *, duplicate: bool
    ) -> EmailAlertImportResponse:
        return EmailAlertImportResponse(
            id=imported.id,
            duplicate=duplicate,
            status=imported.status,
            company_id=imported.company_id,
            source_id=imported.career_source_id,
            poll_run_id=imported.poll_run_id,
            filename=imported.filename,
            subject=imported.subject,
            sender=imported.sender,
            message_id=imported.message_id,
            content_sha256=imported.content_sha256,
            links_found=imported.links_found,
            jobs_created=imported.jobs_created,
            jobs_updated=imported.jobs_updated,
            created_at=imported.created_at,
        )
