from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import database_session
from app.db.models import ImmigrationDatasetImport
from app.immigration.schemas import ImportListResponse, ImportResponse
from app.monitoring.api_schemas import SourceActionResponse, SourcePollRunListResponse
from app.monitoring.operations import MonitoringOperationsService
from app.workers.dispatch import CeleryPollDispatcher

router = APIRouter(prefix="/ops", tags=["operations"])
Session = Annotated[AsyncSession, Depends(database_session)]


@router.get("/imports", response_model=ImportListResponse)
async def list_imports(session: Session) -> ImportListResponse:
    imports = list(
        (
            await session.scalars(
                select(ImmigrationDatasetImport).order_by(
                    ImmigrationDatasetImport.fiscal_year.desc(),
                    ImmigrationDatasetImport.imported_at.desc(),
                )
            )
        ).all()
    )
    return ImportListResponse(
        items=[
            ImportResponse(
                id=item.id,
                dataset_type=item.dataset_type,
                fiscal_year=item.fiscal_year,
                source_url=item.source_url,
                source_sha256=item.source_sha256,
                record_layout_version=item.record_layout_version,
                status=item.status,
                rows_read=item.rows_read,
                rows_accepted=item.rows_accepted,
                imported_at=item.imported_at,
                error_code=item.error_code,
            )
            for item in imports
        ]
    )


@router.get("/source-runs", response_model=SourcePollRunListResponse)
async def list_source_runs(
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: UUID | None = None,
) -> SourcePollRunListResponse:
    return await MonitoringOperationsService(session, CeleryPollDispatcher()).list_runs(
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/sources/{source_id}/poll",
    response_model=SourceActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def poll_source(source_id: UUID, session: Session) -> SourceActionResponse:
    return await MonitoringOperationsService(session, CeleryPollDispatcher()).poll_now(source_id)


@router.post("/sources/{source_id}/pause", response_model=SourceActionResponse)
async def pause_source(source_id: UUID, session: Session) -> SourceActionResponse:
    return await MonitoringOperationsService(session, CeleryPollDispatcher()).pause(source_id)
