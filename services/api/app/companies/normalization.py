import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from app.api.errors import AppError

_CORPORATE_SUFFIX = re.compile(
    r"(?:\s+(?:incorporated|inc|corporation|corp|company|co|limited|ltd|llc|plc))+$",
    re.IGNORECASE,
)
_NON_ALPHANUMERIC = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def normalize_employer_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = _NON_ALPHANUMERIC.sub(" ", normalized)
    normalized = " ".join(normalized.split())
    return _CORPORATE_SUFFIX.sub("", normalized).strip()


def normalize_https_url(value: str) -> str:
    raw = value.strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlsplit(raw)
    hostname = parsed.hostname.casefold() if parsed.hostname else None
    if parsed.scheme.casefold() != "https" or hostname is None:
        raise AppError("INVALID_HTTPS_URL", "Enter a valid public HTTPS URL.")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise AppError("INVALID_HTTPS_URL", "URLs cannot contain credentials or custom ports.")
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunsplit(("https", hostname, path.rstrip("/") or "/", parsed.query, ""))


def domain_from_url(value: str) -> str:
    hostname = urlsplit(normalize_https_url(value)).hostname
    if hostname is None:
        raise AppError("INVALID_HTTPS_URL", "Enter a valid public HTTPS URL.")
    return hostname.removeprefix("www.")
