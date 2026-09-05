from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ImportResponse(BaseModel):
    id: UUID
    dataset_type: str
    fiscal_year: int
    source_url: str
    source_sha256: str
    record_layout_version: str
    status: str
    rows_read: int
    rows_accepted: int
    imported_at: datetime | None
    error_code: str | None


class ImportListResponse(BaseModel):
    items: list[ImportResponse]
