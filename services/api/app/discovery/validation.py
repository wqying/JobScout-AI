from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.ai.schemas.discovery import CompanyProposal, SourceReference
from app.api.errors import AppError
from app.companies.normalization import domain_from_url, normalize_https_url, normalize_query
from app.monitoring.provider_detection import ProviderDetection, detect_provider

# Web search appends tracking parameters to cited URLs (for example `?utm_source=openai`) that the
# manifest entries do not carry, so both sides are compared with those parameters removed.
_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = frozenset({"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid"})

SourceType = Literal[
    "official_company",
    "official_government",
    "reputable_directory",
    "search_lead",
]
CareersUrlStatus = Literal["research_linked", "page_checked", "not_found", "rejected"]
MonitoringSupport = Literal[
    "structured",
    "generic_verified",
    "generic_pending",
    "unsupported",
]

STRUCTURED_PROVIDERS = frozenset({"greenhouse", "lever", "ashby", "smartrecruiters"})

SHARED_IDENTITY_HOSTS = frozenset(
    {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "jobs.lever.co",
        "jobs.eu.lever.co",
        "jobs.ashbyhq.com",
        "careers.smartrecruiters.com",
        "jobs.smartrecruiters.com",
        "greenhouse.io",
        "lever.co",
        "ashbyhq.com",
        "smartrecruiters.com",
        "linkedin.com",
        "crunchbase.com",
        "github.com",
        "facebook.com",
        "instagram.com",
        "x.com",
        "twitter.com",
        "youtube.com",
        "wikipedia.org",
        "glassdoor.com",
        "indeed.com",
        "wellfound.com",
        "builtin.com",
    }
)
_INTERNAL_SUFFIXES = (
    ".internal",
    ".local",
    ".localhost",
    ".invalid",
    ".example",
    ".test",
    ".corp",
    ".lan",
    ".home",
)
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PAGE_CHECKED_REASONS = frozenset(
    {
        "CAREERS_PAGE_LISTING_VERIFIED",
        "CAREERS_PAGE_LISTING_VERIFIED_VIA_LINK",
        "CAREERS_PAGE_ATS_DISCOVERED",
        "CAREERS_PAGE_NO_LISTING_FOUND",
    }
)


@dataclass(frozen=True)
class ValidatedSource:
    source_id: str
    url: str
    title: str | None
    source_type: SourceType
    supports_claims: list[str]

    def as_metadata(self) -> dict[str, str | list[str] | None]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "title": self.title,
            "source_type": self.source_type,
            "supports_claims": self.supports_claims,
        }


@dataclass(frozen=True)
class CareersResolution:
    url: str | None
    url_status: CareersUrlStatus
    reason: str
    monitoring_support: MonitoringSupport
    source_id: str | None = None


@dataclass(frozen=True)
class DiscoveryCandidate:
    proposal: CompanyProposal
    website_url: str | None
    careers: CareersResolution
    official_domain: str | None
    identity_key: str
    research_source_status: Literal["matched", "unmatched"]

    @property
    def careers_url(self) -> str | None:
        return self.careers.url


def validate_proposal(
    proposal: CompanyProposal,
    source_manifest: list[dict[str, str | None]],
) -> DiscoveryCandidate:
    website_url, official_domain = _usable_company_website(proposal.official_website_url)
    manifest_by_id = _manifest_by_id(source_manifest)
    sourced = [
        resolved
        for reference in proposal.source_references
        if (resolved := _resolve_reference(reference, manifest_by_id)) is not None
    ]
    careers = _resolve_careers(proposal, sourced, official_domain)
    identity_key = (
        f"domain:{official_domain}"
        if official_domain is not None
        else f"name:{normalize_query(proposal.canonical_name)}"
    )
    return DiscoveryCandidate(
        proposal=proposal,
        website_url=website_url,
        careers=careers,
        official_domain=official_domain,
        identity_key=identity_key,
        research_source_status="matched" if sourced else "unmatched",
    )


def _resolve_careers(
    proposal: CompanyProposal,
    sourced: list[ValidatedSource],
    official_domain: str | None,
) -> CareersResolution:
    selected_id = proposal.official_careers_source_id
    if selected_id is None:
        return CareersResolution(
            url=None,
            url_status="not_found",
            reason="CAREERS_SOURCE_NOT_SELECTED",
            monitoring_support="unsupported",
        )

    selected_reference = next(
        (
            reference
            for reference in proposal.source_references
            if reference.source_id == selected_id
        ),
        None,
    )
    if selected_reference is None:
        return _rejected_careers(selected_id, "CAREERS_SOURCE_REFERENCE_MISSING")
    if "careers_page" not in _claims(selected_reference):
        return _rejected_careers(selected_id, "CAREERS_SOURCE_NOT_TAGGED")

    selected_source = next(
        (source for source in sourced if source.source_id == selected_id),
        None,
    )
    if selected_source is None:
        return _rejected_careers(selected_id, "CAREERS_SOURCE_NOT_IN_MANIFEST")

    detection = detect_provider(selected_source.url)
    if detection.provider in STRUCTURED_PROVIDERS:
        return CareersResolution(
            url=structured_careers_url(detection, selected_source.url),
            url_status="research_linked",
            reason="CAREERS_SOURCE_STRUCTURED_VERIFIED",
            monitoring_support="structured",
            source_id=selected_id,
        )

    if official_domain is None or not _same_company_domain(selected_source.url, official_domain):
        return _rejected_careers(selected_id, "CAREERS_SOURCE_DOMAIN_MISMATCH")

    # A manifest-backed URL on the official company domain is safe to retain as evidence. Its
    # collection capability remains pending until the bounded generic adapter inspects the page.
    return CareersResolution(
        url=_canonical_url(normalize_https_url(selected_source.url)),
        url_status="research_linked",
        reason="CAREERS_SOURCE_OFFICIAL_DOMAIN_VERIFIED",
        monitoring_support="generic_pending",
        source_id=selected_id,
    )


def _rejected_careers(source_id: str, reason: str) -> CareersResolution:
    return CareersResolution(
        url=None,
        url_status="rejected",
        reason=reason,
        monitoring_support="unsupported",
        source_id=source_id,
    )


def structured_careers_url(detection: ProviderDetection, evidence_url: str) -> str:
    """Reduce a provider job/detail URL to its stable board root.

    ``evidence_url`` must belong to the provider. When a board is discovered inside a company page's
    HTML, pass ``detection.matched_url`` -- the company page's own origin would build a board root
    that does not exist.
    """

    parsed = urlsplit(evidence_url)
    origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    if detection.provider == "greenhouse":
        token = str(detection.provider_config["board_token"])
        return f"{origin}/{token}"
    if detection.provider == "lever":
        site = str(detection.provider_config["site"])
        return f"{origin}/{site}"
    if detection.provider == "ashby":
        organization = str(detection.provider_config["organization"])
        return f"{origin}/{organization}"
    if detection.provider == "smartrecruiters":
        company_identifier = str(detection.provider_config["company_identifier"])
        return f"https://careers.smartrecruiters.com/{company_identifier}"
    raise ValueError("Structured careers URL requires a supported provider")


def _resolve_reference(
    reference: SourceReference,
    manifest_by_id: dict[str, tuple[str, str | None]],
) -> ValidatedSource | None:
    manifest_entry = manifest_by_id.get(reference.source_id)
    if manifest_entry is None:
        return None
    url, title = manifest_entry
    return ValidatedSource(
        source_id=reference.source_id,
        url=url,
        title=title,
        source_type=reference.source_type,
        supports_claims=reference.supports_claims,
    )


def _claims(reference: SourceReference | ValidatedSource) -> set[str]:
    return {claim.strip().lower() for claim in reference.supports_claims}


def _same_company_domain(url: str, official_domain: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return hostname == official_domain or hostname.endswith(f".{official_domain}")


def _usable_company_website(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    try:
        if urlsplit(value).scheme.casefold() != "https":
            return None, None
        website_url = normalize_https_url(value)
        domain = domain_from_url(website_url).rstrip(".")
    except (AppError, ValueError):
        return None, None
    if not _is_public_identity_domain(domain):
        return None, None
    return website_url, domain


def _is_public_identity_domain(domain: str) -> bool:
    hostname = domain.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(_INTERNAL_SUFFIXES):
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return False
    labels = hostname.split(".")
    if (
        len(labels) < 2
        or labels[-1].isdigit()
        or any(not _DNS_LABEL.fullmatch(label) for label in labels)
    ):
        return False
    return not any(
        hostname == shared or hostname.endswith(f".{shared}") for shared in SHARED_IDENTITY_HOSTS
    )


def legacy_careers_url_status(
    value: object,
    *,
    careers_url: object,
    reason: object,
) -> CareersUrlStatus:
    """Translate pre-migration JSON so old rows remain readable during rolling upgrades."""

    if isinstance(value, str) and value in {
        "research_linked",
        "page_checked",
        "not_found",
        "rejected",
    }:
        return cast(CareersUrlStatus, value)
    if isinstance(reason, str) and (
        reason in _PAGE_CHECKED_REASONS or reason.startswith("CAREERS_PAGE_LISTING_")
    ):
        return "page_checked"
    if careers_url or value == "evidence_verified":
        return "research_linked"
    return "not_found"


def _canonical_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, f"{hostname}{port}", path, _strip_tracking_query(parts.query), ""))


def _strip_tracking_query(query: str) -> str:
    if not query:
        return ""
    return urlencode(
        [
            (key, value)
            for key, value in parse_qsl(query, keep_blank_values=True)
            if not key.lower().startswith(_TRACKING_QUERY_PREFIXES)
            and key.lower() not in _TRACKING_QUERY_KEYS
        ]
    )


def _manifest_by_id(
    source_manifest: list[dict[str, str | None]],
) -> dict[str, tuple[str, str | None]]:
    sources: dict[str, tuple[str, str | None]] = {}
    for item in source_manifest:
        source_id = item.get("source_id")
        url = item.get("url")
        if not isinstance(source_id, str) or not isinstance(url, str):
            continue
        try:
            if urlsplit(url).scheme.casefold() != "https":
                continue
            normalized_url = normalize_https_url(url)
        except (AppError, ValueError):
            continue
        canonical_url = _canonical_url(normalized_url)
        if source_id in sources:
            continue
        sources[source_id] = (canonical_url, item.get("title"))
    return sources
