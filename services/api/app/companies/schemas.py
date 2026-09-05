from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class CompanyResolveRequest(BaseModel):
    query: str = Field(min_length=2, max_length=120)
    careers_url: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def clean_values(self) -> "CompanyResolveRequest":
        self.query = " ".join(self.query.split())
        self.careers_url = self.careers_url.strip()
        return self


class CareerSourceResponse(BaseModel):
    id: UUID
    provider: str
    careers_url: str
    status: str
    last_success_at: datetime | None
    next_poll_at: datetime | None
    consecutive_failures: int
    last_error_code: str | None


class SponsorshipSummary(BaseModel):
    status: Literal["unresolved", "no_records", "historical_records"]
    certified_cases: int = 0
    certified_workers: int = 0
    loaded_fiscal_years: list[int] = Field(default_factory=list)
    explanation: str


class CompanyPreview(BaseModel):
    id: UUID
    canonical_name: str
    verification_status: str
    is_saved: bool
    saved_company_id: UUID | None = None
    saved_status: str | None = None
    notify_current_jobs: bool | None = None
    baseline_completed: bool | None = None
    sources: list[CareerSourceResponse] = Field(default_factory=list)
    sponsorship: SponsorshipSummary
    warning: str | None = None


class CompanyResolutionResponse(BaseModel):
    resolution: Literal["existing", "proposal", "ambiguous"]
    companies: list[CompanyPreview] = Field(default_factory=list)
    message: str


class SaveCompanyRequest(BaseModel):
    notify_current_jobs: bool = False
    legal_entity_name: str | None = Field(default=None, min_length=2, max_length=200)
    confirm_legal_entity: bool = False

    @model_validator(mode="after")
    def require_confirmed_legal_name(self) -> "SaveCompanyRequest":
        if self.confirm_legal_entity and not self.legal_entity_name:
            raise ValueError("A legal entity name is required when confirming the mapping")
        if self.legal_entity_name:
            self.legal_entity_name = " ".join(self.legal_entity_name.split())
        return self


class SavedCompanyUpdate(BaseModel):
    status: Literal["active", "paused"] | None = None
    notify_current_jobs: bool | None = None


class CompanyListResponse(BaseModel):
    items: list[CompanyPreview]
    next_cursor: UUID | None = None


class EvidenceResponse(BaseModel):
    id: UUID
    evidence_type: str
    claim: str
    status: str
    source_url: str
    source_title: str | None
    exact_quote: str | None
    source_domain: str
    is_official_source: bool
    observed_at: datetime
    confidence: float


class JobListResponse(BaseModel):
    items: list["JobResponse"] = Field(default_factory=list)
    next_cursor: UUID | None = None


class JobResponse(BaseModel):
    id: UUID
    company_id: UUID
    company_name: str | None = None
    title: str
    location_text: str | None
    department: str | None
    employment_type: str | None
    role_type: str
    apply_url: str
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None
