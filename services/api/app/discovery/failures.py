from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import InvalidStructuredOutput
from app.api.errors import AppError
from app.db.models import AiRun, DiscoveryRun, IndustryQuery


class DiscoveryFailureService:
    """Persist a terminal discovery failure without owning worker orchestration."""

    def __init__(
        self,
        session: AsyncSession,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(UTC))

    async def mark_failed(self, run_id: UUID, error_code: str) -> bool:
        run = await self.session.scalar(
            select(DiscoveryRun).where(DiscoveryRun.id == run_id).with_for_update()
        )
        if run is None or run.status == "succeeded":
            return False

        changed = run.status != "failed"
        finished = run.completed_at or self.clock()
        if changed:
            run.status = "failed"
            run.error_code = error_code
            run.completed_at = finished

        industry_query = (
            await self.session.get(IndustryQuery, run.industry_query_id)
            if run.industry_query_id is not None
            else None
        )
        if industry_query is not None and industry_query.status != "succeeded":
            industry_query.status = "failed"
            industry_query.completed_at = finished

        active_traces = (
            await self.session.scalars(
                select(AiRun).where(
                    AiRun.redacted_input["discovery_run_id"].astext == str(run_id),
                    AiRun.status.in_(("queued", "running")),
                )
            )
        ).all()
        for trace in active_traces:
            trace.status = "failed"
            trace.error_code = run.error_code or error_code
            trace.finished_at = finished

        await self.session.commit()
        return changed


def discovery_error_code(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.code
    if isinstance(exc, InvalidStructuredOutput):
        return "AI_STRUCTURED_OUTPUT_INVALID"
    if isinstance(exc, SQLAlchemyError):
        return "WORKER_DATABASE_ERROR"
    if isinstance(exc, ValueError):
        return "AI_OUTPUT_INVALID"
    message = str(exc)
    if "OPENAI_API_KEY" in message:
        return "OPENAI_NOT_CONFIGURED"
    if "research and structured model IDs" in message:
        return "OPENAI_MODELS_NOT_CONFIGURED"
    if "attached to a different loop" in message or "Event loop is closed" in message:
        return "WORKER_DATABASE_ERROR"
    if isinstance(exc, RuntimeError) and "OpenAI" in message:
        return "OPENAI_REQUEST_FAILED"
    if isinstance(exc, RuntimeError) and "No source-verified" in message:
        return "NO_VERIFIED_RESULTS"
    return "DISCOVERY_FAILED"
