from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

from app.monitoring.http import HttpFetcher, conditional_headers
from app.monitoring.schemas import CareerSourceConfig, FetchResult, RawJob


class JsonAdapterBase:
    def __init__(self, http: HttpFetcher) -> None:
        self.http = http

    async def _fetch_json(
        self, url: str, source: CareerSourceConfig, jobs_field: str | None = None
    ) -> FetchResult:
        response = await self.http.get(
            url,
            headers=conditional_headers(source.etag, source.last_modified),
        )
        if response.status_code == 304:
            return FetchResult(completeness="full", not_modified=True, http_status=304)
        if response.status_code >= 400:
            raise ValueError(f"PROVIDER_HTTP_{response.status_code}")
        payload = response.json()
        rows: object
        if jobs_field is None:
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get(jobs_field)
        else:
            rows = None
        if not isinstance(rows, list):
            raise ValueError("PROVIDER_INVALID_PAYLOAD")
        jobs = [
            RawJob(
                external_job_id=external_id(row),
                raw_payload=row,
            )
            for row in rows
            if isinstance(row, dict)
        ]
        if len(jobs) != len(rows):
            raise ValueError("PROVIDER_INVALID_JOB")
        return FetchResult(
            completeness="full",
            jobs=jobs,
            response_etag=response.headers.get("etag"),
            response_last_modified=response.headers.get("last-modified"),
            http_status=response.status_code,
        )


def external_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("id")
    if value is None:
        return None
    return str(value)


def optional_text(value: object) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def strip_html(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parser = TextParser()
    parser.feed(unescape(value))
    return " ".join(parser.text.split())


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    @property
    def text(self) -> str:
        return " ".join(self.parts)

    def handle_data(self, data: str) -> None:
        self.parts.append(data)
