from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace as replace_dataclass
from datetime import UTC, datetime
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
from app.api.errors import AppError
from app.companies.normalization import normalize_employer_name, normalize_query
from app.core.config import Settings
from app.db.models import (
    AiRun,
    CareerSource,
    Company,
    CompanyAlias,
    CompanyIndustry,
    CompanyLegalEntity,
    DiscoveryResult,
    DiscoveryRun,
    ImmigrationDatasetImport,
    IndustryQuery,
    LcaEmployerYearlyStat,
    SavedCompany,
)
from app.discovery.careers_resolver import (
    RESOLVER_VERSION,
    CareersPageResolver,
    needs_resolution,
    skipped_for_budget,
)
from app.discovery.failures import DiscoveryFailureService, discovery_error_code
from app.discovery.service import MAX_RESULTS_PER_RESEARCH_RUN, PROMPT_VERSION
from app.discovery.validation import DiscoveryCandidate, validate_proposal
from app.monitoring.http import HttpFetcher
from app.monitoring.provider_detection import detect_provider

SCHEMA_VERSION = "company-discovery-schema-v3"


@dataclass
class PersistedCandidate:
    company: Company
    candidate: DiscoveryCandidate
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
                normalized,
                research.source_manifest,
                excluded_identity_keys={
                    _identity_key(item["canonical_name"] or "", item.get("official_domain"))
                    for item in excluded_companies
                },
            )
            if not candidates and run.root_discovery_run_id is None:
                raise RuntimeError("No discovery results remained")
            await self._persist_results(run, candidates)
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
        normalized: NormalizationResponse,
        source_manifest: list[dict[str, str | None]],
        excluded_identity_keys: set[str] | None = None,
    ) -> list[PersistedCandidate]:
        excluded_identity_keys = excluded_identity_keys or set()
        validated: dict[str, DiscoveryCandidate] = {}
        for proposal in normalized.discovery.companies:
            try:
                item = validate_proposal(proposal, source_manifest)
            except (AppError, ValueError) as exc:
                self.logger.info(
                    "discovery_candidate_rejected",
                    discovery_run_id=str(run.id),
                    company_name=proposal.canonical_name,
                    reason=str(exc),
                )
                continue
            if item.identity_key in excluded_identity_keys:
                self.logger.info(
                    "discovery_candidate_excluded",
                    discovery_run_id=str(run.id),
                    company_name=proposal.canonical_name,
                    official_domain=item.official_domain,
                )
                continue
            if item.careers.url_status in {"not_found", "rejected"}:
                self.logger.info(
                    "discovery_careers_source_unavailable",
                    discovery_run_id=str(run.id),
                    company_name=proposal.canonical_name,
                    careers_url_status=item.careers.url_status,
                    reason=item.careers.reason,
                    source_id=item.careers.source_id,
                )
            prior = validated.get(item.identity_key)
            validated[item.identity_key] = item if prior is None else _merge_candidates(prior, item)

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
        visible_candidates = list(validated.values())[:MAX_RESULTS_PER_RESEARCH_RUN]
        resolved = await self._resolve_careers_pages(run, visible_candidates)
        candidates: list[PersistedCandidate] = []
        for item, job_link_count in resolved:
            company, allow_ai_identity_updates = await self._upsert_company(item)
            if allow_ai_identity_updates:
                await self._upsert_aliases(company, item)
            await self._upsert_source(company, item)
            historical_status, certified_cases, loaded_years = await self._historical_sponsorship(
                company,
                item,
                latest_years,
                allow_ai_identity_updates=allow_ai_identity_updates,
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
            candidates.append(
                PersistedCandidate(
                    company=company,
                    candidate=item,
                    historical_status=historical_status,
                    certified_cases=certified_cases,
                    loaded_years=loaded_years,
                )
            )
        return candidates

    async def _resolve_careers_pages(
        self,
        run: DiscoveryRun,
        items: list[DiscoveryCandidate],
    ) -> list[tuple[DiscoveryCandidate, int]]:
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

        selected = pending[:budget]
        selected_keys = {item.identity_key for item in selected}
        resolutions = await CareersPageResolver(
            self.http, self.settings, logger=self.logger
        ).resolve_all(selected)

        outcomes: list[tuple[DiscoveryCandidate, int]] = []
        for item in items:
            resolution = resolutions.get(item.identity_key)
            if resolution is not None:
                outcomes.append(
                    (
                        replace_dataclass(item, careers=resolution.careers),
                        resolution.job_link_count,
                    )
                )
            elif item.identity_key not in selected_keys and needs_resolution(item):
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

    async def _upsert_company(self, item: DiscoveryCandidate) -> tuple[Company, bool]:
        company = None
        if item.official_domain is not None:
            company = await self.session.scalar(
                select(Company).where(Company.official_domain == item.official_domain)
            )
        if company is None and item.official_domain is None:
            company = await self.session.scalar(
                select(Company)
                .where(
                    Company.normalized_name == normalize_query(item.proposal.canonical_name),
                    Company.official_domain.is_(None),
                )
                .order_by(Company.created_at, Company.id)
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
                verification_status="proposed",
                verified_at=None,
            )
            self.session.add(company)
            await self.session.flush()
            return company, True
        else:
            is_saved = await self.session.scalar(
                select(SavedCompany.id).where(SavedCompany.company_id == company.id)
            )
            allow_ai_identity_updates = company.verification_status != "verified" and not is_saved
            if allow_ai_identity_updates:
                company.official_domain = item.official_domain
                company.official_website_url = item.website_url
                company.description = item.proposal.industry_explanation
                company.updated_at = now
            return company, allow_ai_identity_updates

    async def _upsert_aliases(self, company: Company, item: DiscoveryCandidate) -> None:
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
                        source_url=None,
                    )
                )

    async def _upsert_source(self, company: Company, item: DiscoveryCandidate) -> None:
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
        item: DiscoveryCandidate,
        latest_years: list[int],
        *,
        allow_ai_identity_updates: bool = True,
    ) -> tuple[str, int, list[int]]:
        proposed_names = item.proposal.proposed_legal_entities if allow_ai_identity_updates else []
        for legal_name in proposed_names:
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
                        evidence_url=None,
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
                    evidence_url=None,
                    verified_at=datetime.now(UTC),
                )
                self.session.add(mapping)
            elif mapping.match_method != "owner_verified":
                mapping.match_method = "exact"
                mapping.verified_at = datetime.now(UTC)

        allowed_names = list(
            (
                await self.session.scalars(
                    select(CompanyLegalEntity.normalized_legal_name).where(
                        CompanyLegalEntity.company_id == company.id,
                        CompanyLegalEntity.match_method.in_(("exact", "owner_verified")),
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
        item: DiscoveryCandidate,
        normalized: NormalizationResponse,
        historical_status: str,
        certified_cases: int,
        loaded_years: list[int],
        job_link_count: int = 0,
    ) -> None:
        interpretation = normalized.discovery.interpretation
        metadata = {
            "careers_url": item.careers_url,
            "careers_source_id": item.careers.source_id,
            "careers_url_status": item.careers.url_status,
            "careers_url_reason": item.careers.reason,
            "monitoring_support": item.careers.monitoring_support,
            "current_openings_count": job_link_count,
            "careers_resolver_version": RESOLVER_VERSION,
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
                    evidence_json=metadata,
                )
            )
        else:
            industry.industry_label = interpretation.normalized_label
            industry.evidence_json = metadata

    async def _persist_results(
        self, run: DiscoveryRun, candidates: list[PersistedCandidate]
    ) -> None:
        for candidate in candidates[:MAX_RESULTS_PER_RESEARCH_RUN]:
            self.session.add(
                DiscoveryResult(
                    discovery_run_id=run.id,
                    company_id=candidate.company.id,
                    research_source_status=candidate.candidate.research_source_status,
                    internship_research_reported=(
                        candidate.candidate.proposal.internship_research_reported
                    ),
                    explanation=candidate.candidate.proposal.industry_explanation,
                )
            )

    async def _excluded_companies(self, run: DiscoveryRun) -> list[dict[str, str | None]]:
        if run.root_discovery_run_id is None:
            return []
        rows = (
            await self.session.execute(
                select(
                    Company.canonical_name,
                    Company.official_domain,
                    func.lower(Company.canonical_name).label("normalized_order"),
                    Company.id,
                )
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
                .order_by(func.lower(Company.canonical_name), Company.id)
            )
        ).all()
        return [
            {"canonical_name": canonical_name, "official_domain": official_domain}
            for canonical_name, official_domain, _normalized_order, _company_id in rows
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


def _identity_key(canonical_name: str, official_domain: str | None) -> str:
    if official_domain:
        return f"domain:{official_domain.casefold()}"
    return f"name:{normalize_query(canonical_name)}"


def _merge_candidates(
    first: DiscoveryCandidate,
    later: DiscoveryCandidate,
) -> DiscoveryCandidate:
    """Merge duplicate AI proposals while preserving the first proposal's presentation."""

    aliases = _union_strings(first.proposal.aliases, later.proposal.aliases, normalize_query)
    legal_entities = _union_strings(
        first.proposal.proposed_legal_entities,
        later.proposal.proposed_legal_entities,
        normalize_employer_name,
    )
    references = list(first.proposal.source_references)
    seen_references = {
        (reference.source_id, reference.source_type, tuple(reference.supports_claims))
        for reference in references
    }
    for reference in later.proposal.source_references:
        key = (reference.source_id, reference.source_type, tuple(reference.supports_claims))
        if key not in seen_references:
            references.append(reference)
            seen_references.add(key)

    use_later_careers = first.careers.url is None and later.careers.url is not None
    proposal = first.proposal.model_copy(
        update={
            "aliases": aliases,
            "proposed_legal_entities": legal_entities,
            "official_careers_source_id": (
                later.proposal.official_careers_source_id
                if use_later_careers
                else first.proposal.official_careers_source_id
            ),
            "internship_research_reported": (
                first.proposal.internship_research_reported
                or later.proposal.internship_research_reported
            ),
            "source_references": references,
        }
    )
    return replace_dataclass(
        first,
        proposal=proposal,
        careers=later.careers if use_later_careers else first.careers,
        research_source_status=(
            "matched"
            if "matched" in {first.research_source_status, later.research_source_status}
            else "unmatched"
        ),
    )


def _union_strings(
    first: list[str], later: list[str], normalizer: Callable[[str], str]
) -> list[str]:
    output = list(first)
    seen = {normalizer(value) for value in output}
    for value in later:
        key = normalizer(value)
        if key not in seen:
            output.append(value)
            seen.add(key)
    return output
