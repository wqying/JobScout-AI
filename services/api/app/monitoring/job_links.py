"""Shared job-link extraction rules (`job-links-v2`).

Both the discovery-time careers-page resolver and the generic HTML polling adapter read job links
out of server-rendered pages. They must agree: if the resolver verifies a page with one rule and the
poller scrapes it with another, a source can verify and then produce jobs that were never verified.
Section 13.3 of `DESIGN_DOC.md` specifies the rules implemented here.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from itertools import pairwise
from urllib.parse import urljoin, urlsplit, urlunsplit

RULES_VERSION = "job-links-v2"

ATS_HOSTS = frozenset(
    {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "jobs.lever.co",
        "jobs.eu.lever.co",
        "jobs.ashbyhq.com",
        "careers.smartrecruiters.com",
        "jobs.smartrecruiters.com",
    }
)

_JOB_PATH_MARKERS = ("job", "career", "position", "opening", "vacanc")

# A final path segment naming a section or index, never one individual opening.
_INDEX_SEGMENTS = frozenset(
    {
        "about",
        "alerts",
        "all",
        "apply",
        "application",
        "benefits",
        "blog",
        "campus",
        "career",
        "careers",
        "contact",
        "culture",
        "departments",
        "diversity",
        "early-career",
        "early-careers",
        "events",
        "faq",
        "grads",
        "graduates",
        "home",
        "index",
        "internship",
        "internships",
        "job",
        "job-alerts",
        "jobs",
        "life",
        "location",
        "locations",
        "login",
        "news",
        "opening",
        "openings",
        "opportunities",
        "opportunity",
        "overview",
        "position",
        "positions",
        "register",
        "role",
        "roles",
        "search",
        "signin",
        "sign-in",
        "student",
        "students",
        "talent-community",
        "talent-network",
        "team",
        "teams",
        "university",
        "vacancies",
        "values",
        "why-us",
        "work-with-us",
    }
)

# Substrings that mark a final segment as navigation regardless of its exact spelling.
# Pagination segments: `/early-careers/page/2` is another index page, never one opening.
_PAGINATION_SEGMENTS = frozenset({"page", "pages", "p", "pg", "offset", "start"})

_INDEX_SEGMENT_FRAGMENTS = (
    "recruiting",
    "job-alert",
    "jobalert",
    "sitemap",
    "privacy",
    "terms",
    "cookie",
    # Plural program nouns are deliberate: `interns-and-university-graduates` is a program
    # index, while a real posting reads `graduate-software-engineer` and survives.
    "life-at",
    "why-",
    "faqs",
    "asked-question",
    "-graduates",
    "interns-and",
    "co-ops",
)

# Anchor text that names a destination rather than a role. Matched as whole-string equality or as a
# phrase prefix -- never as a bare substring, so that a role called "Search Engineer" survives while
# a link labelled "Search jobs" does not.
_NAV_TEXT_EXACT = frozenset(
    {
        "all jobs",
        "apply",
        "apply now",
        "back",
        "browse",
        "careers",
        "contact",
        "explore",
        "home",
        "jobs",
        "learn more",
        "log in",
        "login",
        "more",
        "next",
        "openings",
        "opportunities",
        "positions",
        "previous",
        "read more",
        "register",
        "roles",
        "search",
        "see more",
        "sign in",
        "sign up",
        "students",
        "view",
        "view more",
    }
)

_NAV_TEXT_PREFIXES = (
    "all current",
    "all open",
    "back to",
    "browse ",
    "campus recruit",
    "current opening",
    "explore ",
    "find a job",
    "find jobs",
    "go to",
    "internship program",
    "join our",
    "join us",
    "learn more",
    "life at",
    "meet ",
    "our benefit",
    "our culture",
    "our team",
    "read more",
    "search all",
    "search job",
    "search open",
    "see all",
    "see open",
    "sign in",
    "skip to",
    "student program",
    "talent community",
    "talent network",
    "university recruit",
    "view all",
    "view open",
    "why join",
    "why work",
    "why ",
)

# A title that ends in a category noun names a group of roles, not one role: "UK Jobs",
# "Engineering Careers". A real posting title does not end this way.
_NAV_TEXT_SUFFIXES = (
    " jobs",
    " careers",
    " openings",
    " opportunities",
    " roles",
    " positions",
    " vacancies",
)

# Positive signals used only to pick hop targets. A false positive here costs one bounded fetch, so
# plain substring matching is acceptable where it is not acceptable for the rejection rules above.
_LISTING_TEXT_SIGNALS: tuple[tuple[str, int], ...] = (
    ("view all job", 60),
    ("see all job", 60),
    ("all open position", 60),
    ("all open role", 60),
    ("open position", 55),
    ("open role", 55),
    ("current opening", 55),
    ("search job", 50),
    ("job search", 50),
    ("browse job", 50),
    ("view opening", 50),
    ("view all", 40),
    ("see all", 40),
    ("all jobs", 45),
    ("all opportunities", 35),
    ("explore job", 35),
    ("find jobs", 40),
    ("job board", 45),
    ("openings", 30),
    ("opportunities", 20),
    ("jobs", 20),
    ("careers", 10),
)

_LISTING_PATH_SEGMENTS = frozenset(
    {"jobs", "job", "openings", "positions", "opportunities", "roles", "search", "all"}
)

MIN_LINKS_FOR_CLUSTERING = 5
CLUSTER_MAJORITY_PERCENT = 60


@dataclass(frozen=True)
class JobLink:
    url: str
    title: str


@dataclass(frozen=True)
class ListingCandidate:
    url: str
    title: str
    score: int


class AnchorParser(HTMLParser):
    """Collect every anchor as a resolved (url, text) pair.

    Anchors are collected first and filtered afterwards so that one parse serves both job-link
    extraction and hop-target ranking.
    """

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.anchors: list[tuple[str, str]] = []
        self._current_url: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        # Nested anchors are invalid HTML but do occur; close the open one rather than losing it.
        self._flush()
        href = dict(attrs).get("href")
        if not href or href.strip().startswith(("#", "javascript:", "mailto:", "tel:")):
            return
        try:
            self._current_url = urljoin(self.page_url, href.strip())
        except ValueError:
            self._current_url = None
        self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_url:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a":
            self._flush()

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        if self._current_url:
            self.anchors.append((self._current_url, " ".join(" ".join(self._current_text).split())))
        self._current_url = None
        self._current_text = []


def parse_anchors(html: str, page_url: str) -> list[tuple[str, str]]:
    parser = AnchorParser(page_url)
    parser.feed(html)
    parser.close()
    return parser.anchors


def extract_job_links(html: str, page_url: str) -> list[JobLink]:
    """Return the anchors on `page_url` that name one individual job opening."""

    base_host = _registrable_host(page_url)
    page_key = canonical_link(page_url)
    links = [
        JobLink(url=url, title=text)
        for url, text in parse_anchors(html, page_url)
        if _is_job_detail(url, text, base_host, page_key)
    ]
    return _keep_dominant_cluster(_dedupe(links))


def rank_listing_candidates(html: str, page_url: str, limit: int) -> list[ListingCandidate]:
    """Return the anchors most likely to lead from an informational page to a real job listing."""

    if limit <= 0:
        return []
    base_host = _registrable_host(page_url)
    page_key = canonical_link(page_url)
    best: dict[str, ListingCandidate] = {}
    for url, text in parse_anchors(html, page_url):
        parts = urlsplit(url)
        if parts.scheme.casefold() not in {"http", "https"}:
            continue
        host = _host_of(url)
        is_ats = host in ATS_HOSTS
        if not is_ats and not _same_site(host, base_host):
            continue
        key = canonical_link(url)
        if key == page_key:
            continue
        score = _listing_score(text, parts.path.casefold(), is_ats=is_ats)
        if score <= 0:
            continue
        current = best.get(key)
        if current is None or score > current.score:
            best[key] = ListingCandidate(url=url, title=text, score=score)
    ordered = sorted(best.values(), key=lambda candidate: (-candidate.score, candidate.url))
    return ordered[:limit]


def canonical_link(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), f"{host}{port}", path, parts.query, ""))


def _is_job_detail(url: str, text: str, base_host: str, page_key: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme.casefold() not in {"http", "https"}:
        return False
    host = _host_of(url)
    is_ats = host in ATS_HOSTS
    if not is_ats and not _same_site(host, base_host):
        return False
    path = parts.path.casefold()
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        return False
    if not is_ats and not any(marker in path for marker in _JOB_PATH_MARKERS):
        return False
    last = segments[-1]
    if last in _INDEX_SEGMENTS:
        return False
    if any(fragment in last for fragment in _INDEX_SEGMENT_FRAGMENTS):
        return False
    if _is_paginated(segments):
        return False
    if not _is_job_title(text):
        return False
    return canonical_link(url) != page_key


def _is_paginated(segments: list[str]) -> bool:
    return any(
        previous in _PAGINATION_SEGMENTS and segment.isdigit()
        for previous, segment in pairwise(segments)
    )


def _is_job_title(text: str) -> bool:
    normalized = " ".join(text.split())
    if not 3 <= len(normalized) <= 150:
        return False
    if not any(character.isalpha() for character in normalized):
        return False
    folded = normalized.casefold()
    if folded in _NAV_TEXT_EXACT or folded.startswith(_NAV_TEXT_PREFIXES):
        return False
    return not folded.endswith(_NAV_TEXT_SUFFIXES)


def _listing_score(text: str, path: str, *, is_ats: bool) -> int:
    folded = " ".join(text.split()).casefold()
    score = 60 if is_ats else 0
    for phrase, value in _LISTING_TEXT_SIGNALS:
        if phrase in folded:
            score += value
            break
    segments = [segment for segment in path.split("/") if segment]
    if segments and segments[-1] in _LISTING_PATH_SEGMENTS:
        score += 25
    elif any(marker in path for marker in _JOB_PATH_MARKERS):
        score += 10
    return score


def _dedupe(links: list[JobLink]) -> list[JobLink]:
    seen: dict[str, JobLink] = {}
    for link in links:
        seen.setdefault(canonical_link(link.url), link)
    return list(seen.values())


def _keep_dominant_cluster(links: list[JobLink]) -> list[JobLink]:
    """Drop residual page chrome from a real listing without discarding a small board."""

    if len(links) < MIN_LINKS_FOR_CLUSTERING:
        return links
    groups: dict[tuple[str, str], list[JobLink]] = defaultdict(list)
    for link in links:
        parts = urlsplit(link.url)
        segments = [segment for segment in parts.path.casefold().split("/") if segment]
        groups[(_host_of(link.url), "/".join(segments[:-1]))].append(link)
    largest = max(groups.values(), key=len)
    if len(largest) * 100 >= len(links) * CLUSTER_MAJORITY_PERCENT:
        return largest
    return links


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.")


def _registrable_host(url: str) -> str:
    return _host_of(url)


def _same_site(host: str, base_host: str) -> bool:
    if not host or not base_host:
        return False
    return host == base_host or host.endswith(f".{base_host}") or base_host.endswith(f".{host}")
