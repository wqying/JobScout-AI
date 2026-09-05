from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace as replace_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import (
    AIUsage,
    DiscoveryAIClient,
    InvalidStructuredOutput,
    NormalizationResponse,
)
from app.ai.pricing import estimate_cost_usd
from app.ai.schemas.discovery import IndustryInterpretation
from app.companies.normalization import normalize_employer_name, normalize_query
from app.core.config import Settings
from app.db.models import (
    AiRun,
    CareerSource,
    Company,
    CompanyAlias,
    CompanyEvidence,
    CompanyIndustry,
    CompanyLegalEntity,
    DiscoveryResult,
    DiscoveryRun,
    ImmigrationDatasetImport,
    IndustryQuery,
    LcaEmployerYearlyStat,
)
from app.discovery.careers_resolver import (
    RESOLVER_VERSION,
    CareersPageResolver,
    needs_resolution,
    openings_score,
    provisional_rank_key,
    skipped_for_budget,
)
from app.discovery.failures import DiscoveryFailureService, discovery_error_code
from app.discovery.scoring import ScoreComponents, opportunity_score, sponsorship_score
from app.discovery.service import MAX_RESULTS_PER_RESEARCH_RUN, PROMPT_VERSION
from app.discovery.validation import ValidatedProposal, validate_proposal
from app.monitoring.http import HttpFetcher
from app.monitoring.provider_detection import detect_provider

SCHEMA_VERSION = "company-discovery-schema-v2"


@dataclass
class RankedCandidate:
    company: Company
    validated: ValidatedProposal
    components: ScoreComponents
    total: int
    historical_status: str
    certified_cases: int
    loaded_years: list[int]


class CompanyDiscoveryWorkflow:
    def __init__(
        self,
        session: AsyncSession,
        client: DiscoveryAIClient,
        settings: Settings,
        http: HttpFetcher | None = None,
    ) -> None:
        self.session = session
        self.client = client
        self.settings = settings
        # Optional by design: without a fetcher the bounded careers-page resolution in Section 13.7
        # is skipped and every candidate keeps its unresolved classification, so discovery still
        # runs with no outbound network access.
        self.http = http
        self.logger = structlog.get_logger("jobscout.discovery")

    async def execute(self, run_id: UUID) -> None:
        run = await self.session.scalar(
            select(DiscoveryRun).where(DiscoveryRun.id == run_id).with_for_update()
        )
        if run is None:
            return
        if run.status != "queued":
            return
        industry_query = await self.session.get(IndustryQuery, run.industry_query_id)
        if industry_query is None:
            await DiscoveryFailureService(self.session).mark_failed(
                run.id, "INDUSTRY_QUERY_MISSING"
            )
            return

        run.status = "running"
        industry_query.status = "running"
        await self.session.commit()
        self.logger.info("discovery_started", discovery_run_id=str(run.id))

        try:
            excluded_companies = await self._excluded_companies(run)
            research_trace = self._new_trace(
                run,
                step="research",
                model_id=self.client.research_model,
                redacted_input={
                    "query": run.raw_query,
                    "country": industry_query.country_code,
                    "excluded_company_count": len(excluded_companies),
                },
            )
            self.session.add(research_trace)
            await self.session.commit()

            research = await self.client.research(
                run.raw_query,
                industry_query.country_code,
                excluded_companies=excluded_companies,
            )
            self._complete_trace(
                research_trace,
                usage=research.usage,
                redacted_output={
                    "report_excerpt": research.report[:20_000],
                    "source_count": len(research.source_manifest),
                },
                source_manifest=research.source_manifest,
            )
            await self.session.commit()

            normalization_trace = self._new_trace(
                run,
                step="normalization",
                model_id=self.client.structured_model,
                parent_run_id=research_trace.id,
                redacted_input={
                    "report_characters": len(research.report),
                    "source_count": len(research.source_manifest),
                },
            )
            self.session.add(normalization_trace)
            await self.session.commit()

            normalized = await self._normalize_with_one_repair(
                normalization_trace,
                research.report,
                research.source_manifest,
            )
            if run.root_discovery_run_id is not None:
                root_run = await self.session.get(DiscoveryRun, run.root_discovery_run_id)
                root_query = (
                    await self.session.get(IndustryQuery, root_run.industry_query_id)
                    if root_run is not None and root_run.industry_query_id is not None
                    else None
                )
                if root_query is not None and root_query.interpretation_json:
                    normalized.discovery.interpretation = IndustryInterpretation.model_validate(
                        root_query.interpretation_json
                    )
            self._complete_trace(
                normalization_trace,
                usage=normalized.usage,
                redacted_output=normalized.raw_output,
                source_manifest=research.source_manifest,
            )
            await self.session.commit()

            candidates = await self._persist_candidates(
                run,
                industry_query,
                normalized,
                research.source_manifest,
                excluded_domains={item["official_domain"] for item in excluded_companies},
            )
            if not candidates and run.root_discovery_run_id is None:
                raise RuntimeError("No source-verified company candidates remained")
            await self._persist_ranked_results(run, candidates)
            finished = datetime.now(UTC)
            industry_query.interpretation_json = normalized.discovery.interpretation.model_dump(
                mode="json"
            )
            industry_query.status = "succeeded"
            industry_query.completed_at = finished
            run.status = "succeeded"
            run.completed_at = finished
            await self.session.commit()
            self.logger.info(
                "discovery_succeeded",
                discovery_run_id=str(run.id),
                result_count=min(len(candidates), MAX_RESULTS_PER_RESEARCH_RUN),
            )
        except Exception as exc:
            await self.session.rollback()
            await DiscoveryFailureService(self.session).mark_failed(
                run_id, discovery_error_code(exc)
            )
            self.logger.exception(
                "discovery_failed",
                discovery_run_id=str(run_id),
                error_code=discovery_error_code(exc),
            )

    async def _normalize_with_one_repair(
        self,
        trace: AiRun,
        report: str,
        source_manifest: list[dict[str, str | None]],
    ) -> NormalizationResponse:
        try:
            return await self.client.normalize(report, source_manifest)
        except InvalidStructuredOutput as first_error:
            trace.retry_count = 1
            repaired = await self.client.normalize(
                report,
                source_manifest,
                repair_feedback=str(first_error)[:1000],
            )
            return NormalizationResponse(
                discovery=repaired.discovery,
                raw_output=repaired.raw_output,
                usage=AIUsage(
                    input_tokens=first_error.usage.input_tokens + repaired.usage.input_tokens,
                    output_tokens=first_error.usage.output_tokens + repaired.usage.output_tokens,
                    web_search_calls=(
                        first_error.usage.web_search_calls + repaired.usage.web_search_calls
                    ),
                ),
            )

    async def _persist_candidates(
        self,
        run: DiscoveryRun,
        industry_query: IndustryQuery,
        normalized: NormalizationResponse,
        source_manifest: list[dict[str, str | None]],
        excluded_domains: set[str] | None = None,
    ) -> list[RankedCandidate]:
        excluded_domains = excluded_domains or set()
        validated: dict[str, ValidatedProposal] = {}
        for proposal in normalized.discovery.companies[:40]:
            try:
                item = validate_proposal(proposal, source_manifest)
            except ValueError as exc:
                self.logger.info(
                    "discovery_candidate_rejected",
                    discovery_run_id=str(run.id),
                    company_name=proposal.canonical_name,
                    reason=str(exc),
                )
                continue
            if item.official_domain in excluded_domains:
                self.logger.info(
                    "discovery_candidate_excluded",
                    discovery_run_id=str(run.id),
                    company_name=proposal.canonical_name,
                    official_domain=item.official_domain,
                )
                continue
            if item.careers.url_status != "evidence_verified":
                self.logger.info(
                    "discovery_careers_source_unavailable",
                    discovery_run_id=str(run.id),
                    company_name=proposal.canonical_name,
                    careers_url_status=item.careers.url_status,
                    reason=item.careers.reason,
                    source_id=item.careers.source_id,
                )
            prior = validated.get(item.official_domain)
            if (
                prior is None
                or item.proposal.industry_relevance > prior.proposal.industry_relevance
            ):
                validated[item.official_domain] = item

        latest_years = list(
            (
                await self.session.scalars(
                    select(ImmigrationDatasetImport.fiscal_year)
                    .where(ImmigrationDatasetImport.status == "succeeded")
                    .distinct()
                    .order_by(ImmigrationDatasetImport.fiscal_year.desc())
                    .limit(3)
                )
            ).all()
        )
        resolved = await self._resolve_careers_pages(run, list(validated.values()))
        candidates: list[RankedCandidate] = []
        for item, job_link_count in resolved:
            company = await self._upsert_company(item)
            await self._upsert_aliases(company, item)
            await self._upsert_source(company, item)
            historical_status, certified_cases, loaded_years = await self._historical_sponsorship(
                company, item, latest_years
            )
            await self._upsert_industry_and_evidence(
                company,
                item,
                normalized,
                historical_status,
                certified_cases,
                loaded_years,
                job_link_count,
            )
            components = ScoreComponents(
                industry=item.proposal.industry_relevance,
                sponsorship=sponsorship_score(
                    certified_cases, resolved=historical_status != "unresolved"
                ),
                internship=100 if item.internship_verified else 0,
                monitorability=item.monitorability_score,
                current_openings=openings_score(
                    job_link_count, self.settings.discovery_resolve_min_job_links
                ),
            )
            candidates.append(
                RankedCandidate(
                    company=company,
                    validated=item,
                    components=components,
                    total=opportunity_score(components),
                    historical_status=historical_status,
                    certified_cases=certified_cases,
                    loaded_years=loaded_years,
                )
            )
        return candidates

    async def _resolve_careers_pages(
        self,
        run: DiscoveryRun,
        items: list[ValidatedProposal],
    ) -> list[tuple[ValidatedProposal, int]]:
        """Verify cited careers pages for the candidates the owner will actually see.

        Returns each candidate paired with the number of job links the resolver counted for it.
        Candidates that were not resolved -- because the stage is disabled, the fetcher is absent,
        the budget ran out, or the page was unreachable -- keep the classification validation gave
        them, so this stage can only ever add evidence.
        """

        pending = [item for item in items if needs_resolution(item)]
        budget = self.settings.discovery_resolve_max_candidates
        if self.http is None or budget <= 0 or not pending:
            return [(item, 0) for item in items]

        selected = sorted(pending, key=provisional_rank_key)[:budget]
        selected_domains = {item.official_domain for item in selected}
        resolutions = await CareersPageResolver(
            self.http, self.settings, logger=self.logger
        ).resolve_all(selected)

        outcomes: list[tuple[ValidatedProposal, int]] = []
        for item in items:
            resolution = resolutions.get(item.official_domain)
            if resolution is not None:
                outcomes.append(
                    (
                        replace_dataclass(item, careers=resolution.careers),
                        resolution.job_link_count,
                    )
                )
            elif item.official_domain not in selected_domains and needs_resolution(item):
                skipped = skipped_for_budget(item)
                outcomes.append((replace_dataclass(item, careers=skipped.careers), 0))
            else:
                outcomes.append((item, 0))
        self.logger.info(
            "careers_resolution_completed",
            discovery_run_id=str(run.id),
            candidates_pending=len(pending),
            candidates_selected=len(selected),
            candidates_resolved=len(resolutions),
        )
        return outcomes

    async def _upsert_company(self, item: ValidatedProposal) -> Company:
        company = await self.session.scalar(
            select(Company).where(Company.official_domain == item.official_domain)
        )
        now = datetime.now(UTC)
        if company is None:
            company = Company(
                canonical_name=item.proposal.canonical_name,
                normalized_name=normalize_query(item.proposal.canonical_name),
                official_domain=item.official_domain,
                official_website_url=item.website_url,
                headquarters_country="US",
                description=item.proposal.industry_explanation,
                verification_status="verified",
                verified_at=now,
            )
            self.session.add(company)
            await self.session.flush()
        else:
            company.official_website_url = item.website_url
            company.description = item.proposal.industry_explanation
            company.verification_status = "verified"
            company.verified_at = now
            company.updated_at = now
        return company

    async def _upsert_aliases(self, company: Company, item: ValidatedProposal) -> None:
        source_url = str(item.industry_source.url)
        for alias in [item.proposal.canonical_name, *item.proposal.aliases]:
            normalized_alias = normalize_query(alias)
            exists = await self.session.scalar(
                select(CompanyAlias.id).where(
                    CompanyAlias.company_id == company.id,
                    CompanyAlias.normalized_alias == normalized_alias,
                )
            )
            if exists is None:
                self.session.add(
                    CompanyAlias(
                        company_id=company.id,
                        alias=alias,
                        normalized_alias=normalized_alias,
                        alias_type="brand",
                        source_url=source_url,
                        confidence=Decimal("0.900"),
                    )
                )

    async def _upsert_source(self, company: Company, item: ValidatedProposal) -> None:
        if item.careers_url is None:
            return
        if item.careers.monitoring_support == "unsupported":
            # The resolver opened this page and found no job listing. Its URL stays visible as
            # evidence, but registering it would schedule polling that can only produce noise.
            return
        detection = detect_provider(item.careers_url, allow_pending_generic=True)
        source = await self.session.scalar(
            select(CareerSource).where(
                CareerSource.canonical_source_key == detection.canonical_source_key
            )
        )
        if source is None:
            self.session.add(
                CareerSource(
                    company_id=company.id,
                    provider=detection.provider,
                    careers_url=item.careers_url,
                    canonical_source_key=detection.canonical_source_key,
                    provider_config=detection.provider_config,
                    status=detection.status,
                )
            )
        elif source.company_id == company.id:
            source.careers_url = item.careers_url

    async def _historical_sponsorship(
        self,
        company: Company,
        item: ValidatedProposal,
        latest_years: list[int],
    ) -> tuple[str, int, list[int]]:
        for legal_name in item.proposal.proposed_legal_entities:
            normalized = normalize_employer_name(legal_name)
            exists = await self.session.scalar(
                select(CompanyLegalEntity.id).where(
                    CompanyLegalEntity.company_id == company.id,
                    CompanyLegalEntity.normalized_legal_name == normalized,
                )
            )
            if exists is None:
                self.session.add(
                    CompanyLegalEntity(
                        company_id=company.id,
                        legal_name=legal_name,
                        normalized_legal_name=normalized,
                        match_method="ai_proposed",
                        confidence=Decimal("0.700"),
                        evidence_url=str(item.industry_source.url),
                    )
                )

        canonical_legal_name = normalize_employer_name(company.canonical_name)
        exact_stat = (
            await self.session.scalar(
                select(LcaEmployerYearlyStat)
                .where(
                    LcaEmployerYearlyStat.normalized_legal_employer_name == canonical_legal_name,
                    LcaEmployerYearlyStat.fiscal_year.in_(latest_years),
                )
                .limit(1)
            )
            if latest_years
            else None
        )
        if exact_stat is not None:
            mapping = await self.session.scalar(
                select(CompanyLegalEntity).where(
                    CompanyLegalEntity.company_id == company.id,
                    CompanyLegalEntity.normalized_legal_name == canonical_legal_name,
                )
            )
            if mapping is None:
                mapping = CompanyLegalEntity(
                    company_id=company.id,
                    legal_name=exact_stat.legal_employer_name,
                    normalized_legal_name=canonical_legal_name,
                    match_method="exact",
                    confidence=Decimal("0.950"),
                    evidence_url=str(item.industry_source.url),
                    verified_at=datetime.now(UTC),
                )
                self.session.add(mapping)
            else:
                mapping.match_method = "exact"
                mapping.confidence = Decimal("0.950")
                mapping.verified_at = datetime.now(UTC)

        allowed_names = list(
            (
                await self.session.scalars(
                    select(CompanyLegalEntity.normalized_legal_name).where(
                        CompanyLegalEntity.company_id == company.id,
                        (
                            (CompanyLegalEntity.match_method == "owner_verified")
                            | (
                                (CompanyLegalEntity.match_method == "exact")
                                & (CompanyLegalEntity.confidence >= Decimal("0.950"))
                            )
                        ),
                    )
                )
            ).all()
        )
        if exact_stat is not None and canonical_legal_name not in allowed_names:
            allowed_names.append(canonical_legal_name)
        if not allowed_names:
            return "unresolved", 0, []
        row = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(LcaEmployerYearlyStat.certified_cases), 0),
                    func.array_agg(func.distinct(LcaEmployerYearlyStat.fiscal_year)),
                ).where(
                    LcaEmployerYearlyStat.normalized_legal_employer_name.in_(allowed_names),
                    LcaEmployerYearlyStat.fiscal_year.in_(latest_years),
                )
            )
        ).one()
        loaded = sorted(year for year in (row[1] or []) if year is not None)
        return ("historical_records" if loaded else "no_records", int(row[0]), loaded)

    async def _upsert_industry_and_evidence(
        self,
        company: Company,
        item: ValidatedProposal,
        normalized: NormalizationResponse,
        historical_status: str,
        certified_cases: int,
        loaded_years: list[int],
        job_link_count: int = 0,
    ) -> None:
        interpretation = normalized.discovery.interpretation
        metadata = {
            "sources": [source.as_metadata() for source in item.verified_sources],
            "careers_url": item.careers_url,
            "careers_source_id": item.careers.source_id,
            "careers_url_status": item.careers.url_status,
            "careers_url_reason": item.careers.reason,
            "monitoring_support": item.careers.monitoring_support,
            "current_openings_count": job_link_count,
            "careers_resolver_version": RESOLVER_VERSION,
            "internship_evidence": item.internship_verified,
            "historical_h1b_status": historical_status,
            "certified_h1b_cases": certified_cases,
            "loaded_fiscal_years": loaded_years,
        }
        industry = await self.session.get(CompanyIndustry, (company.id, interpretation.slug))
        if industry is None:
            self.session.add(
                CompanyIndustry(
                    company_id=company.id,
                    industry_slug=interpretation.slug,
                    industry_label=interpretation.normalized_label,
                    relevance_score=item.proposal.industry_relevance,
                    evidence_json=metadata,
                )
            )
        else:
            industry.industry_label = interpretation.normalized_label
            industry.relevance_score = item.proposal.industry_relevance
            industry.evidence_json = metadata

        await self._ensure_evidence(
            company,
            evidence_type="industry",
            claim=item.proposal.industry_explanation,
            status="supports",
            source_url=str(item.industry_source.url),
            source_title=item.industry_source.title,
            is_official_source=item.industry_source_is_official,
        )
        if item.internship_verified:
            source = next(
                source
                for source in item.verified_sources
                if "internship" in {claim.lower() for claim in source.supports_claims}
            )
            await self._ensure_evidence(
                company,
                evidence_type="internship_program",
                claim="Official company content provides internship evidence.",
                status="supports",
                source_url=str(source.url),
                source_title=source.title,
            )

    async def _ensure_evidence(
        self,
        company: Company,
        *,
        evidence_type: str,
        claim: str,
        status: str,
        source_url: str,
        source_title: str | None,
        is_official_source: bool = True,
    ) -> None:
        exists = await self.session.scalar(
            select(CompanyEvidence.id).where(
                CompanyEvidence.company_id == company.id,
                CompanyEvidence.evidence_type == evidence_type,
                CompanyEvidence.source_url == source_url,
                CompanyEvidence.claim == claim,
            )
        )
        if exists is None:
            from app.companies.normalization import domain_from_url

            self.session.add(
                CompanyEvidence(
                    company_id=company.id,
                    evidence_type=evidence_type,
                    claim=claim,
                    status=status,
                    source_url=source_url,
                    source_title=source_title,
                    source_domain=domain_from_url(source_url),
                    is_official_source=is_official_source,
                    observed_at=datetime.now(UTC),
                    confidence=Decimal("0.900"),
                )
            )

    async def _persist_ranked_results(
        self, run: DiscoveryRun, candidates: list[RankedCandidate]
    ) -> None:
        candidates.sort(
            key=lambda candidate: (
                -candidate.total,
                -candidate.components.sponsorship,
                -candidate.components.internship,
                -candidate.components.monitorability,
                candidate.company.canonical_name.casefold(),
            )
        )
        for rank, candidate in enumerate(candidates[:MAX_RESULTS_PER_RESEARCH_RUN], start=1):
            self.session.add(
                DiscoveryResult(
                    discovery_run_id=run.id,
                    company_id=candidate.company.id,
                    rank=rank,
                    opportunity_score=candidate.total,
                    industry_score=candidate.components.industry,
                    sponsorship_score=candidate.components.sponsorship,
                    internship_score=candidate.components.internship,
                    monitorability_score=candidate.components.monitorability,
                    current_openings_score=candidate.components.current_openings,
                    explanation=candidate.validated.proposal.industry_explanation,
                )
            )

    async def _excluded_companies(self, run: DiscoveryRun) -> list[dict[str, str]]:
        if run.root_discovery_run_id is None:
            return []
        rows = (
            await self.session.execute(
                select(Company.canonical_name, Company.official_domain)
                .join(DiscoveryResult, DiscoveryResult.company_id == Company.id)
                .join(DiscoveryRun, DiscoveryRun.id == DiscoveryResult.discovery_run_id)
                .where(
                    (
                        (DiscoveryRun.id == run.root_discovery_run_id)
                        | (DiscoveryRun.root_discovery_run_id == run.root_discovery_run_id)
                    ),
                    DiscoveryRun.id != run.id,
                    DiscoveryRun.status == "succeeded",
                )
                .distinct()
                .order_by(Company.official_domain)
            )
        ).all()
        return [
            {"canonical_name": canonical_name, "official_domain": official_domain}
            for canonical_name, official_domain in rows
        ]

    def _new_trace(
        self,
        run: DiscoveryRun,
        *,
        step: str,
        model_id: str,
        redacted_input: dict[str, object],
        parent_run_id: UUID | None = None,
    ) -> AiRun:
        return AiRun(
            workflow="company_discovery",
            step=step,
            parent_run_id=parent_run_id,
            status="running",
            model_id=model_id,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            redacted_input={"discovery_run_id": str(run.id), **redacted_input},
            source_manifest=[],
            started_at=datetime.now(UTC),
        )

    def _complete_trace(
        self,
        trace: AiRun,
        *,
        usage: AIUsage,
        redacted_output: dict[str, object],
        source_manifest: list[dict[str, str | None]],
    ) -> None:
        trace.status = "succeeded"
        trace.redacted_output = redacted_output
        trace.source_manifest = source_manifest
        trace.input_tokens = usage.input_tokens
        trace.output_tokens = usage.output_tokens
        trace.estimated_cost_usd = estimate_cost_usd(
            usage,
            input_per_million=self.settings.openai_input_cost_per_million_usd,
            output_per_million=self.settings.openai_output_cost_per_million_usd,
            web_search_per_call=self.settings.openai_web_search_cost_per_call_usd,
        )
        trace.finished_at = datetime.now(UTC)
