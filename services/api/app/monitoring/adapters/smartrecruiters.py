from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from app.monitoring.adapters.common import optional_text, parse_datetime, strip_html
from app.monitoring.http import HttpFetcher, conditional_headers
from app.monitoring.schemas import CareerSourceConfig, FetchResult, NormalizedJob, RawJob

_PAGE_LIMIT = 100


class SmartRecruitersAdapter:
    """Adapter for SmartRecruiters' documented public Posting API."""

    def __init__(self, http: HttpFetcher) -> None:
        self.http = http

    async def fetch_jobs(self, source: CareerSourceConfig) -> FetchResult:
        company_identifier = source.provider_config.get("company_identifier")
        if not isinstance(company_identifier, str) or not company_identifier:
            raise ValueError("SMARTRECRUITERS_COMPANY_IDENTIFIER_REQUIRED")

        encoded_company = quote(company_identifier, safe="")
        base_url = f"https://api.smartrecruiters.com/v1/companies/{encoded_company}/postings"
        summaries: list[dict[str, Any]] = []
        offset = 0
        first_etag: str | None = None
        first_last_modified: str | None = None
        first_http_status: int | None = None

        while True:
            query = urlencode(
                {
                    "destination": "PUBLIC",
                    "limit": _PAGE_LIMIT,
                    "offset": offset,
                }
            )
            headers = (
                conditional_headers(source.etag, source.last_modified)
                if offset == 0
                else {"Accept": "application/json"}
            )
            response = await self.http.get(f"{base_url}?{query}", headers=headers)
            if offset == 0:
                first_etag = response.headers.get("etag")
                first_last_modified = response.headers.get("last-modified")
                first_http_status = response.status_code
                if response.status_code == 304:
                    return FetchResult(completeness="full", not_modified=True, http_status=304)
            if response.status_code >= 400:
                raise ValueError(f"PROVIDER_HTTP_{response.status_code}")

            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("PROVIDER_INVALID_PAYLOAD")
            rows = payload.get("content")
            total_found = payload.get("totalFound")
            if (
                not isinstance(rows, list)
                or isinstance(total_found, bool)
                or not isinstance(total_found, int)
                or total_found < 0
                or any(not isinstance(row, dict) for row in rows)
            ):
                raise ValueError("PROVIDER_INVALID_PAYLOAD")

            summaries.extend(row for row in rows if isinstance(row, dict))
            if len(summaries) >= total_found:
                break
            if not rows:
                raise ValueError("PROVIDER_INCOMPLETE_PAGINATION")
            offset += len(rows)

        jobs: list[RawJob] = []
        for summary in summaries:
            posting_id = summary.get("id") or summary.get("uuid")
            if not isinstance(posting_id, (str, int)) or isinstance(posting_id, bool):
                raise ValueError("PROVIDER_INVALID_JOB")
            external_job_id = str(posting_id)
            detail_url = f"{base_url}/{quote(external_job_id, safe='')}"
            detail_response = await self.http.get(
                detail_url,
                headers={"Accept": "application/json"},
            )
            if detail_response.status_code >= 400:
                raise ValueError(f"PROVIDER_HTTP_{detail_response.status_code}")
            detail = detail_response.json()
            if not isinstance(detail, dict):
                raise ValueError("PROVIDER_INVALID_JOB")
            jobs.append(
                RawJob(
                    external_job_id=external_job_id,
                    raw_payload={**summary, **detail},
                )
            )

        return FetchResult(
            completeness="full",
            jobs=jobs,
            response_etag=first_etag,
            response_last_modified=first_last_modified,
            http_status=first_http_status,
        )

    def normalize(self, raw: RawJob) -> NormalizedJob:
        row = raw.raw_payload
        apply_url = row.get("applyUrl") or row.get("postingUrl")
        if not isinstance(apply_url, str):
            raise ValueError("SMARTRECRUITERS_APPLY_URL_REQUIRED")

        location = row.get("location")
        location_data = location if isinstance(location, dict) else {}
        location_parts = [
            optional_text(location_data.get(field)) for field in ("city", "region", "country")
        ]
        location_text = optional_text(", ".join(part for part in location_parts if part))
        if location_data.get("remote") is True:
            location_text = f"{location_text} (Remote)" if location_text else "Remote"

        department = row.get("department")
        employment_type = row.get("typeOfEmployment")
        return NormalizedJob(
            external_job_id=raw.external_job_id,
            title=str(row.get("name") or ""),
            location_text=location_text,
            department=_object_label(department),
            employment_type=_object_label(employment_type),
            description_text=_description_text(row.get("jobAd")),
            apply_url=apply_url,
            source_posted_at=parse_datetime(row.get("releasedDate")),
        )


def _object_label(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return optional_text(value.get("label"))


def _description_text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    sections = value.get("sections")
    if not isinstance(sections, dict):
        return ""
    parts: list[str] = []
    for section_name in ("jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(section_name)
        if not isinstance(section, dict):
            continue
        text = strip_html(section.get("text"))
        if text:
            parts.append(text)
    return " ".join(parts)
