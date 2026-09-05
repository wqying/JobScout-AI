from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CareerSource, Company, SourcePollRun
from app.monitoring.api_schemas import (
    SourceActionResponse,
    SourcePollRunListResponse,
    SourcePollRunResponse,
)
from app.monitoring.scheduling import SourceScheduler


class PollDispatcher(Protocol):
    def enqueue(self, source_id: UUID, lease_owner: str) -> None: ...


class MonitoringOperationsService:
    def __init__(self, session: AsyncSession, dispatcher: PollDispatcher) -> None:
        self.session = session
        self.dispatcher = dispatcher

    async def list_runs(
        self, *, limit: int = 50, cursor: UUID | None = None
    ) -> SourcePollRunListResponse:
        statement = (
            select(SourcePollRun, CareerSource.provider, Company.canonical_name)
            .join(CareerSource, CareerSource.id == SourcePollRun.career_source_id)
            .join(Company, Company.id == CareerSource.company_id)
            .order_by(SourcePollRun.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(SourcePollRun.id < cursor)
        rows = list((await self.session.execute(statement)).all())
        return SourcePollRunListResponse(
            items=[
                SourcePollRunResponse(
                    id=row[0].id,
                    career_source_id=row[0].career_source_id,
                    company_name=row[2],
                    provider=row[1],
                    status=row[0].status,
                    http_status=row[0].http_status,
                    jobs_received=row[0].jobs_received,
                    jobs_created=row[0].jobs_created,
                    jobs_updated=row[0].jobs_updated,
                    jobs_closed=row[0].jobs_closed,
                    started_at=row[0].started_at,
                    finished_at=row[0].finished_at,
                    error_code=row[0].error_code,
                )
                for row in rows[:limit]
            ],
            next_cursor=rows[limit - 1][0].id if len(rows) > limit else None,
        )

    async def poll_now(self, source_id: UUID) -> SourceActionResponse:
        claimed_id, owner = await SourceScheduler(self.session).claim_one(source_id)
        try:
            self.dispatcher.enqueue(claimed_id, owner)
        except Exception:
            source = await self.session.get(CareerSource, source_id)
            if source is not None and source.lease_owner == owner:
                source.lease_owner = None
                source.lease_expires_at = None
                await self.session.commit()
            raise
        return SourceActionResponse(
            source_id=source_id,
            status="queued",
            message="The source poll was queued.",
        )

    async def pause(self, source_id: UUID) -> SourceActionResponse:
        await SourceScheduler(self.session).pause(source_id)
        return SourceActionResponse(
            source_id=source_id,
            status="paused",
            message="The career source is paused.",
        )
