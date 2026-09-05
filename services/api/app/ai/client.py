from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from app.ai.schemas.discovery import NormalizedDiscovery


@dataclass(frozen=True)
class AIUsage:
    input_tokens: int
    output_tokens: int
    web_search_calls: int = 0


@dataclass(frozen=True)
class ResearchResponse:
    report: str
    source_manifest: list[dict[str, str | None]]
    usage: AIUsage


@dataclass(frozen=True)
class NormalizationResponse:
    discovery: NormalizedDiscovery
    raw_output: dict[str, Any]
    usage: AIUsage


class DiscoveryAIClient(Protocol):
    research_model: str
    structured_model: str

    async def research(
        self,
        query: str,
        country: str,
        excluded_companies: list[dict[str, str]] | None = None,
    ) -> ResearchResponse: ...

    async def normalize(
        self,
        report: str,
        source_manifest: list[dict[str, str | None]],
        repair_feedback: str | None = None,
    ) -> NormalizationResponse: ...


class InvalidStructuredOutput(ValueError):
    def __init__(self, message: str, usage: AIUsage) -> None:
        super().__init__(message)
        self.usage = usage


class OpenAIResponsesClient:
    """Small typed Responses API adapter; domain code never depends on HTTP response shapes."""

    def __init__(
        self,
        *,
        api_key: str,
        research_model: str,
        structured_model: str,
        base_url: str = "https://api.openai.com/v1",
        research_max_output_tokens: int = 6000,
        structured_max_output_tokens: int = 10000,
        max_web_search_calls: int = 4,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required for live discovery")
        self.research_model = research_model
        self.structured_model = structured_model
        self.research_max_output_tokens = research_max_output_tokens
        self.structured_max_output_tokens = structured_max_output_tokens
        self.max_web_search_calls = max_web_search_calls
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    async def __aenter__(self) -> OpenAIResponsesClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def research(
        self,
        query: str,
        country: str,
        excluded_companies: list[dict[str, str]] | None = None,
    ) -> ResearchResponse:
        exclusions = excluded_companies or []
        exclusion_text = (
            "\nDo not return any company in this exclusion list: "
            + json.dumps(exclusions, ensure_ascii=False)
            if exclusions
            else ""
        )
        response = await self._post(
            {
                "model": self.research_model,
                "store": False,
                "instructions": (
                    "Research US employers matching the requested industry. Find 30 to 40 "
                    "credible candidates when evidence permits. For every company, identify the "
                    "official website and cite sources for industry relevance and internships. "
                    "For the careers page, cite the page that LISTS INDIVIDUAL OPEN ROLES with "
                    "clickable job titles: an applicant-tracking board root such as a Greenhouse, "
                    "Lever, Ashby, or SmartRecruiters board, or an 'all open positions' page on "
                    "the company's own domain. An early-careers, university-recruiting, "
                    "student-programs, or 'life at' landing page is NOT an acceptable careers "
                    "page unless individual openings are listed on it; when only such a page is "
                    "available, follow its link to the job listing and cite that instead. "
                    "Sponsorship history will be calculated separately from official DOL data; "
                    "do not promise it."
                ),
                "input": (f"Industry query: {query}\nCountry: {country}{exclusion_text}"),
                "tools": [{"type": "web_search"}],
                "tool_choice": "auto",
                "include": ["web_search_call.action.sources"],
                "max_tool_calls": self.max_web_search_calls,
                "max_output_tokens": self.research_max_output_tokens,
                "prompt_cache_key": "jobscout-company-research-v1",
            }
        )
        report = _extract_output_text(response)
        if not report.strip():
            raise RuntimeError("OpenAI research response contained no report text")
        return ResearchResponse(
            report=report,
            source_manifest=_extract_sources(response),
            usage=_extract_usage(response),
        )

    async def normalize(
        self,
        report: str,
        source_manifest: list[dict[str, str | None]],
        repair_feedback: str | None = None,
    ) -> NormalizationResponse:
        feedback = (
            f"\nThe previous structured response failed validation: {repair_feedback}"
            if repair_feedback
            else ""
        )
        response = await self._post(
            {
                "model": self.structured_model,
                "store": False,
                "instructions": (
                    "Transform only the supplied research report and source manifest into the "
                    "requested schema. Do not browse, invent URLs or source IDs, or add claims "
                    "absent from the report. Each source_reference must select a source_id "
                    "verbatim from the supplied manifest. When a careers page is supported, "
                    "official_careers_source_id must select that same source_reference and the "
                    "reference must include careers_page in supports_claims. Use concise claim "
                    "labels such as industry, official_identity, careers_page, or internship."
                    f"{feedback}"
                ),
                "input": json.dumps(
                    {"report": report, "source_manifest": source_manifest},
                    ensure_ascii=False,
                ),
                "max_output_tokens": self.structured_max_output_tokens,
                "prompt_cache_key": "jobscout-company-normalization-v2",
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "jobscout_company_discovery",
                        "strict": True,
                        "schema": _openai_discovery_schema(),
                    }
                },
            }
        )
        raw_text = _extract_output_text(response)
        try:
            raw_output = json.loads(raw_text)
            discovery = NormalizedDiscovery.model_validate(raw_output)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise InvalidStructuredOutput(
                f"Invalid structured discovery output: {exc}", _extract_usage(response)
            ) from exc
        return NormalizationResponse(
            discovery=discovery,
            raw_output=raw_output,
            usage=_extract_usage(response),
        )

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post("responses", json=payload)
        if response.is_error:
            request_id = response.headers.get("x-request-id", "unknown")
            error_detail = _extract_openai_error(response)
            raise RuntimeError(
                f"OpenAI Responses API failed with HTTP {response.status_code} "
                f"(request {request_id}): {error_detail}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("OpenAI Responses API returned an unexpected payload")
        return data


def _openai_discovery_schema() -> dict[str, Any]:
    """Return the Pydantic schema with unsupported OpenAI format hints removed.

    Pydantic emits ``format: uri`` for HttpUrl. OpenAI Structured Outputs does
    not support that JSON Schema format, while Pydantic still validates URLs
    after the response is received.
    """
    schema = NormalizedDiscovery.model_json_schema()

    def remove_unsupported_formats(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("format") == "uri":
                value.pop("format")
            for child in value.values():
                remove_unsupported_formats(child)
        elif isinstance(value, list):
            for child in value:
                remove_unsupported_formats(child)

    remove_unsupported_formats(schema)
    return schema


def _extract_openai_error(response: httpx.Response) -> str:
    """Extract bounded diagnostic fields without logging request credentials."""
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return "no structured error details"

    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "no structured error details"

    details = []
    for key in ("message", "type", "code", "param"):
        value = error.get(key)
        if isinstance(value, (str, int, float, bool)):
            details.append(f"{key}={value}")
    return "; ".join(details)[:1000] or "no structured error details"


def _extract_output_text(payload: dict[str, Any]) -> str:
    convenience = payload.get("output_text")
    if isinstance(convenience, str):
        return convenience
    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "\n".join(parts)


def _extract_sources(payload: dict[str, Any]) -> list[dict[str, str | None]]:
    by_url: dict[str, dict[str, str | None]] = {}
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        if isinstance(action, dict):
            for source in action.get("sources", []):
                _add_source(by_url, source)
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            for annotation in content.get("annotations", []):
                _add_source(by_url, annotation)
    return [
        {"source_id": f"source_{index}", **source}
        for index, source in enumerate(by_url.values(), start=1)
    ]


def _add_source(by_url: dict[str, dict[str, str | None]], source: object) -> None:
    if not isinstance(source, dict):
        return
    url = source.get("url")
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return
    title = source.get("title")
    by_url[url] = {"url": url, "title": title if isinstance(title, str) else None}


def _extract_usage(payload: dict[str, Any]) -> AIUsage:
    raw_usage = payload.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    web_search_calls = sum(
        1
        for item in payload.get("output", [])
        if isinstance(item, dict) and item.get("type") == "web_search_call"
    )
    return AIUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        web_search_calls=web_search_calls,
    )
