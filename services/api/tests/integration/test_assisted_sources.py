from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import database_session
from app.api.errors import AppError
from app.assisted_sources.service import (
    EmailAlertImportService,
    ReviewReminderService,
    SourceRepairService,
)
from app.companies.schemas import SaveCompanyRequest, SavedCompanyUpdate
from app.companies.service import CompanyService
from app.db.models import (
    AppSettings,
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
from app.main import app
from app.notifications.schemas import NotificationPayload, ReviewReminderPayload

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="Set RUN_INTEGRATION_TESTS=1 with local PostgreSQL and Redis running",
    ),
]

NOW = datetime(2026, 8, 30, 16, 0, tzinfo=UTC)


def job_alert_email(
    *,
    message_id: str = "alert-1",
    title: str = "Software Engineer Intern",
    job_url: str = "https://jobs.lever.co/acme/job-1?utm_source=employer-email",
) -> bytes:
    return "\r\n".join(
        [
            "From: Acme Careers <careers@acme.example>",
            f"Message-ID: <{message_id}@acme.example>",
            f"Subject: {title}",
            "Date: Sun, 30 Aug 2026 10:00:00 -0400",
            "MIME-Version: 1.0",
            'Content-Type: multipart/mixed; boundary="alert"',
            "",
            "--alert",
            'Content-Type: text/html; charset="utf-8"',
            "",
            f'<a href="{job_url}">{title}</a>',
            '<a href="https://acme.example/unsubscribe">Unsubscribe</a>',
            "--alert",
            'Content-Type: text/plain; name="ignored.txt"',
            'Content-Disposition: attachment; filename="ignored.txt"',
            "",
            "Hidden job https://jobs.example.test/jobs/attachment-only",
            "--alert--",
            "",
        ]
    ).encode()


async def configured_company(
    session: AsyncSession,
    *,
    source_status: str = "unsupported",
) -> tuple[Company, SavedCompany, CareerSource]:
    settings = AppSettings(
        role_types=["internship", "new_grad", "entry_level"],
        keywords=[],
        excluded_keywords=[],
        preferred_locations=[],
        remote_preference="any",
        notify_current_jobs_on_save=False,
        email_notifications_enabled=True,
        notification_email="owner@example.com",
    )
    company = Company(
        canonical_name="Acme Games",
        normalized_name="acme games",
        official_domain="acme.example",
        official_website_url="https://acme.example/",
        headquarters_country="US",
        verification_status="verified",
    )
    session.add_all([settings, company])
    await session.flush()
    saved = SavedCompany(
        company_id=company.id,
        status="active",
        notify_current_jobs=False,
    )
    source = CareerSource(
        company_id=company.id,
        provider="generic_html",
        careers_url="https://acme.example/careers",
        canonical_source_key="generic_html:acme-careers",
        provider_config={"rules_version": "generic-html-v1"},
        status=source_status,
        next_poll_at=None,
    )
    session.add_all([saved, source])
    await session.commit()
    return company, saved, source


async def test_email_import_creates_partial_observations_and_is_exactly_once(
    isolated_session: AsyncSession,
) -> None:
    company, _, _ = await configured_company(isolated_session)
    service = EmailAlertImportService(isolated_session, clock=lambda: NOW)
    raw = job_alert_email()

    first = await service.import_message(
        company_id=company.id,
        raw=raw,
        filename="../../alerts/acme.eml",
    )
    duplicate = await service.import_message(
        company_id=company.id,
        raw=raw,
        filename="renamed-copy.eml",
    )

    source = await isolated_session.get(CareerSource, first.source_id)
    run = await isolated_session.get(SourcePollRun, first.poll_run_id)
    imported = await isolated_session.get(EmailAlertImport, first.id)
    jobs = list((await isolated_session.scalars(select(Job))).all())
    events = list((await isolated_session.scalars(select(JobEvent))).all())
    outbox = list(
        (
            await isolated_session.scalars(
                select(NotificationOutbox).order_by(NotificationOutbox.channel)
            )
        ).all()
    )

    assert first.duplicate is False
    assert first.status == "imported"
    assert first.filename == "acme.eml"
    assert first.jobs_created == 1
    assert first.links_found == 2
    assert duplicate.id == first.id
    assert duplicate.duplicate is True
    assert source is not None
    assert source.provider == "email_alert"
    assert source.status == "assisted"
    assert source.next_poll_at is None
    assert source.provider_config["completeness"] == "partial"
    assert run is not None
    assert run.status == "partial"
    assert run.jobs_received == 1
    assert imported is not None
    assert "attachment-only" not in imported.text_excerpt
    assert len(jobs) == 1
    assert jobs[0].apply_url == "https://jobs.lever.co/acme/job-1"
    assert jobs[0].role_type == "internship"
    assert len(events) == 1
    assert events[0].event_payload["completeness"] == "partial"
    assert await isolated_session.scalar(select(func.count()).select_from(JobSnapshot)) == 1
    assert await isolated_session.scalar(select(func.count()).select_from(EmailAlertImport)) == 1
    assert await isolated_session.scalar(select(func.count()).select_from(SourcePollRun)) == 1
    assert [row.channel for row in outbox] == ["email", "in_app"]
    assert all(NotificationPayload.model_validate(row.payload).jobs for row in outbox)


async def test_email_import_deduplicates_a_reencoded_message_by_message_id(
    isolated_session: AsyncSession,
) -> None:
    company, _, _ = await configured_company(isolated_session)
    service = EmailAlertImportService(isolated_session, clock=lambda: NOW)

    first = await service.import_message(
        company_id=company.id,
        raw=job_alert_email(message_id="stable-id"),
        filename="first.eml",
    )
    duplicate = await service.import_message(
        company_id=company.id,
        raw=job_alert_email(
            message_id="stable-id",
            job_url="https://jobs.lever.co/acme/a-different-job",
        ),
        filename="second.eml",
    )

    assert duplicate.id == first.id
    assert duplicate.duplicate is True
    assert await isolated_session.scalar(select(func.count()).select_from(EmailAlertImport)) == 1
    assert await isolated_session.scalar(select(func.count()).select_from(Job)) == 1


async def test_email_import_with_no_job_links_records_no_jobs_without_alerting(
    isolated_session: AsyncSession,
) -> None:
    company, _, _ = await configured_company(isolated_session)
    raw = b"\r\n".join(
        [
            b"From: Acme Careers <careers@acme.example>",
            b"Message-ID: <no-jobs@acme.example>",
            b"Subject: Your job alert preferences",
            b"Content-Type: text/plain; charset=utf-8",
            b"",
            b"Manage preferences at https://acme.example/email-preferences",
        ]
    )

    result = await EmailAlertImportService(isolated_session, clock=lambda: NOW).import_message(
        company_id=company.id, raw=raw, filename="no-jobs.eml"
    )

    assert result.status == "no_jobs"
    assert result.jobs_created == 0
    assert await isolated_session.scalar(select(func.count()).select_from(Job)) == 0
    assert await isolated_session.scalar(select(func.count()).select_from(NotificationOutbox)) == 0
    run = await isolated_session.get(SourcePollRun, result.poll_run_id)
    assert run is not None and run.status == "partial" and run.jobs_received == 0


async def test_company_lifecycle_never_schedules_assisted_or_retired_sources(
    isolated_session: AsyncSession,
) -> None:
    company, saved, unsupported = await configured_company(isolated_session)
    imported = await EmailAlertImportService(isolated_session, clock=lambda: NOW).import_message(
        company_id=company.id,
        raw=job_alert_email(message_id="lifecycle"),
        filename="lifecycle.eml",
    )
    retired = CareerSource(
        company_id=company.id,
        provider="lever",
        careers_url="https://jobs.lever.co/acme-retired",
        canonical_source_key="lever:acme-retired",
        provider_config={"site": "acme-retired", "region": "global"},
        status="retired",
        next_poll_at=None,
        retired_at=NOW,
    )
    retryable = CareerSource(
        company_id=company.id,
        provider="generic_html",
        careers_url="https://retryable.example/careers",
        canonical_source_key="generic_html:retryable",
        provider_config={"rules_version": "generic-html-v1"},
        status="supported",
        next_poll_at=NOW,
        last_error_code="URL_DNS_FAILED",
    )
    isolated_session.add_all([retired, retryable])
    await isolated_session.commit()

    service = CompanyService(isolated_session)
    await service.save(company.id, SaveCompanyRequest())
    await service.update_saved(saved.id, SavedCompanyUpdate(status="paused"))
    await service.update_saved(saved.id, SavedCompanyUpdate(status="active"))

    assisted = await isolated_session.get(CareerSource, imported.source_id)
    await isolated_session.refresh(unsupported)
    await isolated_session.refresh(retired)
    await isolated_session.refresh(retryable)
    assert unsupported.status == "unsupported" and unsupported.next_poll_at is None
    assert assisted is not None
    assert assisted.status == "assisted" and assisted.next_poll_at is None
    assert retired.status == "retired" and retired.next_poll_at is None
    assert retryable.status == "pending_resolution" and retryable.next_poll_at is not None


async def test_source_repair_preview_and_confirm_preserve_history_on_a_new_source(
    isolated_session: AsyncSession,
) -> None:
    company, _, old_source = await configured_company(isolated_session)
    isolated_session.add(
        SourcePollRun(
            career_source_id=old_source.id,
            status="failed",
            started_at=NOW - timedelta(days=1),
            finished_at=NOW - timedelta(days=1),
            error_code="ROBOTS_DISALLOWED",
        )
    )
    await isolated_session.commit()
    service = SourceRepairService(isolated_session, clock=lambda: NOW)

    preview = await service.preview(
        company_id=company.id,
        source_id=old_source.id,
        careers_url="https://careers.smartrecruiters.com/AcmeGames",
        notify_current_jobs=True,
    )
    repeated_preview = await service.preview(
        company_id=company.id,
        source_id=old_source.id,
        careers_url="https://careers.smartrecruiters.com/AcmeGames",
        notify_current_jobs=True,
    )

    assert preview.id == repeated_preview.id
    assert preview.action == "create_replacement"
    assert preview.provider == "smartrecruiters"
    assert preview.canonical_source_key == "smartrecruiters:acmegames"
    assert preview.status == "previewed"
    assert preview.replacement_source_id is None
    assert await isolated_session.scalar(select(func.count()).select_from(CareerSource)) == 1

    confirmed = await service.confirm(preview.id)
    confirmed_again = await service.confirm(preview.id)
    await isolated_session.refresh(old_source)
    replacement = await isolated_session.get(CareerSource, confirmed.replacement_source_id)

    assert confirmed_again.id == confirmed.id
    assert confirmed_again.status == "confirmed"
    assert replacement is not None
    assert replacement.id != old_source.id
    assert replacement.provider == "smartrecruiters"
    assert replacement.status == "supported"
    assert replacement.next_poll_at == NOW
    assert replacement.baseline_completed_at is None
    assert replacement.notify_current_jobs_on_baseline is True
    assert old_source.status == "retired"
    assert old_source.next_poll_at is None
    assert old_source.replaced_by_source_id == replacement.id
    assert old_source.retired_at == NOW
    assert await isolated_session.scalar(select(func.count()).select_from(CareerSourceRepair)) == 1
    assert await isolated_session.scalar(select(func.count()).select_from(CareerSource)) == 2


async def test_source_repair_rejects_unstructured_and_cross_company_targets(
    isolated_session: AsyncSession,
) -> None:
    company, _, source = await configured_company(isolated_session)
    other = Company(
        canonical_name="Other Studio",
        normalized_name="other studio",
        official_domain="other.example",
        official_website_url="https://other.example/",
        headquarters_country="US",
        verification_status="verified",
    )
    isolated_session.add(other)
    await isolated_session.flush()
    isolated_session.add(
        CareerSource(
            company_id=other.id,
            provider="lever",
            careers_url="https://jobs.lever.co/already-owned",
            canonical_source_key="lever:already-owned",
            provider_config={"site": "already-owned", "region": "global"},
            status="supported",
            next_poll_at=NOW,
        )
    )
    await isolated_session.commit()
    service = SourceRepairService(isolated_session, clock=lambda: NOW)

    with pytest.raises(AppError) as unsupported:
        await service.preview(
            company_id=company.id,
            source_id=source.id,
            careers_url="https://acme.example/careers",
            notify_current_jobs=False,
        )
    assert unsupported.value.code == "SUPPORTED_SOURCE_REQUIRED"

    with pytest.raises(AppError) as owned:
        await service.preview(
            company_id=company.id,
            source_id=source.id,
            careers_url="https://jobs.lever.co/already-owned",
            notify_current_jobs=False,
        )
    assert owned.value.code == "SOURCE_ALREADY_OWNED"


async def test_source_repair_preview_expires_before_any_source_is_changed(
    isolated_session: AsyncSession,
) -> None:
    company, _, source = await configured_company(isolated_session)
    clock_now = NOW

    def clock() -> datetime:
        return clock_now

    service = SourceRepairService(isolated_session, clock=clock)
    preview = await service.preview(
        company_id=company.id,
        source_id=source.id,
        careers_url="https://jobs.ashbyhq.com/acme",
        notify_current_jobs=False,
    )
    clock_now = NOW + timedelta(minutes=16)

    with pytest.raises(AppError) as expired:
        await service.confirm(preview.id)

    await isolated_session.refresh(source)
    stored = await isolated_session.get(CareerSourceRepair, preview.id)
    assert expired.value.code == "SOURCE_REPAIR_PREVIEW_EXPIRED"
    assert stored is not None and stored.status == "expired"
    assert source.provider == "generic_html"
    assert source.status == "unsupported"


async def test_review_reminders_are_versioned_due_and_enqueued_once_per_schedule(
    isolated_session: AsyncSession,
) -> None:
    _, saved, source = await configured_company(isolated_session)
    service = ReviewReminderService(isolated_session, clock=lambda: NOW)
    local_due = datetime(2026, 8, 30, 11, 30, tzinfo=UTC)

    scheduled = await service.schedule(
        saved_company_id=saved.id,
        career_source_id=source.id,
        due_at=local_due,
    )
    due = await service.due()
    first_created = await service.enqueue_due_notifications()
    repeated_created = await service.enqueue_due_notifications()
    first_outbox = list((await isolated_session.scalars(select(NotificationOutbox))).all())

    assert [item.id for item in due.items] == [scheduled.id]
    assert scheduled.schedule_version == 1
    assert first_created == 1
    assert repeated_created == 0
    assert len(first_outbox) == 1
    first_payload = ReviewReminderPayload.model_validate(first_outbox[0].payload)
    assert first_payload.reminder_id == scheduled.id
    assert first_payload.company_name == "Acme Games"
    assert first_payload.due_at == local_due

    rescheduled = await service.reschedule(scheduled.id, NOW - timedelta(minutes=1))
    second_created = await service.enqueue_due_notifications()
    rows = list(
        (
            await isolated_session.scalars(
                select(NotificationOutbox).order_by(NotificationOutbox.event_group_key)
            )
        ).all()
    )
    checked = await service.checked(scheduled.id)

    assert rescheduled.schedule_version == 2
    assert rescheduled.notified_at is None
    assert second_created == 1
    assert len(rows) == 2
    assert {row.event_group_key for row in rows} == {
        f"review-reminder:{scheduled.id}:1",
        f"review-reminder:{scheduled.id}:2",
    }
    assert checked.status == "checked"
    assert checked.checked_at == NOW
    assert (await service.due()).items == []
    assert (await service.list(status="checked")).items[0].id == scheduled.id


async def test_assisted_source_http_contract_enforces_rfc822_and_local_time_offsets(
    isolated_session: AsyncSession,
) -> None:
    company, saved, _ = await configured_company(isolated_session)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield isolated_session

    app.dependency_overrides[database_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            wrong_type = await client.post(
                f"/api/v1/companies/{company.id}/email-alert-imports",
                content=job_alert_email(),
                headers={"Content-Type": "text/plain"},
            )
            imported = await client.post(
                f"/api/v1/companies/{company.id}/email-alert-imports",
                content=job_alert_email(message_id="http-import"),
                headers={
                    "Content-Type": "message/rfc822",
                    "X-JobScout-Filename": "../../http-alert.eml",
                },
            )
            listed = await client.get(f"/api/v1/companies/{company.id}/email-alert-imports")
            naive_time = await client.post(
                f"/api/v1/saved-companies/{saved.id}/review-reminders",
                json={"career_source_id": None, "due_at": "2026-08-31T09:00:00"},
            )
            local_time = await client.post(
                f"/api/v1/saved-companies/{saved.id}/review-reminders",
                json={
                    "career_source_id": None,
                    "due_at": "2026-08-31T09:00:00-04:00",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert wrong_type.status_code == 415
    assert wrong_type.json()["error"]["code"] == "EMAIL_CONTENT_TYPE_REQUIRED"
    assert imported.status_code == 200
    assert imported.json()["filename"] == "http-alert.eml"
    assert imported.json()["completeness"] == "partial"
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [imported.json()["id"]]
    assert naive_time.status_code == 422
    assert naive_time.json()["error"]["code"] == "VALIDATION_ERROR"
    assert local_time.status_code == 200
    assert local_time.json()["due_at"] == "2026-08-31T13:00:00Z"
    assert await isolated_session.scalar(select(func.count()).select_from(ReviewReminder)) == 1
