from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import database_session
from app.api.errors import AppError
from app.core.config import Settings, get_settings
from app.db.models import (
    AppSettings,
    CareerSource,
    Company,
    InAppNotification,
    NotificationOutbox,
    OwnerProfile,
    SavedCompany,
)
from app.main import app
from app.monitoring.polling import PollingService
from app.monitoring.schemas import FetchResult, NormalizedJob, RawJob
from app.notifications.email import (
    EmailDelivery,
    EmailMessage,
    FakeEmailSender,
    TransientEmailError,
)
from app.notifications.schemas import (
    EmailTestPayload,
    NotificationPayload,
    SourceDegradedPayload,
)
from app.notifications.service import MAX_DELIVERY_ATTEMPTS, NotificationService

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

    async def fetch_jobs(self, _source: object) -> FetchResult:
        return self.result

    def normalize(self, raw: RawJob) -> NormalizedJob:
        return NormalizedJob.model_validate(raw.raw_payload)


class FailingAdapter:
    async def fetch_jobs(self, _source: object) -> FetchResult:
        raise httpx.ReadTimeout("controlled source timeout")

    def normalize(self, _raw: RawJob) -> NormalizedJob:
        raise AssertionError("A failed fetch has no jobs to normalize")


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.now
        self.now += timedelta(minutes=1)
        return current


class RecordingEmailSender:
    """Credential-free sender that records every attempted idempotency key."""

    adapter_name = "fake"

    def __init__(self, scripted_errors: list[Exception | None] | None = None) -> None:
        self.scripted_errors = scripted_errors or []
        self.messages: list[EmailMessage] = []
        self.idempotency_keys: list[str | None] = []

    async def send(
        self,
        message: EmailMessage,
        *,
        idempotency_key: str | None = None,
    ) -> EmailDelivery:
        self.messages.append(message)
        self.idempotency_keys.append(idempotency_key)
        if self.scripted_errors:
            error = self.scripted_errors.pop(0)
            if error is not None:
                raise error
        return EmailDelivery(provider_message_id="recording-email-1")

    async def aclose(self) -> None:
        return None


def job_payload(
    external_id: str = "job-1",
    title: str = "Software Engineer Intern",
    location: str = "New York, NY",
) -> dict[str, object]:
    return {
        "external_job_id": external_id,
        "title": title,
        "location_text": location,
        "department": "Engineering",
        "employment_type": "Intern",
        "description_text": "Build reliable Python services.",
        "apply_url": f"https://jobs.lever.co/acme/{external_id}/apply",
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


async def configured_installation(
    session: AsyncSession,
    **settings_overrides: object,
) -> tuple[SavedCompany, CareerSource]:
    session.add(OwnerProfile(display_name="Local owner", email="owner@example.com"))
    values: dict[str, object] = {
        "role_types": ["internship", "new_grad", "entry_level"],
        "keywords": [],
        "excluded_keywords": [],
        "preferred_locations": [],
        "remote_preference": "any",
        "notify_current_jobs_on_save": False,
        "email_notifications_enabled": True,
        "notification_email": "owner@example.com",
    }
    values.update(settings_overrides)
    session.add(AppSettings(**values))
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


def polling(session: AsyncSession, adapter: MutableAdapter) -> PollingService:
    return PollingService(
        session,
        UnusedHttp(),
        clock=MutableClock(),
        adapter_factory=lambda _provider, _http: adapter,
    )


def delivery_clock() -> MutableClock:
    """A clock strictly after every poll above, so freshly written rows are already due."""

    clock = MutableClock()
    clock.now = datetime(2026, 8, 27, 13, 0, tzinfo=UTC)
    return clock


def live_email_runtime() -> Settings:
    """A ready configuration used only for enqueue validation; it never builds a live client."""

    return Settings(
        _env_file=None,
        app_env="local",
        resend_api_key="credential-free-test-key",
        email_from="JobScout AI <alerts@example.com>",
        email_delivery_mode="auto",
    )


def email_test_payload(
    title: str = "Your JobScout AI email alerts are configured",
) -> dict[str, object]:
    return EmailTestPayload(
        test_id=uuid4(),
        title=title,
        body="This controlled test uses the durable notification outbox.",
        requested_at=datetime(2026, 8, 27, 13, 0, tzinfo=UTC),
    ).model_dump(mode="json")


async def outbox_rows(session: AsyncSession) -> list[NotificationOutbox]:
    return list(
        (
            await session.scalars(
                select(NotificationOutbox).order_by(
                    NotificationOutbox.channel, NotificationOutbox.created_at
                )
            )
        ).all()
    )


async def test_baseline_import_does_not_notify_but_later_jobs_do(
    isolated_session: AsyncSession,
) -> None:
    _, source = await configured_installation(isolated_session)
    adapter = MutableAdapter()
    service = polling(isolated_session, adapter)

    await service.poll_source(source.id)
    assert await outbox_rows(isolated_session) == []

    adapter.result = result(job_payload(), job_payload("job-2", "Data Analyst, New Grad"))
    await service.poll_source(source.id)

    rows = await outbox_rows(isolated_session)
    assert [row.channel for row in rows] == ["email", "in_app"]
    assert {row.status for row in rows} == {"pending"}
    payload = NotificationPayload.model_validate(rows[0].payload)
    assert [job.title for job in payload.jobs] == ["Data Analyst, New Grad"]
    assert payload.jobs[0].match_reasons == ["Role type: new grad"]
    # Stored payloads carry no owner contact data; the recipient is read at delivery time.
    assert "owner@example.com" not in json.dumps(rows[0].payload)


async def test_opted_in_baseline_alerts_on_currently_open_jobs(
    isolated_session: AsyncSession,
) -> None:
    saved, source = await configured_installation(isolated_session)
    saved.notify_current_jobs = True
    await isolated_session.commit()

    await polling(isolated_session, MutableAdapter()).poll_source(source.id)

    rows = await outbox_rows(isolated_session)
    assert [row.channel for row in rows] == ["email", "in_app"]


async def test_each_replacement_source_honors_its_own_baseline_alert_choice(
    isolated_session: AsyncSession,
) -> None:
    saved, original = await configured_installation(isolated_session)
    established_at = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    saved.baseline_completed_at = established_at
    original.baseline_completed_at = established_at
    saved.notify_current_jobs = True
    quiet_source = CareerSource(
        company_id=original.company_id,
        provider="lever",
        careers_url="https://jobs.lever.co/acme-quiet-replacement",
        canonical_source_key="lever:acme-quiet-replacement",
        provider_config={"site": "acme-quiet-replacement", "region": "global"},
        status="supported",
        next_poll_at=established_at,
        notify_current_jobs_on_baseline=False,
    )
    isolated_session.add(quiet_source)
    await isolated_session.commit()

    await polling(isolated_session, MutableAdapter()).poll_source(quiet_source.id)
    await isolated_session.refresh(quiet_source)

    assert quiet_source.baseline_completed_at is not None
    assert await outbox_rows(isolated_session) == []

    saved.notify_current_jobs = False
    loud_source = CareerSource(
        company_id=original.company_id,
        provider="lever",
        careers_url="https://jobs.lever.co/acme-loud-replacement",
        canonical_source_key="lever:acme-loud-replacement",
        provider_config={"site": "acme-loud-replacement", "region": "global"},
        status="supported",
        next_poll_at=established_at,
        notify_current_jobs_on_baseline=True,
    )
    isolated_session.add(loud_source)
    await isolated_session.commit()

    await polling(isolated_session, MutableAdapter()).poll_source(loud_source.id)
    await isolated_session.refresh(loud_source)

    assert loud_source.baseline_completed_at is not None
    assert [row.channel for row in await outbox_rows(isolated_session)] == ["email", "in_app"]


async def test_multiple_matching_jobs_from_one_poll_group_into_one_email(
    isolated_session: AsyncSession,
) -> None:
    _, source = await configured_installation(isolated_session)
    adapter = MutableAdapter()
    service = polling(isolated_session, adapter)
    await service.poll_source(source.id)

    adapter.result = result(
        job_payload(),
        job_payload("job-2", "Data Analyst, New Grad"),
        job_payload("job-3", "Machine Learning Intern"),
    )
    await service.poll_source(source.id)

    rows = await outbox_rows(isolated_session)
    assert len(rows) == 2
    email = next(row for row in rows if row.channel == "email")
    payload = NotificationPayload.model_validate(email.payload)
    assert len(payload.jobs) == 2
    assert payload.title == "2 matching Acme Games openings"


async def test_preferences_filter_alerts_without_blocking_job_collection(
    isolated_session: AsyncSession,
) -> None:
    _, source = await configured_installation(
        isolated_session,
        keywords=["python"],
        excluded_keywords=["analyst"],
        preferred_locations=["New York"],
    )
    adapter = MutableAdapter()
    service = polling(isolated_session, adapter)
    await service.poll_source(source.id)

    adapter.result = result(
        job_payload(),
        job_payload("job-2", "Data Analyst, New Grad"),
        job_payload("job-3", "Research Intern", "Austin, TX"),
        job_payload("job-4", "Platform Intern"),
    )
    run = await service.poll_source(source.id)

    assert run.jobs_created == 3
    rows = await outbox_rows(isolated_session)
    payload = NotificationPayload.model_validate(rows[0].payload)
    assert [job.title for job in payload.jobs] == ["Platform Intern"]


async def test_paused_company_collects_nothing_and_alerts_nothing(
    isolated_session: AsyncSession,
) -> None:
    saved, source = await configured_installation(isolated_session)
    adapter = MutableAdapter()
    service = polling(isolated_session, adapter)
    await service.poll_source(source.id)

    saved.status = "paused"
    await isolated_session.commit()
    adapter.result = result(job_payload(), job_payload("job-2", "Platform Intern"))
    await service.poll_source(source.id)

    assert await outbox_rows(isolated_session) == []


async def test_delivery_is_idempotent_and_a_retry_cannot_duplicate_an_email(
    isolated_session: AsyncSession,
) -> None:
    _, source = await configured_installation(isolated_session)
    adapter = MutableAdapter()
    service = polling(isolated_session, adapter)
    await service.poll_source(source.id)
    adapter.result = result(job_payload(), job_payload("job-2", "Platform Intern"))
    await service.poll_source(source.id)

    sender = FakeEmailSender()
    notifications = NotificationService(
        isolated_session,
        clock=delivery_clock(),
        email_sender=sender,
        app_base_url="http://127.0.0.1:3000",
    )

    claimed = await notifications.claim_pending()
    assert len(claimed) == 2
    assert await notifications.claim_pending() == []

    for outbox_id in claimed:
        assert await notifications.deliver(outbox_id) == "sent"
    # An at-least-once redelivery of the same rows must change nothing.
    for outbox_id in claimed:
        assert await notifications.deliver(outbox_id) == "sent"

    rows = await outbox_rows(isolated_session)
    assert {row.status for row in rows} == {"sent"}
    assert len(sender.delivered) == 1
    assert await isolated_session.scalar(select(func.count()).select_from(InAppNotification)) == 1
    email = next(row for row in rows if row.channel == "email")
    assert email.provider_message_id == "fake-email-1"
    assert email.delivery_adapter == "fake"
    assert email.next_attempt_at is not None
    assert (await notifications.get_email_delivery(email.id)).next_attempt_at is None


async def test_transient_email_failures_retry_and_permanent_ones_stop(
    isolated_session: AsyncSession,
) -> None:
    _, source = await configured_installation(isolated_session)
    adapter = MutableAdapter()
    service = polling(isolated_session, adapter)
    await service.poll_source(source.id)
    adapter.result = result(job_payload(), job_payload("job-2", "Platform Intern"))
    await service.poll_source(source.id)

    sender = FakeEmailSender(scripted_errors=[TransientEmailError("EMAIL_RATE_LIMITED"), None])
    clock = delivery_clock()
    notifications = NotificationService(
        isolated_session, clock=clock, email_sender=sender, app_base_url="http://127.0.0.1:3000"
    )
    email = next(row for row in await outbox_rows(isolated_session) if row.channel == "email")

    await notifications.claim_pending()
    assert await notifications.deliver(email.id) == "pending"
    assert email.last_error_code == "EMAIL_RATE_LIMITED"
    assert sender.delivered == []

    # Fast-forward past the backoff window so the retry becomes claimable.
    clock.now = email.next_attempt_at + timedelta(seconds=1)
    assert email.id in await notifications.claim_pending()
    assert await notifications.deliver(email.id) == "sent"
    assert len(sender.delivered) == 1


async def test_disabled_email_notifications_cancel_instead_of_sending(
    isolated_session: AsyncSession,
) -> None:
    _, source = await configured_installation(isolated_session)
    adapter = MutableAdapter()
    service = polling(isolated_session, adapter)
    await service.poll_source(source.id)
    adapter.result = result(job_payload(), job_payload("job-2", "Platform Intern"))
    await service.poll_source(source.id)

    settings = await isolated_session.scalar(select(AppSettings).limit(1))
    assert settings is not None
    settings.email_notifications_enabled = False
    await isolated_session.commit()

    sender = FakeEmailSender()
    notifications = NotificationService(
        isolated_session,
        clock=delivery_clock(),
        email_sender=sender,
        app_base_url="http://127.0.0.1:3000",
    )
    email = next(row for row in await outbox_rows(isolated_session) if row.channel == "email")

    await notifications.claim_pending()
    assert await notifications.deliver(email.id) == "cancelled"
    assert email.last_error_code == "EMAIL_NOTIFICATIONS_DISABLED"
    assert sender.delivered == []


async def test_email_rows_are_not_written_when_email_alerts_are_off(
    isolated_session: AsyncSession,
) -> None:
    _, source = await configured_installation(
        isolated_session, email_notifications_enabled=False, notification_email=None
    )
    adapter = MutableAdapter()
    service = polling(isolated_session, adapter)
    await service.poll_source(source.id)
    adapter.result = result(job_payload(), job_payload("job-2", "Platform Intern"))
    await service.poll_source(source.id)

    assert [row.channel for row in await outbox_rows(isolated_session)] == ["in_app"]


async def test_email_configuration_reports_live_readiness_without_exposing_the_key(
    isolated_session: AsyncSession,
) -> None:
    await configured_installation(isolated_session)
    service = NotificationService(isolated_session)

    configuration = await service.get_email_configuration(live_email_runtime())

    assert configuration.active_adapter == "resend"
    assert configuration.live_delivery_ready is True
    assert configuration.can_send_test is True
    assert configuration.diagnostic_code == "READY_TO_SEND"
    serialized = configuration.model_dump_json()
    assert "credential-free-test-key" not in serialized
    assert "resend_api_key" not in serialized


async def test_fake_mode_is_visible_and_cannot_queue_a_misleading_live_test(
    isolated_session: AsyncSession,
) -> None:
    await configured_installation(isolated_session)
    runtime = Settings(
        _env_file=None,
        app_env="local",
        email_delivery_mode="fake",
        resend_api_key="unused-test-key",
        email_from="alerts@example.com",
    )
    service = NotificationService(isolated_session)

    configuration = await service.get_email_configuration(runtime)
    assert configuration.active_adapter == "fake"
    assert configuration.can_send_test is False
    assert configuration.diagnostic_code == "FAKE_EMAIL_MODE"

    with pytest.raises(AppError) as caught:
        await service.enqueue_test_email(runtime)

    assert caught.value.code == "LIVE_EMAIL_NOT_CONFIGURED"
    assert await outbox_rows(isolated_session) == []


async def test_test_email_enqueue_is_durable_and_keeps_recipient_and_key_out_of_payload(
    isolated_session: AsyncSession,
) -> None:
    await configured_installation(isolated_session)
    notifications = NotificationService(isolated_session, clock=delivery_clock())

    queued = await notifications.enqueue_test_email(live_email_runtime())
    row = await isolated_session.get(NotificationOutbox, queued.id)

    assert row is not None
    assert queued.notification_type == "test_email"
    assert queued.status == "pending"
    assert row.channel == "email"
    assert row.event_group_key.startswith("test:")
    parsed = EmailTestPayload.model_validate(row.payload)
    assert parsed.test_id is not None
    stored = json.dumps(row.payload)
    assert "owner@example.com" not in stored
    assert "credential-free-test-key" not in stored


@pytest.mark.parametrize(
    ("settings_overrides", "expected_code"),
    [
        ({"email_notifications_enabled": False}, "EMAIL_NOTIFICATIONS_DISABLED"),
        (
            {"email_notifications_enabled": True, "notification_email": None},
            "NOTIFICATION_EMAIL_MISSING",
        ),
    ],
)
async def test_test_email_enqueue_requires_complete_owner_settings(
    isolated_session: AsyncSession,
    settings_overrides: dict[str, object],
    expected_code: str,
) -> None:
    await configured_installation(isolated_session, **settings_overrides)

    with pytest.raises(AppError) as caught:
        await NotificationService(isolated_session).enqueue_test_email(live_email_runtime())

    assert caught.value.code == expected_code
    assert await outbox_rows(isolated_session) == []


async def test_test_email_delivery_reuses_one_outbox_idempotency_key_across_retry(
    isolated_session: AsyncSession,
) -> None:
    await configured_installation(isolated_session)
    clock = delivery_clock()
    sender = RecordingEmailSender(
        scripted_errors=[TransientEmailError("EMAIL_NETWORK_ERROR"), None]
    )
    notifications = NotificationService(
        isolated_session,
        clock=clock,
        email_sender=sender,
        app_base_url="http://127.0.0.1:3000",
    )
    queued = await notifications.enqueue_test_email(live_email_runtime())
    expected_key = f"jobscout-email:{queued.id}"

    assert queued.id in await notifications.claim_pending()
    assert await notifications.deliver(queued.id) == "pending"
    row = await isolated_session.get(NotificationOutbox, queued.id)
    assert row is not None
    clock.now = row.next_attempt_at + timedelta(seconds=1)
    assert queued.id in await notifications.claim_pending()
    assert await notifications.deliver(queued.id) == "sent"

    assert sender.idempotency_keys == [expected_key, expected_key]
    assert len(sender.messages) == 2
    assert sender.messages[-1].to == "owner@example.com"
    assert sender.messages[-1].subject == "JobScout AI: email alerts are working"


async def test_malformed_payload_fails_terminally_instead_of_recycling_its_lease(
    isolated_session: AsyncSession,
) -> None:
    await configured_installation(isolated_session)
    row = NotificationOutbox(
        channel="email",
        event_group_key=f"test:{uuid4()}",
        payload={"kind": "test_email", "unexpected": True},
        status="pending",
        next_attempt_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
    )
    isolated_session.add(row)
    await isolated_session.commit()
    notifications = NotificationService(
        isolated_session,
        clock=delivery_clock(),
        email_sender=FakeEmailSender(),
    )

    assert row.id in await notifications.claim_pending()
    assert await notifications.deliver(row.id) == "failed"

    assert row.last_error_code == "NOTIFICATION_PAYLOAD_INVALID"
    assert row.attempt_count == 1


async def test_unexpected_delivery_failures_obey_the_attempt_cap(
    isolated_session: AsyncSession,
) -> None:
    await configured_installation(isolated_session)
    row = NotificationOutbox(
        channel="email",
        event_group_key=f"test:{uuid4()}",
        payload=email_test_payload(),
        status="sending",
        attempt_count=MAX_DELIVERY_ATTEMPTS,
        next_attempt_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
    )
    isolated_session.add(row)
    await isolated_session.commit()
    sender = RecordingEmailSender(scripted_errors=[RuntimeError("controlled sender failure")])

    status = await NotificationService(
        isolated_session,
        clock=delivery_clock(),
        email_sender=sender,
    ).deliver(row.id)

    assert status == "failed"
    assert row.last_error_code == "NOTIFICATION_DELIVERY_UNEXPECTED"
    assert row.attempt_count == MAX_DELIVERY_ATTEMPTS


async def test_claim_cap_terminally_fails_exhausted_rows_and_release_does_not_spend_an_attempt(
    isolated_session: AsyncSession,
) -> None:
    exhausted = NotificationOutbox(
        channel="email",
        event_group_key=f"test:{uuid4()}",
        payload=email_test_payload("Exhausted test"),
        status="sending",
        attempt_count=MAX_DELIVERY_ATTEMPTS,
        next_attempt_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
    )
    claimable = NotificationOutbox(
        channel="email",
        event_group_key=f"test:{uuid4()}",
        payload=email_test_payload("Claimable test"),
        status="pending",
        next_attempt_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
    )
    isolated_session.add_all([exhausted, claimable])
    await isolated_session.commit()
    notifications = NotificationService(isolated_session, clock=delivery_clock())

    claimed = await notifications.claim_pending()

    assert claimed == [claimable.id]
    assert exhausted.status == "failed"
    assert exhausted.last_error_code == "DELIVERY_ATTEMPTS_EXHAUSTED"
    assert claimable.attempt_count == 1

    await notifications.release_claim(claimable.id)

    assert claimable.status == "pending"
    assert claimable.attempt_count == 0


async def test_email_delivery_history_get_pagination_and_manual_retry(
    isolated_session: AsyncSession,
) -> None:
    await configured_installation(isolated_session)
    notifications = NotificationService(isolated_session, clock=delivery_clock())
    first = await notifications.enqueue_test_email(live_email_runtime())
    first_row = await isolated_session.get(NotificationOutbox, first.id)
    assert first_row is not None
    first_row.status = "failed"
    first_row.attempt_count = MAX_DELIVERY_ATTEMPTS
    first_row.last_error_code = "EMAIL_PROVIDER_UNAVAILABLE"
    first_row.provider_message_id = "stale-provider-id"
    first_row.sent_at = datetime(2026, 8, 27, 14, 0, tzinfo=UTC)
    await isolated_session.commit()
    second = await notifications.enqueue_test_email(live_email_runtime())

    page_one = await notifications.list_email_deliveries(limit=1)
    assert len(page_one.items) == 1
    assert page_one.next_cursor == page_one.items[0].id
    page_two = await notifications.list_email_deliveries(
        limit=1,
        cursor=page_one.next_cursor,
    )
    assert len(page_two.items) == 1
    assert page_two.next_cursor is None
    assert {page_one.items[0].id, page_two.items[0].id} == {first.id, second.id}
    assert (await notifications.get_email_delivery(first.id)).retryable is True

    retried = await notifications.retry_email_delivery(first.id)

    assert retried.status == "pending"
    assert retried.attempt_count == 0
    assert retried.provider_message_id is None
    assert retried.last_error_code is None
    assert retried.sent_at is None
    assert retried.retryable is False
    with pytest.raises(AppError) as caught:
        await notifications.retry_email_delivery(first.id)
    assert caught.value.code == "EMAIL_DELIVERY_NOT_RETRYABLE"


async def test_email_configuration_test_delivery_and_retry_http_contract(
    isolated_session: AsyncSession,
) -> None:
    await configured_installation(isolated_session)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield isolated_session

    app.dependency_overrides[database_session] = override_session
    app.dependency_overrides[get_settings] = live_email_runtime
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            configuration_response = await client.get("/api/v1/email/configuration")
            rejected_response = await client.post(
                "/api/v1/email/test",
                json={"acknowledge_external_email": False},
            )
            enqueue_response = await client.post(
                "/api/v1/email/test",
                json={"acknowledge_external_email": True},
            )
            outbox_id = enqueue_response.json()["id"]
            get_response = await client.get(f"/api/v1/email/deliveries/{outbox_id}")
            list_response = await client.get("/api/v1/email/deliveries?limit=1")

            row = await isolated_session.get(NotificationOutbox, UUID(outbox_id))
            assert row is not None
            row.status = "failed"
            row.last_error_code = "EMAIL_PROVIDER_UNAVAILABLE"
            await isolated_session.commit()
            retry_response = await client.post(f"/api/v1/email/deliveries/{outbox_id}/retry")
    finally:
        app.dependency_overrides.clear()

    assert configuration_response.status_code == 200
    assert configuration_response.json()["can_send_test"] is True
    assert "resend_api_key" not in configuration_response.text
    assert "credential-free-test-key" not in configuration_response.text
    assert rejected_response.status_code == 422
    assert enqueue_response.status_code == 202
    assert enqueue_response.json()["notification_type"] == "test_email"
    assert get_response.status_code == 200
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["id"] == outbox_id
    assert retry_response.status_code == 202
    assert retry_response.json()["status"] == "pending"


async def test_only_one_owner_test_email_can_be_in_flight(
    isolated_session: AsyncSession,
) -> None:
    await configured_installation(isolated_session)
    notifications = NotificationService(isolated_session, clock=delivery_clock())
    first = await notifications.enqueue_test_email(live_email_runtime())

    with pytest.raises(AppError) as caught:
        await notifications.enqueue_test_email(live_email_runtime())

    assert caught.value.code == "TEST_EMAIL_ALREADY_QUEUED"
    assert caught.value.details == {"delivery_id": str(first.id)}
    assert len(await outbox_rows(isolated_session)) == 1


async def test_tenth_source_failure_creates_one_local_admin_alert(
    isolated_session: AsyncSession,
) -> None:
    _, source = await configured_installation(isolated_session)
    clock = MutableClock()
    service = PollingService(
        isolated_session,
        UnusedHttp(),
        clock=clock,
        adapter_factory=lambda _provider, _http: FailingAdapter(),
    )

    for _ in range(10):
        await service.poll_source(source.id)

    rows = await outbox_rows(isolated_session)
    assert len(rows) == 1
    assert rows[0].channel == "in_app"
    payload = SourceDegradedPayload.model_validate(rows[0].payload)
    assert payload.company_name == "Acme Games"
    assert payload.consecutive_failures == 10
    assert payload.error_code == "SOURCE_TIMEOUT"

    await service.poll_source(source.id)
    assert len(await outbox_rows(isolated_session)) == 1

    notifications = NotificationService(isolated_session, clock=delivery_clock())
    assert rows[0].id in await notifications.claim_pending()
    assert await notifications.deliver(rows[0].id) == "sent"
    listing = await notifications.list_notifications()
    assert len(listing.items) == 1
    assert listing.items[0].title == "Monitoring degraded for Acme Games"
    assert listing.items[0].jobs == []


async def test_notification_history_pages_and_marks_read(
    isolated_session: AsyncSession,
) -> None:
    _, source = await configured_installation(isolated_session)
    adapter = MutableAdapter()
    service = polling(isolated_session, adapter)
    await service.poll_source(source.id)
    adapter.result = result(job_payload(), job_payload("job-2", "Platform Intern"))
    await service.poll_source(source.id)

    notifications = NotificationService(
        isolated_session,
        clock=delivery_clock(),
        email_sender=FakeEmailSender(),
        app_base_url="http://127.0.0.1:3000",
    )
    for outbox_id in await notifications.claim_pending():
        await notifications.deliver(outbox_id)

    listing = await notifications.list_notifications()
    assert listing.unread_count == 1
    assert len(listing.items) == 1
    item = listing.items[0]
    assert item.company_name == "Acme Games"
    assert item.action_url == f"/companies/{source.company_id}"
    assert [job.title for job in item.jobs] == ["Platform Intern"]

    marked = await notifications.mark_read(item.id)
    assert marked.read_at is not None
    assert (await notifications.list_notifications()).unread_count == 0
    assert (await notifications.list_notifications(unread_only=True)).items == []


async def test_reopened_jobs_alert_again(isolated_session: AsyncSession) -> None:
    _, source = await configured_installation(isolated_session)
    adapter = MutableAdapter()
    service = polling(isolated_session, adapter)
    await service.poll_source(source.id)

    adapter.result = result(job_payload(), job_payload("job-2", "Platform Intern"))
    await service.poll_source(source.id)
    adapter.result = result(job_payload())
    await service.poll_source(source.id)
    await service.poll_source(source.id)
    adapter.result = result(job_payload(), job_payload("job-2", "Platform Intern"))
    await service.poll_source(source.id)

    rows = await outbox_rows(isolated_session)
    payloads = [NotificationPayload.model_validate(row.payload) for row in rows]
    events = sorted(job.event_type for payload in payloads for job in payload.jobs if payload.jobs)
    assert events == ["discovered", "discovered", "reopened", "reopened"]
