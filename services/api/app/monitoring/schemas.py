from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, HttpUrl


class CareerSourceConfig(BaseModel):
    careers_url: HttpUrl
    provider_config: dict[str, Any] = Field(default_factory=dict)
    etag: str | None = None
    last_modified: str | None = None


class RawJob(BaseModel):
    external_job_id: str | None
    raw_payload: dict[str, Any]


class FetchResult(BaseModel):
    completeness: Literal["full", "partial"]
    not_modified: bool = False
    jobs: list[RawJob] = Field(default_factory=list)
    response_etag: str | None = None
    response_last_modified: str | None = None
    http_status: int | None = None


class NormalizedJob(BaseModel):
    external_job_id: str | None
    title: str = Field(min_length=1, max_length=500)
    location_text: str | None = Field(default=None, max_length=1000)
    department: str | None = Field(default=None, max_length=500)
    employment_type: str | None = Field(default=None, max_length=200)
    description_text: str = ""
    apply_url: HttpUrl
    source_posted_at: datetime | None = None


class CareerSourceAdapter(Protocol):
    async def fetch_jobs(self, source: CareerSourceConfig) -> FetchResult: ...

    def normalize(self, raw: RawJob) -> NormalizedJob: ...
