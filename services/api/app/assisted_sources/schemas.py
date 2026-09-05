from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class SourceRepairPreviewRequest(BaseModel):
    source_id: UUID
    careers_url: str = Field(min_length=1, max_length=2048)
    notify_current_jobs: bool = False

    @field_validator("careers_url")
    @classmethod
    def clean_url(cls, value: str) -> str:
        return value.strip()


RepairAction = Literal["update_in_place", "create_replacement", "reuse_existing", "reactivate"]


class SourceRepairResponse(BaseModel):
    id: UUID
    company_id: UUID
    replacing_source_id: UUID
    replacement_source_id: UUID | None
    normalized_url: str
    provider: str
    canonical_source_key: str
    monitoring_mode: Literal["automatic"] = "automatic"
    completeness: Literal["full"] = "full"
    action: RepairAction
    notify_current_jobs: bool
    status: Literal["previewed", "confirmed", "expired", "cancelled"]
    expires_at: datetime
    confirmed_at: datetime | None


class ReviewReminderCreateRequest(BaseModel):
    career_source_id: UUID | None = None
    due_at: datetime

    @field_validator("due_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("due_at must include a timezone offset")
        return value


class ReviewReminderRescheduleRequest(BaseModel):
    due_at: datetime

    @field_validator("due_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("due_at must include a timezone offset")
        return value


class ReviewReminderResponse(BaseModel):
    id: UUID
    saved_company_id: UUID
    company_id: UUID
    company_name: str
    career_source_id: UUID | None
    careers_url: str | None
    due_at: datetime
    schedule_version: int
    status: Literal["scheduled", "checked", "dismissed"]
    notified_at: datetime | None
    checked_at: datetime | None
    dismissed_at: datetime | None


class ReviewReminderListResponse(BaseModel):
    items: list[ReviewReminderResponse] = Field(default_factory=list)


class EmailAlertImportResponse(BaseModel):
    id: UUID
    duplicate: bool
    status: Literal["imported", "no_jobs"]
    company_id: UUID
    source_id: UUID
    poll_run_id: UUID | None
    filename: str | None
    subject: str | None
    sender: str | None
    message_id: str | None
    content_sha256: str
    completeness: Literal["partial"] = "partial"
    links_found: int
    jobs_created: int
    jobs_updated: int
    created_at: datetime


class EmailAlertImportListResponse(BaseModel):
    items: list[EmailAlertImportResponse] = Field(default_factory=list)
