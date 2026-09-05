from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db.models import AppSettings, Job
from app.notifications.matching import match_job


def settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "role_types": ["internship", "new_grad", "entry_level"],
        "keywords": [],
        "excluded_keywords": [],
        "preferred_locations": [],
        "remote_preference": "any",
        "notify_current_jobs_on_save": False,
        "email_notifications_enabled": False,
        "notification_email": None,
    }
    values.update(overrides)
    return AppSettings(**values)


def job(**overrides: object) -> Job:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "id": uuid4(),
        "career_source_id": uuid4(),
        "company_id": uuid4(),
        "external_job_id": "job-1",
        "canonical_job_key": "lever:job-1",
        "title": "Software Engineer Intern",
        "location_text": "New York, NY",
        "department": "Engineering",
        "employment_type": "Intern",
        "role_type": "internship",
        "description_text": "Work on Python services and distributed data pipelines.",
        "apply_url": "https://jobs.lever.co/acme/job-1/apply",
        "first_seen_at": now,
        "last_seen_at": now,
        "status": "active",
        "current_content_hash": "0" * 64,
        "consecutive_absences": 0,
    }
    values.update(overrides)
    return Job(**values)


def test_selected_role_with_no_filters_matches_and_explains_itself() -> None:
    decision = match_job(job(), settings())

    assert decision.matched
    assert decision.reasons == ("Role type: internship",)


@pytest.mark.parametrize("role", ["experienced", "unknown"])
def test_unselected_role_types_never_alert(role: str) -> None:
    decision = match_job(job(role_type=role), settings())

    assert not decision.matched
    assert decision.rejection_code == "ROLE_TYPE_NOT_SELECTED"


def test_excluded_keyword_in_description_blocks_the_alert() -> None:
    decision = match_job(job(), settings(excluded_keywords=["distributed"]))

    assert not decision.matched
    assert decision.rejection_code == "EXCLUDED_KEYWORD_PRESENT"


def test_required_keywords_need_one_hit_and_are_reported() -> None:
    configured = settings(keywords=["python", "rust"])

    matched = match_job(job(), configured)
    missed = match_job(job(description_text="Work on Java services."), configured)

    assert matched.matched
    assert "Keyword match: python" in matched.reasons
    assert not missed.matched
    assert missed.rejection_code == "NO_REQUIRED_KEYWORD"


def test_keyword_matching_uses_whole_tokens_not_substrings() -> None:
    decision = match_job(
        job(title="Data Intern", description_text="Scala tooling."), settings(keywords=["cal"])
    )

    assert not decision.matched


def test_remote_preference_requires_a_remote_location() -> None:
    remote_only = settings(remote_preference="remote")

    onsite = match_job(job(), remote_only)
    remote = match_job(job(location_text="Remote - United States"), remote_only)

    assert not onsite.matched
    assert onsite.rejection_code == "NOT_REMOTE"
    assert remote.matched
    assert "Work arrangement: remote" in remote.reasons


def test_onsite_preference_rejects_remote_postings() -> None:
    decision = match_job(job(location_text="Remote (US)"), settings(remote_preference="onsite"))

    assert not decision.matched
    assert decision.rejection_code == "NOT_ONSITE"


def test_hybrid_preference_requires_a_hybrid_location() -> None:
    hybrid_only = settings(remote_preference="hybrid")

    assert match_job(job(location_text="Hybrid - Boston, MA"), hybrid_only).matched
    assert match_job(job(), hybrid_only).rejection_code == "NOT_HYBRID"


def test_preferred_locations_filter_and_report_the_hit() -> None:
    configured = settings(preferred_locations=["New York", "Seattle"])

    matched = match_job(job(), configured)
    missed = match_job(job(location_text="Austin, TX"), configured)

    assert matched.matched
    assert "Location match: New York" in matched.reasons
    assert not missed.matched
    assert missed.rejection_code == "LOCATION_NOT_PREFERRED"


def test_remote_role_satisfies_a_location_filter_unless_onsite_is_required() -> None:
    remote_job = job(location_text="Remote, US")

    allowed = match_job(remote_job, settings(preferred_locations=["Seattle"]))
    refused = match_job(
        remote_job,
        settings(preferred_locations=["Seattle"], remote_preference="onsite"),
    )

    assert allowed.matched
    assert "Remote role, which satisfies your location filter" in allowed.reasons
    assert not refused.matched


def test_missing_location_text_cannot_satisfy_an_explicit_location_filter() -> None:
    decision = match_job(job(location_text=None), settings(preferred_locations=["New York"]))

    assert not decision.matched
    assert decision.rejection_code == "LOCATION_NOT_PREFERRED"
