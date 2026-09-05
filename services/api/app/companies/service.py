from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.companies.normalization import (
    domain_from_url,
    normalize_employer_name,
    normalize_https_url,
    normalize_query,
)
from app.companies.schemas import (
    CareerSourceResponse,
    CompanyListResponse,
    CompanyPreview,
    CompanyResolutionResponse,
    JobResponse,
    SaveCompanyRequest,
    SavedCompanyUpdate,
    SponsorshipSummary,
)
from app.db.models import (
    CareerSource,
    Company,
    CompanyAlias,
    CompanyEvidence,
    CompanyLegalEntity,
    Job,
    LcaEmployerYearlyStat,
    SavedCompany,
)
from app.monitoring.polling import is_permanent_source_error
from app.monitoring.provider_detection import detect_provider


class CompanyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(
        self,
        query: str,
        careers_url: str,
    ) -> CompanyResolutionResponse:
        normalized_careers_url = normalize_https_url(careers_url)
        detection = detect_provider(normalized_careers_url, allow_pending_generic=True)
        existing_source = await self.session.scalar(
            select(CareerSource).where(
                CareerSource.canonical_source_key == detection.canonical_source_key
            )
        )
        if existing_source is not None:
            existing = await self._get_company(existing_source.company_id)
            existing_source.careers_url = normalized_careers_url
            await self.session.commit()
            return CompanyResolutionResponse(
                resolution="existing",
                companies=[await self._preview(existing)],
                message="This careers source already exists in your local catalog.",
            )

        normalized_name = normalize_query(query)
        matches = list(
            (
                await self.session.scalars(
                    select(Company)
                    .outerjoin(CompanyAlias, CompanyAlias.company_id == Company.id)
                    .where(
                        or_(
                            Company.normalized_name == normalized_name,
                            CompanyAlias.normalized_alias == normalized_name,
                        )
                    )
                    .distinct()
                    .limit(5)
                )
            ).all()
        )
        if len(matches) > 1:
            return CompanyResolutionResponse(
                resolution="ambiguous",
                companies=[await self._preview(company) for company in matches],
                message="More than one local company matches this name. Refine the company name.",
            )
        if len(matches) == 1:
            existing = matches[0]
            await self._ensure_source(existing, normalized_careers_url)
            await self.session.commit()
            return CompanyResolutionResponse(
                resolution="existing",
                companies=[await self._preview(existing)],
                message="This company already exists; the careers page is ready for review.",
            )

        canonical_name = query
        company = Company(
            canonical_name=canonical_name,
            normalized_name=normalized_name,
            official_domain=None,
            official_website_url=None,
            headquarters_country="US",
            verification_status="proposed",
        )
        self.session.add(company)
        await self.session.flush()
        self.session.add(
            CompanyAlias(
                company_id=company.id,
                alias=canonical_name,
                normalized_alias=normalized_name,
                alias_type="brand",
                source_url=normalized_careers_url,
                confidence=Decimal("1.000"),
            )
        )
        await self._ensure_source(company, normalized_careers_url)
        await self.session.commit()
        await self.session.refresh(company)
        return CompanyResolutionResponse(
            resolution="proposal",
            companies=[await self._preview(company)],
            message="Review the careers page and monitoring status before saving this company.",
        )

    async def save(self, company_id: UUID, payload: SaveCompanyRequest) -> CompanyPreview:
        company = await self._get_company(company_id)
        now = datetime.now(UTC)
        saved = await self.session.scalar(
            select(SavedCompany).where(SavedCompany.company_id == company.id)
        )
        if saved is None:
            saved = SavedCompany(
                company_id=company.id,
                status="active",
                notify_current_jobs=payload.notify_current_jobs,
            )
            self.session.add(saved)
        else:
            saved.status = "active"
            saved.notify_current_jobs = payload.notify_current_jobs
            saved.updated_at = now

        company.verification_status = "verified"
        company.verified_at = now
        company.updated_at = now
        sources = list(
            (
                await self.session.scalars(
                    select(CareerSource).where(CareerSource.company_id == company.id)
                )
            ).all()
        )
        for source in sources:
            self._activate_source(source, now)
        await self._ensure_confirmation_evidence(company, now)
        await self._ensure_exact_legal_mapping(company, now)

        if payload.confirm_legal_entity and payload.legal_entity_name:
            normalized_legal_name = normalize_employer_name(payload.legal_entity_name)
            existing_mapping = await self.session.scalar(
                select(CompanyLegalEntity).where(
                    CompanyLegalEntity.company_id == company.id,
                    CompanyLegalEntity.normalized_legal_name == normalized_legal_name,
                )
            )
            if existing_mapping is None:
                self.session.add(
                    CompanyLegalEntity(
                        company_id=company.id,
                        legal_name=payload.legal_entity_name,
                        normalized_legal_name=normalized_legal_name,
                        match_method="owner_verified",
                        confidence=Decimal("1.000"),
                        evidence_url=company.official_website_url,
                        verified_at=now,
                    )
                )

        await self.session.commit()
        return await self._preview(company)

    async def list_saved(self, limit: int = 50, cursor: UUID | None = None) -> CompanyListResponse:
        statement = (
            select(Company)
            .join(SavedCompany, SavedCompany.company_id == Company.id)
            .order_by(Company.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(Company.id > cursor)
        companies = list((await self.session.scalars(statement)).all())
        next_cursor = companies[limit - 1].id if len(companies) > limit else None
        return CompanyListResponse(
            items=[await self._preview(company) for company in companies[:limit]],
            next_cursor=next_cursor,
        )

    async def get(self, company_id: UUID) -> CompanyPreview:
        return await self._preview(await self._get_company(company_id))

    async def evidence(self, company_id: UUID) -> list[CompanyEvidence]:
        await self._get_company(company_id)
        return list(
            (
                await self.session.scalars(
                    select(CompanyEvidence)
                    .where(CompanyEvidence.company_id == company_id)
                    .order_by(CompanyEvidence.observed_at.desc())
                )
            ).all()
        )

    async def jobs(self, company_id: UUID, limit: int = 50) -> list[JobResponse]:
        await self._get_company(company_id)
        jobs = list(
            (
                await self.session.scalars(
                    select(Job)
                    .where(Job.company_id == company_id)
                    .order_by(Job.first_seen_at.desc())
                    .limit(limit)
                )
            ).all()
        )
        company = await self._get_company(company_id)
        return [job_response(job, company.canonical_name) for job in jobs]

    async def update_saved(
        self, saved_company_id: UUID, payload: SavedCompanyUpdate
    ) -> CompanyPreview:
        saved = await self.session.get(SavedCompany, saved_company_id)
        if saved is None:
            raise AppError("SAVED_COMPANY_NOT_FOUND", "Saved company not found.", status_code=404)
        if payload.status is not None:
            saved.status = payload.status
            sources = list(
                (
                    await self.session.scalars(
                        select(CareerSource).where(CareerSource.company_id == saved.company_id)
                    )
                ).all()
            )
            for source in sources:
                if payload.status == "paused":
                    if source.status in {"pending_resolution", "supported", "degraded"}:
                        source.status = "paused"
                        source.next_poll_at = None
                        source.lease_owner = None
                        source.lease_expires_at = None
                else:
                    self._activate_source(source, datetime.now(UTC))
        if payload.notify_current_jobs is not None:
            saved.notify_current_jobs = payload.notify_current_jobs
        saved.updated_at = datetime.now(UTC)
        await self.session.commit()
        return await self.get(saved.company_id)

    async def remove_saved(self, saved_company_id: UUID) -> None:
        saved = await self.session.get(SavedCompany, saved_company_id)
        if saved is None:
            raise AppError("SAVED_COMPANY_NOT_FOUND", "Saved company not found.", status_code=404)
        sources = list(
            (
                await self.session.scalars(
                    select(CareerSource).where(CareerSource.company_id == saved.company_id)
                )
            ).all()
        )
        for source in sources:
            if source.status in {"pending_resolution", "supported", "degraded"}:
                source.status = "paused"
                source.next_poll_at = None
                source.lease_owner = None
                source.lease_expires_at = None
        await self.session.delete(saved)
        await self.session.commit()

    @staticmethod
    def _activate_source(source: CareerSource, now: datetime) -> None:
        """Schedule only sources that support deterministic automatic polling."""

        if source.provider == "email_alert" or source.status == "assisted":
            source.status = "assisted"
            source.next_poll_at = None
            return
        if source.status == "retired":
            source.next_poll_at = None
            return
        if source.provider == "unsupported" or source.status == "unsupported":
            source.status = "unsupported"
            source.next_poll_at = None
            return
        if source.last_error_code == "NO_SERVER_RENDERED_JOB_LINKS" or (
            source.last_error_code is not None and is_permanent_source_error(source.last_error_code)
        ):
            source.status = "unsupported"
            source.next_poll_at = None
            return
        if source.provider == "generic_html" and source.last_success_at is None:
            source.status = "pending_resolution"
        else:
            source.status = "supported"
        source.next_poll_at = now

    async def _ensure_source(self, company: Company, careers_url: str) -> CareerSource:
        normalized_url = normalize_https_url(careers_url)
        detection = detect_provider(normalized_url, allow_pending_generic=True)
        existing = await self.session.scalar(
            select(CareerSource).where(
                CareerSource.canonical_source_key == detection.canonical_source_key
            )
        )
        if existing is not None:
            if existing.company_id != company.id:
                raise AppError(
                    "SOURCE_ALREADY_OWNED",
                    "This canonical careers source is already attached to another company.",
                    status_code=409,
                )
            return existing
        source = CareerSource(
            company_id=company.id,
            provider=detection.provider,
            careers_url=normalized_url,
            canonical_source_key=detection.canonical_source_key,
            provider_config=detection.provider_config,
            status=detection.status,
        )
        self.session.add(source)
        await self.session.flush()
        return source

    async def _ensure_exact_legal_mapping(self, company: Company, now: datetime) -> None:
        normalized_name = normalize_employer_name(company.canonical_name)
        matching_stat = await self.session.scalar(
            select(LcaEmployerYearlyStat)
            .where(LcaEmployerYearlyStat.normalized_legal_employer_name == normalized_name)
            .order_by(LcaEmployerYearlyStat.fiscal_year.desc())
            .limit(1)
        )
        if matching_stat is None:
            return

        mapping = await self.session.scalar(
            select(CompanyLegalEntity).where(
                CompanyLegalEntity.company_id == company.id,
                CompanyLegalEntity.normalized_legal_name == normalized_name,
            )
        )
        if mapping is None:
            self.session.add(
                CompanyLegalEntity(
                    company_id=company.id,
                    legal_name=matching_stat.legal_employer_name,
                    normalized_legal_name=normalized_name,
                    match_method="exact",
                    confidence=Decimal("0.950"),
                    evidence_url=company.official_website_url,
                    verified_at=now,
                )
            )
            return

        mapping.legal_name = matching_stat.legal_employer_name
        mapping.match_method = "exact"
        mapping.confidence = Decimal("0.950")
        mapping.evidence_url = company.official_website_url
        mapping.verified_at = now

    async def _ensure_confirmation_evidence(self, company: Company, now: datetime) -> None:
        sources = list(
            (
                await self.session.scalars(
                    select(CareerSource).where(CareerSource.company_id == company.id)
                )
            ).all()
        )
        evidence_specs = [
            (
                "careers_page",
                "The local owner confirmed this careers URL for monitoring evaluation.",
                source.careers_url,
            )
            for source in sources
            if source.provider != "email_alert"
        ]
        for evidence_type, claim, source_url in evidence_specs:
            exists = await self.session.scalar(
                select(CompanyEvidence.id).where(
                    CompanyEvidence.company_id == company.id,
                    CompanyEvidence.evidence_type == evidence_type,
                    CompanyEvidence.source_url == source_url,
                )
            )
            if exists is None:
                self.session.add(
                    CompanyEvidence(
                        company_id=company.id,
                        evidence_type=evidence_type,
                        claim=claim,
                        status="supports",
                        source_url=source_url,
                        source_domain=domain_from_url(source_url),
                        is_official_source=True,
                        observed_at=now,
                        confidence=Decimal("1.000"),
                    )
                )

    async def _preview(self, company: Company) -> CompanyPreview:
        saved = await self.session.scalar(
            select(SavedCompany).where(SavedCompany.company_id == company.id)
        )
        sources = list(
            (
                await self.session.scalars(
                    select(CareerSource)
                    .where(CareerSource.company_id == company.id)
                    .order_by(CareerSource.created_at)
                )
            ).all()
        )
        warning = None
        automatic_statuses = {"pending_resolution", "supported", "degraded"}
        if not any(source.status in automatic_statuses for source in sources):
            warning = (
                "Automatic monitoring is not currently active for this company. "
                "Use a supported replacement source, imported employer alerts, or review reminders."
            )
        elif any(source.status == "pending_resolution" for source in sources):
            warning = "Saved, but this careers page still needs monitoring verification."
        return CompanyPreview(
            id=company.id,
            canonical_name=company.canonical_name,
            verification_status=company.verification_status,
            is_saved=saved is not None,
            saved_company_id=saved.id if saved else None,
            saved_status=saved.status if saved else None,
            notify_current_jobs=saved.notify_current_jobs if saved else None,
            baseline_completed=saved.baseline_completed_at is not None if saved else None,
            sources=[
                CareerSourceResponse(
                    id=source.id,
                    provider=source.provider,
                    careers_url=source.careers_url,
                    status=source.status,
                    last_success_at=source.last_success_at,
                    next_poll_at=source.next_poll_at,
                    consecutive_failures=source.consecutive_failures,
                    last_error_code=source.last_error_code,
                )
                for source in sources
            ],
            sponsorship=await self._sponsorship(company.id),
            warning=warning,
        )

    async def _sponsorship(self, company_id: UUID) -> SponsorshipSummary:
        allowed_names = list(
            (
                await self.session.scalars(
                    select(CompanyLegalEntity.normalized_legal_name).where(
                        CompanyLegalEntity.company_id == company_id,
                        or_(
                            CompanyLegalEntity.match_method == "owner_verified",
                            (
                                (CompanyLegalEntity.match_method == "exact")
                                & (CompanyLegalEntity.confidence >= Decimal("0.950"))
                            ),
                        ),
                    )
                )
            ).all()
        )
        if not allowed_names:
            return SponsorshipSummary(
                status="unresolved",
                explanation=(
                    "No confident employer match was found in the imported H-1B sponsorship data."
                ),
            )
        row = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(LcaEmployerYearlyStat.certified_cases), 0),
                    func.coalesce(func.sum(LcaEmployerYearlyStat.certified_workers), 0),
                    func.array_agg(func.distinct(LcaEmployerYearlyStat.fiscal_year)),
                ).where(LcaEmployerYearlyStat.normalized_legal_employer_name.in_(allowed_names))
            )
        ).one()
        years = sorted(year for year in (row[2] or []) if year is not None)
        if not years:
            return SponsorshipSummary(
                status="no_records",
                explanation=(
                    "No matching records were found in the imported H-1B sponsorship data."
                ),
            )
        return SponsorshipSummary(
            status="historical_records",
            certified_cases=int(row[0]),
            certified_workers=int(row[1]),
            loaded_fiscal_years=years,
            explanation=(
                "Historical certified H-1B sponsorship records; this does not guarantee "
                "sponsorship for a specific role."
            ),
        )

    async def _get_company(self, company_id: UUID) -> Company:
        company = await self.session.get(Company, company_id)
        if company is None:
            raise AppError("COMPANY_NOT_FOUND", "Company not found.", status_code=404)
        return company


def job_response(job: Job, company_name: str | None = None) -> JobResponse:
    return JobResponse(
        id=job.id,
        company_id=job.company_id,
        company_name=company_name,
        title=job.title,
        location_text=job.location_text,
        department=job.department,
        employment_type=job.employment_type,
        role_type=job.role_type,
        apply_url=job.apply_url,
        status=job.status,
        first_seen_at=job.first_seen_at,
        last_seen_at=job.last_seen_at,
        closed_at=job.closed_at,
    )
