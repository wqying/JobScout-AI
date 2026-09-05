import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import database_session
from app.companies.normalization import normalize_employer_name
from app.companies.schemas import SaveCompanyRequest
from app.companies.service import CompanyService
from app.db.models import (
    AppSettings,
    CareerSource,
    Company,
    CompanyLegalEntity,
    ImmigrationDatasetImport,
    OwnerProfile,
    SavedCompany,
)
from app.immigration.importer import LcaImporter
from app.main import app
from app.profiles.schemas import ProfileUpdate, SettingsUpdate
from app.profiles.service import ProfileService

FIXTURE = Path(__file__).parents[1] / "fixtures" / "dol" / "lca_synthetic.csv"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="Set RUN_INTEGRATION_TESTS=1 with local PostgreSQL and Redis running",
    ),
]


async def test_milestone_2_http_confirmation_flow(isolated_session: AsyncSession) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield isolated_session

    app.dependency_overrides[database_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            profile_response = await client.put(
                "/api/v1/profile",
                json={"display_name": "Ada Student", "email": "ada@example.com"},
            )
            resolution_response = await client.post(
                "/api/v1/companies/resolve",
                json={
                    "query": "Acme Games",
                    "careers_url": "https://jobs.lever.co/acme-http",
                },
            )
            company_id = resolution_response.json()["companies"][0]["id"]
            before_save = await client.get("/api/v1/companies")
            save_response = await client.post(
                f"/api/v1/companies/{company_id}/save",
                json={"notify_current_jobs": False},
            )
            after_save = await client.get("/api/v1/companies")
    finally:
        app.dependency_overrides.clear()

    assert profile_response.status_code == 200
    assert resolution_response.status_code == 200
    assert resolution_response.json()["resolution"] == "proposal"
    assert before_save.json()["items"] == []
    assert save_response.status_code == 200
    assert after_save.json()["items"][0]["id"] == company_id


async def test_onboarding_creates_one_profile_and_settings(isolated_session: AsyncSession) -> None:
    service = ProfileService(isolated_session)
    await service.upsert_profile(ProfileUpdate(display_name="Ada Student", email="ada@example.com"))
    await service.upsert_profile(ProfileUpdate(display_name="Ada Lovelace", email=None))
    settings = await service.update_settings(
        SettingsUpdate(role_types=["internship"], keywords=["python", "python", " ai "])
    )

    assert await isolated_session.scalar(select(func.count()).select_from(OwnerProfile)) == 1
    assert await isolated_session.scalar(select(func.count()).select_from(AppSettings)) == 1
    assert (await service.get_profile()).display_name == "Ada Lovelace"
    assert settings.keywords == ["python", "ai"]


async def test_resolve_requires_confirm_then_repeated_save_is_idempotent(
    isolated_session: AsyncSession,
) -> None:
    service = CompanyService(isolated_session)
    resolution = await service.resolve(
        "Acme Games",
        careers_url="https://acme-m2.example/careers",
    )
    company = resolution.companies[0]

    assert resolution.resolution == "proposal"
    assert company.is_saved is False
    assert company.warning is not None

    first = await service.save(company.id, SaveCompanyRequest())
    second = await service.save(company.id, SaveCompanyRequest())

    assert first.is_saved is True
    assert second.saved_company_id == first.saved_company_id
    assert await isolated_session.scalar(select(func.count()).select_from(Company)) == 1
    assert await isolated_session.scalar(select(func.count()).select_from(CareerSource)) == 1
    assert await isolated_session.scalar(select(func.count()).select_from(SavedCompany)) == 1


async def test_synthetic_dol_import_is_idempotent(isolated_session: AsyncSession) -> None:
    importer = LcaImporter(isolated_session)
    first = await importer.import_file(
        FIXTURE,
        2025,
        "https://www.dol.gov/example-lca.csv",
        "synthetic-v1",
    )
    second = await importer.import_file(
        FIXTURE,
        2025,
        "https://www.dol.gov/example-lca.csv",
        "synthetic-v1",
    )

    assert first.cached is False
    assert second.cached is True
    assert first.import_id == second.import_id
    assert (
        await isolated_session.scalar(select(func.count()).select_from(ImmigrationDatasetImport))
        == 1
    )


async def test_confirmed_exact_company_name_counts_historical_evidence(
    isolated_session: AsyncSession,
) -> None:
    await LcaImporter(isolated_session).import_file(
        FIXTURE,
        2025,
        "https://www.dol.gov/example-lca.csv",
        "synthetic-v1",
    )
    service = CompanyService(isolated_session)
    resolution = await service.resolve(
        "Acme Games",
        careers_url="https://jobs.lever.co/acme-exact",
    )

    company = await service.save(resolution.companies[0].id, SaveCompanyRequest())

    assert company.sponsorship.status == "historical_records"
    assert company.sponsorship.certified_cases == 2
    assert company.sponsorship.certified_workers == 3
    assert company.sponsorship.loaded_fiscal_years == [2025]


async def test_ai_proposed_legal_entity_is_not_counted(isolated_session: AsyncSession) -> None:
    await LcaImporter(isolated_session).import_file(
        FIXTURE,
        2025,
        "https://www.dol.gov/example-lca.csv",
        "synthetic-v1",
    )
    service = CompanyService(isolated_session)
    resolution = await service.resolve(
        "Acme Games",
        careers_url="https://jobs.lever.co/acmegames",
    )
    company_id = resolution.companies[0].id
    isolated_session.add(
        CompanyLegalEntity(
            company_id=company_id,
            legal_name="Acme Games Inc",
            normalized_legal_name=normalize_employer_name("Acme Games Inc"),
            match_method="ai_proposed",
            confidence=0.999,
        )
    )
    await isolated_session.commit()

    company = await service.get(company_id)

    assert company.sponsorship.status == "unresolved"
    assert company.sponsorship.certified_cases == 0
