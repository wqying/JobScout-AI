from datetime import UTC, datetime
from uuid import UUID

from app.monitoring.job_identity import (
    canonical_job_key,
    canonicalize_url,
    classify_role,
    content_hash,
)
from app.monitoring.schemas import NormalizedJob


def job(**changes: object) -> NormalizedJob:
    values: dict[str, object] = {
        "external_job_id": "ABC-123",
        "title": "Software Engineer Intern",
        "location_text": "New York, NY",
        "department": "Engineering",
        "employment_type": "Intern",
        "description_text": "Build useful systems.",
        "apply_url": "https://jobs.example.com/posting?utm_source=test&id=123",
        "source_posted_at": datetime(2026, 8, 1, tzinfo=UTC),
    }
    values.update(changes)
    return NormalizedJob.model_validate(values)


def test_external_id_is_preferred_for_job_identity() -> None:
    company_id = UUID("00000000-0000-0000-0000-000000000001")

    assert canonical_job_key("greenhouse", company_id, job()) == "greenhouse:ABC-123"


def test_content_hash_ignores_tracking_parameters_but_detects_material_change() -> None:
    first = job()
    tracking_change = job(apply_url="https://jobs.example.com/posting?id=123&utm_campaign=summer")
    title_change = job(title="Software Engineer Intern II")

    assert content_hash(first) == content_hash(tracking_change)
    assert content_hash(first) != content_hash(title_change)
    assert canonicalize_url(str(first.apply_url)).endswith("?id=123")


def test_role_classification_is_deterministic_and_conservative() -> None:
    assert classify_role("Software Engineering Intern") == "internship"
    assert classify_role("University New Graduate Engineer") == "new_grad"
    assert classify_role("Junior Data Analyst") == "entry_level"
    assert classify_role("Senior Staff Engineer") == "experienced"
    assert classify_role("Software Engineer") == "unknown"
