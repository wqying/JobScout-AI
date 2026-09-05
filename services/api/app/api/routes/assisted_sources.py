from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import database_session
from app.api.errors import AppError
from app.assisted_sources.eml import MAX_EML_BYTES
from app.assisted_sources.schemas import (
    EmailAlertImportListResponse,
    EmailAlertImportResponse,
    ReviewReminderCreateRequest,
    ReviewReminderListResponse,
    ReviewReminderRescheduleRequest,
    ReviewReminderResponse,
    SourceRepairPreviewRequest,
    SourceRepairResponse,
)
from app.assisted_sources.service import (
    EmailAlertImportService,
    ReviewReminderService,
    SourceRepairService,
)

router = APIRouter(tags=["assisted sources"])
Session = Annotated[AsyncSession, Depends(database_session)]


@router.post(
    "/companies/{company_id}/source-repairs/preview",
    response_model=SourceRepairResponse,
    status_code=status.HTTP_201_CREATED,
)
async def preview_source_repair(
    company_id: UUID,
    payload: SourceRepairPreviewRequest,
    session: Session,
) -> SourceRepairResponse:
    return await SourceRepairService(session).preview(
        company_id=company_id,
        source_id=payload.source_id,
        careers_url=payload.careers_url,
        notify_current_jobs=payload.notify_current_jobs,
    )


@router.post(
    "/source-repairs/{repair_id}/confirm",
    response_model=SourceRepairResponse,
)
async def confirm_source_repair(repair_id: UUID, session: Session) -> SourceRepairResponse:
    return await SourceRepairService(session).confirm(repair_id)


@router.post(
    "/saved-companies/{saved_company_id}/review-reminders",
    response_model=ReviewReminderResponse,
)
async def schedule_review_reminder(
    saved_company_id: UUID,
    payload: ReviewReminderCreateRequest,
    session: Session,
) -> ReviewReminderResponse:
    return await ReviewReminderService(session).schedule(
        saved_company_id=saved_company_id,
        career_source_id=payload.career_source_id,
        due_at=payload.due_at,
    )


@router.get("/review-reminders", response_model=ReviewReminderListResponse)
async def list_review_reminders(
    session: Session,
    reminder_status: Annotated[
        Literal["scheduled", "checked", "dismissed"] | None,
        Query(alias="status"),
    ] = None,
) -> ReviewReminderListResponse:
    return await ReviewReminderService(session).list(status=reminder_status)


@router.get("/review-reminders/due", response_model=ReviewReminderListResponse)
async def list_due_review_reminders(
    session: Session,
    as_of: datetime | None = None,
) -> ReviewReminderListResponse:
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise AppError(
            "REMINDER_TIMEZONE_REQUIRED",
            "as_of must include a timezone offset.",
        )
    return await ReviewReminderService(session).due(as_of=as_of)


@router.post(
    "/review-reminders/{reminder_id}/checked",
    response_model=ReviewReminderResponse,
)
async def mark_review_reminder_checked(
    reminder_id: UUID, session: Session
) -> ReviewReminderResponse:
    return await ReviewReminderService(session).checked(reminder_id)


@router.post(
    "/review-reminders/{reminder_id}/reschedule",
    response_model=ReviewReminderResponse,
)
async def reschedule_review_reminder(
    reminder_id: UUID,
    payload: ReviewReminderRescheduleRequest,
    session: Session,
) -> ReviewReminderResponse:
    return await ReviewReminderService(session).reschedule(reminder_id, payload.due_at)


@router.post(
    "/review-reminders/{reminder_id}/dismiss",
    response_model=ReviewReminderResponse,
)
async def dismiss_review_reminder(reminder_id: UUID, session: Session) -> ReviewReminderResponse:
    return await ReviewReminderService(session).dismiss(reminder_id)


@router.post(
    "/companies/{company_id}/email-alert-imports",
    response_model=EmailAlertImportResponse,
)
async def import_email_alert(
    company_id: UUID,
    request: Request,
    session: Session,
    filename: Annotated[str | None, Header(alias="X-JobScout-Filename")] = None,
) -> EmailAlertImportResponse:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().casefold()
    if content_type != "message/rfc822":
        raise AppError(
            "EMAIL_CONTENT_TYPE_REQUIRED",
            "Upload the raw .eml bytes with Content-Type: message/rfc822.",
            status_code=415,
        )
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_EML_BYTES:
                raise AppError(
                    "EMAIL_FILE_TOO_LARGE",
                    f"The .eml file must be no larger than {MAX_EML_BYTES // 1024} KiB.",
                    status_code=413,
                )
        except ValueError as exc:
            raise AppError(
                "EMAIL_CONTENT_LENGTH_INVALID", "Invalid Content-Length header."
            ) from exc

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_EML_BYTES:
            raise AppError(
                "EMAIL_FILE_TOO_LARGE",
                f"The .eml file must be no larger than {MAX_EML_BYTES // 1024} KiB.",
                status_code=413,
            )
        chunks.append(chunk)
    return await EmailAlertImportService(session).import_message(
        company_id=company_id,
        raw=b"".join(chunks),
        filename=filename,
    )


@router.get(
    "/companies/{company_id}/email-alert-imports",
    response_model=EmailAlertImportListResponse,
)
async def list_email_alert_imports(
    company_id: UUID,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> EmailAlertImportListResponse:
    return await EmailAlertImportService(session).list_for_company(company_id, limit=limit)
