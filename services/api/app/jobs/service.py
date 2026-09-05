from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.companies.schemas import JobListResponse
from app.companies.service import job_response
from app.db.models import Company, Job, SavedCompany


class JobQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_saved_jobs(
        self,
        *,
        limit: int = 100,
        cursor: UUID | None = None,
        status: str | None = None,
    ) -> JobListResponse:
        statement = (
            select(Job, Company.canonical_name)
            .join(SavedCompany, SavedCompany.company_id == Job.company_id)
            .join(Company, Company.id == Job.company_id)
            .order_by(Job.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(Job.id < cursor)
        if status is not None:
            statement = statement.where(Job.status == status)
        rows = list((await self.session.execute(statement)).all())
        return JobListResponse(
            items=[job_response(row[0], row[1]) for row in rows[:limit]],
            next_cursor=rows[limit - 1][0].id if len(rows) > limit else None,
        )
