from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.companies.normalization import normalize_query
from app.companies.schemas import SaveCompanyRequest
from app.companies.service import CompanyService
from app.core.config import Settings
from app.db.models import (
    AiRun,
    Company,
    CompanyIndustry,
    DiscoveryResult,
    DiscoveryRun,
    IndustryQuery,
    SavedCompany,
)
from app.discovery.local_day import LocalDayWindow
from app.discovery.schemas import (
    DiscoveryCreate,
    DiscoveryListResponse,
    DiscoveryResearchMoreRequest,
    DiscoveryResultResponse,
    DiscoveryResultsResponse,
    DiscoveryRunResponse,
    DiscoverySaveResponse,
    DiscoveryScoreBreakdown,
    DiscoverySource,
)

PROMPT_VERSION = "company-discovery-v4"
MAX_RESULTS_PER_RESEARCH_RUN = 40


class DiscoveryDispatcher(Protocol):
    def enqueue(self, run_id: UUID) -> None: ...


class DiscoveryService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        dispatcher: DiscoveryDispatcher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.dispatcher = dispatcher
        self.clock = clock or (lambda: datetime.now(UTC))

    async def create(
        self,
        payload: DiscoveryCreate,
        local_day: LocalDayWindow,
    ) -> DiscoveryRunResponse:
        research_model, structured_model = self._configured_models()
        now = self.clock()
        normalized = normalize_query(payload.query)
        model_signature = f"{research_model}|{structured_model}"
        cached_query = await self.session.scalar(
            select(IndustryQuery)
            .join(DiscoveryRun, DiscoveryRun.industry_query_id == IndustryQuery.id)
            .where(
                IndustryQuery.normalized_query == normalized,
                IndustryQuery.country_code == payload.country,
                IndustryQuery.model_id == model_signature,
                IndustryQuery.prompt_version == PROMPT_VERSION,
                IndustryQuery.status == "succeeded",
                IndustryQuery.expires_at > now,
                DiscoveryRun.root_discovery_run_id.is_(None),
                DiscoveryRun.continuation_index == 0,
            )
            .order_by(IndustryQuery.completed_at.desc())
            .limit(1)
        )
        if cached_query is not None:
            run = await self._clone_cached_run(cached_query, payload, now)
            await self.session.commit()
            return await self._run_response(run)

        await self._ensure_uncached_research_allowed(local_day)

        industry_query = IndustryQuery(
            raw_query=payload.query,
            normalized_query=normalized,
            country_code=payload.country,
            interpretation_json={},
            prompt_version=PROMPT_VERSION,
            model_id=model_signature,
            status="pending",
            expires_at=now + timedelta(hours=self.settings.discovery_cache_ttl_hours),
            created_at=now,
        )
        self.session.add(industry_query)
        await self.session.flush()
        run = DiscoveryRun(
            industry_query_id=industry_query.id,
            raw_query=payload.query,
            status="queued",
            cache_hit=False,
            requested_limit=payload.limit,
            created_at=now,
        )
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)

        if self.dispatcher is not None:
            try:
                self.dispatcher.enqueue(run.id)
            except Exception as exc:
                run.status = "failed"
                run.error_code = "QUEUE_UNAVAILABLE"
                run.completed_at = self.clock()
                industry_query.status = "failed"
                await self.session.commit()
                raise AppError(
                    "QUEUE_UNAVAILABLE",
                    "The research worker could not be reached. Check Redis and the worker.",
                    status_code=503,
                ) from exc
        return await self._run_response(run)

    async def research_more(
        self,
        run_id: UUID,
        payload: DiscoveryResearchMoreRequest,
        local_day: LocalDayWindow,
    ) -> DiscoveryRunResponse:
        del payload  # Literal[True] validation is the explicit acknowledgement gate.
        requested_run = await self._get_run(run_id)
        root_id = requested_run.root_discovery_run_id or requested_run.id
        root = await self.session.scalar(
            select(DiscoveryRun).where(DiscoveryRun.id == root_id).with_for_update()
        )
        if root is None:
            raise AppError("DISCOVERY_NOT_FOUND", "Discovery run not found.", status_code=404)
        if root.status != "succeeded":
            raise AppError(
                "DISCOVERY_NOT_COMPLETE",
                "Wait for the initial research to finish before researching more companies.",
                status_code=409,
            )
        active = await self.session.scalar(
            select(DiscoveryRun.id).where(
                DiscoveryRun.root_discovery_run_id == root.id,
                DiscoveryRun.status.in_({"queued", "running"}),
            )
        )
        if active is not None:
            raise AppError(
                "DISCOVERY_CONTINUATION_ACTIVE",
                "Additional research is already queued or running.",
                status_code=409,
            )

        now = self.clock()
        await self._ensure_uncached_research_allowed(local_day)
        research_model, structured_model = self._configured_models()
        model_signature = f"{research_model}|{structured_model}"
        root_query = (
            await self.session.get(IndustryQuery, root.industry_query_id)
            if root.industry_query_id
            else None
        )
        if root_query is None:
            raise AppError(
                "INDUSTRY_QUERY_NOT_FOUND",
                "The initial industry query could not be loaded.",
                status_code=409,
            )
        latest_index = int(
            await self.session.scalar(
                select(func.coalesce(func.max(DiscoveryRun.continuation_index), 0)).where(
                    (DiscoveryRun.id == root.id) | (DiscoveryRun.root_discovery_run_id == root.id)
                )
            )
            or 0
        )
        industry_query = IndustryQuery(
            raw_query=root.raw_query,
            normalized_query=normalize_query(root.raw_query),
            country_code=root_query.country_code,
            interpretation_json={},
            prompt_version=PROMPT_VERSION,
            model_id=model_signature,
            status="pending",
            expires_at=now + timedelta(hours=self.settings.discovery_cache_ttl_hours),
            created_at=now,
        )
        self.session.add(industry_query)
        await self.session.flush()
        continuation = DiscoveryRun(
            industry_query_id=industry_query.id,
            root_discovery_run_id=root.id,
            continuation_index=latest_index + 1,
            raw_query=root.raw_query,
            status="queued",
            cache_hit=False,
            requested_limit=root.requested_limit,
            created_at=now,
        )
        self.session.add(continuation)
        await self.session.commit()
        await self.session.refresh(continuation)
        await self._dispatch(continuation, industry_query)
        return await self._run_response(continuation)

    async def list(self, limit: int = 20, cursor: UUID | None = None) -> DiscoveryListResponse:
        statement = (
            select(DiscoveryRun)
            .where(DiscoveryRun.root_discovery_run_id.is_(None))
            .order_by(DiscoveryRun.created_at.desc(), DiscoveryRun.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            cursor_run = await self.session.get(DiscoveryRun, cursor)
            if cursor_run is not None:
                statement = statement.where(
                    or_(
                        DiscoveryRun.created_at < cursor_run.created_at,
                        and_(
                            DiscoveryRun.created_at == cursor_run.created_at,
                            DiscoveryRun.id < cursor_run.id,
                        ),
                    )
                )
        runs = list((await self.session.scalars(statement)).all())
        return DiscoveryListResponse(
            items=[await self._run_response(run) for run in runs[:limit]],
            next_cursor=runs[limit - 1].id if len(runs) > limit else None,
        )

    async def daily_history(
        self,
        local_day: LocalDayWindow,
        *,
        limit: int = 10,
        cursor: UUID | None = None,
    ) -> DiscoveryListResponse:
        """List today's root searches without rerunning or deleting research."""

        statement = (
            select(DiscoveryRun)
            .where(
                DiscoveryRun.root_discovery_run_id.is_(None),
                DiscoveryRun.created_at >= local_day.start,
                DiscoveryRun.created_at < local_day.end,
            )
            .order_by(DiscoveryRun.created_at.desc(), DiscoveryRun.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            cursor_run = await self.session.get(DiscoveryRun, cursor)
            if cursor_run is not None:
                statement = statement.where(
                    or_(
                        DiscoveryRun.created_at < cursor_run.created_at,
                        and_(
                            DiscoveryRun.created_at == cursor_run.created_at,
                            DiscoveryRun.id < cursor_run.id,
                        ),
                    )
                )

        runs = list((await self.session.scalars(statement)).all())
        return DiscoveryListResponse(
            items=[await self._run_response(run) for run in runs[:limit]],
            next_cursor=runs[limit - 1].id if len(runs) > limit else None,
        )

    async def get(self, run_id: UUID) -> DiscoveryRunResponse:
        return await self._run_response(await self._get_run(run_id))

    async def results(
        self, run_id: UUID, *, offset: int = 0, limit: int = 20
    ) -> DiscoveryResultsResponse:
        run = await self._get_run(run_id)
        root_id = run.root_discovery_run_id or run.id
        root = run if run.id == root_id else await self._get_run(root_id)
        if root.status not in {"succeeded", "failed"}:
            return DiscoveryResultsResponse(
                items=[], total=0, offset=offset, limit=limit, has_more=False
            )
        industry_query = (
            await self.session.get(IndustryQuery, root.industry_query_id)
            if root.industry_query_id
            else None
        )
        industry_slug = (
            str(industry_query.interpretation_json.get("slug", ""))
            if industry_query is not None
            else ""
        )
        rows = list(
            (
                await self.session.execute(
                    select(
                        DiscoveryResult,
                        Company,
                        CompanyIndustry,
                        DiscoveryRun.continuation_index,
                    )
                    .join(Company, Company.id == DiscoveryResult.company_id)
                    .join(DiscoveryRun, DiscoveryRun.id == DiscoveryResult.discovery_run_id)
                    .outerjoin(
                        CompanyIndustry,
                        (CompanyIndustry.company_id == Company.id)
                        & (CompanyIndustry.industry_slug == industry_slug),
                    )
                    .where(
                        (
                            (DiscoveryRun.id == root_id)
                            | (DiscoveryRun.root_discovery_run_id == root_id)
                        ),
                        DiscoveryRun.status == "succeeded",
                    )
                    .order_by(DiscoveryRun.continuation_index, DiscoveryResult.rank)
                )
            ).all()
        )
        total = len(rows)
        page_rows = rows[offset : offset + limit]
        company_ids = [company.id for _, company, _, _ in page_rows]
        saved_ids = (
            set(
                (
                    await self.session.scalars(
                        select(SavedCompany.company_id).where(
                            SavedCompany.company_id.in_(company_ids)
                        )
                    )
                ).all()
            )
            if company_ids
            else set()
        )
        items: list[DiscoveryResultResponse] = []
        for global_index, (result, company, industry, _continuation_index) in enumerate(
            page_rows, start=offset + 1
        ):
            metadata = industry.evidence_json if industry is not None else {}
            source_items = metadata.get("sources", [])
            careers_url = metadata.get("careers_url")
            legacy_support = metadata.get("careers_page_support", "unavailable")
            careers_url_status = metadata.get(
                "careers_url_status",
                "evidence_verified" if careers_url else "not_found",
            )
            monitoring_support = metadata.get(
                "monitoring_support",
                {
                    "supported": "structured",
                    "needs_review": "generic_pending",
                    "unavailable": "unsupported",
                }.get(legacy_support, "unsupported"),
            )
            items.append(
                DiscoveryResultResponse(
                    id=result.id,
                    company_id=company.id,
                    rank=global_index,
                    company_name=company.canonical_name,
                    careers_url=careers_url,
                    opportunity_score=result.opportunity_score,
                    scores=DiscoveryScoreBreakdown(
                        industry=result.industry_score,
                        historical_h1b_sponsorship=result.sponsorship_score,
                        internship=result.internship_score,
                        careers_page_support=result.monitorability_score,
                        current_openings=result.current_openings_score,
                    ),
                    explanation=result.explanation,
                    historical_h1b_status=metadata.get("historical_h1b_status", "unresolved"),
                    certified_h1b_cases=int(metadata.get("certified_h1b_cases", 0)),
                    loaded_fiscal_years=metadata.get("loaded_fiscal_years", []),
                    internship_evidence=bool(metadata.get("internship_evidence", False)),
                    careers_url_status=careers_url_status,
                    careers_url_reason=metadata.get(
                        "careers_url_reason",
                        "LEGACY_CAREERS_SOURCE_VERIFIED"
                        if careers_url
                        else "CAREERS_SOURCE_NOT_SELECTED",
                    ),
                    monitoring_support=monitoring_support,
                    sources=[DiscoverySource.model_validate(item) for item in source_items],
                    is_hidden=result.is_hidden,
                    is_saved=company.id in saved_ids,
                )
            )
        return DiscoveryResultsResponse(
            items=items,
            total=total,
            offset=offset,
            limit=limit,
            has_more=offset + len(items) < total,
        )

    async def hide(self, run_id: UUID, result_id: UUID) -> None:
        await self._set_hidden(run_id, result_id, hidden=True)

    async def show(self, run_id: UUID, result_id: UUID) -> None:
        await self._set_hidden(run_id, result_id, hidden=False)

    async def _set_hidden(self, run_id: UUID, result_id: UUID, *, hidden: bool) -> None:
        series_run_ids = await self._series_run_ids(run_id)
        result = await self.session.scalar(
            select(DiscoveryResult).where(
                DiscoveryResult.id == result_id,
                DiscoveryResult.discovery_run_id.in_(series_run_ids),
            )
        )
        if result is None:
            raise AppError(
                "DISCOVERY_RESULT_NOT_FOUND", "Discovery result not found.", status_code=404
            )
        result.is_hidden = hidden
        await self.session.commit()

    async def save_selected(
        self, run_id: UUID, result_ids: Sequence[UUID]
    ) -> DiscoverySaveResponse:
        series_run_ids = await self._series_run_ids(run_id)
        rows = list(
            (
                await self.session.scalars(
                    select(DiscoveryResult).where(
                        DiscoveryResult.discovery_run_id.in_(series_run_ids),
                        DiscoveryResult.id.in_(result_ids),
                    )
                )
            ).all()
        )
        if len(rows) != len(set(result_ids)):
            raise AppError(
                "DISCOVERY_RESULT_NOT_FOUND",
                "One or more results were not found.",
                status_code=404,
            )
        company_service = CompanyService(self.session)
        saved: list[UUID] = []
        for row in rows:
            await company_service.save(row.company_id, SaveCompanyRequest())
            saved.append(row.company_id)
        return DiscoverySaveResponse(saved_company_ids=saved)

    async def save_all_monitorable(self, run_id: UUID) -> DiscoverySaveResponse:
        results = await self.results(run_id, limit=1000)
        selected = [
            item.id
            for item in results.items
            if not item.is_hidden and item.monitoring_support != "unsupported"
        ]
        if not selected:
            return DiscoverySaveResponse(saved_company_ids=[])
        return await self.save_selected(run_id, selected)

    async def _clone_cached_run(
        self, industry_query: IndustryQuery, payload: DiscoveryCreate, now: datetime
    ) -> DiscoveryRun:
        source_run = await self.session.scalar(
            select(DiscoveryRun)
            .where(
                DiscoveryRun.industry_query_id == industry_query.id,
                DiscoveryRun.status == "succeeded",
                DiscoveryRun.root_discovery_run_id.is_(None),
                DiscoveryRun.continuation_index == 0,
            )
            .order_by(DiscoveryRun.completed_at.desc())
            .limit(1)
        )
        if source_run is None:
            raise AppError(
                "CACHE_INCONSISTENT",
                "Cached discovery results could not be loaded.",
                status_code=500,
            )
        run = DiscoveryRun(
            industry_query_id=industry_query.id,
            raw_query=payload.query,
            status="succeeded",
            cache_hit=True,
            requested_limit=payload.limit,
            created_at=now,
            completed_at=now,
        )
        self.session.add(run)
        await self.session.flush()
        source_results = list(
            (
                await self.session.scalars(
                    select(DiscoveryResult)
                    .where(DiscoveryResult.discovery_run_id == source_run.id)
                    .order_by(DiscoveryResult.rank)
                )
            ).all()
        )
        for source in source_results:
            self.session.add(
                DiscoveryResult(
                    discovery_run_id=run.id,
                    company_id=source.company_id,
                    rank=source.rank,
                    opportunity_score=source.opportunity_score,
                    industry_score=source.industry_score,
                    sponsorship_score=source.sponsorship_score,
                    internship_score=source.internship_score,
                    monitorability_score=source.monitorability_score,
                    current_openings_score=source.current_openings_score,
                    explanation=source.explanation,
                    is_hidden=False,
                )
            )
        return run

    async def _series_run_ids(self, run_id: UUID) -> Sequence[UUID]:
        run = await self._get_run(run_id)
        root_id = run.root_discovery_run_id or run.id
        return list(
            (
                await self.session.scalars(
                    select(DiscoveryRun.id).where(
                        (DiscoveryRun.id == root_id)
                        | (DiscoveryRun.root_discovery_run_id == root_id)
                    )
                )
            ).all()
        )

    async def _ensure_uncached_research_allowed(self, local_day: LocalDayWindow) -> None:
        if not self.settings.openai_api_key:
            raise AppError(
                "OPENAI_NOT_CONFIGURED",
                "Add OPENAI_API_KEY to the server environment before starting a new search.",
                status_code=503,
            )
        uncached_today = await self.session.scalar(
            select(func.count())
            .select_from(IndustryQuery)
            .where(
                IndustryQuery.created_at >= local_day.start,
                IndustryQuery.created_at < local_day.end,
            )
        )
        if int(uncached_today or 0) >= self.settings.discovery_daily_quota:
            raise AppError(
                "DISCOVERY_DAILY_QUOTA_REACHED",
                "Today's new-search limit has been reached. Cached searches remain available.",
                status_code=429,
            )

    async def _dispatch(self, run: DiscoveryRun, industry_query: IndustryQuery) -> None:
        if self.dispatcher is None:
            return
        try:
            self.dispatcher.enqueue(run.id)
        except Exception as exc:
            run.status = "failed"
            run.error_code = "QUEUE_UNAVAILABLE"
            run.completed_at = self.clock()
            industry_query.status = "failed"
            await self.session.commit()
            raise AppError(
                "QUEUE_UNAVAILABLE",
                "The research worker could not be reached. Check Redis and the worker.",
                status_code=503,
            ) from exc

    async def _run_response(self, run: DiscoveryRun) -> DiscoveryRunResponse:
        result_count = int(
            await self.session.scalar(
                select(func.count())
                .select_from(DiscoveryResult)
                .where(DiscoveryResult.discovery_run_id == run.id)
            )
            or 0
        )
        cost = Decimal("0")
        if not run.cache_hit:
            cost_value = await self.session.scalar(
                select(func.coalesce(func.sum(AiRun.estimated_cost_usd), 0)).where(
                    AiRun.redacted_input["discovery_run_id"].astext == str(run.id)
                )
            )
            cost = Decimal(str(cost_value or 0))
        return DiscoveryRunResponse(
            id=run.id,
            query=run.raw_query,
            status=run.status,
            cached=run.cache_hit,
            result_count=result_count,
            error_code=run.error_code,
            estimated_cost_usd=float(cost),
            created_at=run.created_at,
            completed_at=run.completed_at,
        )

    async def _get_run(self, run_id: UUID) -> DiscoveryRun:
        run = await self.session.get(DiscoveryRun, run_id)
        if run is None:
            raise AppError("DISCOVERY_NOT_FOUND", "Discovery run not found.", status_code=404)
        return run

    def _configured_models(self) -> tuple[str, str]:
        if not self.settings.openai_research_model or not self.settings.openai_structured_model:
            raise AppError(
                "OPENAI_MODELS_NOT_CONFIGURED",
                "Set OPENAI_RESEARCH_MODEL and OPENAI_STRUCTURED_MODEL before searching.",
                status_code=503,
            )
        return self.settings.openai_research_model, self.settings.openai_structured_model
