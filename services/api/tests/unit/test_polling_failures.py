import pytest

from app.monitoring.polling import is_permanent_source_error


@pytest.mark.parametrize(
    "code",
    [
        "ROBOTS_DISALLOWED",
        "ROBOTS_TXT_HTTP_401",
        "ROBOTS_TXT_HTTP_403",
        "URL_SCHEME_NOT_ALLOWED",
        "URL_IP_NOT_PUBLIC",
    ],
)
def test_deterministic_source_errors_are_permanent(code: str) -> None:
    assert is_permanent_source_error(code)


@pytest.mark.parametrize(
    "code",
    ["URL_DNS_FAILED", "ROBOTS_FETCH_FAILED", "SOURCE_TIMEOUT", "SOURCE_NETWORK_ERROR"],
)
def test_transient_source_errors_remain_retryable(code: str) -> None:
    assert not is_permanent_source_error(code)
