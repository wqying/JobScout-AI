from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationJobSummary(BaseModel):
    """One matching job as it is frozen into an outbox payload."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    title: str
    location_text: str | None = None
    role_type: str
    apply_url: str
    event_type: Literal["discovered", "reopened"]
    first_seen_at: datetime
    match_reasons: list[str] = Field(default_factory=list)


class NotificationPayload(BaseModel):
    """Immutable snapshot of one grouped alert.

    The payload deliberately carries no email address: the recipient is read from
    ``app_settings`` at delivery time, so a stored payload never holds owner contact data.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["job_alert"] = "job_alert"
    company_id: UUID
    company_name: str
    career_source_id: UUID
    poll_run_id: UUID
    title: str
    body: str
    action_path: str
    sponsorship_caveat: str | None = None
    matching_rules_version: str
    jobs: list[NotificationJobSummary] = Field(default_factory=list)


class EmailTestPayload(BaseModel):
    """Durable payload for an owner-requested delivery test."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["test_email"] = "test_email"
    test_id: UUID
    title: str
    body: str
    action_path: str = "/alerts"
    requested_at: datetime


class SourceDegradedPayload(BaseModel):
    """Local administrative alert raised when a monitored source becomes degraded."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["source_degraded"] = "source_degraded"
    company_id: UUID
    company_name: str
    career_source_id: UUID
    poll_run_id: UUID
    title: str
    body: str
    action_path: str
    error_code: str
    consecutive_failures: int
    occurred_at: datetime


class ReviewReminderPayload(BaseModel):
    """A local owner-created reminder for a source that needs manual review."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["review_reminder"] = "review_reminder"
    reminder_id: UUID
    company_id: UUID
    company_name: str
    career_source_id: UUID | None = None
    title: str
    body: str
    action_path: str
    due_at: datetime


OutboxPayload = (
    NotificationPayload | EmailTestPayload | SourceDegradedPayload | ReviewReminderPayload
)


def parse_outbox_payload(payload: object) -> OutboxPayload:
    if isinstance(payload, dict) and payload.get("kind") == "test_email":
        return EmailTestPayload.model_validate(payload)
    if isinstance(payload, dict) and payload.get("kind") == "source_degraded":
        return SourceDegradedPayload.model_validate(payload)
    if isinstance(payload, dict) and payload.get("kind") == "review_reminder":
        return ReviewReminderPayload.model_validate(payload)
    # Existing persisted job-alert payloads predate the discriminator and remain valid.
    return NotificationPayload.model_validate(payload)


class NotificationResponse(BaseModel):
    id: UUID
    outbox_id: UUID
    title: str
    body: str
    action_url: str
    read_at: datetime | None
    created_at: datetime
    company_id: UUID | None = None
    company_name: str | None = None
    jobs: list[NotificationJobSummary] = Field(default_factory=list)


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse] = Field(default_factory=list)
    next_cursor: UUID | None = None
    unread_count: int = 0


class EmailConfigurationResponse(BaseModel):
    requested_mode: Literal["auto", "fake", "resend"]
    active_adapter: Literal["fake", "resend", "unavailable"]
    live_delivery_ready: bool
    email_notifications_enabled: bool
    notification_email_configured: bool
    from_address: str | None = None
    diagnostic_code: str
    can_send_test: bool


class EmailTestRequest(BaseModel):
    acknowledge_external_email: Literal[True]


class EmailDeliveryResponse(BaseModel):
    id: UUID
    notification_type: Literal["job_alert", "test_email"]
    title: str
    status: Literal["pending", "sending", "sent", "failed", "cancelled"]
    attempt_count: int
    next_attempt_at: datetime | None = None
    delivery_adapter: Literal["fake", "resend", "unavailable"] | None = None
    provider_message_id: str | None = None
    last_error_code: str | None = None
    created_at: datetime
    sent_at: datetime | None = None
    retryable: bool = False


class EmailDeliveryListResponse(BaseModel):
    items: list[EmailDeliveryResponse] = Field(default_factory=list)
    next_cursor: UUID | None = None
