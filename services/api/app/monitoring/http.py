from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from app.monitoring.ssrf import Resolver, require_https, validate_public_url

MAX_RESPONSE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class SafeHttpResponse:
    status_code: int
    url: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> object:
        return httpx.Response(self.status_code, content=self.body).json()

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class HttpFetcher(Protocol):
    async def get(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse: ...


class DomainRateLimiter(Protocol):
    async def acquire(self, hostname: str) -> None: ...


class RedisEvalClient(Protocol):
    async def eval(
        self, script: str, numkeys: int, *keys_and_args: object
    ) -> int | float | str | bytes: ...


class NoopRateLimiter:
    async def acquire(self, hostname: str) -> None:
        del hostname


class RedisDomainRateLimiter:
    """Small Redis-backed token bucket shared by API and worker processes."""

    _SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])
local data = redis.call('HMGET', key, 'tokens', 'updated')
local tokens = tonumber(data[1]) or capacity
local updated = tonumber(data[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - updated) * rate)
if tokens < 1 then
  redis.call('HMSET', key, 'tokens', tokens, 'updated', now)
  redis.call('EXPIRE', key, 120)
  return math.ceil((1 - tokens) / rate)
end
redis.call('HMSET', key, 'tokens', tokens - 1, 'updated', now)
redis.call('EXPIRE', key, 120)
return 0
"""

    def __init__(self, redis_client: RedisEvalClient, requests_per_second: float = 1.0) -> None:
        self.redis = redis_client
        self.requests_per_second = requests_per_second

    async def acquire(self, hostname: str) -> None:
        domain = registrable_domain(hostname)
        while True:
            now = time.time()
            wait_seconds = await self.redis.eval(
                self._SCRIPT,
                1,
                f"jobscout:rate:{domain}",
                now,
                self.requests_per_second,
                2,
            )
            if int(wait_seconds) <= 0:
                return
            await asyncio.sleep(min(float(wait_seconds), 5.0))


class SafeHttpClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
        rate_limiter: DomainRateLimiter | None = None,
        max_attempts: int = 3,
        max_redirects: int = 3,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0),
            headers={"User-Agent": "JobScout-AI/0.1 (local monitoring)"},
        )
        self._resolver = resolver
        self._limiter = rate_limiter or NoopRateLimiter()
        self._max_attempts = max_attempts
        self._max_redirects = max_redirects
        self._max_response_bytes = max_response_bytes

    async def __aenter__(self) -> SafeHttpClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        current_url = url
        redirects = 0
        while True:
            await validate_public_url(current_url, self._resolver)
            hostname = urlsplit(current_url).hostname or ""
            await self._limiter.acquire(hostname)
            response = await self._request_with_retries(current_url, headers or {})
            if response.status_code not in {301, 302, 303, 307, 308}:
                try:
                    require_https(str(response.url))
                except Exception:
                    await response.aclose()
                    raise
                return await self._read_bounded(response)
            if redirects >= self._max_redirects:
                await response.aclose()
                raise httpx.TooManyRedirects("More than three redirects", request=response.request)
            location = response.headers.get("location")
            if not location:
                await response.aclose()
                raise httpx.HTTPError("Redirect response has no Location header")
            current_url = urljoin(str(response.url), location)
            await response.aclose()
            redirects += 1

    async def _request_with_retries(self, url: str, headers: dict[str, str]) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                request = self._client.build_request("GET", url, headers=headers)
                response = await self._client.send(request, stream=True)
                if response.status_code not in {429, 500, 502, 503, 504}:
                    return response
                last_error = httpx.HTTPStatusError(
                    "Retriable response", request=response.request, response=response
                )
                if attempt + 1 < self._max_attempts:
                    await response.aclose()
                    await asyncio.sleep(_retry_delay(response, attempt))
                else:
                    await response.aclose()
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep((2**attempt) + random.random())
        assert last_error is not None
        raise last_error

    async def _read_bounded(self, response: httpx.Response) -> SafeHttpResponse:
        try:
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > self._max_response_bytes:
                        raise httpx.HTTPError("RESPONSE_TOO_LARGE")
                except ValueError:
                    pass
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._max_response_bytes:
                    raise httpx.HTTPError("RESPONSE_TOO_LARGE")
                chunks.append(chunk)
            return SafeHttpResponse(
                status_code=response.status_code,
                url=str(response.url),
                headers={key.casefold(): value for key, value in response.headers.items()},
                body=b"".join(chunks),
            )
        finally:
            await response.aclose()


def conditional_headers(etag: str | None, last_modified: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return headers


def registrable_domain(hostname: str) -> str:
    labels = hostname.casefold().rstrip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    common_two_level_suffixes = {"co.uk", "com.au", "co.jp", "co.nz"}
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in common_two_level_suffixes else suffix


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return min(float(retry_after), 60.0)
        except ValueError:
            try:
                delta = parsedate_to_datetime(retry_after).timestamp() - time.time()
                return float(max(0.0, min(delta, 60.0)))
            except (TypeError, ValueError):
                pass
    return float((2**attempt) + random.random())
