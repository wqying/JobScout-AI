import json
from pathlib import Path

import pytest

from app.ai.schemas.discovery import CompanyProposal, NormalizedDiscovery
from app.discovery.validation import validate_proposal

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ai"
DISCOVERY_FIXTURE = FIXTURES / "gaming_discovery_v1.json"
MANIFEST_FIXTURE = FIXTURES / "gaming_discovery_manifest_v1.json"


def _manifest() -> list[dict[str, str | None]]:
    return json.loads(MANIFEST_FIXTURE.read_text())


def _proposal(
    *,
    careers_source_id: str | None = "source_2",
    careers_claims: list[str] | None = None,
) -> CompanyProposal:
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
                    "supports_claims": careers_claims or ["careers_page"],
                },
            ],
        }
    )


def test_source_ids_resolve_manifest_evidence_and_canonicalize_structured_boards() -> None:
    discovery = NormalizedDiscovery.model_validate_json(DISCOVERY_FIXTURE.read_text())

    valid = validate_proposal(discovery.companies[0], _manifest())

    assert valid.official_domain == "acme-games.com"
    assert valid.careers.url == "https://jobs.lever.co/acme-games"
    assert valid.careers.url_status == "research_linked"
    assert valid.careers.monitoring_support == "structured"
    assert valid.research_source_status == "matched"
    assert valid.careers.source_id == "source_3"


def test_smartrecruiters_manifest_source_is_structured_and_canonicalized() -> None:
    manifest = [
        {
            "source_id": "source_1",
            "url": "https://acme.com/about",
            "title": "About",
        },
        {
            "source_id": "source_2",
            "url": "https://jobs.smartrecruiters.com/AcmeGames/101-software-engineer",
            "title": "Software Engineer",
        },
    ]

    valid = validate_proposal(_proposal(), manifest)

    assert valid.careers.url == "https://careers.smartrecruiters.com/AcmeGames"
    assert valid.careers.url_status == "research_linked"
    assert valid.careers.monitoring_support == "structured"


def test_unmatched_manifest_source_does_not_remove_company() -> None:
    discovery = NormalizedDiscovery.model_validate_json(DISCOVERY_FIXTURE.read_text())

    candidate = validate_proposal(discovery.companies[2], _manifest())

    assert candidate.proposal.canonical_name == "Unsourced Studio"
    assert candidate.research_source_status == "unmatched"


def test_official_domain_careers_source_is_verified_but_generic_monitoring_is_pending() -> None:
    manifest = [
        {
            "source_id": "source_1",
            "url": "https://acme.com/about",
            "title": "About",
        },
        {
            "source_id": "source_2",
            "url": "https://acme.com/careers/?utm_source=openai#jobs",
            "title": "Careers",
        },
    ]

    valid = validate_proposal(_proposal(), manifest)

    assert valid.careers.url == "https://acme.com/careers"
    assert valid.careers.url_status == "research_linked"
    assert valid.careers.reason == "CAREERS_SOURCE_OFFICIAL_DOMAIN_VERIFIED"
    assert valid.careers.monitoring_support == "generic_pending"


def test_missing_manifest_source_is_rejected_with_reason_without_dropping_company() -> None:
    manifest = [
        {
            "source_id": "source_1",
            "url": "https://acme.com/about",
            "title": "About",
        }
    ]

    valid = validate_proposal(_proposal(), manifest)

    assert valid.careers.url is None
    assert valid.careers.url_status == "rejected"
    assert valid.careers.reason == "CAREERS_SOURCE_NOT_IN_MANIFEST"
    assert valid.careers.monitoring_support == "unsupported"


def test_canonical_duplicate_manifest_urls_keep_each_source_id_resolvable() -> None:
    manifest = [
        {
            "source_id": "source_1",
            "url": "https://acme.com/about",
            "title": "About",
        },
        {
            "source_id": "source_2",
            "url": "https://acme.com/careers?utm_source=openai",
            "title": "Careers from annotation",
        },
        {
            "source_id": "source_3",
            "url": "https://acme.com/careers",
            "title": "Careers from search action",
        },
    ]
    proposal = _proposal(careers_source_id="source_3")
    proposal.source_references[1].source_id = "source_3"

    valid = validate_proposal(proposal, manifest)

    assert valid.careers.url == "https://acme.com/careers"
    assert valid.careers.source_id == "source_3"


def test_malformed_manifest_url_is_ignored_instead_of_failing_the_run() -> None:
    manifest = [
        {
            "source_id": "source_1",
            "url": "https://acme.com/about",
            "title": "About",
        },
        {
            "source_id": "source_2",
            "url": "https://acme.com:invalid/careers",
            "title": "Malformed",
        },
    ]

    valid = validate_proposal(_proposal(), manifest)

    assert valid.careers.url_status == "rejected"
    assert valid.careers.reason == "CAREERS_SOURCE_NOT_IN_MANIFEST"


def test_untagged_source_cannot_be_used_as_careers_evidence() -> None:
    manifest = [
        {
            "source_id": "source_1",
            "url": "https://acme.com/about",
            "title": "About",
        },
        {
            "source_id": "source_2",
            "url": "https://acme.com/careers",
            "title": "Careers",
        },
    ]

    valid = validate_proposal(_proposal(careers_claims=["internship"]), manifest)

    assert valid.careers.url is None
    assert valid.careers.reason == "CAREERS_SOURCE_NOT_TAGGED"


def test_unrelated_non_ats_domain_cannot_be_used_as_careers_evidence() -> None:
    manifest = [
        {
            "source_id": "source_1",
            "url": "https://acme.com/about",
            "title": "About",
        },
        {
            "source_id": "source_2",
            "url": "https://unrelated.com/careers",
            "title": "Wrong careers",
        },
    ]

    valid = validate_proposal(_proposal(), manifest)

    assert valid.careers.url is None
    assert valid.careers.reason == "CAREERS_SOURCE_DOMAIN_MISMATCH"


def test_no_selected_source_is_an_explicit_not_found_state() -> None:
    manifest = [
        {
            "source_id": "source_1",
            "url": "https://acme.com/about",
            "title": "About",
        }
    ]

    valid = validate_proposal(_proposal(careers_source_id=None), manifest)

    assert valid.careers.url_status == "not_found"
    assert valid.careers.reason == "CAREERS_SOURCE_NOT_SELECTED"


def test_fixture_is_strict_json_schema_compatible() -> None:
    payload = json.loads(DISCOVERY_FIXTURE.read_text())
    discovery = NormalizedDiscovery.model_validate(payload)

    assert discovery.interpretation.slug == "gaming-companies"
    assert discovery.companies[0].official_careers_source_id == "source_3"


@pytest.mark.parametrize(
    "website",
    [
        "http://acme.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://internal.corp",
        "https://acme.example",
        "https://acme.test",
        "https://[not-an-ipv6-address]/careers",
        "https://acme.com:invalid/careers",
        "https://linkedin.com/company/acme",
        "https://github.com/acme",
        "https://boards.greenhouse.io/acme",
        "https://greenhouse.io/acme",
    ],
)
def test_unsuitable_website_is_removed_without_dropping_company(website: str) -> None:
    candidate = validate_proposal(
        _proposal().model_copy(update={"official_website_url": website}),
        [],
    )

    assert candidate.website_url is None
    assert candidate.official_domain is None
    assert candidate.identity_key == "name:acme"
    assert candidate.research_source_status == "unmatched"
