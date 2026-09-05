from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import database_session
from app.companies.schemas import (
    CompanyListResponse,
    CompanyPreview,
    CompanyResolutionResponse,
    CompanyResolveRequest,
    EvidenceResponse,
    JobListResponse,
    SaveCompanyRequest,
    SavedCompanyUpdate,
)
from app.companies.service import CompanyService

router = APIRouter(tags=["companies"])
Session = Annotated[AsyncSession, Depends(database_session)]


@router.get("/companies", response_model=CompanyListResponse)
async def list_companies(
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: UUID | None = None,
) -> CompanyListResponse:
    return await CompanyService(session).list_saved(limit=limit, cursor=cursor)


@router.get("/companies/{company_id}", response_model=CompanyPreview)
async def get_company(company_id: UUID, session: Session) -> CompanyPreview:
    return await CompanyService(session).get(company_id)


@router.get("/companies/{company_id}/evidence", response_model=list[EvidenceResponse])
async def get_company_evidence(company_id: UUID, session: Session) -> list[EvidenceResponse]:
    evidence = await CompanyService(session).evidence(company_id)
    return [
        EvidenceResponse(
            id=item.id,
            evidence_type=item.evidence_type,
            claim=item.claim,
            status=item.status,
            source_url=item.source_url,
            source_title=item.source_title,
            exact_quote=item.exact_quote,
            source_domain=item.source_domain,
            is_official_source=item.is_official_source,
            observed_at=item.observed_at,
            confidence=float(item.confidence),
        )
        for item in evidence
    ]


@router.get("/companies/{company_id}/jobs", response_model=JobListResponse)
async def get_company_jobs(company_id: UUID, session: Session) -> JobListResponse:
    jobs = await CompanyService(session).jobs(company_id)
    return JobListResponse(items=jobs)


@router.post("/companies/resolve", response_model=CompanyResolutionResponse)
async def resolve_company(
    payload: CompanyResolveRequest, session: Session
) -> CompanyResolutionResponse:
    return await CompanyService(session).resolve(
        payload.query,
        careers_url=payload.careers_url,
    )


@router.post("/companies/{company_id}/save", response_model=CompanyPreview)
async def save_company(
    company_id: UUID, payload: SaveCompanyRequest, session: Session
) -> CompanyPreview:
    return await CompanyService(session).save(company_id, payload)


@router.patch("/saved-companies/{saved_company_id}", response_model=CompanyPreview)
async def update_saved_company(
    saved_company_id: UUID, payload: SavedCompanyUpdate, session: Session
) -> CompanyPreview:
    return await CompanyService(session).update_saved(saved_company_id, payload)


@router.delete("/saved-companies/{saved_company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_company(saved_company_id: UUID, session: Session) -> Response:
    await CompanyService(session).remove_saved(saved_company_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
