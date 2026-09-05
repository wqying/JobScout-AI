"""Shared robots.txt policy.

The generic HTML adapter and the discovery-time careers-page resolver must apply exactly the same
policy and raise exactly the same stable codes, so the rule lives in one place. Section 13.3 of
`DESIGN_DOC.md` documents the codes.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from app.monitoring.http import HttpFetcher

USER_AGENT = "JobScout-AI"


async def ensure_robots_allows(http: HttpFetcher, target_url: str) -> None:
    """Raise ``ValueError`` with a stable code when robots policy forbids or cannot be read."""

    parsed = urlsplit(target_url)
    robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    response = await http.get(robots_url, headers={"Accept": "text/plain"})
    if response.status_code in {401, 403}:
        # Keep an explicit robots.txt Disallow distinct from an HTTP access denial, while making the
        # denial diagnosable without exposing response content.
        raise ValueError(f"ROBOTS_TXT_HTTP_{response.status_code}")
    if response.status_code == 429 or response.status_code >= 500:
        # A temporary robots-policy failure is not permission to crawl. Retry through the source
        # backoff path instead of fetching the target or retiring the source.
        raise ValueError("ROBOTS_FETCH_FAILED")
    if response.status_code < 400:
        robots = RobotFileParser()
        robots.parse(response.text.splitlines())
        if not robots.can_fetch(USER_AGENT, target_url):
            raise ValueError("ROBOTS_DISALLOWED")
