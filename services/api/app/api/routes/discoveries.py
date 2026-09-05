from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import database_session, local_day_window
from app.core.config import Settings, get_settings
from app.discovery.local_day import LocalDayWindow
from app.discovery.schemas import (
    DiscoveryCreate,
    DiscoveryListResponse,
    DiscoveryResearchMoreRequest,
    DiscoveryResultSelection,
    DiscoveryResultsResponse,
    DiscoveryRunResponse,
    DiscoverySaveResponse,
)
from app.discovery.service import DiscoveryService
from app.workers.dispatch import CeleryDiscoveryDispatcher

router = APIRouter(tags=["discoveries"])
Session = Annotated[AsyncSession, Depends(database_session)]
ConfiguredSettings = Annotated[Settings, Depends(get_settings)]
BrowserLocalDay = Annotated[LocalDayWindow, Depends(local_day_window)]


@router.post(
    "/discoveries",
    response_model=DiscoveryRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_discovery(
    payload: DiscoveryCreate,
    session: Session,
    settings: ConfiguredSettings,
    local_day: BrowserLocalDay,
) -> DiscoveryRunResponse:
    return await DiscoveryService(
        session,
        settings,
        dispatcher=CeleryDiscoveryDispatcher(),
    ).create(payload, local_day)


@router.get("/discoveries", response_model=DiscoveryListResponse)
async def list_discoveries(
    session: Session,
    settings: ConfiguredSettings,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: UUID | None = None,
) -> DiscoveryListResponse:
    return await DiscoveryService(session, settings).list(limit, cursor)


@router.get("/discoveries/daily-history", response_model=DiscoveryListResponse)
async def list_daily_discovery_history(
    session: Session,
    settings: ConfiguredSettings,
    local_day: BrowserLocalDay,
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
    cursor: UUID | None = None,
) -> DiscoveryListResponse:
    return await DiscoveryService(session, settings).daily_history(
        local_day,
        limit=limit,
        cursor=cursor,
    )


@router.get("/discoveries/{run_id}", response_model=DiscoveryRunResponse)
async def get_discovery(
    run_id: UUID,
    session: Session,
    settings: ConfiguredSettings,
) -> DiscoveryRunResponse:
    return await DiscoveryService(session, settings).get(run_id)


@router.get("/discoveries/{run_id}/results", response_model=DiscoveryResultsResponse)
async def get_discovery_results(
    run_id: UUID,
    session: Session,
    settings: ConfiguredSettings,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 20,
) -> DiscoveryResultsResponse:
    return await DiscoveryService(session, settings).results(run_id, offset=offset, limit=limit)


@router.post(
    "/discoveries/{run_id}/research-more",
    response_model=DiscoveryRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def research_more_companies(
    run_id: UUID,
    payload: DiscoveryResearchMoreRequest,
    session: Session,
    settings: ConfiguredSettings,
    local_day: BrowserLocalDay,
) -> DiscoveryRunResponse:
    return await DiscoveryService(
        session,
        settings,
        dispatcher=CeleryDiscoveryDispatcher(),
    ).research_more(run_id, payload, local_day)


@router.post(
    "/discoveries/{run_id}/results/{result_id}/hide",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def hide_discovery_result(
    run_id: UUID,
    result_id: UUID,
    session: Session,
    settings: ConfiguredSettings,
) -> Response:
    await DiscoveryService(session, settings).hide(run_id, result_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/discoveries/{run_id}/results/{result_id}/show",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def show_discovery_result(
    run_id: UUID,
    result_id: UUID,
    session: Session,
    settings: ConfiguredSettings,
) -> Response:
    await DiscoveryService(session, settings).show(run_id, result_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/discoveries/{run_id}/save-selected",
    response_model=DiscoverySaveResponse,
)
async def save_selected_discovery_results(
    run_id: UUID,
    payload: DiscoveryResultSelection,
    session: Session,
    settings: ConfiguredSettings,
) -> DiscoverySaveResponse:
    return await DiscoveryService(session, settings).save_selected(run_id, payload.result_ids)


@router.post(
    "/discoveries/{run_id}/save-all-monitorable",
    response_model=DiscoverySaveResponse,
)
async def save_all_monitorable_discovery_results(
    run_id: UUID,
    session: Session,
    settings: ConfiguredSettings,
) -> DiscoverySaveResponse:
    return await DiscoveryService(session, settings).save_all_monitorable(run_id)
