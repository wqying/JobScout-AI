from __future__ import annotations

from typing import Any

import pytest

from app.monitoring.adapters import (
    AshbyAdapter,
    GenericHtmlAdapter,
    GreenhouseAdapter,
    LeverAdapter,
    SmartRecruitersAdapter,
    adapter_for,
)
from app.monitoring.http import SafeHttpResponse
from app.monitoring.schemas import CareerSourceConfig


class StaticFetcher:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        self.requests.append((url, headers or {}))
        return SafeHttpResponse(
            status_code=200,
            url=url,
            headers={"etag": '"fixture-v1"'},
            body=self.payload,
        )


class GenericFetcher:
    async def get(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        del headers
        if url.endswith("/robots.txt"):
            body = b"User-agent: *\nAllow: /careers"
        else:
            body = (
                b'<html><body><a href="/careers/software-engineer">'
                b'Software Engineer</a><a href="https://jobs.ashbyhq.com/acme/uuid-1">'
                b"Product Engineer</a><script>dynamicJob()</script></body></html>"
            )
        return SafeHttpResponse(status_code=200, url=url, headers={}, body=body)


class InformationalFetcher:
    """An early-careers landing page: navigation only, no individual openings."""

    async def get(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        del headers
        if url.endswith("/robots.txt"):
            body = b"User-agent: *\nAllow: /"
        else:
            body = (
                b"<html><body>"
                b'<a href="/careers">Careers</a>'
                b'<a href="/careers/search">Search jobs</a>'
                b'<a href="/careers/job-alerts">Get job alerts</a>'
                b'<a href="/early-careers/students">Students</a>'
                b'<a href="/careers/life-at-acme">Life at Acme</a>'
                b'<a href="/careers/internships">Internship Program</a>'
                b"</body></html>"
            )
        return SafeHttpResponse(status_code=200, url=url, headers={}, body=body)


class RoutedFetcher:
    def __init__(self, routes: dict[str, bytes]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        self.requests.append((url, headers or {}))
        return SafeHttpResponse(
            status_code=200,
            url=url,
            headers={"etag": '"smartrecruiters-fixture-v1"'},
            body=self.routes[url],
        )


class RobotsFetcher:
    def __init__(self, status_code: int, body: bytes = b"") -> None:
        self.status_code = status_code
        self.body = body

    async def get(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        del headers
        if not url.endswith("/robots.txt"):
            raise AssertionError("The careers page must not be fetched after robots rejection")
        return SafeHttpResponse(
            status_code=self.status_code,
            url=url,
            headers={},
            body=self.body,
        )


@pytest.mark.parametrize(
    ("adapter_type", "config", "payload", "expected_url", "expected_title"),
    [
        (
            GreenhouseAdapter,
            {"board_token": "acme"},
            b'{"jobs":[{"id":101,"title":"Engineer","location":{"name":"NYC"},'
            b'"absolute_url":"https://boards.greenhouse.io/acme/jobs/101",'
            b'"content":"<p>Build things</p>","departments":[{"name":"R&D"}]}]}',
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
            "Engineer",
        ),
        (
            LeverAdapter,
            {"site": "acme"},
            b'[{"id":"lever-1","text":"Data Engineer","categories":'
            b'{"location":"Remote","team":"Data","commitment":"Full-time"},'
            b'"descriptionPlain":"Build pipelines","applyUrl":'
            b'"https://jobs.lever.co/acme/lever-1/apply"}]',
            "https://api.lever.co/v0/postings/acme?mode=json",
            "Data Engineer",
        ),
        (
            LeverAdapter,
            {"site": "acme", "region": "eu"},
            b'[{"id":"lever-eu-1","text":"Platform Engineer","categories":'
            b'{"location":"Berlin","team":"Platform","commitment":"Full-time"},'
            b'"descriptionPlain":"Build infrastructure","applyUrl":'
            b'"https://jobs.eu.lever.co/acme/lever-eu-1/apply"}]',
            "https://api.eu.lever.co/v0/postings/acme?mode=json",
            "Platform Engineer",
        ),
        (
            AshbyAdapter,
            {"organization": "acme"},
            b'{"apiVersion":"1","jobs":[{"title":"Product Engineer",'
            b'"location":"Boston","department":"Product","employmentType":"FullTime",'
            b'"descriptionPlain":"Build products","publishedAt":"2026-08-01T12:00:00Z",'
            b'"jobUrl":"https://jobs.ashbyhq.com/acme/job-1",'
            b'"applyUrl":"https://jobs.ashbyhq.com/acme/job-1/application"}]}',
            "https://api.ashbyhq.com/posting-api/job-board/acme",
            "Product Engineer",
        ),
    ],
)
async def test_official_provider_contracts_normalize_fixture_payloads(
    adapter_type: type[Any],
    config: dict[str, str],
    payload: bytes,
    expected_url: str,
    expected_title: str,
) -> None:
    fetcher = StaticFetcher(payload)
    adapter = adapter_type(fetcher)
    result = await adapter.fetch_jobs(
        CareerSourceConfig(
            careers_url="https://example.com/jobs",
            provider_config=config,
            etag='"old"',
        )
    )
    normalized = adapter.normalize(result.jobs[0])

    assert fetcher.requests[0][0] == expected_url
    assert fetcher.requests[0][1]["If-None-Match"] == '"old"'
    assert result.completeness == "full"
    assert result.response_etag == '"fixture-v1"'
    assert normalized.title == expected_title
    assert str(normalized.apply_url).startswith("https://")


async def test_smartrecruiters_contract_paginates_then_fetches_documented_details() -> None:
    base_url = "https://api.smartrecruiters.com/v1/companies/AcmeGames/postings"
    first_page_url = f"{base_url}?destination=PUBLIC&limit=100&offset=0"
    second_page_url = f"{base_url}?destination=PUBLIC&limit=100&offset=1"
    routes = {
        first_page_url: (
            b'{"limit":100,"offset":0,"totalFound":2,"content":['
            b'{"id":"101","name":"Software Engineer","releasedDate":'
            b'"2026-08-01T12:00:00Z"}]}'
        ),
        second_page_url: (
            b'{"limit":100,"offset":1,"totalFound":2,"content":['
            b'{"id":"102","name":"Product Engineer"}]}'
        ),
        f"{base_url}/101": (
            b'{"id":"101","name":"Software Engineer","location":'
            b'{"city":"Pittsburgh","region":"PA","country":"us","remote":true},'
            b'"department":{"label":"Engineering"},'
            b'"typeOfEmployment":{"label":"Intern"},'
            b'"applyUrl":"https://jobs.smartrecruiters.com/AcmeGames/101/apply",'
            b'"jobAd":{"sections":{"jobDescription":{"text":"<p>Build systems</p>"},'
            b'"qualifications":{"text":"Learn quickly"}}}}'
        ),
        f"{base_url}/102": (
            b'{"id":"102","name":"Product Engineer",'
            b'"postingUrl":"https://jobs.smartrecruiters.com/AcmeGames/102-product-engineer"}'
        ),
    }
    fetcher = RoutedFetcher(routes)
    adapter = SmartRecruitersAdapter(fetcher)

    result = await adapter.fetch_jobs(
        CareerSourceConfig(
            careers_url="https://careers.smartrecruiters.com/AcmeGames",
            provider_config={"company_identifier": "AcmeGames"},
            etag='"old"',
        )
    )
    normalized = adapter.normalize(result.jobs[0])

    assert [request[0] for request in fetcher.requests] == [
        first_page_url,
        second_page_url,
        f"{base_url}/101",
        f"{base_url}/102",
    ]
    assert fetcher.requests[0][1]["If-None-Match"] == '"old"'
    assert "If-None-Match" not in fetcher.requests[1][1]
    assert result.completeness == "full"
    assert len(result.jobs) == 2
    assert result.response_etag == '"smartrecruiters-fixture-v1"'
    assert normalized.title == "Software Engineer"
    assert normalized.location_text == "Pittsburgh, PA, us (Remote)"
    assert normalized.department == "Engineering"
    assert normalized.employment_type == "Intern"
    assert normalized.description_text == "Build systems Learn quickly"
    assert normalized.source_posted_at is not None


def test_smartrecruiters_is_available_from_the_adapter_factory() -> None:
    fetcher = RoutedFetcher({})

    assert isinstance(adapter_for("smartrecruiters", fetcher), SmartRecruitersAdapter)


async def test_generic_adapter_respects_robots_and_only_reads_server_html() -> None:
    adapter = GenericHtmlAdapter(GenericFetcher())
    result = await adapter.fetch_jobs(
        CareerSourceConfig(careers_url="https://acme.example/careers")
    )

    assert result.completeness == "partial"
    assert len(result.jobs) == 2
    assert adapter.normalize(result.jobs[0]).title == "Software Engineer"
    assert adapter.normalize(result.jobs[1]).title == "Product Engineer"


async def test_generic_adapter_ignores_navigation_on_an_informational_page() -> None:
    """An early-careers landing page must yield no jobs at all (DESIGN_DOC.md Section 13.3)."""

    adapter = GenericHtmlAdapter(InformationalFetcher())

    result = await adapter.fetch_jobs(
        CareerSourceConfig(careers_url="https://acme.example/early-careers")
    )

    assert result.jobs == []


@pytest.mark.parametrize("status_code", [401, 403])
async def test_generic_adapter_distinguishes_robots_http_denials(status_code: int) -> None:
    adapter = GenericHtmlAdapter(RobotsFetcher(status_code))

    with pytest.raises(ValueError, match=rf"^ROBOTS_TXT_HTTP_{status_code}$"):
        await adapter.fetch_jobs(CareerSourceConfig(careers_url="https://acme.example/careers"))


@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_generic_adapter_retries_temporary_robots_failures(status_code: int) -> None:
    adapter = GenericHtmlAdapter(RobotsFetcher(status_code))

    with pytest.raises(ValueError, match=r"^ROBOTS_FETCH_FAILED$"):
        await adapter.fetch_jobs(CareerSourceConfig(careers_url="https://acme.example/careers"))


async def test_explicit_robots_disallow_keeps_the_existing_error_code() -> None:
    adapter = GenericHtmlAdapter(RobotsFetcher(200, b"User-agent: JobScout-AI\nDisallow: /careers"))

    with pytest.raises(ValueError, match=r"^ROBOTS_DISALLOWED$"):
        await adapter.fetch_jobs(CareerSourceConfig(careers_url="https://acme.example/careers"))
