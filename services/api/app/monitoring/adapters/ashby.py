from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.monitoring.adapters.common import JsonAdapterBase, optional_text, parse_datetime
from app.monitoring.schemas import CareerSourceConfig, FetchResult, NormalizedJob, RawJob


class AshbyAdapter(JsonAdapterBase):
    async def fetch_jobs(self, source: CareerSourceConfig) -> FetchResult:
        organization = source.provider_config.get("organization")
        if not isinstance(organization, str) or not organization:
            raise ValueError("ASHBY_ORGANIZATION_REQUIRED")
        url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(organization)}"
        return await self._fetch_json(url, source, "jobs")

    def normalize(self, raw: RawJob) -> NormalizedJob:
        row: dict[str, Any] = raw.raw_payload
        apply_url = row.get("applyUrl") or row.get("jobUrl")
        if not isinstance(apply_url, str):
            raise ValueError("ASHBY_APPLY_URL_REQUIRED")
        # The Ashby feed has no separate posting id, so the stable job URL is its external id.
        external_id = raw.external_job_id or optional_text(row.get("jobUrl"))
        return NormalizedJob(
            external_job_id=external_id,
            title=str(row.get("title") or ""),
            location_text=optional_text(row.get("location")),
            department=optional_text(row.get("department") or row.get("team")),
            employment_type=optional_text(row.get("employmentType")),
            description_text=str(row.get("descriptionPlain") or "").strip(),
            apply_url=apply_url,
            source_posted_at=parse_datetime(row.get("publishedAt")),
        )
