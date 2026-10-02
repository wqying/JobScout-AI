from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class DiscoveryCreate(BaseModel):
    query: str = Field(min_length=3, max_length=120)
    country: Literal["US"] = "US"
    limit: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def normalize_whitespace(self) -> "DiscoveryCreate":
        self.query = " ".join(self.query.split())
        return self


class DiscoveryRunResponse(BaseModel):
    id: UUID
    query: str
    status: Literal["queued", "running", "succeeded", "failed"]
    cached: bool
    result_count: int = 0
    error_code: str | None = None
    estimated_cost_usd: float = 0
    created_at: datetime
    completed_at: datetime | None = None


class DiscoveryResearchMoreRequest(BaseModel):
    acknowledge_additional_api_usage: Literal[True]


class DiscoveryListResponse(BaseModel):
    items: list[DiscoveryRunResponse]
    next_cursor: UUID | None = None


class DiscoveryResultResponse(BaseModel):
    id: UUID
    company_id: UUID
    company_name: str
    official_website_url: str | None
    careers_url: str | None
    research_source_status: Literal["matched", "unmatched"]
    internship_research_reported: bool
    current_openings_count: int
    explanation: str
    historical_h1b_status: Literal["historical_records", "no_records", "unresolved"]
    certified_h1b_cases: int
    loaded_fiscal_years: list[int]
    careers_url_status: Literal["research_linked", "page_checked", "not_found", "rejected"]
    careers_url_reason: str
    monitoring_support: Literal[
        "structured",
        "generic_verified",
        "generic_pending",
        "unsupported",
    ]
    is_hidden: bool
    is_saved: bool


class DiscoveryResultsResponse(BaseModel):
    items: list[DiscoveryResultResponse]
    total: int
    offset: int
    limit: int
    has_more: bool


class DiscoveryResultSelection(BaseModel):
    result_ids: list[UUID] = Field(min_length=1, max_length=20)


class DiscoverySaveResponse(BaseModel):
    saved_company_ids: list[UUID]
