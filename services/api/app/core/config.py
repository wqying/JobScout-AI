from __future__ import annotations

import ipaddress
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _environment_files() -> tuple[str, ...]:
    """Load the repository-root env file even when a host process starts in ``services/api``.

    Compose injects environment variables directly, so the fallback remains harmless in containers.
    Keeping this lookup here gives the API, worker, Beat, and CLI one consistent
    configuration source.
    """

    source = Path(__file__).resolve()
    for parent in source.parents:
        if (parent / "pnpm-workspace.yaml").is_file():
            return (str(parent / ".env"),)
    return (".env",)


class Settings(BaseSettings):
    """Validated server-side configuration.

    Secrets intentionally have no non-empty defaults. Optional integrations remain disabled until
    a later milestone selects a real adapter.
    """

    model_config = SettingsConfigDict(
        env_file=_environment_files(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["local", "test", "container"] = "local"
    app_base_url: str = "http://127.0.0.1:3000"
    web_host: str = "127.0.0.1"
    api_host: str = "127.0.0.1"
    allow_remote_access: bool = False
    database_url: str = "postgresql+asyncpg://jobscout:jobscout@127.0.0.1:5432/jobscout"
    redis_url: str = "redis://127.0.0.1:6379/0"
    openai_api_key: str | None = None
    openai_research_model: str | None = None
    openai_structured_model: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_research_max_output_tokens: int = Field(default=6000, ge=1000, le=20000)
    openai_structured_max_output_tokens: int = Field(default=10000, ge=1000, le=20000)
    openai_max_web_search_calls: int = Field(default=6, ge=1, le=8)
    openai_input_cost_per_million_usd: Decimal = Field(default=Decimal("0.20"), ge=0)
    openai_output_cost_per_million_usd: Decimal = Field(default=Decimal("1.20"), ge=0)
    openai_web_search_cost_per_call_usd: Decimal = Field(default=Decimal("0.01"), ge=0)
    resend_api_key: str | None = None
    email_from: str | None = None
    email_delivery_mode: Literal["auto", "fake", "resend"] = "auto"
    otel_exporter_otlp_endpoint: str | None = None
    log_level: str = "INFO"
    discovery_daily_quota: int = Field(default=5, ge=1, le=25)
    discovery_cache_ttl_hours: int = Field(default=168, ge=1, le=720)
    # Careers-page resolution (DESIGN_DOC.md Section 13.7). The candidate budget defaults to the
    # maximum shown result count so the bounded fetch is spent on results the owner will see; 0
    # disables the stage and leaves every candidate `generic_pending`.
    discovery_resolve_max_candidates: int = Field(default=20, ge=0, le=40)
    discovery_resolve_max_link_candidates: int = Field(default=3, ge=0, le=5)
    discovery_resolve_min_job_links: int = Field(default=3, ge=1, le=20)
    discovery_resolve_concurrency: int = Field(default=8, ge=1, le=16)
    discovery_resolve_deadline_seconds: int = Field(default=120, ge=10, le=600)
    default_poll_interval_minutes: int = Field(default=60, ge=15, le=1440)

    @model_validator(mode="after")
    def enforce_local_bindings(self) -> Settings:
        non_loopback = [host for host in (self.web_host, self.api_host) if not _is_loopback(host)]
        if non_loopback and not self.allow_remote_access:
            hosts = ", ".join(non_loopback)
            raise ValueError(
                f"Refusing non-loopback bind ({hosts}). Set ALLOW_REMOTE_ACCESS=true only when "
                "the surrounding network boundary is intentionally secured."
            )
        return self


def _is_loopback(host: str) -> bool:
    normalized = host.strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
