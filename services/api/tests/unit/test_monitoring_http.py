import httpx
import pytest

from app.monitoring.http import SafeHttpClient
from app.monitoring.ssrf import UnsafeUrlError


async def public_resolver(_hostname: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


async def test_redirect_target_is_revalidated_before_following() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = SafeHttpClient(client=transport_client, resolver=public_resolver)
        with pytest.raises(UnsafeUrlError):
            await client.get("https://example.com/jobs")
    finally:
        await transport_client.aclose()

    assert calls == ["https://example.com/jobs"]


async def test_response_body_limit_is_enforced_while_streaming() -> None:
    transport_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"12345"))
    )
    try:
        client = SafeHttpClient(
            client=transport_client,
            resolver=public_resolver,
            max_response_bytes=4,
        )
        with pytest.raises(httpx.HTTPError, match="RESPONSE_TOO_LARGE"):
            await client.get("https://example.com/jobs")
    finally:
        await transport_client.aclose()
