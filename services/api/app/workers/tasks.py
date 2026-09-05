import asyncio
from uuid import UUID

import structlog
from redis.asyncio import Redis

from app.ai.client import OpenAIResponsesClient
from app.ai.workflows.company_discovery import CompanyDiscoveryWorkflow
from app.assisted_sources.service import ReviewReminderService
from app.core.config import get_settings
from app.db.session import get_worker_session_factory
from app.discovery.failures import DiscoveryFailureService, discovery_error_code
from app.monitoring.http import RedisDomainRateLimiter, SafeHttpClient
from app.monitoring.polling import PollingService
from app.monitoring.scheduling import SourceScheduler
from app.notifications.email import build_email_sender
from app.notifications.service import NotificationService
from app.workers.celery_app import celery_app


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.heartbeat", ignore_result=True
)
def heartbeat() -> None:
    """Prove Beat-to-worker delivery without creating domain side effects."""

    structlog.get_logger("jobscout.worker").info("foundation_heartbeat")


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.run_company_discovery",
    ignore_result=True,
    acks_late=True,
)
def run_company_discovery(run_id: str) -> None:
    """Thin task wrapper; the typed workflow owns all domain behavior."""

    asyncio.run(_run_company_discovery_guarded(UUID(run_id)))


async def _run_company_discovery_guarded(run_id: UUID) -> None:
    try:
        await _run_company_discovery(run_id)
    except Exception as exc:
        try:
            await _record_discovery_failure(run_id, exc)
        except Exception:
            structlog.get_logger("jobscout.worker").exception(
                "discovery_failure_recording_failed",
                discovery_run_id=str(run_id),
                original_error_code=discovery_error_code(exc),
            )
        raise


async def _record_discovery_failure(run_id: UUID, exc: Exception) -> None:
    async with get_worker_session_factory()() as session:
        await DiscoveryFailureService(session).mark_failed(run_id, discovery_error_code(exc))


async def _run_company_discovery(run_id: UUID) -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for company discovery")
    if not settings.openai_research_model or not settings.openai_structured_model:
        raise RuntimeError("OpenAI research and structured model IDs are required")
    # Careers-page resolution shares the polling fetcher, so SSRF validation, robots policy, the
    # response bound, and the per-domain rate limit are the same on both paths.
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with (
            OpenAIResponsesClient(
                api_key=settings.openai_api_key,
                research_model=settings.openai_research_model,
                structured_model=settings.openai_structured_model,
                base_url=settings.openai_base_url,
                research_max_output_tokens=settings.openai_research_max_output_tokens,
                structured_max_output_tokens=settings.openai_structured_max_output_tokens,
                max_web_search_calls=settings.openai_max_web_search_calls,
            ) as client,
            SafeHttpClient(rate_limiter=RedisDomainRateLimiter(redis_client)) as http,
            get_worker_session_factory()() as session,
        ):
            await CompanyDiscoveryWorkflow(session, client, settings, http).execute(run_id)
    finally:
        await redis_client.aclose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.enqueue_due_sources",
    ignore_result=True,
)
def enqueue_due_sources() -> None:
    """Claim due sources in PostgreSQL before placing their poll tasks on Redis."""

    asyncio.run(_enqueue_due_sources())


async def _enqueue_due_sources() -> None:
    async with get_worker_session_factory()() as session:
        claims = await SourceScheduler(session).claim_due()
    for source_id, lease_owner in claims:
        try:
            poll_career_source.delay(str(source_id), lease_owner)
        except Exception:
            async with get_worker_session_factory()() as session:
                await SourceScheduler(session).release_claim(source_id, lease_owner)
            structlog.get_logger("jobscout.scheduler").exception(
                "source_poll_enqueue_failed", source_id=str(source_id)
            )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.poll_career_source",
    ignore_result=True,
    acks_late=True,
)
def poll_career_source(source_id: str, lease_owner: str) -> None:
    """Thin task wrapper around deterministic polling and lifecycle services."""

    asyncio.run(_poll_career_source(UUID(source_id), lease_owner))


async def _poll_career_source(source_id: UUID, lease_owner: str) -> None:
    settings = get_settings()
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with (
            SafeHttpClient(rate_limiter=RedisDomainRateLimiter(redis_client)) as http,
            get_worker_session_factory()() as session,
        ):
            await PollingService(session, http).poll_source(source_id, lease_owner)
    finally:
        await redis_client.aclose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.dispatch_notifications",
    ignore_result=True,
)
def dispatch_notifications() -> None:
    """Claim due outbox rows in PostgreSQL before placing their delivery tasks on Redis."""

    asyncio.run(_dispatch_notifications())


async def _dispatch_notifications() -> None:
    async with get_worker_session_factory()() as session:
        claimed = await NotificationService(session).claim_pending()
    for outbox_id in claimed:
        try:
            deliver_notification.delay(str(outbox_id))
        except Exception:
            async with get_worker_session_factory()() as session:
                await NotificationService(session).release_claim(outbox_id)
            structlog.get_logger("jobscout.notifications").exception(
                "notification_enqueue_failed", outbox_id=str(outbox_id)
            )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.enqueue_due_review_reminders",
    ignore_result=True,
)
def enqueue_due_review_reminders() -> None:
    """Create durable in-app notifications for newly due review reminders."""

    asyncio.run(_enqueue_due_review_reminders())


async def _enqueue_due_review_reminders() -> None:
    async with get_worker_session_factory()() as session:
        await ReviewReminderService(session).enqueue_due_notifications()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.deliver_notification",
    ignore_result=True,
    acks_late=True,
)
def deliver_notification(outbox_id: str) -> None:
    """Thin task wrapper; the outbox service owns idempotency, retries, and failure states."""

    asyncio.run(_deliver_notification(UUID(outbox_id)))


async def _deliver_notification(outbox_id: UUID) -> None:
    settings = get_settings()
    sender = build_email_sender(settings)
    try:
        async with get_worker_session_factory()() as session:
            await NotificationService(
                session,
                email_sender=sender,
                app_base_url=settings.app_base_url,
            ).deliver(outbox_id)
    finally:
        await sender.aclose()
