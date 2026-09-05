from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import database_session
from app.core.config import Settings, get_settings
from app.notifications.schemas import (
    EmailConfigurationResponse,
    EmailDeliveryListResponse,
    EmailDeliveryResponse,
    EmailTestRequest,
    NotificationListResponse,
    NotificationResponse,
)
from app.notifications.service import NotificationService

router = APIRouter(tags=["notifications"])
Session = Annotated[AsyncSession, Depends(database_session)]
RuntimeSettings = Annotated[Settings, Depends(get_settings)]


@router.get("/email/configuration", response_model=EmailConfigurationResponse)
async def get_email_configuration(
    session: Session, runtime: RuntimeSettings
) -> EmailConfigurationResponse:
    return await NotificationService(session).get_email_configuration(runtime)


@router.post(
    "/email/test",
    response_model=EmailDeliveryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_test_email(
    payload: EmailTestRequest,
    session: Session,
    runtime: RuntimeSettings,
) -> EmailDeliveryResponse:
    del payload
    return await NotificationService(session).enqueue_test_email(runtime)


@router.get("/email/deliveries", response_model=EmailDeliveryListResponse)
async def list_email_deliveries(
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: UUID | None = None,
) -> EmailDeliveryListResponse:
    return await NotificationService(session).list_email_deliveries(
        limit=limit,
        cursor=cursor,
    )


@router.get("/email/deliveries/{outbox_id}", response_model=EmailDeliveryResponse)
async def get_email_delivery(outbox_id: UUID, session: Session) -> EmailDeliveryResponse:
    return await NotificationService(session).get_email_delivery(outbox_id)


@router.post(
    "/email/deliveries/{outbox_id}/retry",
    response_model=EmailDeliveryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_email_delivery(outbox_id: UUID, session: Session) -> EmailDeliveryResponse:
    return await NotificationService(session).retry_email_delivery(outbox_id)


@router.get("/notifications", response_model=NotificationListResponse)
async def list_notifications(
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: UUID | None = None,
    unread_only: bool = False,
) -> NotificationListResponse:
    return await NotificationService(session).list_notifications(
        limit=limit,
        cursor=cursor,
        unread_only=unread_only,
    )


@router.post("/notifications/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(notification_id: UUID, session: Session) -> NotificationResponse:
    return await NotificationService(session).mark_read(notification_id)
