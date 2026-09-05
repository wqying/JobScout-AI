from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from app.api.errors import AppError
from app.monitoring.job_identity import canonicalize_url

MAX_EML_BYTES = 1024 * 1024
MAX_EXTRACTED_TEXT_CHARS = 200_000
MAX_EXCERPT_CHARS = 5_000

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_JOB_PATH_PATTERN = re.compile(
    r"(?:^|[/_-])(job|jobs|career|careers|position|positions|posting|requisition)(?:[/_-]|$)",
    re.IGNORECASE,
)
_JOB_LABEL_PATTERN = re.compile(
    r"\b(job|position|opening|apply|intern|engineer|developer|designer|analyst|scientist|"
    r"coordinator|associate|manager|director|specialist|consultant|architect)\b",
    re.IGNORECASE,
)
_EXCLUDED_LINK_PATTERN = re.compile(
    r"unsubscribe|email[-_ ]?preferences|manage[-_ ]?preferences|privacy|view[-_ ]?email|"
    r"tracking|opt[-_ ]?out",
    re.IGNORECASE,
)
_GENERIC_LABEL_PATTERN = re.compile(
    r"^(view|view job|apply|apply now|learn more|read more|job details|open position)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EmailJobCandidate:
    title: str
    apply_url: str


@dataclass(frozen=True)
class ParsedEmailAlert:
    content_sha256: str
    message_id: str | None
    subject: str | None
    sender: str | None
    sent_at: datetime | None
    text: str
    links_found: int
    candidates: tuple[EmailJobCandidate, ...]


class _EmailHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        self._anchor_href = dict(attrs).get("href")
        self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._anchor_href is not None:
            self._anchor_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._anchor_href is None:
            return
        self.links.append((self._anchor_href, " ".join(self._anchor_parts)))
        self._anchor_href = None
        self._anchor_parts = []


def parse_eml(raw: bytes) -> ParsedEmailAlert:
    """Parse an RFC 822 message without loading attachments or remote resources."""

    if not raw:
        raise AppError("EMAIL_FILE_EMPTY", "Choose a non-empty .eml file.")
    if len(raw) > MAX_EML_BYTES:
        raise AppError(
            "EMAIL_FILE_TOO_LARGE",
            f"The .eml file must be no larger than {MAX_EML_BYTES // 1024} KiB.",
            status_code=413,
        )

    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except (TypeError, ValueError) as exc:
        raise AppError("EMAIL_PARSE_FAILED", "The uploaded file is not a valid email.") from exc

    plain_parts: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type().casefold()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError):
            continue
        if not isinstance(content, str):
            continue
        if content_type == "text/plain":
            plain_parts.append(content)
        else:
            html_parts.append(content)

    anchors: list[tuple[str, str]] = []
    html_text: list[str] = []
    for html_part in html_parts:
        parser = _EmailHtmlParser()
        try:
            parser.feed(html_part)
            parser.close()
        except (ValueError, AssertionError):
            # Malformed marketing HTML should not make an otherwise readable message unsafe.
            continue
        anchors.extend(parser.links)
        html_text.extend(parser.text_parts)

    combined_text = _clean_text("\n".join([*plain_parts, *html_text]))
    combined_text = combined_text[:MAX_EXTRACTED_TEXT_CHARS]
    raw_links = [(url, "") for url in _URL_PATTERN.findall("\n".join(plain_parts))]
    all_links = [*anchors, *raw_links]

    subject = _clean_header(message.get("Subject"))
    candidates = _job_candidates(all_links, subject)
    return ParsedEmailAlert(
        content_sha256=hashlib.sha256(raw).hexdigest(),
        message_id=_message_id(message.get("Message-ID")),
        subject=subject,
        sender=_clean_header(message.get("From")),
        sent_at=_email_date(message.get("Date")),
        text=combined_text,
        links_found=len({_safe_url(url) for url, _label in all_links if _safe_url(url)}),
        candidates=tuple(candidates),
    )


def _job_candidates(links: list[tuple[str, str]], subject: str | None) -> list[EmailJobCandidate]:
    candidates: list[EmailJobCandidate] = []
    seen: set[str] = set()
    for raw_url, raw_label in links:
        url = _safe_url(raw_url)
        if url is None or url in seen:
            continue
        label = _clean_text(html.unescape(raw_label))[:500]
        if not _looks_like_job_link(url, label):
            continue
        seen.add(url)
        candidates.append(
            EmailJobCandidate(
                title=_candidate_title(label, url, subject),
                apply_url=url,
            )
        )
    return candidates


def _safe_url(raw_url: str) -> str | None:
    value = html.unescape(raw_url).strip().rstrip('.,;:!?)"]}')
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return canonicalize_url(value)


def _looks_like_job_link(url: str, label: str) -> bool:
    parsed = urlsplit(url)
    searchable = f"{parsed.path} {parsed.query} {label}"
    if _EXCLUDED_LINK_PATTERN.search(searchable):
        return False
    path_parts = [part for part in parsed.path.split("/") if part]
    hosted_job_detail = (parsed.hostname or "").casefold().startswith(
        ("jobs.", "careers.")
    ) and len(path_parts) >= 2
    return bool(
        hosted_job_detail
        or _JOB_PATH_PATTERN.search(parsed.path)
        or (_JOB_LABEL_PATTERN.search(label) and len(path_parts) >= 1)
    )


def _candidate_title(label: str, url: str, subject: str | None) -> str:
    if label and not _GENERIC_LABEL_PATTERN.fullmatch(label):
        return label[:500]
    path_parts = [unquote(item) for item in urlsplit(url).path.split("/") if item]
    for value in reversed(path_parts):
        slug = re.sub(r"[_-]+", " ", value)
        slug = re.sub(r"\b(?:req|jr|job)?\d{4,}\b", "", slug, flags=re.IGNORECASE)
        slug = _clean_text(slug)
        if len(slug) >= 4 and slug.casefold() not in {"apply", "job", "jobs", "position"}:
            return slug[:500]
    if subject:
        return f"Job opportunity: {subject}"[:500]
    return "Job opportunity from employer alert"


def _clean_header(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = _clean_text(str(value))
    return cleaned[:1000] or None


def _message_id(value: object | None) -> str | None:
    cleaned = _clean_header(value)
    return cleaned.casefold()[:500] if cleaned else None


def _email_date(value: object | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\x00", " ").split())
