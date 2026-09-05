from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from app.monitoring.ssrf import UnsafeUrlError, validate_public_url


def resolver_for(*addresses: str) -> Callable[[str, int], Awaitable[list[str]]]:
    async def resolve(_hostname: str, _port: int) -> list[str]:
        return list(addresses)

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:password@example.com/jobs",
        "https://localhost/jobs",
        "https://localhost.example@127.0.0.1/jobs",
        "https://127.1/jobs",
        "https://[::1]/jobs",
        "https://169.254.169.254/latest/meta-data",
        "https://example.com:8443/jobs",
        "https://printer.local/jobs",
        "https://intranet/jobs",
    ],
)
async def test_ssrf_bypass_urls_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        await validate_public_url(url, resolver_for("93.184.216.34"))


@pytest.mark.parametrize(
    "address",
    ["10.0.0.1", "172.16.0.1", "192.168.1.1", "100.64.0.1", "fe80::1", "fc00::1"],
)
async def test_dns_results_must_all_be_public(address: str) -> None:
    with pytest.raises(UnsafeUrlError, match="URL_IP_NOT_PUBLIC"):
        await validate_public_url("https://example.com/jobs", resolver_for(address))


async def test_public_https_url_is_accepted() -> None:
    assert await validate_public_url(
        "https://example.com/jobs", resolver_for("93.184.216.34", "2606:2800:220:1::")
    ) == ["93.184.216.34", "2606:2800:220:1::"]
