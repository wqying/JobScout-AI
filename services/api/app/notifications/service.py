"""Transactional outbox for alerts (Section 16).

Two boundaries meet here.

**Write path.** ``enqueue_job_alerts`` runs inside the polling transaction and only adds rows; it
never sends anything and never commits. If the poll rolls back, so do its alerts. Matching jobs
from one company and one poll collapse into a single ``event_group_key``, so a company that posts
five roles at once produces one email, not five.

**Delivery path.** A worker claims pending rows, marks them ``sending``, delivers, and records the
outcome. Claiming takes a short lease through ``next_attempt_at``, so a crashed worker's row
becomes eligible again instead of stalling forever. Delivery is idempotent: an in-app row is keyed
by ``outbox_id``, and a row already ``sent`` is never re-delivered.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import structlog
from pydantic import ValidationError
from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import Settings
from app.db.models import (
    AppSettings,
    CareerSource,
    Company,
    InAppNotification,
    Job,
    NotificationOutbox,
    SavedCompany,
)
from app.notifications.email import (
    EmailSender,
    PermanentEmailError,
    TransientEmailError,
    select_email_adapter,
)
from app.notifications.matching import MATCHING_RULES_VERSION, match_job
from app.notifications.rendering import (
    SPONSORSHIP_CAVEAT,
    alert_body,
    alert_title,
    render_email,
    render_test_email,
)
from app.notifications.schemas import (
    EmailConfigurationResponse,
    EmailDeliveryListResponse,
    EmailDeliveryResponse,
    EmailTestPayload,
    NotificationJobSummary,
    NotificationListResponse,
    NotificationPayload,
    NotificationResponse,
    OutboxPayload,
    ReviewReminderPayload,
    SourceDegradedPayload,
    parse_outbox_payload,
)

Clock = Callable[[], datetime]

IN_APP_CHANNEL = "in_app"
EMAIL_CHANNEL = "email"
MAX_DELIVERY_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = (60, 300, 900, 3600)
CLAIM_LEASE_SECONDS = 300

logger = structlog.get_logger("jobscout.notifications")


@dataclass(frozen=True)
class AlertCandidate:
    """One job whose lifecycle event may deserve an alert."""

    job: Job
    event_type: str


class NotificationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Clock | None = None,
        email_sender: EmailSender | None = None,
        app_base_url: str = "http://127.0.0.1:3000",
    ) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(UTC))
        self.email_sender = email_sender
        self.app_base_url = app_base_url

    # ------------------------------------------------------------------ write path

    async def enqueue_job_alerts(
        self,
        *,
        source: CareerSource,
        poll_run_id: UUID,
        candidates: Sequence[AlertCandidate],
        is_baseline_poll: bool,
    ) -> int:
        """Add outbox rows for matching jobs. Caller owns the transaction."""

        if not candidates:
            return 0
        saved = await self.session.scalar(
            select(SavedCompany).where(SavedCompany.company_id == source.company_id)
        )
        if saved is None or saved.status != "active":
            return 0
        settings = await self.session.scalar(select(AppSettings).limit(1))
        if settings is None:
            return 0
        notify_on_baseline = source.notify_current_jobs_on_baseline
        if notify_on_baseline is None:
            notify_on_baseline = saved.notify_current_jobs or settings.notify_current_jobs_on_save
        if is_baseline_poll and not notify_on_baseline:
            logger.info(
                "baseline_alerts_suppressed",
                company_id=str(source.company_id),
                candidate_count=len(candidates),
            )
            return 0

        summaries: list[NotificationJobSummary] = []
        for candidate in candidates:
            decision = match_job(candidate.job, settings)
            if not decision.matched:
                logger.info(
                    "job_alert_filtered",
                    job_id=str(candidate.job.id),
                    rejection_code=decision.rejection_code,
                )
                continue
            summaries.append(
                NotificationJobSummary(
                    job_id=candidate.job.id,
                    title=candidate.job.title,
                    location_text=candidate.job.location_text,
                    role_type=candidate.job.role_type,
                    apply_url=candidate.job.apply_url,
                    event_type="reopened" if candidate.event_type == "reopened" else "discovered",
                    first_seen_at=candidate.job.first_seen_at,
                    match_reasons=list(decision.reasons),
                )
            )
        if not summaries:
            return 0

        company = await self.session.get(Company, source.company_id)
        company_name = company.canonical_name if company else "Saved company"
        payload = NotificationPayload(
            company_id=source.company_id,
            company_name=company_name,
            career_source_id=source.id,
            poll_run_id=poll_run_id,
            title=alert_title(company_name, summaries),
            body=alert_body(summaries),
            action_path=f"/companies/{source.company_id}",
            sponsorship_caveat=SPONSORSHIP_CAVEAT,
            matching_rules_version=MATCHING_RULES_VERSION,
            jobs=summaries,
        )
        group_key = f"poll:{poll_run_id}:{source.company_id}"
        channels = [IN_APP_CHANNEL]
        if settings.email_notifications_enabled and settings.notification_email:
            channels.append(EMAIL_CHANNEL)

        now = self.clock()
        serialized = payload.model_dump(mode="json")
        for channel in channels:
            duplicate = await self.session.scalar(
                select(NotificationOutbox.id).where(
                    NotificationOutbox.channel == channel,
                    NotificationOutbox.event_group_key == group_key,
                )
            )
            if duplicate is not None:
                continue
            self.session.add(
                NotificationOutbox(
                    channel=channel,
                    event_group_key=group_key,
                    payload=serialized,
                    status="pending",
                    next_attempt_at=now,
                )
            )
        logger.info(
            "job_alerts_enqueued",
            company_id=str(source.company_id),
            poll_run_id=str(poll_run_id),
            job_count=len(summaries),
            channels=channels,
        )
        return len(summaries)

    async def enqueue_source_degraded_alert(
        self,
        *,
        source: CareerSource,
        poll_run_id: UUID,
    ) -> None:
        """Add one local admin alert when a source crosses the degraded threshold.

        The polling service owns the surrounding transaction, so the degraded source state and
        its alert either commit together or both roll back.
        """

        company = await self.session.get(Company, source.company_id)
        company_name = company.canonical_name if company else "Saved company"
        now = self.clock()
        payload = SourceDegradedPayload(
            company_id=source.company_id,
            company_name=company_name,
            career_source_id=source.id,
            poll_run_id=poll_run_id,
            title=f"Monitoring degraded for {company_name}",
            body=(
                f"The careers source failed {source.consecutive_failures} consecutive polls. "
                f"Latest error: {source.last_error_code or 'SOURCE_POLL_FAILED'}."
            ),
            action_path=f"/companies/{source.company_id}",
            error_code=source.last_error_code or "SOURCE_POLL_FAILED",
            consecutive_failures=source.consecutive_failures,
            occurred_at=now,
        )
        group_key = f"source-degraded:{source.id}:{poll_run_id}"
        duplicate = await self.session.scalar(
            select(NotificationOutbox.id).where(
                NotificationOutbox.channel == IN_APP_CHANNEL,
                NotificationOutbox.event_group_key == group_key,
            )
        )
        if duplicate is None:
            self.session.add(
                NotificationOutbox(
                    channel=IN_APP_CHANNEL,
                    event_group_key=group_key,
                    payload=payload.model_dump(mode="json"),
                    status="pending",
                    next_attempt_at=now,
                )
            )
        logger.warning(
            "source_degraded_alert_enqueued",
            company_id=str(source.company_id),
            source_id=str(source.id),
            poll_run_id=str(poll_run_id),
        )

    async def get_email_configuration(self, runtime: Settings) -> EmailConfigurationResponse:
        stored = await self.session.scalar(select(AppSettings).limit(1))
        selection = select_email_adapter(runtime)
        email_enabled = bool(stored and stored.email_notifications_enabled)
        recipient_configured = bool(stored and stored.notification_email)
        diagnostic = selection.diagnostic_code
        if selection.live_delivery_ready:
            if stored is None:
                diagnostic = "SETTINGS_NOT_CONFIGURED"
            elif not email_enabled:
                diagnostic = "EMAIL_NOTIFICATIONS_DISABLED"
            elif not recipient_configured:
                diagnostic = "NOTIFICATION_EMAIL_MISSING"
            else:
                diagnostic = "READY_TO_SEND"
        return EmailConfigurationResponse(
            requested_mode=selection.requested_mode,
            active_adapter=selection.active_adapter,
            live_delivery_ready=selection.live_delivery_ready,
            email_notifications_enabled=email_enabled,
            notification_email_configured=recipient_configured,
            from_address=runtime.email_from if runtime.email_from else None,
            diagnostic_code=diagnostic,
            can_send_test=(
                selection.live_delivery_ready and email_enabled and recipient_configured
            ),
        )

    async def enqueue_test_email(self, runtime: Settings) -> EmailDeliveryResponse:
        """Queue a real provider test through the same durable outbox as job alerts."""

        configuration = await self.get_email_configuration(runtime)
        if not configuration.live_delivery_ready:
            raise AppError(
                "LIVE_EMAIL_NOT_CONFIGURED",
                "Configure live Resend delivery before sending a test email.",
                status_code=409,
                details={"diagnostic_code": configuration.diagnostic_code},
            )
        if not configuration.email_notifications_enabled:
            raise AppError(
                "EMAIL_NOTIFICATIONS_DISABLED",
                "Enable email alerts in Settings before sending a test email.",
                status_code=409,
            )
        if not configuration.notification_email_configured:
            raise AppError(
                "NOTIFICATION_EMAIL_MISSING",
                "Save a notification email before sending a test email.",
                status_code=409,
            )

        existing = await self.session.scalar(
            select(NotificationOutbox)
            .where(
                NotificationOutbox.channel == EMAIL_CHANNEL,
                NotificationOutbox.status.in_(("pending", "sending")),
                NotificationOutbox.payload["kind"].astext == "test_email",
            )
            .order_by(NotificationOutbox.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            raise AppError(
                "TEST_EMAIL_ALREADY_QUEUED",
                "A test email is already queued or sending.",
                status_code=409,
                details={"delivery_id": str(existing.id)},
            )

        now = self.clock()
        test_id = uuid4()
        payload = EmailTestPayload(
            test_id=test_id,
            title="Your JobScout AI email alerts are configured",
            body=(
                "This owner-requested test used the same transactional outbox and worker "
                "delivery path as a matching job alert."
            ),
            requested_at=now,
        )
        row = NotificationOutbox(
            channel=EMAIL_CHANNEL,
            event_group_key=f"test:{test_id}",
            payload=payload.model_dump(mode="json"),
            status="pending",
            next_attempt_at=now,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        logger.info("test_email_enqueued", outbox_id=str(row.id))
        return self._delivery_response(row)

    # --------------------------------------------------------------- delivery path

    async def claim_pending(self, limit: int = 20) -> list[UUID]:
        """Claim a bounded batch of due rows with row locks, then hand them to workers."""

        now = self.clock()
        statement = (
            select(NotificationOutbox)
            .where(
                NotificationOutbox.status.in_(("pending", "sending")),
                NotificationOutbox.next_attempt_at <= now,
            )
            .order_by(NotificationOutbox.next_attempt_at, NotificationOutbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list((await self.session.scalars(statement)).all())
        claimed: list[UUID] = []
        for row in rows:
            if row.attempt_count >= MAX_DELIVERY_ATTEMPTS:
                row.status = "failed"
                row.last_error_code = row.last_error_code or "DELIVERY_ATTEMPTS_EXHAUSTED"
                continue
            row.status = "sending"
            row.attempt_count += 1
            row.next_attempt_at = now + timedelta(seconds=CLAIM_LEASE_SECONDS)
            claimed.append(row.id)
        await self.session.commit()
        return claimed

    async def release_claim(self, outbox_id: UUID) -> None:
        row = await self.session.get(NotificationOutbox, outbox_id)
        if row is not None and row.status == "sending":
            row.status = "pending"
            row.attempt_count = max(row.attempt_count - 1, 0)
            row.next_attempt_at = self.clock()
            await self.session.commit()

    async def deliver(self, outbox_id: UUID) -> str:
        # Serialize duplicate broker deliveries for one row. The provider call is bounded by the
        # adapter timeout, and the lock prevents two workers from racing a sent state back to
        # pending after Resend reports a concurrent idempotent request.
        row = await self.session.scalar(
            select(NotificationOutbox).where(NotificationOutbox.id == outbox_id).with_for_update()
        )
        if row is None:
            await self.session.commit()
            return "missing"
        if row.status in {"sent", "failed", "cancelled"}:
            await self.session.commit()
            return row.status

        try:
            payload = parse_outbox_payload(row.payload)
            if row.channel == IN_APP_CHANNEL:
                if not isinstance(
                    payload,
                    (NotificationPayload, SourceDegradedPayload, ReviewReminderPayload),
                ):
                    raise PermanentEmailError("IN_APP_PAYLOAD_TYPE_INVALID")
                await self._deliver_in_app(row, payload)
            else:
                await self._deliver_email(row, payload)
        except ValidationError:
            row.status = "failed"
            row.last_error_code = "NOTIFICATION_PAYLOAD_INVALID"
        except TransientEmailError as exc:
            self._schedule_retry(row, exc.code)
        except PermanentEmailError as exc:
            row.status = "failed"
            row.last_error_code = exc.code
        except Exception:
            logger.exception(
                "notification_delivery_unexpected",
                outbox_id=str(outbox_id),
                channel=row.channel,
            )
            self._schedule_retry(row, "NOTIFICATION_DELIVERY_UNEXPECTED")
        await self.session.commit()
        logger.info(
            "notification_delivery_finished",
            outbox_id=str(outbox_id),
            channel=row.channel,
            status=row.status,
            attempt_count=row.attempt_count,
            error_code=row.last_error_code,
        )
        return row.status

    async def _deliver_in_app(
        self,
        row: NotificationOutbox,
        payload: NotificationPayload | SourceDegradedPayload | ReviewReminderPayload,
    ) -> None:
        await self.session.execute(
            pg_insert(InAppNotification)
            .values(
                outbox_id=row.id,
                title=payload.title,
                body=payload.body,
                action_url=payload.action_path,
            )
            .on_conflict_do_nothing(index_elements=[InAppNotification.outbox_id])
        )
        row.status = "sent"
        row.sent_at = self.clock()
        row.last_error_code = None

    async def _deliver_email(self, row: NotificationOutbox, payload: OutboxPayload) -> None:
        settings = await self.session.scalar(select(AppSettings).limit(1))
        if settings is None or not settings.email_notifications_enabled:
            row.status = "cancelled"
            row.last_error_code = "EMAIL_NOTIFICATIONS_DISABLED"
            return
        if not settings.notification_email:
            row.status = "cancelled"
            row.last_error_code = "NOTIFICATION_EMAIL_MISSING"
            return
        if self.email_sender is None:
            row.status = "cancelled"
            row.last_error_code = "EMAIL_ADAPTER_NOT_CONFIGURED"
            return

        if isinstance(payload, NotificationPayload):
            message = render_email(
                payload,
                recipient=settings.notification_email,
                app_base_url=self.app_base_url,
            )
        elif isinstance(payload, EmailTestPayload):
            message = render_test_email(
                payload,
                recipient=settings.notification_email,
                app_base_url=self.app_base_url,
            )
        else:
            raise PermanentEmailError("EMAIL_PAYLOAD_TYPE_INVALID")
        row.delivery_adapter = self.email_sender.adapter_name
        delivery = await self.email_sender.send(
            message,
            idempotency_key=f"jobscout-email:{row.id}",
        )
        row.status = "sent"
        row.sent_at = self.clock()
        row.provider_message_id = delivery.provider_message_id
        row.last_error_code = None

    def _schedule_retry(self, row: NotificationOutbox, code: str) -> None:
        row.last_error_code = code
        if row.attempt_count >= MAX_DELIVERY_ATTEMPTS:
            row.status = "failed"
            return
        index = min(max(row.attempt_count - 1, 0), len(RETRY_BACKOFF_SECONDS) - 1)
        row.status = "pending"
        row.next_attempt_at = self.clock() + timedelta(seconds=RETRY_BACKOFF_SECONDS[index])

    async def list_email_deliveries(
        self,
        *,
        limit: int = 20,
        cursor: UUID | None = None,
    ) -> EmailDeliveryListResponse:
        statement = (
            select(NotificationOutbox)
            .where(NotificationOutbox.channel == EMAIL_CHANNEL)
            .order_by(NotificationOutbox.created_at.desc(), NotificationOutbox.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            anchor = await self.session.get(NotificationOutbox, cursor)
            if anchor is None or anchor.channel != EMAIL_CHANNEL:
                raise AppError(
                    "EMAIL_DELIVERY_NOT_FOUND",
                    "Email delivery cursor not found.",
                    status_code=404,
                )
            statement = statement.where(
                tuple_(NotificationOutbox.created_at, NotificationOutbox.id)
                < (anchor.created_at, anchor.id)
            )
        rows = list((await self.session.scalars(statement)).all())
        return EmailDeliveryListResponse(
            items=[self._delivery_response(row) for row in rows[:limit]],
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )

    async def get_email_delivery(self, outbox_id: UUID) -> EmailDeliveryResponse:
        row = await self.session.get(NotificationOutbox, outbox_id)
        if row is None or row.channel != EMAIL_CHANNEL:
            raise AppError("EMAIL_DELIVERY_NOT_FOUND", "Email delivery not found.", status_code=404)
        return self._delivery_response(row)

    async def retry_email_delivery(self, outbox_id: UUID) -> EmailDeliveryResponse:
        row = await self.session.scalar(
            select(NotificationOutbox).where(NotificationOutbox.id == outbox_id).with_for_update()
        )
        if row is None or row.channel != EMAIL_CHANNEL:
            raise AppError("EMAIL_DELIVERY_NOT_FOUND", "Email delivery not found.", status_code=404)
        if row.status not in {"failed", "cancelled"}:
            raise AppError(
                "EMAIL_DELIVERY_NOT_RETRYABLE",
                "Only failed or cancelled email deliveries can be retried.",
                status_code=409,
            )
        row.status = "pending"
        row.attempt_count = 0
        row.next_attempt_at = self.clock()
        row.provider_message_id = None
        row.delivery_adapter = None
        row.last_error_code = None
        row.sent_at = None
        await self.session.commit()
        await self.session.refresh(row)
        logger.info("email_delivery_requeued", outbox_id=str(row.id))
        return self._delivery_response(row)

    @staticmethod
    def _delivery_response(row: NotificationOutbox) -> EmailDeliveryResponse:
        try:
            payload = parse_outbox_payload(row.payload)
            notification_type: Literal["job_alert", "test_email"] = (
                "test_email" if isinstance(payload, EmailTestPayload) else "job_alert"
            )
            title = payload.title
        except ValidationError:
            notification_type = "job_alert"
            title = "Invalid notification payload"
        return EmailDeliveryResponse(
            id=row.id,
            notification_type=notification_type,
            title=title,
            status=row.status,
            attempt_count=row.attempt_count,
            next_attempt_at=(row.next_attempt_at if row.status in {"pending", "sending"} else None),
            delivery_adapter=row.delivery_adapter,
            provider_message_id=row.provider_message_id,
            last_error_code=row.last_error_code,
            created_at=row.created_at,
            sent_at=row.sent_at,
            retryable=row.status in {"failed", "cancelled"},
        )

    # ------------------------------------------------------------------- read path

    async def list_notifications(
        self,
        *,
        limit: int = 50,
        cursor: UUID | None = None,
        unread_only: bool = False,
    ) -> NotificationListResponse:
        statement = (
            select(InAppNotification, NotificationOutbox.payload)
            .join(NotificationOutbox, NotificationOutbox.id == InAppNotification.outbox_id)
            .order_by(InAppNotification.created_at.desc(), InAppNotification.id.desc())
            .limit(limit + 1)
        )
        if unread_only:
            statement = statement.where(InAppNotification.read_at.is_(None))
        if cursor is not None:
            anchor = await self.session.get(InAppNotification, cursor)
            if anchor is None:
                raise AppError(
                    "NOTIFICATION_NOT_FOUND", "Notification cursor not found.", status_code=404
                )
            statement = statement.where(
                tuple_(InAppNotification.created_at, InAppNotification.id)
                < (anchor.created_at, anchor.id)
            )
        rows = list((await self.session.execute(statement)).all())
        unread_count = await self.session.scalar(
            select(func.count())
            .select_from(InAppNotification)
            .where(InAppNotification.read_at.is_(None))
        )
        return NotificationListResponse(
            items=[self._response(row[0], row[1]) for row in rows[:limit]],
            next_cursor=rows[limit - 1][0].id if len(rows) > limit else None,
            unread_count=int(unread_count or 0),
        )

    async def mark_read(self, notification_id: UUID) -> NotificationResponse:
        notification = await self.session.get(InAppNotification, notification_id)
        if notification is None:
            raise AppError("NOTIFICATION_NOT_FOUND", "Notification not found.", status_code=404)
        if notification.read_at is None:
            notification.read_at = self.clock()
            await self.session.commit()
        outbox = await self.session.get(NotificationOutbox, notification.outbox_id)
        return self._response(notification, outbox.payload if outbox else None)

    def _response(
        self, notification: InAppNotification, payload: dict[str, object] | None
    ) -> NotificationResponse:
        try:
            parsed = parse_outbox_payload(payload) if payload else None
        except ValidationError:
            parsed = None
        job_alert = parsed if isinstance(parsed, NotificationPayload) else None
        source_alert = parsed if isinstance(parsed, SourceDegradedPayload) else None
        reminder = parsed if isinstance(parsed, ReviewReminderPayload) else None
        company_id = (
            job_alert.company_id
            if job_alert
            else source_alert.company_id
            if source_alert
            else reminder.company_id
            if reminder
            else None
        )
        company_name = (
            job_alert.company_name
            if job_alert
            else source_alert.company_name
            if source_alert
            else reminder.company_name
            if reminder
            else None
        )
        return NotificationResponse(
            id=notification.id,
            outbox_id=notification.outbox_id,
            title=notification.title,
            body=notification.body,
            action_url=notification.action_url,
            read_at=notification.read_at,
            created_at=notification.created_at,
            company_id=company_id,
            company_name=company_name,
            jobs=job_alert.jobs if job_alert else [],
        )
