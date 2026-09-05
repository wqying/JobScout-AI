import pytest

from app.api.errors import AppError
from app.companies.normalization import (
    domain_from_url,
    normalize_employer_name,
    normalize_https_url,
    normalize_query,
)


def test_query_normalization_handles_unicode_case_and_whitespace() -> None:
    assert normalize_query("  GÁME   Studios  ") == "gáme studios"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Games, Inc.", "acme games"),
        ("ACME GAMES LLC", "acme games"),
        ("Studio.Co", "studio"),
        ("Company Heroes Corporation", "company heroes"),
    ],
)
def test_employer_normalization_removes_only_trailing_corporate_suffixes(
    raw: str, expected: str
) -> None:
    assert normalize_employer_name(raw) == expected


def test_https_url_normalization_and_domain() -> None:
    assert normalize_https_url("Example.com/careers/") == "https://example.com/careers"
    assert domain_from_url("https://www.Example.com/careers") == "example.com"


def test_http_url_is_rejected() -> None:
    with pytest.raises(AppError, match="public HTTPS"):
        normalize_https_url("http://example.com")
