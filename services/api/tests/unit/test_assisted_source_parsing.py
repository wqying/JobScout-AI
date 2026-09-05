from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.api.errors import AppError
from app.assisted_sources.eml import MAX_EML_BYTES, parse_eml
from app.assisted_sources.schemas import (
    ReviewReminderCreateRequest,
    ReviewReminderRescheduleRequest,
)


def multipart_alert() -> bytes:
    return b"\r\n".join(
        [
            b"From: Acme Careers <careers@acme.example>",
            b"Subject: New engineering roles at Acme",
            b"Date: Sat, 29 Aug 2026 09:30:00 -0400",
            b"Message-ID: <ALERT-123@acme.example>",
            b"MIME-Version: 1.0",
            b'Content-Type: multipart/mixed; boundary="outer"',
            b"",
            b"--outer",
            b'Content-Type: multipart/alternative; boundary="alternative"',
            b"",
            b"--alternative",
            b'Content-Type: text/plain; charset="utf-8"',
            b"",
            b"Software Engineer Intern: https://jobs.lever.co/acme/job-1?utm_source=email",
            b"Unsubscribe: https://acme.example/unsubscribe",
            b"--alternative",
            b'Content-Type: text/html; charset="utf-8"',
            b"",
            (
                b'<html><body><a href="https://jobs.lever.co/acme/job-1?utm_source=email">'
                b"Software Engineer Intern</a>"
                b'<a href="javascript:alert(1)">Unsafe job</a>'
                b'<a href="https://acme.example/email-preferences">Preferences</a></body></html>'
            ),
            b"--alternative--",
            b"--outer",
            b'Content-Type: text/plain; name="not-a-job.txt"',
            b'Content-Disposition: attachment; filename="not-a-job.txt"',
            b"",
            b"Senior Engineer https://jobs.example.test/jobs/attachment-only",
            b"--outer--",
            b"",
        ]
    )


def test_eml_parser_extracts_safe_deduplicated_jobs_without_attachments() -> None:
    parsed = parse_eml(multipart_alert())

    assert parsed.message_id == "<alert-123@acme.example>"
    assert parsed.subject == "New engineering roles at Acme"
    assert parsed.sender == "Acme Careers <careers@acme.example>"
    assert parsed.sent_at == datetime(2026, 8, 29, 13, 30, tzinfo=UTC)
    assert parsed.links_found == 3
    assert [(candidate.title, candidate.apply_url) for candidate in parsed.candidates] == [
        ("Software Engineer Intern", "https://jobs.lever.co/acme/job-1")
    ]
    assert "attachment-only" not in parsed.text
    assert "javascript:" not in {candidate.apply_url for candidate in parsed.candidates}


def test_eml_parser_uses_subject_or_url_slug_for_generic_anchor_labels() -> None:
    raw = b"\r\n".join(
        [
            b"Subject: Weekly openings",
            b"Content-Type: text/html; charset=utf-8",
            b"",
            b'<a href="https://careers.example.test/jobs/data-science-intern-12345">Apply now</a>',
        ]
    )

    parsed = parse_eml(raw)

    assert [(candidate.title, candidate.apply_url) for candidate in parsed.candidates] == [
        (
            "data science intern",
            "https://careers.example.test/jobs/data-science-intern-12345",
        )
    ]


@pytest.mark.parametrize(
    ("raw", "expected_code", "expected_status"),
    [
        (b"", "EMAIL_FILE_EMPTY", 400),
        (b"x" * (MAX_EML_BYTES + 1), "EMAIL_FILE_TOO_LARGE", 413),
    ],
)
def test_eml_parser_rejects_empty_and_oversized_messages(
    raw: bytes,
    expected_code: str,
    expected_status: int,
) -> None:
    with pytest.raises(AppError) as caught:
        parse_eml(raw)

    assert caught.value.code == expected_code
    assert caught.value.status_code == expected_status


@pytest.mark.parametrize(
    "schema",
    [ReviewReminderCreateRequest, ReviewReminderRescheduleRequest],
)
def test_reminder_inputs_require_a_browser_supplied_timezone_offset(
    schema: type[ReviewReminderCreateRequest] | type[ReviewReminderRescheduleRequest],
) -> None:
    payload: dict[str, object] = {"due_at": "2026-08-30T09:00:00"}
    if schema is ReviewReminderCreateRequest:
        payload["career_source_id"] = None

    with pytest.raises(ValidationError):
        schema.model_validate(payload)

    accepted = schema.model_validate({**payload, "due_at": "2026-08-30T09:00:00-04:00"})
    assert accepted.due_at.astimezone(UTC) == datetime(2026, 8, 30, 13, 0, tzinfo=UTC)
