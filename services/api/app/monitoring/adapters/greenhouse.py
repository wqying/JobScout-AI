from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.monitoring.adapters.common import (
    JsonAdapterBase,
    optional_text,
    parse_datetime,
    strip_html,
)
from app.monitoring.schemas import CareerSourceConfig, FetchResult, NormalizedJob, RawJob


class GreenhouseAdapter(JsonAdapterBase):
    async def fetch_jobs(self, source: CareerSourceConfig) -> FetchResult:
        token = source.provider_config.get("board_token")
        if not isinstance(token, str) or not token:
            raise ValueError("GREENHOUSE_BOARD_TOKEN_REQUIRED")
        url = f"https://boards-api.greenhouse.io/v1/boards/{quote(token)}/jobs?content=true"
        return await self._fetch_json(url, source, "jobs")

    def normalize(self, raw: RawJob) -> NormalizedJob:
        row: dict[str, Any] = raw.raw_payload
        location = row.get("location")
        departments = row.get("departments")
        department = None
        if isinstance(departments, list):
            names = [item.get("name") for item in departments if isinstance(item, dict)]
            department = optional_text(", ".join(str(name) for name in names if name))
        apply_url = row.get("absolute_url")
        if not isinstance(apply_url, str):
            raise ValueError("GREENHOUSE_APPLY_URL_REQUIRED")
        return NormalizedJob(
            external_job_id=raw.external_job_id,
            title=str(row.get("title") or ""),
            location_text=(
                optional_text(location.get("name")) if isinstance(location, dict) else None
            ),
            department=department,
            employment_type=None,
            description_text=strip_html(row.get("content")),
            apply_url=apply_url,
            source_posted_at=parse_datetime(row.get("first_published")),
        )
