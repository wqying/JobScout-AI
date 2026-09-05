import httpx

from app.main import app


async def test_validation_errors_use_safe_stable_envelope() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/companies/resolve",
            json={"query": "x"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "The request contains invalid or missing values.",
            "details": {},
        }
    }


async def test_manual_company_rejects_non_https_careers_url() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/companies/resolve",
            json={
                "query": "Acme Games",
                "careers_url": "http://jobs.lever.co/acme",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "INVALID_HTTPS_URL",
        "message": "Enter a valid public HTTPS URL.",
        "details": {},
    }
