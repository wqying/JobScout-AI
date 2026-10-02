from app.ai.schemas.discovery import CompanyProposal
from app.ai.workflows.company_discovery import _merge_candidates
from app.discovery.validation import validate_proposal


def _proposal(
    name: str,
    website: str,
    *,
    aliases: list[str] | None = None,
    legal_entities: list[str] | None = None,
    internship: bool = False,
) -> CompanyProposal:
    return CompanyProposal.model_validate(
        {
            "canonical_name": name,
            "aliases": aliases or [],
            "proposed_legal_entities": legal_entities or [],
            "official_website_url": website,
            "official_careers_source_id": None,
            "industry_explanation": f"{name} is a company returned by the research model.",
            "internship_research_reported": internship,
            "source_references": [],
        }
    )


def test_shared_profile_urls_use_company_names_as_distinct_identities() -> None:
    first = validate_proposal(_proposal("Alpha Labs", "https://linkedin.com/company/alpha"), [])
    second = validate_proposal(_proposal("Beta Labs", "https://linkedin.com/company/beta"), [])

    assert first.identity_key == "name:alpha labs"
    assert second.identity_key == "name:beta labs"
    assert first.identity_key != second.identity_key


def test_duplicate_candidates_keep_first_presentation_and_union_facts() -> None:
    first = validate_proposal(
        _proposal(
            "Acme",
            "https://acme.com",
            aliases=["Acme Games"],
            legal_entities=["Acme Inc"],
        ),
        [],
    )
    later = validate_proposal(
        _proposal(
            "ACME",
            "https://acme.com/about",
            aliases=["Acme Interactive"],
            legal_entities=["Acme Holdings LLC"],
            internship=True,
        ),
        [],
    )

    merged = _merge_candidates(first, later)

    assert merged.proposal.canonical_name == "Acme"
    assert merged.proposal.industry_explanation == first.proposal.industry_explanation
    assert merged.proposal.aliases == ["Acme Games", "Acme Interactive"]
    assert merged.proposal.proposed_legal_entities == ["Acme Inc", "Acme Holdings LLC"]
    assert merged.proposal.internship_research_reported is True
