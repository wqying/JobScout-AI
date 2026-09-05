"""Alert copy.

Titles and bodies are computed once, when the outbox row is written, so the in-app record and the
email describe exactly the evidence that existed at that poll. Sponsorship wording follows Section
2.2: historical records only, never a guarantee.
"""

from __future__ import annotations

from html import escape

from app.notifications.email import EmailMessage
from app.notifications.schemas import EmailTestPayload, NotificationJobSummary, NotificationPayload

SPONSORSHIP_CAVEAT = (
    "Historical H-1B filing records do not guarantee sponsorship for a specific role."
)
DISCLAIMER = "JobScout AI is informational only and is not legal or immigration advice."


def alert_title(company_name: str, jobs: list[NotificationJobSummary]) -> str:
    noun = "opening" if len(jobs) == 1 else "openings"
    return f"{len(jobs)} matching {company_name} {noun}"


def alert_body(jobs: list[NotificationJobSummary]) -> str:
    lines = []
    for job in jobs:
        location = job.location_text or "Location not provided"
        verb = "New" if job.event_type == "discovered" else "Reopened"
        reasons = "; ".join(job.match_reasons) or "Matches your saved preferences"
        lines.append(f"{verb}: {job.title} — {location} ({reasons})")
    return "\n".join(lines)


def render_email(
    payload: NotificationPayload,
    *,
    recipient: str,
    app_base_url: str,
) -> EmailMessage:
    action_url = absolute_url(app_base_url, payload.action_path)
    subject = f"JobScout AI: {payload.title}"

    text_lines = [payload.title, ""]
    for job in payload.jobs:
        text_lines.append(f"* {job.title}")
        text_lines.append(f"  Company: {payload.company_name}")
        text_lines.append(f"  Location: {job.location_text or 'Location not provided'}")
        text_lines.append(f"  First observed: {job.first_seen_at.isoformat()}")
        text_lines.append(
            f"  Why it matched: {'; '.join(job.match_reasons) or 'Matches your saved preferences'}"
        )
        text_lines.append(f"  Apply: {job.apply_url}")
        text_lines.append("")
    text_lines.append(f"Open in JobScout: {action_url}")
    if payload.sponsorship_caveat:
        text_lines.append(payload.sponsorship_caveat)
    text_lines.append(DISCLAIMER)

    items = "".join(
        "<li>"
        f"<strong>{escape(job.title)}</strong><br />"
        f"{escape(payload.company_name)} — {escape(job.location_text or 'Location not provided')}"
        "<br />"
        f"First observed {escape(job.first_seen_at.isoformat())}<br />"
        "Why it matched: "
        f"{escape('; '.join(job.match_reasons) or 'Matches your saved preferences')}<br />"
        f'<a href="{escape(job.apply_url, quote=True)}">Open the official posting</a>'
        "</li>"
        for job in payload.jobs
    )
    caveat = f"<p>{escape(payload.sponsorship_caveat)}</p>" if payload.sponsorship_caveat else ""
    html_body = (
        f"<h1>{escape(payload.title)}</h1>"
        f"<ul>{items}</ul>"
        f'<p><a href="{escape(action_url, quote=True)}">Open in JobScout</a></p>'
        f"{caveat}"
        f"<p>{escape(DISCLAIMER)}</p>"
    )

    return EmailMessage(
        to=recipient,
        subject=subject,
        text_body="\n".join(text_lines),
        html_body=html_body,
    )


def render_test_email(
    payload: EmailTestPayload,
    *,
    recipient: str,
    app_base_url: str,
) -> EmailMessage:
    action_url = absolute_url(app_base_url, payload.action_path)
    subject = "JobScout AI: email alerts are working"
    text_body = "\n".join(
        (
            payload.title,
            "",
            payload.body,
            "",
            f"Open alert history: {action_url}",
            DISCLAIMER,
        )
    )
    html_body = (
        f"<h1>{escape(payload.title)}</h1>"
        f"<p>{escape(payload.body)}</p>"
        f'<p><a href="{escape(action_url, quote=True)}">Open alert history</a></p>'
        f"<p>{escape(DISCLAIMER)}</p>"
    )
    return EmailMessage(
        to=recipient,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )


def absolute_url(app_base_url: str, path: str) -> str:
    return f"{app_base_url.rstrip('/')}/{path.lstrip('/')}"
