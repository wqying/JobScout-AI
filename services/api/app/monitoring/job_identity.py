from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from app.monitoring.schemas import NormalizedJob

RoleType = Literal["internship", "new_grad", "entry_level", "experienced", "unknown"]
_TRACKING_KEYS = {"gh_src", "lever-source", "source", "ref", "referrer"}


def canonical_job_key(
    provider: str,
    company_id: UUID,
    job: NormalizedJob,
) -> str:
    if job.external_job_id:
        return f"{provider}:{job.external_job_id}"
    canonical_url = canonicalize_url(str(job.apply_url))
    if canonical_url:
        return f"url:{canonical_url}"
    fallback = "\n".join(
        (
            str(company_id),
            normalize_text(job.title),
            normalize_text(job.location_text or ""),
        )
    )
    return f"fallback:{hashlib.sha256(fallback.encode()).hexdigest()}"


def content_hash(job: NormalizedJob) -> str:
    return hashlib.sha256(canonical_json(job).encode()).hexdigest()


def canonical_payload(job: NormalizedJob) -> dict[str, Any]:
    return {
        "title": " ".join(job.title.split()),
        "location_text": optional_clean(job.location_text),
        "department": optional_clean(job.department),
        "employment_type": optional_clean(job.employment_type),
        "description_text": " ".join(job.description_text.split()),
        "apply_url": canonicalize_url(str(job.apply_url)),
        "source_posted_at": job.source_posted_at.isoformat() if job.source_posted_at else None,
    }


def canonical_json(job: NormalizedJob) -> str:
    return json.dumps(canonical_payload(job), sort_keys=True, separators=(",", ":"))


def canonicalize_url(value: str) -> str:
    parsed = urlsplit(value)
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_KEYS
        )
    )
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, query, ""))


def classify_role(title: str, description: str = "") -> RoleType:
    title_text = normalize_text(title)
    combined = f"{title_text} {normalize_text(description[:4000])}"
    if re.search(r"\b(intern|internship|co[ -]?op)\b", title_text):
        return "internship"
    if re.search(r"\b(new grad|new graduate|recent graduate|university grad)\b", combined):
        return "new_grad"
    if re.search(r"\b(entry[ -]?level|junior|associate|early career)\b", title_text):
        return "entry_level"
    if re.search(r"\b(senior|staff|principal|lead|manager|director|[3-9]\+? years)\b", combined):
        return "experienced"
    return "unknown"


def normalize_text(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def optional_clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def event_dedupe_key(job_id: UUID, event_type: str, poll_run_id: UUID) -> str:
    return f"{job_id}:{event_type}:{poll_run_id}"
