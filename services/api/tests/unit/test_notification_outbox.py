from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.db.models import NotificationOutbox
from app.notifications.rendering import (
    DISCLAIMER,
    alert_body,
    alert_title,
    render_email,
    render_test_email,
)
from app.notifications.schemas import (
    EmailTestPayload,
    NotificationJobSummary,
    NotificationPayload,
    parse_outbox_payload,
)
from app.notifications.service import (
    MAX_DELIVERY_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    NotificationService,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def summary(title: str = "Software Engineer Intern", **overrides: object) -> NotificationJobSummary:
    values: dict[str, object] = {
        "job_id": uuid4(),
        "title": title,
        "location_text": "New York, NY",
        "role_type": "internship",
        "apply_url": "https://jobs.lever.co/acme/job-1/apply",
        "event_type": "discovered",
        "first_seen_at": NOW,
        "match_reasons": ["Role type: internship", "Keyword match: python"],
    }
    values.update(overrides)
    return NotificationJobSummary.model_validate(values)


def payload(*jobs: NotificationJobSummary) -> NotificationPayload:
    items = list(jobs) or [summary()]
    return NotificationPayload(
        company_id=uuid4(),
        company_name="Acme Games",
        career_source_id=uuid4(),
        poll_run_id=uuid4(),
        title=alert_title("Acme Games", items),
        body=alert_body(items),
        action_path="/companies/acme",
        sponsorship_caveat="Historical H-1B filing records do not guarantee sponsorship.",
        matching_rules_version="preference-matching-v1",
        jobs=items,
    )


def outbox(channel: str = "email", attempt_count: int = 1) -> NotificationOutbox:
    return NotificationOutbox(
        channel=channel,
        event_group_key="poll:1:1",
        payload={},
        status="sending",
        attempt_count=attempt_count,
        next_attempt_at=NOW,
    )


def service() -> NotificationService:
    # The retry schedule is pure arithmetic over the row; no session work is involved.
    return NotificationService(None, clock=lambda: NOW)  # type: ignore[arg-type]


def test_grouped_title_and_body_name_every_matching_job() -> None:
    jobs = [summary(), summary("Data Analyst, New Grad", event_type="reopened")]

    assert alert_title("Acme Games", jobs) == "2 matching Acme Games openings"
    assert alert_title("Acme Games", jobs[:1]) == "1 matching Acme Games opening"
    body = alert_body(jobs)
    assert "New: Software Engineer Intern — New York, NY" in body
    assert "Reopened: Data Analyst, New Grad" in body


def test_rendered_email_carries_links_reasons_and_honest_caveats() -> None:
    message = render_email(
        payload(),
        recipient="owner@example.com",
        app_base_url="http://127.0.0.1:3000/",
    )

    assert message.to == "owner@example.com"
    assert message.subject == "JobScout AI: 1 matching Acme Games opening"
    assert "https://jobs.lever.co/acme/job-1/apply" in message.text_body
    assert "http://127.0.0.1:3000/companies/acme" in message.text_body
    assert "Keyword match: python" in message.text_body
    assert "2026-08-27T12:00:00+00:00" in message.text_body
    assert "do not guarantee sponsorship" in message.text_body
    assert DISCLAIMER in message.text_body
    assert "guaranteed sponsorship" not in message.text_body.casefold()


def test_rendered_html_escapes_external_job_text() -> None:
    message = render_email(
        payload(summary("<script>alert('x')</script> Intern")),
        recipient="owner@example.com",
        app_base_url="http://127.0.0.1:3000",
    )

    assert "<script>" not in message.html_body
    assert "&lt;script&gt;" in message.html_body


def test_legacy_job_alert_payloads_remain_parseable_without_a_kind_field() -> None:
    stored = payload().model_dump(mode="json")
    stored.pop("kind")

    parsed = parse_outbox_payload(stored)

    assert isinstance(parsed, NotificationPayload)
    assert parsed.kind == "job_alert"
    assert parsed.company_name == "Acme Games"


def test_test_email_payload_renders_a_safe_owner_requested_message() -> None:
    stored = EmailTestPayload(
        test_id=uuid4(),
        title="Your <JobScout> alerts are configured",
        body="This is a controlled & credential-free rendering test.",
        requested_at=NOW,
    ).model_dump(mode="json")

    parsed = parse_outbox_payload(stored)
    assert isinstance(parsed, EmailTestPayload)
    message = render_test_email(
        parsed,
        recipient="owner@example.com",
        app_base_url="http://127.0.0.1:3000/",
    )

    assert message.to == "owner@example.com"
    assert message.subject == "JobScout AI: email alerts are working"
    assert "http://127.0.0.1:3000/alerts" in message.text_body
    assert DISCLAIMER in message.text_body
    assert "<JobScout>" not in message.html_body
    assert "&lt;JobScout&gt;" in message.html_body
    assert "controlled &amp; credential-free" in message.html_body


def test_transient_failures_back_off_and_stop_at_the_attempt_cap() -> None:
    first = outbox(attempt_count=1)
    service()._schedule_retry(first, "EMAIL_RATE_LIMITED")

    assert first.status == "pending"
    assert first.last_error_code == "EMAIL_RATE_LIMITED"
    assert first.next_attempt_at == NOW + timedelta(seconds=RETRY_BACKOFF_SECONDS[0])

    later = outbox(attempt_count=3)
    service()._schedule_retry(later, "EMAIL_PROVIDER_UNAVAILABLE")
    assert later.next_attempt_at == NOW + timedelta(seconds=RETRY_BACKOFF_SECONDS[2])

    exhausted = outbox(attempt_count=MAX_DELIVERY_ATTEMPTS)
    service()._schedule_retry(exhausted, "EMAIL_PROVIDER_UNAVAILABLE")
    assert exhausted.status == "failed"
    assert exhausted.last_error_code == "EMAIL_PROVIDER_UNAVAILABLE"
