from pathlib import Path

import pytest

from app.monitoring.provider_detection import detect_provider

FIXTURES = Path(__file__).parents[1] / "fixtures" / "providers"


@pytest.mark.parametrize(
    ("url", "provider", "key"),
    [
        ("https://boards.greenhouse.io/AcmeGames", "greenhouse", "greenhouse:acmegames"),
        ("https://jobs.lever.co/AcmeGames", "lever", "lever:acmegames"),
        ("https://jobs.eu.lever.co/AcmeGames", "lever", "lever:eu:acmegames"),
        ("https://jobs.ashbyhq.com/AcmeGames", "ashby", "ashby:acmegames"),
        (
            "https://careers.smartrecruiters.com/AcmeGames",
            "smartrecruiters",
            "smartrecruiters:acmegames",
        ),
        (
            "https://jobs.smartrecruiters.com/AcmeGames/123-software-engineer",
            "smartrecruiters",
            "smartrecruiters:acmegames",
        ),
    ],
)
def test_structured_provider_urls_are_detected(url: str, provider: str, key: str) -> None:
    detection = detect_provider(url)

    assert detection.provider == provider
    assert detection.canonical_source_key == key
    assert detection.status == "supported"


@pytest.mark.parametrize(
    "fixture", ["greenhouse.html", "lever.html", "ashby.html", "smartrecruiters.html"]
)
def test_embedded_provider_links_are_detected_from_fixtures(fixture: str) -> None:
    html = (FIXTURES / fixture).read_text()

    assert detect_provider("https://acme.example/careers", html).provider != "unsupported"


def test_eu_lever_source_selects_the_documented_eu_api_region() -> None:
    detection = detect_provider("https://jobs.eu.lever.co/AcmeGames")

    assert detection.provider_config == {"site": "AcmeGames", "region": "eu"}


def test_embedded_eu_lever_link_keeps_its_region() -> None:
    html = '<a href="https://jobs.eu.lever.co/AcmeGames/job-id">Engineer</a>'

    detection = detect_provider("https://acme.example/careers", html)

    assert detection.provider == "lever"
    assert detection.canonical_source_key == "lever:eu:acmegames"
    assert detection.provider_config == {"site": "AcmeGames", "region": "eu"}


def test_generic_fixture_is_pending_not_silently_supported() -> None:
    html = (FIXTURES / "generic.html").read_text()
    detection = detect_provider("https://acme.example/careers", html)

    assert detection.provider == "generic_html"
    assert detection.status == "pending_resolution"


def test_unknown_site_is_honestly_unsupported_without_html_evidence() -> None:
    detection = detect_provider("https://acme.example/careers")

    assert detection.provider == "unsupported"
    assert detection.status == "unsupported"


def test_saved_unknown_site_can_enter_bounded_generic_resolution() -> None:
    detection = detect_provider("https://acme.example/careers", allow_pending_generic=True)

    assert detection.provider == "generic_html"
    assert detection.status == "pending_resolution"
