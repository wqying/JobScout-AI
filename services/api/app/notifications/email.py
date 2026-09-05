"""Typed email adapters.

Domain code never depends on a provider's HTTP shape. It sends an :class:`EmailMessage` and gets
back either a delivery record or one of two errors:

* :class:`TransientEmailError` — worth retrying later (rate limits, provider outages, network);
* :class:`PermanentEmailError` — never retry (rejected address, bad credentials, invalid payload).

The fake adapter records structured deliveries without touching the network, so local runs and CI
exercise the same outbox code path as production.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal, Protocol

import httpx
import structlog

from app.core.config import Settings

RESEND_BASE_URL = "https://api.resend.com"


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text_body: str
    html_body: str


@dataclass(frozen=True)
class EmailDelivery:
    provider_message_id: str


class TransientEmailError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PermanentEmailError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


EmailAdapterName = Literal["fake", "resend", "unavailable"]


class EmailSender(Protocol):
    @property
    def adapter_name(self) -> EmailAdapterName: ...

    async def send(
        self, message: EmailMessage, *, idempotency_key: str | None = None
    ) -> EmailDelivery: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class EmailAdapterSelection:
    requested_mode: Literal["auto", "fake", "resend"]
    active_adapter: EmailAdapterName
    live_delivery_ready: bool
    diagnostic_code: str


class ResendEmailSender:
    """Minimal Resend adapter over the transactional send endpoint."""

    @property
    def adapter_name(self) -> EmailAdapterName:
        return "resend"

    def __init__(
        self,
        *,
        api_key: str,
        sender: str,
        base_url: str = RESEND_BASE_URL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("RESEND_API_KEY is required for the Resend email adapter")
        if not sender.strip():
            raise ValueError("EMAIL_FROM is required for the Resend email adapter")
        self.sender = sender
        self._base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0)
        )
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def __aenter__(self) -> ResendEmailSender:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send(
        self, message: EmailMessage, *, idempotency_key: str | None = None
    ) -> EmailDelivery:
        request_payload = {
            "from": self.sender,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text_body,
            "html": message.html_body,
        }
        headers = dict(self._headers)
        if idempotency_key:
            fingerprint = hashlib.sha256(
                json.dumps(
                    request_payload,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:24]
            headers["Idempotency-Key"] = f"{idempotency_key}:{fingerprint}"
        try:
            response = await self._client.post(
                f"{self._base_url}/emails",
                headers=headers,
                json=request_payload,
            )
        except httpx.TransportError as exc:
            raise TransientEmailError("EMAIL_NETWORK_ERROR") from exc

        if response.status_code == 408:
            raise TransientEmailError("EMAIL_REQUEST_TIMEOUT")
        if response.status_code == 429:
            raise TransientEmailError("EMAIL_RATE_LIMITED")
        if response.status_code >= 500:
            raise TransientEmailError("EMAIL_PROVIDER_UNAVAILABLE")
        if response.status_code == 409:
            try:
                error_body = response.json()
            except ValueError:
                error_body = None
            error_name = error_body.get("name") if isinstance(error_body, dict) else None
            if error_name == "concurrent_idempotent_requests":
                raise TransientEmailError("EMAIL_IDEMPOTENCY_CONCURRENT")
            if error_name == "invalid_idempotent_request":
                raise PermanentEmailError("EMAIL_IDEMPOTENCY_CONFLICT")
        if response.status_code >= 400:
            raise PermanentEmailError(f"EMAIL_REJECTED_{response.status_code}")

        body: Any = None
        try:
            body = response.json()
        except ValueError as exc:
            raise PermanentEmailError("EMAIL_RESPONSE_UNPARSABLE") from exc
        message_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise PermanentEmailError("EMAIL_RESPONSE_MISSING_ID")
        return EmailDelivery(provider_message_id=message_id)


@dataclass
class FakeEmailSender:
    """Credential-free adapter for local development, tests, and CI.

    ``scripted_errors`` lets a test drive transient and permanent failure paths deterministically
    without any network access.
    """

    @property
    def adapter_name(self) -> EmailAdapterName:
        return "fake"

    delivered: list[EmailMessage] = field(default_factory=list)
    idempotency_keys: list[str | None] = field(default_factory=list)
    scripted_errors: list[Exception | None] = field(default_factory=list)

    async def __aenter__(self) -> FakeEmailSender:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        return None

    async def send(
        self, message: EmailMessage, *, idempotency_key: str | None = None
    ) -> EmailDelivery:
        if self.scripted_errors:
            error = self.scripted_errors.pop(0)
            if error is not None:
                raise error
        self.delivered.append(message)
        self.idempotency_keys.append(idempotency_key)
        structlog.get_logger("jobscout.notifications").info(
            "fake_email_delivered",
            subject=message.subject,
            recipient_domain=message.to.rpartition("@")[2] or "unknown",
        )
        return EmailDelivery(provider_message_id=f"fake-email-{len(self.delivered)}")


@dataclass(frozen=True)
class UnavailableEmailSender:
    """Fail queued email explicitly when live mode is selected but incomplete."""

    error_code: str

    @property
    def adapter_name(self) -> EmailAdapterName:
        return "unavailable"

    async def send(
        self, _message: EmailMessage, *, idempotency_key: str | None = None
    ) -> EmailDelivery:
        del idempotency_key
        raise PermanentEmailError(self.error_code)

    async def aclose(self) -> None:
        return None


def select_email_adapter(settings: Settings) -> EmailAdapterSelection:
    """Resolve the configured adapter without ever allowing tests to select a live provider."""

    if settings.app_env == "test":
        return EmailAdapterSelection(
            requested_mode=settings.email_delivery_mode,
            active_adapter="fake",
            live_delivery_ready=False,
            diagnostic_code="TEST_MODE_USES_FAKE_EMAIL",
        )
    if settings.email_delivery_mode == "fake":
        return EmailAdapterSelection(
            requested_mode="fake",
            active_adapter="fake",
            live_delivery_ready=False,
            diagnostic_code="FAKE_EMAIL_MODE",
        )

    missing_key = not bool(settings.resend_api_key and settings.resend_api_key.strip())
    missing_sender = not bool(settings.email_from and settings.email_from.strip())
    if not missing_key and not missing_sender:
        return EmailAdapterSelection(
            requested_mode=settings.email_delivery_mode,
            active_adapter="resend",
            live_delivery_ready=True,
            diagnostic_code="RESEND_READY",
        )

    if missing_key and missing_sender:
        code = "RESEND_CREDENTIALS_MISSING"
    elif missing_key:
        code = "RESEND_API_KEY_MISSING"
    else:
        code = "EMAIL_FROM_MISSING"
    if settings.email_delivery_mode == "resend":
        return EmailAdapterSelection(
            requested_mode="resend",
            active_adapter="unavailable",
            live_delivery_ready=False,
            diagnostic_code=code,
        )
    return EmailAdapterSelection(
        requested_mode="auto",
        active_adapter="fake",
        live_delivery_ready=False,
        diagnostic_code=f"AUTO_FAKE_{code}",
    )


def build_email_sender(settings: Settings) -> EmailSender:
    """Build the selected adapter while keeping incomplete explicit live mode fail-closed."""

    selection = select_email_adapter(settings)
    if selection.active_adapter == "resend":
        assert settings.resend_api_key is not None and settings.email_from is not None
        return ResendEmailSender(api_key=settings.resend_api_key, sender=settings.email_from)
    if selection.active_adapter == "unavailable":
        return UnavailableEmailSender(selection.diagnostic_code)
    return FakeEmailSender()
