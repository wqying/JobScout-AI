from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

from app.companies.normalization import normalize_https_url

Provider = Literal[
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "generic_html",
    "unsupported",
]


@dataclass(frozen=True)
class ProviderDetection:
    provider: Provider
    canonical_source_key: str
    provider_config: dict[str, Any]
    status: Literal["supported", "pending_resolution", "unsupported"]
    version: str = "provider-detection-v2"
    # The provider URL the detection actually matched. It differs from the inspected URL when a
    # board is discovered inside a company page's HTML, and the board root must be derived from the
    # board's own origin rather than the company's.
    matched_url: str | None = None


def detect_provider(
    url: str, html: str | None = None, *, allow_pending_generic: bool = False
) -> ProviderDetection:
    normalized_url = normalize_https_url(url)
    parsed = urlsplit(normalized_url)
    hostname = (parsed.hostname or "").removeprefix("www.")
    parts = [part for part in parsed.path.split("/") if part]

    if hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io"}:
        query_token = parse_qs(parsed.query).get("for", [None])[0]
        token = query_token or (parts[0] if parts else None)
        if token:
            return _structured("greenhouse", token, "board_token")

    if hostname in {"jobs.lever.co", "jobs.eu.lever.co"} and parts:
        region = "eu" if hostname == "jobs.eu.lever.co" else "global"
        return _structured(
            "lever",
            parts[0],
            "site",
            provider_config={"region": region},
            canonical_scope=region if region == "eu" else None,
        )

    if hostname == "jobs.ashbyhq.com" and parts:
        return _structured("ashby", parts[0], "organization")

    if hostname in {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"} and parts:
        return _structured("smartrecruiters", parts[0], "company_identifier")

    if html:
        for pattern in (
            r"https://(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)",
            r"https://jobs(?:\.eu)?\.lever\.co/([a-zA-Z0-9_-]+)",
            r"https://jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)",
            r"https://(?:careers|jobs)\.smartrecruiters\.com/([a-zA-Z0-9_-]+)",
        ):
            match = re.search(pattern, html)
            if match:
                provider: Provider
                config_name: str
                provider_config: dict[str, Any] | None = None
                canonical_scope: str | None = None
                if "greenhouse" in pattern:
                    provider, config_name = "greenhouse", "board_token"
                elif "lever" in pattern:
                    provider, config_name = "lever", "site"
                    region = "eu" if "jobs.eu.lever.co" in match.group(0) else "global"
                    provider_config = {"region": region}
                    canonical_scope = region if region == "eu" else None
                elif "ashby" in pattern:
                    provider, config_name = "ashby", "organization"
                else:
                    provider, config_name = "smartrecruiters", "company_identifier"
                return _structured(
                    provider,
                    match.group(1),
                    config_name,
                    provider_config=provider_config,
                    canonical_scope=canonical_scope,
                    matched_url=match.group(0),
                )

        if re.search(r"<a\b[^>]*href=[\"'][^\"']*(?:job|career|position)", html, re.I):
            digest = hashlib.sha256(normalized_url.encode()).hexdigest()[:24]
            return ProviderDetection(
                provider="generic_html",
                canonical_source_key=f"generic_html:{digest}",
                provider_config={"rules_version": "generic-html-v1"},
                status="pending_resolution",
            )

    digest = hashlib.sha256(normalized_url.encode()).hexdigest()[:24]
    if allow_pending_generic:
        return ProviderDetection(
            provider="generic_html",
            canonical_source_key=f"generic_html:{digest}",
            provider_config={"rules_version": "generic-html-v1"},
            status="pending_resolution",
        )
    return ProviderDetection(
        provider="unsupported",
        canonical_source_key=f"unsupported:{digest}",
        provider_config={"reason": "unrecognized_without_verified_server_rendered_html"},
        status="unsupported",
    )


def _structured(
    provider: Provider,
    token: str,
    config_name: str,
    *,
    provider_config: dict[str, Any] | None = None,
    canonical_scope: str | None = None,
    matched_url: str | None = None,
) -> ProviderDetection:
    normalized_token = token.casefold()
    key_parts: list[str] = [provider]
    if canonical_scope:
        key_parts.append(canonical_scope)
    key_parts.append(normalized_token)
    return ProviderDetection(
        provider=provider,
        canonical_source_key=":".join(key_parts),
        provider_config={config_name: token, **(provider_config or {})},
        status="supported",
        matched_url=matched_url,
    )
