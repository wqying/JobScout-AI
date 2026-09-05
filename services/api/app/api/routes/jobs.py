from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import database_session
from app.companies.schemas import JobListResponse
from app.jobs.service import JobQueryService

router = APIRouter(tags=["jobs"])
Session = Annotated[AsyncSession, Depends(database_session)]


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    cursor: UUID | None = None,
    status: Literal["active", "closed"] | None = None,
) -> JobListResponse:
    return await JobQueryService(session).list_saved_jobs(
        limit=limit,
        cursor=cursor,
        status=status,
    )
