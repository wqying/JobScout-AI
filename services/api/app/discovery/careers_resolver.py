"""Bounded discovery-time careers-page resolution (DESIGN_DOC.md Section 13.7).

``validate_proposal`` can only prove that a cited URL sits on the company's own domain. It cannot
prove that the page lists jobs, and neither can the research model -- it never opened the page
either. Left there, an informational early-careers page is indistinguishable from a real job board,
gets monitored, and the generic HTML adapter turns its navigation into fabricated jobs.

This module closes that gap with a bounded fetch: open the cited page, promote an embedded provider
board, count real job links, follow at most one hop toward a listing, and demote anything that still
has no listing so it is never monitored. Every failure falls back to the unresolved classification;
a company's website being down must never fail a discovery run.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

import structlog

from app.core.config import Settings
from app.discovery.validation import (
    MONITORABILITY_SCORES,
    STRUCTURED_PROVIDERS,
    CareersResolution,
    ValidatedProposal,
    structured_careers_url,
)
from app.monitoring.http import HttpFetcher
from app.monitoring.job_links import JobLink, extract_job_links, rank_listing_candidates
from app.monitoring.provider_detection import detect_provider
from app.monitoring.robots import ensure_robots_allows

RESOLVER_VERSION = "careers-resolver-v1"


@dataclass(frozen=True)
class ResolvedCareers:
    """A careers classification plus the opening count that produced it."""

    careers: CareersResolution
    job_link_count: int = 0


@dataclass(frozen=True)
class _PageOutcome:
    url: str
    html: str
    board_url: str | None
    job_links: list[JobLink]


def openings_score(job_link_count: int, minimum: int) -> int:
    """Score current openings from what the resolver actually counted."""

    if job_link_count >= minimum:
        return 100
    if job_link_count > 0:
        return 50
    return 0


def needs_resolution(item: ValidatedProposal) -> bool:
    return item.careers.url is not None and item.careers.monitoring_support == "generic_pending"


def provisional_rank_key(item: ValidatedProposal) -> tuple[int, int, int, str]:
    """Order candidates before resolution so the fetch budget buys the most visible results.

    Sponsorship is deliberately absent: it needs per-company database work that has not happened
    yet at this point in the workflow, and adding it here would serialize the whole stage.
    """

    return (
        -item.proposal.industry_relevance,
        -int(item.internship_verified),
        -item.careers.monitorability_score,
        item.proposal.canonical_name.casefold(),
    )


def skipped_for_budget(item: ValidatedProposal) -> ResolvedCareers:
    return ResolvedCareers(
        careers=replace(item.careers, reason="CAREERS_RESOLUTION_SKIPPED_BUDGET"),
        job_link_count=0,
    )


class CareersPageResolver:
    def __init__(
        self,
        http: HttpFetcher,
        settings: Settings,
        *,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self.http = http
        self.settings = settings
        self.logger = logger or structlog.get_logger("jobscout.discovery")

    async def resolve_all(self, items: list[ValidatedProposal]) -> dict[str, ResolvedCareers]:
        """Resolve `items` concurrently, returning results keyed by official domain.

        Candidates whose resolution did not finish before the stage deadline are simply absent from
        the result; the caller keeps their unresolved classification.
        """

        if not items:
            return {}
        semaphore = asyncio.Semaphore(max(1, self.settings.discovery_resolve_concurrency))

        async def run(item: ValidatedProposal) -> tuple[str, ResolvedCareers]:
            async with semaphore:
                return item.official_domain, await self.resolve(item)

        tasks = [asyncio.create_task(run(item)) for item in items]
        done, pending = await asyncio.wait(
            tasks, timeout=float(self.settings.discovery_resolve_deadline_seconds)
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            self.logger.info(
                "careers_resolution_deadline_reached",
                resolved=len(done),
                unresolved=len(pending),
            )
        resolved: dict[str, ResolvedCareers] = {}
        for task in done:
            if task.cancelled():
                continue
            try:
                domain, outcome = task.result()
            except Exception:  # Defensive: an unresolved candidate must never fail the run.
                self.logger.exception("careers_resolution_task_failed")
                continue
            resolved[domain] = outcome
        return resolved

    async def resolve(self, item: ValidatedProposal) -> ResolvedCareers:
        seed = item.careers.url
        if seed is None or not needs_resolution(item):
            return ResolvedCareers(careers=item.careers)

        try:
            seed_page = await self._inspect(seed)
        except Exception as exc:
            # Robots policy, SSRF rejection, HTTP failure, or a malformed page. All of them mean
            # "not verified", never "run failed".
            self.logger.info(
                "careers_resolution_unreachable",
                company_name=item.proposal.canonical_name,
                careers_url=seed,
                reason=_failure_code(exc),
            )
            return ResolvedCareers(careers=replace(item.careers, reason="CAREERS_PAGE_UNREACHABLE"))

        if seed_page.board_url is not None:
            return self._structured(item, seed_page.board_url, "CAREERS_PAGE_ATS_DISCOVERED")

        minimum = self.settings.discovery_resolve_min_job_links
        if len(seed_page.job_links) >= minimum:
            return self._listing(
                item, seed_page, "CAREERS_PAGE_LISTING_VERIFIED", seed_page.job_links
            )

        best = await self._follow_one_hop(item, seed_page, minimum)
        if best is not None:
            return best
        if seed_page.job_links:
            # A small board is still a board: keep a weak but real listing rather than
            # discarding it.
            return self._listing(
                item, seed_page, "CAREERS_PAGE_LISTING_VERIFIED", seed_page.job_links
            )

        self.logger.info(
            "careers_resolution_no_listing",
            company_name=item.proposal.canonical_name,
            careers_url=seed,
        )
        return ResolvedCareers(
            careers=replace(
                item.careers,
                reason="CAREERS_PAGE_NO_LISTING_FOUND",
                monitoring_support="unsupported",
                monitorability_score=MONITORABILITY_SCORES["unsupported"],
            )
        )

    async def _follow_one_hop(
        self,
        item: ValidatedProposal,
        seed_page: _PageOutcome,
        minimum: int,
    ) -> ResolvedCareers | None:
        """Try the best listing-shaped links on the seed page. Hops never follow hops."""

        candidates = rank_listing_candidates(
            seed_page.html,
            seed_page.url,
            self.settings.discovery_resolve_max_link_candidates,
        )
        fallback: ResolvedCareers | None = None
        for candidate in candidates:
            try:
                page = await self._inspect(candidate.url)
            except Exception:  # One unusable hop target must not end the hop search.
                continue
            if page.board_url is not None:
                return self._structured(item, page.board_url, "CAREERS_PAGE_ATS_DISCOVERED")
            if len(page.job_links) >= minimum:
                return self._listing(
                    item, page, "CAREERS_PAGE_LISTING_VERIFIED_VIA_LINK", page.job_links
                )
            if page.job_links and fallback is None:
                fallback = self._listing(
                    item, page, "CAREERS_PAGE_LISTING_VERIFIED_VIA_LINK", page.job_links
                )
        return fallback

    async def _inspect(self, url: str) -> _PageOutcome:
        await ensure_robots_allows(self.http, url)
        response = await self.http.get(url, headers={"Accept": "text/html"})
        if response.status_code >= 400:
            raise ValueError(f"CAREERS_PAGE_HTTP_{response.status_code}")
        final_url = response.url or url
        html = response.text
        detection = detect_provider(final_url, html)
        board_url: str | None = None
        if detection.provider in STRUCTURED_PROVIDERS:
            board_url = structured_careers_url(detection, detection.matched_url or final_url)
        return _PageOutcome(
            url=final_url,
            html=html,
            board_url=board_url,
            job_links=[] if board_url else extract_job_links(html, final_url),
        )

    def _structured(self, item: ValidatedProposal, board_url: str, reason: str) -> ResolvedCareers:
        self.logger.info(
            "careers_resolution_structured",
            company_name=item.proposal.canonical_name,
            careers_url=board_url,
            reason=reason,
        )
        return ResolvedCareers(
            careers=replace(
                item.careers,
                url=board_url,
                url_status="evidence_verified",
                reason=reason,
                monitoring_support="structured",
                monitorability_score=MONITORABILITY_SCORES["structured"],
            )
        )

    def _listing(
        self,
        item: ValidatedProposal,
        page: _PageOutcome,
        reason: str,
        job_links: list[JobLink],
    ) -> ResolvedCareers:
        self.logger.info(
            "careers_resolution_listing",
            company_name=item.proposal.canonical_name,
            careers_url=page.url,
            reason=reason,
            job_link_count=len(job_links),
        )
        return ResolvedCareers(
            careers=replace(
                item.careers,
                url=_https_only(page.url) or item.careers.url,
                url_status="evidence_verified",
                reason=reason,
                monitoring_support="generic_verified",
                monitorability_score=MONITORABILITY_SCORES["generic_verified"],
            ),
            job_link_count=len(job_links),
        )


def _https_only(url: str) -> str | None:
    return url if urlsplit(url).scheme.casefold() == "https" else None


def _failure_code(error: Exception) -> str:
    text = str(error)
    if text and len(text) <= 80 and text.replace("_", "").isalnum():
        return text.upper()
    return type(error).__name__
