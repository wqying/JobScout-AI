from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.ai.schemas.discovery import CompanyProposal, SourceReference
from app.api.errors import AppError
from app.companies.normalization import domain_from_url, normalize_https_url
from app.monitoring.provider_detection import ProviderDetection, detect_provider

# Industry relevance is often established by third-party coverage rather than a company's own
# copy, so these types may support it. Company identity still requires an own-domain source, and
# `search_lead` stays excluded because unverified snippets are leads, not evidence.
SECONDARY_INDUSTRY_SOURCE_TYPES = frozenset({"reputable_directory", "official_government"})

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
CareersUrlStatus = Literal["evidence_verified", "not_found", "rejected"]
MonitoringSupport = Literal[
    "structured",
    "generic_verified",
    "generic_pending",
    "unsupported",
]

STRUCTURED_PROVIDERS = frozenset({"greenhouse", "lever", "ashby", "smartrecruiters"})

# Monitorability by support level. `generic_verified` sits below a documented provider board
# because a scraped listing is add/update-only, and above `generic_pending` because the resolver
# has actually opened the page and counted real openings on it.
MONITORABILITY_SCORES: dict[str, int] = {
    "structured": 100,
    "generic_verified": 75,
    "generic_pending": 50,
    "unsupported": 0,
}


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
    monitorability_score: int
    source_id: str | None = None


@dataclass(frozen=True)
class ValidatedProposal:
    proposal: CompanyProposal
    website_url: str
    careers: CareersResolution
    official_domain: str
    industry_source: ValidatedSource
    industry_source_is_official: bool
    verified_sources: list[ValidatedSource]
    internship_verified: bool

    @property
    def careers_url(self) -> str | None:
        return self.careers.url

    @property
    def monitorability_score(self) -> int:
        return self.careers.monitorability_score


def validate_proposal(
    proposal: CompanyProposal,
    source_manifest: list[dict[str, str | None]],
) -> ValidatedProposal:
    website_url = normalize_https_url(str(proposal.official_website_url))
    official_domain = domain_from_url(website_url)
    manifest_by_id = _manifest_by_id(source_manifest)
    sourced = [
        resolved
        for reference in proposal.source_references
        if (resolved := _resolve_reference(reference, manifest_by_id)) is not None
    ]
    own_domain_sources = [
        reference for reference in sourced if _same_company_domain(reference.url, official_domain)
    ]
    if not own_domain_sources:
        raise ValueError(
            "Company has no official or reputable research-manifest-backed industry evidence"
        )
    official_industry_sources = [
        reference
        for reference in own_domain_sources
        if reference.source_type == "official_company" and "industry" in _claims(reference)
    ]
    secondary_industry_sources = [
        reference
        for reference in sourced
        if reference.source_type in SECONDARY_INDUSTRY_SOURCE_TYPES
        and "industry" in _claims(reference)
    ]
    # An explicit industry citation is preferred, but a verified first-party page is enough on its
    # own: identity is proven by the domain, and the model judges industry fit from its research.
    industry_source = next(
        iter([*official_industry_sources, *secondary_industry_sources, *own_domain_sources])
    )
    industry_source_is_official = industry_source.source_type == "official_company" and (
        _same_company_domain(industry_source.url, official_domain)
    )

    careers = _resolve_careers(proposal, sourced, official_domain)
    internship_verified = proposal.has_internship_evidence and any(
        reference.source_type == "official_company"
        and "internship" in _claims(reference)
        and _same_company_domain(reference.url, official_domain)
        for reference in sourced
    )
    return ValidatedProposal(
        proposal=proposal,
        website_url=website_url,
        careers=careers,
        official_domain=official_domain,
        industry_source=industry_source,
        industry_source_is_official=industry_source_is_official,
        verified_sources=sourced,
        internship_verified=internship_verified,
    )


def _resolve_careers(
    proposal: CompanyProposal,
    sourced: list[ValidatedSource],
    official_domain: str,
) -> CareersResolution:
    selected_id = proposal.official_careers_source_id
    if selected_id is None:
        return CareersResolution(
            url=None,
            url_status="not_found",
            reason="CAREERS_SOURCE_NOT_SELECTED",
            monitoring_support="unsupported",
            monitorability_score=0,
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
            url_status="evidence_verified",
            reason="CAREERS_SOURCE_STRUCTURED_VERIFIED",
            monitoring_support="structured",
            monitorability_score=MONITORABILITY_SCORES["structured"],
            source_id=selected_id,
        )

    if not _same_company_domain(selected_source.url, official_domain):
        return _rejected_careers(selected_id, "CAREERS_SOURCE_DOMAIN_MISMATCH")

    # A manifest-backed URL on the official company domain is safe to retain as evidence. Its
    # collection capability remains pending until the bounded generic adapter inspects the page.
    return CareersResolution(
        url=_canonical_url(normalize_https_url(selected_source.url)),
        url_status="evidence_verified",
        reason="CAREERS_SOURCE_OFFICIAL_DOMAIN_VERIFIED",
        monitoring_support="generic_pending",
        monitorability_score=MONITORABILITY_SCORES["generic_pending"],
        source_id=selected_id,
    )


def _rejected_careers(source_id: str, reason: str) -> CareersResolution:
    return CareersResolution(
        url=None,
        url_status="rejected",
        reason=reason,
        monitoring_support="unsupported",
        monitorability_score=0,
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
        if urlsplit(url).scheme.casefold() != "https":
            continue
        try:
            normalized_url = normalize_https_url(url)
        except (AppError, ValueError):
            continue
        canonical_url = _canonical_url(normalized_url)
        if source_id in sources:
            continue
        sources[source_id] = (canonical_url, item.get("title"))
    return sources
