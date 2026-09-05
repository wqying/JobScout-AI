import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.fake import FakeDiscoveryAIClient
from app.ai.schemas.discovery import NormalizedDiscovery
from app.ai.workflows.company_discovery import CompanyDiscoveryWorkflow
from app.api.errors import AppError
from app.companies.service import CompanyService
from app.core.config import Settings
from app.db.models import (
    AiRun,
    CareerSource,
    Company,
    DiscoveryResult,
    DiscoveryRun,
    IndustryQuery,
    SavedCompany,
)
from app.discovery.failures import DiscoveryFailureService
from app.discovery.local_day import LocalDayWindow
from app.discovery.schemas import DiscoveryCreate, DiscoveryResearchMoreRequest
from app.discovery.service import DiscoveryService
from app.immigration.importer import LcaImporter
from app.monitoring.http import SafeHttpResponse

AI_FIXTURE = Path(__file__).parents[1] / "fixtures" / "ai" / "gaming_discovery_v1.json"
AI_MANIFEST_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "ai" / "gaming_discovery_manifest_v1.json"
)
DOL_FIXTURE = Path(__file__).parents[1] / "fixtures" / "dol" / "lca_synthetic.csv"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="Set RUN_INTEGRATION_TESTS=1 with local PostgreSQL and Redis running",
    ),
]


class RecordingDispatcher:
    def __init__(self) -> None:
        self.run_ids: list[str] = []

    def enqueue(self, run_id: object) -> None:
        self.run_ids.append(str(run_id))


def _local_day(now: datetime | None = None) -> LocalDayWindow:
    current = now or datetime.now(UTC)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return LocalDayWindow.validated(start, start + timedelta(days=1), now=current)


def _studio_discovery(indices: list[int]) -> NormalizedDiscovery:
    return NormalizedDiscovery.model_validate(
        {
            "interpretation": {
                "normalized_label": "Gaming",
                "slug": "gaming",
                "included_segments": ["video games"],
                "excluded_segments": [],
                "target_role_families": ["software"],
                "country_code": "US",
            },
            "companies": [
                {
                    "canonical_name": f"Studio {index}",
                    "aliases": [],
                    "proposed_legal_entities": [],
                    "official_website_url": f"https://studio-{index}.example",
                    "official_careers_source_id": f"source_{index * 2 + 2}",
                    "industry_relevance": 90 - (index % 10),
                    "industry_explanation": (f"Studio {index} develops and publishes video games."),
                    "has_internship_evidence": False,
                    "source_references": [
                        {
                            "source_id": f"source_{index * 2 + 1}",
                            "source_type": "official_company",
                            "supports_claims": ["industry", "official_identity"],
                        },
                        {
                            "source_id": f"source_{index * 2 + 2}",
                            "source_type": "search_lead",
                            "supports_claims": ["careers_page"],
                        },
                    ],
                    "unresolved_questions": [],
                }
                for index in indices
            ],
        }
    )


def _manifest(indices: list[int]) -> list[dict[str, str | None]]:
    return [
        source
        for index in indices
        for source in (
            {
                "source_id": f"source_{index * 2 + 1}",
                "url": f"https://studio-{index}.example/about",
                "title": f"Studio {index} about",
            },
            {
                "source_id": f"source_{index * 2 + 2}",
                "url": f"https://jobs.lever.co/studio-{index}/job-id",
                "title": f"Studio {index} job",
            },
        )
    ]


async def test_fake_discovery_orders_filters_traces_and_caches(
    isolated_session: AsyncSession,
) -> None:
    discovery = NormalizedDiscovery.model_validate(json.loads(AI_FIXTURE.read_text()))
    manifest = json.loads(AI_MANIFEST_FIXTURE.read_text())
    fake = FakeDiscoveryAIClient(discovery, manifest)
    settings = Settings(
        app_env="test",
        openai_api_key="test-key",
        openai_research_model=fake.research_model,
        openai_structured_model=fake.structured_model,
    )
    dispatcher = RecordingDispatcher()
    service = DiscoveryService(isolated_session, settings, dispatcher)
    await LcaImporter(isolated_session).import_file(
        DOL_FIXTURE,
        2025,
        "https://www.dol.gov/example-lca.csv",
        "synthetic-v1",
    )

    first = await service.create(DiscoveryCreate(query="gaming companies"), _local_day())
    await CompanyDiscoveryWorkflow(isolated_session, fake, settings).execute(first.id)
    completed = await service.get(first.id)
    results = await service.results(first.id)

    assert completed.status == "succeeded"
    assert completed.cached is False
    assert completed.estimated_cost_usd > 0
    assert [item.company_name for item in results.items] == ["Acme Games", "Pixel Forge"]
    assert results.items[0].opportunity_score == 65
    assert results.items[0].certified_h1b_cases == 2
    assert all(item.company_name != "Unsourced Studio" for item in results.items)
    assert fake.research_calls == 1
    assert fake.normalization_calls == 1

    cached = await service.create(
        DiscoveryCreate(query="  GAMING   companies "),
        _local_day(),
    )
    cached_results = await service.results(cached.id)

    assert cached.status == "succeeded"
    assert cached.cached is True
    assert cached.estimated_cost_usd == 0
    assert [item.company_name for item in cached_results.items] == [
        "Acme Games",
        "Pixel Forge",
    ]
    assert fake.research_calls == 1
    assert await isolated_session.scalar(select(func.count()).select_from(IndustryQuery)) == 1
    assert await isolated_session.scalar(select(func.count()).select_from(DiscoveryRun)) == 2
    assert await isolated_session.scalar(select(func.count()).select_from(DiscoveryResult)) == 4
    assert await isolated_session.scalar(select(func.count()).select_from(AiRun)) == 2

    assert all(item.is_saved is False for item in results.items)
    selected = await service.save_selected(first.id, [results.items[0].id])
    assert selected.saved_company_ids == [results.items[0].company_id]

    refreshed_results = await service.results(first.id)
    saved_companies = await CompanyService(isolated_session).list_saved()
    assert [item.is_saved for item in refreshed_results.items] == [True, False]
    assert [item.id for item in saved_companies.items] == [results.items[0].company_id]

    await service.save_selected(first.id, [results.items[0].id])
    assert await isolated_session.scalar(select(func.count()).select_from(SavedCompany)) == 1

    saved_all = await service.save_all_monitorable(cached.id)
    assert set(saved_all.saved_company_ids) == {item.company_id for item in cached_results.items}
    assert await isolated_session.scalar(select(func.count()).select_from(SavedCompany)) == 2
    assert all(item.is_saved for item in (await service.results(cached.id)).items)


async def test_bounded_results_pagination_and_research_more_avoid_series_duplicates(
    isolated_session: AsyncSession,
) -> None:
    initial_discovery = _studio_discovery(list(range(25)))
    fake = FakeDiscoveryAIClient(initial_discovery, _manifest(list(range(25))))
    settings = Settings(
        app_env="test",
        openai_api_key="test-key",
        openai_research_model=fake.research_model,
        openai_structured_model=fake.structured_model,
    )
    dispatcher = RecordingDispatcher()
    service = DiscoveryService(isolated_session, settings, dispatcher)

    initial = await service.create(DiscoveryCreate(query="gaming studios"), _local_day())
    await CompanyDiscoveryWorkflow(isolated_session, fake, settings).execute(initial.id)

    first_page = await service.results(initial.id)
    second_page = await service.results(initial.id, offset=20, limit=20)
    assert len(first_page.items) == 20
    assert first_page.total == 25
    assert first_page.has_more is True
    assert len(second_page.items) == 5
    assert second_page.has_more is False
    assert fake.research_calls == 1

    continuation_discovery = _studio_discovery([0, 25])
    fake.discovery = continuation_discovery
    fake.source_manifest = _manifest([0, 25])
    continuation = await service.research_more(
        initial.id,
        DiscoveryResearchMoreRequest(acknowledge_additional_api_usage=True),
        _local_day(),
    )
    await CompanyDiscoveryWorkflow(isolated_session, fake, settings).execute(continuation.id)

    completed_continuation = await service.get(continuation.id)
    combined = await service.results(initial.id, limit=100)
    company_names = [item.company_name for item in combined.items]
    assert completed_continuation.status == "succeeded"
    assert completed_continuation.cached is False
    assert completed_continuation.result_count == 1
    assert combined.total == 26
    assert len(company_names) == len(set(company_names))
    assert fake.research_calls == 2
    assert len(fake.research_exclusions[1]) == 25
    assert {item["official_domain"] for item in fake.research_exclusions[1]} == {
        f"studio-{index}.example" for index in range(25)
    }

    await service.hide(initial.id, combined.items[0].id)
    assert (await service.results(initial.id, limit=100)).items[0].is_hidden is True
    await service.show(initial.id, combined.items[0].id)
    assert (await service.results(initial.id, limit=100)).items[0].is_hidden is False

    cached_initial = await service.create(
        DiscoveryCreate(query="  GAMING STUDIOS "),
        _local_day(),
    )
    cached_initial_results = await service.results(cached_initial.id, limit=100)
    assert cached_initial.cached is True
    assert cached_initial_results.total == 25
    assert fake.research_calls == 2


def _generic_discovery(indices: list[int]) -> NormalizedDiscovery:
    """Candidates whose cited careers page is on their own domain, so the resolver must run."""

    discovery = _studio_discovery(indices)
    for company in discovery.companies:
        company.source_references[1].source_type = "official_company"
    return discovery


def _generic_manifest(indices: list[int]) -> list[dict[str, str | None]]:
    return [
        source
        for index in indices
        for source in (
            {
                "source_id": f"source_{index * 2 + 1}",
                "url": f"https://studio-{index}.example/about",
                "title": f"Studio {index} about",
            },
            {
                "source_id": f"source_{index * 2 + 2}",
                "url": f"https://studio-{index}.example/early-careers",
                "title": f"Studio {index} early careers",
            },
        )
    ]


class _DiscoveryFetcher:
    """Studio 0 lists jobs, studio 1 embeds a board, studio 2 is purely informational."""

    LISTING = "".join(
        f'<a href="/careers/job/role-{index}">Engineer {index}</a>' for index in range(4)
    )
    EMBEDDED = '<a href="https://boards.greenhouse.io/studio1">Open positions</a>'
    INFORMATIONAL = '<a href="/early-careers/students">Students</a><a href="/careers">Careers</a>'

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        del headers
        self.requests.append(url)
        if url.endswith("/robots.txt"):
            return SafeHttpResponse(200, url, {}, b"User-agent: *\nAllow: /")
        bodies = {
            "https://studio-0.example/early-careers": self.LISTING,
            "https://studio-1.example/early-careers": self.EMBEDDED,
            "https://studio-2.example/early-careers": self.INFORMATIONAL,
        }
        body = bodies.get(url)
        if body is None:
            return SafeHttpResponse(404, url, {}, b"")
        return SafeHttpResponse(200, url, {}, body.encode())


async def test_discovery_resolves_only_budgeted_careers_pages(
    isolated_session: AsyncSession,
) -> None:
    indices = [0, 1, 2, 3]
    fake = FakeDiscoveryAIClient(_generic_discovery(indices), _generic_manifest(indices))
    settings = Settings(
        app_env="test",
        openai_api_key="test-key",
        openai_research_model=fake.research_model,
        openai_structured_model=fake.structured_model,
        discovery_resolve_max_candidates=3,
    )
    service = DiscoveryService(isolated_session, settings, RecordingDispatcher())
    fetcher = _DiscoveryFetcher()

    run = await service.create(DiscoveryCreate(query="gaming studios"), _local_day())
    await CompanyDiscoveryWorkflow(isolated_session, fake, settings, fetcher).execute(run.id)

    results = {item.company_name: item for item in (await service.results(run.id, limit=100)).items}
    assert results["Studio 0"].monitoring_support == "generic_verified"
    assert results["Studio 0"].scores.careers_page_support == 75
    assert results["Studio 0"].scores.current_openings == 100
    assert results["Studio 1"].monitoring_support == "structured"
    assert results["Studio 1"].careers_url == "https://boards.greenhouse.io/studio1"
    assert results["Studio 2"].monitoring_support == "unsupported"
    assert results["Studio 2"].careers_url_status == "evidence_verified"
    assert results["Studio 2"].careers_url_reason == "CAREERS_PAGE_NO_LISTING_FOUND"

    # The fourth candidate is outside the budget: unresolved, never fetched, still shown.
    assert results["Studio 3"].monitoring_support == "generic_pending"
    assert results["Studio 3"].careers_url_reason == "CAREERS_RESOLUTION_SKIPPED_BUDGET"
    assert "https://studio-3.example/early-careers" not in fetcher.requests

    # A page with no listing must never become a pollable source.
    sources = list(
        (
            await isolated_session.scalars(
                select(CareerSource).join(Company, Company.id == CareerSource.company_id)
            )
        ).all()
    )
    monitored = {source.careers_url for source in sources}
    assert "https://studio-2.example/early-careers" not in monitored


async def test_discovery_without_a_fetcher_performs_no_http_and_stays_pending(
    isolated_session: AsyncSession,
) -> None:
    indices = [0, 1]
    fake = FakeDiscoveryAIClient(_generic_discovery(indices), _generic_manifest(indices))
    settings = Settings(
        app_env="test",
        openai_api_key="test-key",
        openai_research_model=fake.research_model,
        openai_structured_model=fake.structured_model,
    )
    service = DiscoveryService(isolated_session, settings, RecordingDispatcher())

    run = await service.create(DiscoveryCreate(query="gaming studios"), _local_day())
    await CompanyDiscoveryWorkflow(isolated_session, fake, settings).execute(run.id)

    results = await service.results(run.id, limit=100)
    assert results.items
    assert all(item.monitoring_support == "generic_pending" for item in results.items)
    assert all(item.scores.current_openings == 0 for item in results.items)


async def test_daily_history_uses_local_day_boundaries_and_lists_only_root_runs(
    isolated_session: AsyncSession,
) -> None:
    start = datetime(2026, 8, 29, 4, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    local_day = LocalDayWindow.validated(start, end, now=start + timedelta(hours=12))
    settings = Settings(
        app_env="test",
        openai_api_key="test-key",
        openai_research_model="fake-research",
        openai_structured_model="fake-structured",
    )
    service = DiscoveryService(isolated_session, settings)

    before = DiscoveryRun(
        raw_query="before local midnight",
        status="succeeded",
        cache_hit=False,
        requested_limit=20,
        created_at=start - timedelta(microseconds=1),
        completed_at=start - timedelta(microseconds=1),
    )
    at_start = DiscoveryRun(
        raw_query="at local midnight",
        status="succeeded",
        cache_hit=False,
        requested_limit=20,
        created_at=start,
        completed_at=start,
    )
    later = DiscoveryRun(
        raw_query="later today",
        status="queued",
        cache_hit=False,
        requested_limit=20,
        created_at=start + timedelta(hours=8),
    )
    at_end = DiscoveryRun(
        raw_query="at next local midnight",
        status="queued",
        cache_hit=False,
        requested_limit=20,
        created_at=end,
    )
    isolated_session.add_all([before, at_start, later, at_end])
    await isolated_session.flush()
    continuation = DiscoveryRun(
        root_discovery_run_id=later.id,
        continuation_index=1,
        raw_query=later.raw_query,
        status="succeeded",
        cache_hit=False,
        requested_limit=20,
        created_at=start + timedelta(hours=9),
        completed_at=start + timedelta(hours=9),
    )
    isolated_session.add(continuation)
    await isolated_session.commit()

    history = await service.daily_history(local_day)
    first_page = await service.daily_history(local_day, limit=1)
    second_page = await service.daily_history(
        local_day,
        limit=1,
        cursor=first_page.next_cursor,
    )

    assert [item.id for item in history.items] == [later.id, at_start.id]
    assert continuation.id not in {item.id for item in history.items}
    assert [item.id for item in first_page.items] == [later.id]
    assert first_page.next_cursor == later.id
    assert [item.id for item in second_page.items] == [at_start.id]
    assert second_page.next_cursor is None


async def test_daily_quota_uses_half_open_local_day_for_new_and_continuation_research(
    isolated_session: AsyncSession,
) -> None:
    start = datetime(2026, 8, 29, 4, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    now = start + timedelta(hours=12)
    local_day = LocalDayWindow.validated(start, end, now=now)
    settings = Settings(
        app_env="test",
        openai_api_key="test-key",
        openai_research_model="fake-research",
        openai_structured_model="fake-structured",
        discovery_daily_quota=2,
    )
    service = DiscoveryService(
        isolated_session,
        settings,
        RecordingDispatcher(),
        clock=lambda: now,
    )

    old_root_query = IndustryQuery(
        raw_query="old root",
        normalized_query="old root",
        country_code="US",
        interpretation_json={},
        prompt_version="test",
        model_id="fake-research|fake-structured",
        status="succeeded",
        expires_at=end + timedelta(days=7),
        created_at=start - timedelta(hours=2),
        completed_at=start - timedelta(hours=1),
    )
    at_start_query = IndustryQuery(
        raw_query="boundary included",
        normalized_query="boundary included",
        country_code="US",
        interpretation_json={},
        prompt_version="test",
        model_id="fake-research|fake-structured",
        status="failed",
        expires_at=end + timedelta(days=7),
        created_at=start,
    )
    at_end_query = IndustryQuery(
        raw_query="boundary excluded",
        normalized_query="boundary excluded",
        country_code="US",
        interpretation_json={},
        prompt_version="test",
        model_id="fake-research|fake-structured",
        status="pending",
        expires_at=end + timedelta(days=7),
        created_at=end,
    )
    isolated_session.add_all([old_root_query, at_start_query, at_end_query])
    await isolated_session.flush()
    old_root = DiscoveryRun(
        industry_query_id=old_root_query.id,
        raw_query="old root",
        status="succeeded",
        cache_hit=False,
        requested_limit=20,
        created_at=start - timedelta(hours=1),
        completed_at=start - timedelta(minutes=30),
    )
    isolated_session.add(old_root)
    await isolated_session.commit()

    accepted = await service.create(DiscoveryCreate(query="robotics companies"), local_day)

    assert accepted.status == "queued"
    with pytest.raises(AppError) as create_error:
        await service.create(DiscoveryCreate(query="biotech companies"), local_day)
    assert create_error.value.code == "DISCOVERY_DAILY_QUOTA_REACHED"
    assert create_error.value.status_code == 429

    accepted_run = await isolated_session.get(DiscoveryRun, accepted.id)
    assert accepted_run is not None
    accepted_query = await isolated_session.get(IndustryQuery, accepted_run.industry_query_id)
    assert accepted_query is not None
    accepted_run.status = "succeeded"
    accepted_run.completed_at = now
    accepted_query.status = "succeeded"
    accepted_query.completed_at = now
    await isolated_session.commit()

    query_count = await isolated_session.scalar(select(func.count()).select_from(IndustryQuery))
    cached = await service.create(DiscoveryCreate(query="  ROBOTICS COMPANIES "), local_day)
    assert cached.cached is True
    assert (
        await isolated_session.scalar(select(func.count()).select_from(IndustryQuery))
        == query_count
    )

    with pytest.raises(AppError) as continuation_error:
        await service.research_more(
            old_root.id,
            DiscoveryResearchMoreRequest(acknowledge_additional_api_usage=True),
            local_day,
        )
    assert continuation_error.value.code == "DISCOVERY_DAILY_QUOTA_REACHED"
    assert continuation_error.value.status_code == 429


async def test_uncaught_worker_failure_reaches_a_terminal_state(
    isolated_session: AsyncSession,
) -> None:
    settings = Settings(
        app_env="test",
        openai_api_key="test-key",
        openai_research_model="fake-research",
        openai_structured_model="fake-structured",
    )
    service = DiscoveryService(isolated_session, settings, RecordingDispatcher())
    queued = await service.create(DiscoveryCreate(query="healthcare"), _local_day())
    trace = AiRun(
        workflow="company_discovery",
        step="research",
        status="running",
        model_id="fake-research",
        prompt_version="test",
        schema_version="test",
        redacted_input={"discovery_run_id": str(queued.id)},
        source_manifest=[],
        started_at=datetime.now(UTC),
    )
    isolated_session.add(trace)
    await isolated_session.commit()

    changed = await DiscoveryFailureService(isolated_session).mark_failed(
        queued.id, "WORKER_DATABASE_ERROR"
    )
    repeated = await DiscoveryFailureService(isolated_session).mark_failed(
        queued.id, "DISCOVERY_FAILED"
    )
    failed = await service.get(queued.id)
    run = await isolated_session.get(DiscoveryRun, queued.id)
    assert run is not None
    query = await isolated_session.get(IndustryQuery, run.industry_query_id)
    await isolated_session.refresh(trace)

    assert changed is True
    assert repeated is False
    assert failed.status == "failed"
    assert failed.error_code == "WORKER_DATABASE_ERROR"
    assert failed.completed_at is not None
    assert query is not None
    assert query.status == "failed"
    assert query.completed_at is not None
    assert trace.status == "failed"
    assert trace.error_code == "WORKER_DATABASE_ERROR"
    assert trace.finished_at is not None
