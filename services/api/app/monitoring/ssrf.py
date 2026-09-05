from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit


class UnsafeUrlError(ValueError):
    """A URL failed JobScout's outbound-network safety policy."""


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def validate_public_url(url: str, resolver: Resolver | None = None) -> list[str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("URL_SCHEME_NOT_ALLOWED")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URL_CREDENTIALS_NOT_ALLOWED")
    if parsed.hostname is None:
        raise UnsafeUrlError("URL_HOST_REQUIRED")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrlError("URL_PORT_INVALID") from exc
    if port not in {80, 443}:
        raise UnsafeUrlError("URL_PORT_NOT_ALLOWED")

    hostname = parsed.hostname.rstrip(".").casefold()
    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or "." not in hostname
        or hostname.endswith((".local", ".internal", ".home", ".lan"))
    ):
        raise UnsafeUrlError("URL_HOST_NOT_PUBLIC")

    addresses: list[str]
    try:
        addresses = [_normalize_ip(hostname)]
    except ValueError:
        addresses = await (resolver or resolve_hostname)(hostname, port)
    if not addresses:
        raise UnsafeUrlError("URL_DNS_EMPTY")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise UnsafeUrlError("URL_DNS_INVALID") from exc
        if not ip.is_global:
            raise UnsafeUrlError("URL_IP_NOT_PUBLIC")
    return addresses


async def resolve_hostname(hostname: str, port: int) -> list[str]:
    try:
        results = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeUrlError("URL_DNS_FAILED") from exc
    return sorted({str(result[4][0]) for result in results})


def require_https(url: str) -> None:
    if urlsplit(url).scheme != "https":
        raise UnsafeUrlError("FINAL_URL_MUST_USE_HTTPS")


def _normalize_ip(hostname: str) -> str:
    candidate = hostname.removeprefix("[").removesuffix("]")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        # IPv4 parsers and resolvers accept legacy shorthand such as 127.1 and
        # integer/hex forms. Normalize those before treating a value as DNS.
        if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", candidate):
            try:
                return socket.inet_ntoa(socket.inet_aton(candidate))
            except OSError:
                pass
        raise
