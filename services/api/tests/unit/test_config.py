from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, _environment_files


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_bind_is_allowed(host: str) -> None:
    settings = Settings(_env_file=None, api_host=host, web_host=host)

    assert settings.api_host == host


def test_non_loopback_bind_is_refused_without_explicit_flag() -> None:
    with pytest.raises(ValidationError, match="Refusing non-loopback bind"):
        Settings(_env_file=None, api_host="0.0.0.0")


def test_non_loopback_bind_requires_explicit_flag() -> None:
    settings = Settings(_env_file=None, api_host="0.0.0.0", allow_remote_access=True)

    assert settings.allow_remote_access is True


def test_repository_root_env_file_is_found_from_the_api_package() -> None:
    env_files = tuple(Path(path).resolve() for path in _environment_files())
    repository_env = next(
        path for path in env_files if (path.parent / "pnpm-workspace.yaml").is_file()
    )

    assert repository_env.name == ".env"


@pytest.mark.parametrize("mode", ["auto", "fake", "resend"])
def test_email_delivery_modes_are_validated(mode: str) -> None:
    settings = Settings(_env_file=None, email_delivery_mode=mode)

    assert settings.email_delivery_mode == mode


def test_unknown_email_delivery_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, email_delivery_mode="smtp")
