from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.monitoring.adapters.common import JsonAdapterBase, optional_text
from app.monitoring.schemas import CareerSourceConfig, FetchResult, NormalizedJob, RawJob


class LeverAdapter(JsonAdapterBase):
    async def fetch_jobs(self, source: CareerSourceConfig) -> FetchResult:
        site = source.provider_config.get("site")
        if not isinstance(site, str) or not site:
            raise ValueError("LEVER_SITE_REQUIRED")
        region = source.provider_config.get("region", "global")
        if region not in {"global", "eu"}:
            raise ValueError("LEVER_REGION_INVALID")
        api_host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
        url = f"https://{api_host}/v0/postings/{quote(site, safe='')}?mode=json"
        return await self._fetch_json(url, source)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        row: dict[str, Any] = raw.raw_payload
        categories = row.get("categories")
        category = categories if isinstance(categories, dict) else {}
        apply_url = row.get("applyUrl") or row.get("hostedUrl")
        if not isinstance(apply_url, str):
            raise ValueError("LEVER_APPLY_URL_REQUIRED")
        return NormalizedJob(
            external_job_id=raw.external_job_id,
            title=str(row.get("text") or ""),
            location_text=optional_text(category.get("location")),
            department=optional_text(category.get("department") or category.get("team")),
            employment_type=optional_text(category.get("commitment")),
            description_text=str(row.get("descriptionPlain") or "").strip(),
            apply_url=apply_url,
            # Lever's public contract does not document a stable posted timestamp.
            source_posted_at=None,
        )
