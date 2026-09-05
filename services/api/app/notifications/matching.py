"""Deterministic job-preference matching (Section 15.2).

V1 spends no LLM call per polled job. A job matches the owner's saved preferences when all of
these hold, evaluated in order:

1. the job's classified ``role_type`` is one of the selected ``role_types``;
2. no configured excluded keyword appears in the title or description;
3. if required keywords are configured, at least one of them appears in the title or description;
4. the configured ``remote_preference`` is satisfied by the job's location text;
5. if preferred locations are configured, the location text names one of them, or the job is
   remote and the owner did not ask for on-site work only.

Comparisons run over normalized text (lowercased, punctuation collapsed to spaces) and match whole
tokens, so ``ny`` does not match ``anywhere``. Every accepted job carries a compact list of reasons
that is stored with the notification, so an alert can always explain itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.db.models import AppSettings, Job
from app.monitoring.job_identity import normalize_text

MATCHING_RULES_VERSION = "preference-matching-v1"

# Descriptions are bounded so that one enormous posting cannot dominate a poll.
MAX_DESCRIPTION_CHARS = 20_000

_REMOTE_PATTERN = re.compile(r"\b(remote|remotely|work from home|wfh|distributed|anywhere)\b")
_HYBRID_PATTERN = re.compile(r"\bhybrid\b")


@dataclass(frozen=True)
class MatchDecision:
    """Why one job did or did not qualify for an alert."""

    matched: bool
    reasons: tuple[str, ...] = field(default=())
    rejection_code: str | None = None


def match_job(job: Job, settings: AppSettings) -> MatchDecision:
    reasons: list[str] = []

    if job.role_type not in set(settings.role_types):
        return MatchDecision(False, rejection_code="ROLE_TYPE_NOT_SELECTED")
    reasons.append(f"Role type: {_humanize(job.role_type)}")

    haystack = _padded(f"{job.title} {job.description_text[:MAX_DESCRIPTION_CHARS]}")

    for excluded in settings.excluded_keywords:
        needle = normalize_text(excluded)
        if needle and f" {needle} " in haystack:
            return MatchDecision(False, rejection_code="EXCLUDED_KEYWORD_PRESENT")

    required = [keyword for keyword in settings.keywords if normalize_text(keyword)]
    if required:
        hits = [keyword for keyword in required if f" {normalize_text(keyword)} " in haystack]
        if not hits:
            return MatchDecision(False, rejection_code="NO_REQUIRED_KEYWORD")
        reasons.append(f"Keyword match: {', '.join(hits)}")

    location = normalize_text(job.location_text or "")
    is_remote = bool(_REMOTE_PATTERN.search(location))
    is_hybrid = bool(_HYBRID_PATTERN.search(location))
    preference = settings.remote_preference
    if preference == "remote" and not is_remote:
        return MatchDecision(False, rejection_code="NOT_REMOTE")
    if preference == "hybrid" and not is_hybrid:
        return MatchDecision(False, rejection_code="NOT_HYBRID")
    if preference == "onsite" and is_remote:
        return MatchDecision(False, rejection_code="NOT_ONSITE")
    if preference != "any":
        reasons.append(f"Work arrangement: {preference}")

    preferred = [item for item in settings.preferred_locations if normalize_text(item)]
    if preferred:
        padded_location = _padded(job.location_text or "")
        hits = [item for item in preferred if f" {normalize_text(item)} " in padded_location]
        if hits:
            reasons.append(f"Location match: {', '.join(hits)}")
        elif is_remote and preference != "onsite":
            reasons.append("Remote role, which satisfies your location filter")
        else:
            return MatchDecision(False, rejection_code="LOCATION_NOT_PREFERRED")

    return MatchDecision(True, tuple(reasons))


def _padded(value: str) -> str:
    return f" {normalize_text(value)} "


def _humanize(value: str) -> str:
    return value.replace("_", " ")
