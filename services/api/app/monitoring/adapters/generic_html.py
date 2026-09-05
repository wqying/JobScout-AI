from __future__ import annotations

from app.monitoring.http import HttpFetcher
from app.monitoring.job_links import extract_job_links
from app.monitoring.robots import ensure_robots_allows
from app.monitoring.schemas import CareerSourceConfig, FetchResult, NormalizedJob, RawJob


class GenericHtmlAdapter:
    def __init__(self, http: HttpFetcher) -> None:
        self.http = http

    async def fetch_jobs(self, source: CareerSourceConfig) -> FetchResult:
        careers_url = str(source.careers_url)
        await ensure_robots_allows(self.http, careers_url)

        headers = {"Accept": "text/html"}
        if source.etag:
            headers["If-None-Match"] = source.etag
        if source.last_modified:
            headers["If-Modified-Since"] = source.last_modified
        response = await self.http.get(careers_url, headers=headers)
        if response.status_code == 304:
            return FetchResult(completeness="partial", not_modified=True, http_status=304)
        if response.status_code >= 400:
            raise ValueError(f"GENERIC_HTTP_{response.status_code}")

        # Resolve links against the final URL so a redirected careers page still yields same-site
        # links, and apply the shared `job-links-v2` rules the resolver used to verify this page.
        jobs = [
            RawJob(
                external_job_id=link.url,
                raw_payload={"title": link.title, "apply_url": link.url},
            )
            for link in extract_job_links(response.text, response.url or careers_url)
        ]
        # Generic pages can hide jobs behind JavaScript or pagination, so absence is never complete.
        return FetchResult(
            completeness="partial",
            jobs=jobs,
            response_etag=response.headers.get("etag"),
            response_last_modified=response.headers.get("last-modified"),
            http_status=response.status_code,
        )

    def normalize(self, raw: RawJob) -> NormalizedJob:
        apply_url = raw.raw_payload.get("apply_url")
        if not isinstance(apply_url, str):
            raise ValueError("GENERIC_APPLY_URL_REQUIRED")
        return NormalizedJob(
            external_job_id=raw.external_job_id,
            title=str(raw.raw_payload.get("title") or ""),
            apply_url=apply_url,
        )
