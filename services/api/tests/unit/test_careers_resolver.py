"""Bounded careers-page resolution (DESIGN_DOC.md Section 13.7)."""

from __future__ import annotations

import httpx
import pytest

from app.ai.schemas.discovery import CompanyProposal
from app.core.config import Settings
from app.discovery.careers_resolver import (
    CareersPageResolver,
    needs_resolution,
)
from app.discovery.validation import DiscoveryCandidate, validate_proposal
from app.monitoring.http import SafeHttpResponse

SEED = "https://acme.com/early-careers"

MANIFEST: list[dict[str, str | None]] = [
    {"source_id": "source_1", "url": "https://acme.com/about", "title": "About"},
    {"source_id": "source_2", "url": SEED, "title": "Early careers"},
]

INFORMATIONAL_HTML = """
<html><body>
  <p>Our internship program runs every summer.</p>
  <a href="/careers/search">Search jobs</a>
  <a href="/early-careers/students">Students</a>
</body></html>
"""

LISTING_HTML = "".join(
    f'<a href="/careers/job/role-{index}">Engineer {index}</a>' for index in range(4)
)

EMBEDDED_BOARD_HTML = (
    '<html><body><a href="https://boards.greenhouse.io/acmegames">Open positions</a></body></html>'
)


class RouteFetcher:
    """Serve fixed bodies per URL; anything unrouted is a 404."""

    def __init__(self, routes: dict[str, str], *, robots: str = "User-agent: *\nAllow: /") -> None:
        self.routes = routes
        self.robots = robots
        self.requests: list[str] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        del headers
        self.requests.append(url)
        if url.endswith("/robots.txt"):
            return SafeHttpResponse(200, url, {}, self.robots.encode())
        body = self.routes.get(url)
        if body is None:
            return SafeHttpResponse(404, url, {}, b"")
        return SafeHttpResponse(200, url, {}, body.encode())

    @property
    def page_requests(self) -> list[str]:
        return [url for url in self.requests if not url.endswith("/robots.txt")]


class FailingFetcher:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def get(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        del url, headers
        raise self.error


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "discovery_resolve_max_candidates": 20,
        "discovery_resolve_max_link_candidates": 3,
        "discovery_resolve_min_job_links": 3,
        "discovery_resolve_concurrency": 4,
        "discovery_resolve_deadline_seconds": 30,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _proposal(careers_source_id: str | None = "source_2") -> CompanyProposal:
    return CompanyProposal.model_validate(
        {
            "canonical_name": "Acme",
            "aliases": [],
            "proposed_legal_entities": [],
            "official_website_url": "https://acme.com",
            "official_careers_source_id": careers_source_id,
            "industry_explanation": "Acme builds verified test products.",
            "internship_research_reported": False,
            "source_references": [
                {
                    "source_id": "source_1",
                    "source_type": "official_company",
                    "supports_claims": ["industry", "official_identity"],
                },
                {
                    "source_id": "source_2",
                    "source_type": "official_company",
                    "supports_claims": ["careers_page"],
                },
            ],
        }
    )


def _validated(careers_source_id: str | None = "source_2") -> DiscoveryCandidate:
    return validate_proposal(_proposal(careers_source_id), MANIFEST)


def test_a_cited_page_starts_unverified() -> None:
    item = _validated()

    assert item.careers.monitoring_support == "generic_pending"
    assert item.careers.url_status == "research_linked"
    assert needs_resolution(item) is True


async def test_a_listing_page_is_verified_without_any_hop() -> None:
    fetcher = RouteFetcher({SEED: LISTING_HTML})

    resolved = await CareersPageResolver(fetcher, _settings()).resolve(_validated())

    assert resolved.careers.monitoring_support == "generic_verified"
    assert resolved.careers.url_status == "page_checked"
    assert resolved.careers.reason == "CAREERS_PAGE_LISTING_VERIFIED"
    assert resolved.job_link_count == 4
    assert fetcher.page_requests == [SEED]


async def test_an_embedded_board_is_promoted_to_its_provider_root() -> None:
    fetcher = RouteFetcher({SEED: EMBEDDED_BOARD_HTML})

    resolved = await CareersPageResolver(fetcher, _settings()).resolve(_validated())

    assert resolved.careers.monitoring_support == "structured"
    assert resolved.careers.url_status == "page_checked"
    assert resolved.careers.url == "https://boards.greenhouse.io/acmegames"
    assert resolved.careers.reason == "CAREERS_PAGE_ATS_DISCOVERED"


async def test_an_informational_page_is_resolved_one_hop_to_its_listing() -> None:
    listing = "https://acme.com/careers/search"
    fetcher = RouteFetcher({SEED: INFORMATIONAL_HTML, listing: LISTING_HTML})

    resolved = await CareersPageResolver(fetcher, _settings()).resolve(_validated())

    assert resolved.careers.monitoring_support == "generic_verified"
    assert resolved.careers.url == listing
    assert resolved.careers.reason == "CAREERS_PAGE_LISTING_VERIFIED_VIA_LINK"
    assert resolved.job_link_count == 4


async def test_a_hop_never_follows_another_hop() -> None:
    listing = "https://acme.com/careers/search"
    # The hop target is itself informational and links onward; the resolver must stop there.
    fetcher = RouteFetcher(
        {
            SEED: INFORMATIONAL_HTML,
            listing: '<a href="/jobs/search">View all jobs</a>',
            "https://acme.com/jobs/search": LISTING_HTML,
        }
    )

    resolved = await CareersPageResolver(fetcher, _settings()).resolve(_validated())

    assert "https://acme.com/jobs/search" not in fetcher.page_requests
    assert resolved.careers.monitoring_support == "unsupported"


async def test_a_listing_free_page_becomes_unsupported_but_keeps_its_url() -> None:
    fetcher = RouteFetcher({SEED: INFORMATIONAL_HTML})

    resolved = await CareersPageResolver(fetcher, _settings()).resolve(_validated())

    assert resolved.careers.monitoring_support == "unsupported"
    assert resolved.careers.url_status == "page_checked"
    assert resolved.careers.url == SEED
    assert resolved.careers.reason == "CAREERS_PAGE_NO_LISTING_FOUND"


async def test_a_weak_listing_is_kept_rather_than_discarded() -> None:
    fetcher = RouteFetcher({SEED: '<a href="/careers/job/only-role">Staff Engineer</a>'})

    resolved = await CareersPageResolver(fetcher, _settings()).resolve(_validated())

    assert resolved.careers.monitoring_support == "generic_verified"
    assert resolved.job_link_count == 1


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("down"),
        httpx.ReadTimeout("slow"),
        ValueError("ROBOTS_DISALLOWED"),
    ],
)
async def test_every_fetch_failure_falls_back_instead_of_raising(error: Exception) -> None:
    resolved = await CareersPageResolver(FailingFetcher(error), _settings()).resolve(_validated())

    assert resolved.careers.monitoring_support == "generic_pending"
    assert resolved.careers.url_status == "research_linked"
    assert resolved.careers.reason == "CAREERS_PAGE_UNREACHABLE"
    assert resolved.careers.url == SEED


async def test_an_http_error_page_falls_back() -> None:
    resolved = await CareersPageResolver(RouteFetcher({}), _settings()).resolve(_validated())

    assert resolved.careers.reason == "CAREERS_PAGE_UNREACHABLE"


async def test_robots_disallow_leaves_the_candidate_unresolved_and_unfetched() -> None:
    fetcher = RouteFetcher(
        {SEED: LISTING_HTML}, robots="User-agent: JobScout-AI\nDisallow: /early-careers"
    )

    resolved = await CareersPageResolver(fetcher, _settings()).resolve(_validated())

    assert resolved.careers.reason == "CAREERS_PAGE_UNREACHABLE"
    assert fetcher.page_requests == []


async def test_a_candidate_with_no_careers_source_is_left_alone() -> None:
    item = _validated(careers_source_id=None)
    fetcher = RouteFetcher({})

    resolved = await CareersPageResolver(fetcher, _settings()).resolve(item)

    assert resolved.careers == item.careers
    assert fetcher.requests == []


async def test_resolve_all_keys_results_by_stable_identity() -> None:
    fetcher = RouteFetcher({SEED: LISTING_HTML})

    resolved = await CareersPageResolver(fetcher, _settings()).resolve_all([_validated()])

    assert set(resolved) == {"domain:acme.com"}
    assert resolved["domain:acme.com"].careers.monitoring_support == "generic_verified"


async def test_resolve_all_is_empty_without_candidates() -> None:
    assert await CareersPageResolver(RouteFetcher({}), _settings()).resolve_all([]) == {}
