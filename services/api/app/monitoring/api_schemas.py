from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SourcePollRunResponse(BaseModel):
    id: UUID
    career_source_id: UUID
    company_name: str
    provider: str
    status: str
    http_status: int | None
    jobs_received: int
    jobs_created: int
    jobs_updated: int
    jobs_closed: int
    started_at: datetime
    finished_at: datetime | None
    error_code: str | None


class SourcePollRunListResponse(BaseModel):
    items: list[SourcePollRunResponse] = Field(default_factory=list)
    next_cursor: UUID | None = None


class SourceActionResponse(BaseModel):
    source_id: UUID
    status: str
    message: str
