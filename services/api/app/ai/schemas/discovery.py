from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class StrictAIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceReference(StrictAIModel):
    source_id: str = Field(pattern=r"^source_[1-9][0-9]*$")
    source_type: Literal[
        "official_company",
        "official_government",
        "reputable_directory",
        "search_lead",
    ]
    supports_claims: list[str]


class IndustryInterpretation(StrictAIModel):
    normalized_label: str = Field(min_length=2, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=120)
    included_segments: list[str]
    excluded_segments: list[str]
    target_role_families: list[str]
    country_code: Literal["US"]


class CompanyProposal(StrictAIModel):
    canonical_name: str = Field(min_length=2, max_length=160)
    aliases: list[str]
    proposed_legal_entities: list[str]
    official_website_url: HttpUrl
    official_careers_source_id: str | None = Field(
        pattern=r"^source_[1-9][0-9]*$",
    )
    industry_relevance: int = Field(ge=0, le=100)
    industry_explanation: str = Field(min_length=10, max_length=1200)
    has_internship_evidence: bool
    source_references: list[SourceReference]
    unresolved_questions: list[str]


class NormalizedDiscovery(StrictAIModel):
    interpretation: IndustryInterpretation
    companies: list[CompanyProposal]
