"""Resend and fake email adapter contracts. No test here touches a live provider."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.notifications.email import (
    EmailMessage,
    FakeEmailSender,
    PermanentEmailError,
    ResendEmailSender,
    TransientEmailError,
    UnavailableEmailSender,
    build_email_sender,
    select_email_adapter,
)

MESSAGE = EmailMessage(
    to="owner@example.com",
    subject="JobScout AI: 1 matching Acme Games opening",
    text_body="Software Engineer Intern",
    html_body="<h1>Software Engineer Intern</h1>",
)


def resend(handler: object) -> ResendEmailSender:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return ResendEmailSender(
        api_key="test-key",
        sender="alerts@example.com",
        http_client=httpx.AsyncClient(transport=transport),
    )


async def test_successful_send_returns_the_provider_message_id() -> None:
    captured: list[dict[str, object]] = []
    captured_idempotency_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        captured_idempotency_keys.append(request.headers["idempotency-key"])
        assert request.url.path == "/emails"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"id": "resend-123"})

    async with resend(handler) as sender:
        delivery = await sender.send(
            MESSAGE,
            idempotency_key="jobscout-email:00000000-0000-0000-0000-000000000001",
        )

    assert delivery.provider_message_id == "resend-123"
    assert captured[0]["from"] == "alerts@example.com"
    assert captured[0]["to"] == ["owner@example.com"]
    assert captured[0]["subject"] == MESSAGE.subject
    assert len(captured_idempotency_keys) == 1
    assert captured_idempotency_keys[0].startswith(
        "jobscout-email:00000000-0000-0000-0000-000000000001:"
    )


async def test_a_retry_reuses_the_same_resend_idempotency_key() -> None:
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["idempotency-key"])
        return httpx.Response(200, json={"id": "resend-stable"})

    key = "jobscout-email:00000000-0000-0000-0000-000000000002"
    async with resend(handler) as sender:
        first = await sender.send(MESSAGE, idempotency_key=key)
        second = await sender.send(MESSAGE, idempotency_key=key)

    assert first.provider_message_id == second.provider_message_id == "resend-stable"
    assert keys[0] == keys[1]
    assert keys[0].startswith(f"{key}:")


async def test_recipient_or_body_changes_produce_a_different_resend_idempotency_key() -> None:
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["idempotency-key"])
        return httpx.Response(200, json={"id": f"resend-{len(keys)}"})

    changed_recipient = EmailMessage(
        to="second-owner@example.com",
        subject=MESSAGE.subject,
        text_body=MESSAGE.text_body,
        html_body=MESSAGE.html_body,
    )
    changed_body = EmailMessage(
        to=MESSAGE.to,
        subject=MESSAGE.subject,
        text_body="A changed body",
        html_body=MESSAGE.html_body,
    )
    base_key = "jobscout-email:00000000-0000-0000-0000-000000000003"
    async with resend(handler) as sender:
        await sender.send(MESSAGE, idempotency_key=base_key)
        await sender.send(changed_recipient, idempotency_key=base_key)
        await sender.send(changed_body, idempotency_key=base_key)

    assert all(key.startswith(f"{base_key}:") for key in keys)
    assert len(set(keys)) == 3


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503])
async def test_rate_limits_and_provider_outages_are_transient(status_code: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"message": "try again"})

    async with resend(handler) as sender:
        with pytest.raises(TransientEmailError):
            await sender.send(MESSAGE)


async def test_network_failures_are_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with resend(handler) as sender:
        with pytest.raises(TransientEmailError) as caught:
            await sender.send(MESSAGE)

    assert caught.value.code == "EMAIL_NETWORK_ERROR"


async def test_protocol_failures_are_also_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("peer disconnected", request=request)

    async with resend(handler) as sender:
        with pytest.raises(TransientEmailError) as caught:
            await sender.send(MESSAGE)

    assert caught.value.code == "EMAIL_NETWORK_ERROR"


@pytest.mark.parametrize("status_code", [400, 401, 403, 422])
async def test_rejected_requests_are_permanent(status_code: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"message": "rejected"})

    async with resend(handler) as sender:
        with pytest.raises(PermanentEmailError) as caught:
            await sender.send(MESSAGE)

    assert caught.value.code == f"EMAIL_REJECTED_{status_code}"


async def test_concurrent_idempotent_request_conflict_is_transient() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"name": "concurrent_idempotent_requests"})

    async with resend(handler) as sender:
        with pytest.raises(TransientEmailError) as caught:
            await sender.send(MESSAGE, idempotency_key="jobscout-email:concurrent")

    assert caught.value.code == "EMAIL_IDEMPOTENCY_CONCURRENT"


async def test_changed_idempotent_request_conflict_is_permanent() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"name": "invalid_idempotent_request"})

    async with resend(handler) as sender:
        with pytest.raises(PermanentEmailError) as caught:
            await sender.send(MESSAGE, idempotency_key="jobscout-email:changed")

    assert caught.value.code == "EMAIL_IDEMPOTENCY_CONFLICT"


async def test_a_success_without_a_message_id_is_permanent() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    async with resend(handler) as sender:
        with pytest.raises(PermanentEmailError) as caught:
            await sender.send(MESSAGE)

    assert caught.value.code == "EMAIL_RESPONSE_MISSING_ID"


async def test_fake_sender_records_deliveries_and_replays_scripted_failures() -> None:
    sender = FakeEmailSender(scripted_errors=[TransientEmailError("EMAIL_RATE_LIMITED"), None])

    with pytest.raises(TransientEmailError):
        await sender.send(MESSAGE)
    delivery = await sender.send(MESSAGE, idempotency_key="jobscout-email:test")

    assert delivery.provider_message_id == "fake-email-1"
    assert [item.subject for item in sender.delivered] == [MESSAGE.subject]
    assert sender.idempotency_keys == ["jobscout-email:test"]


def runtime(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "local",
        "resend_api_key": None,
        "email_from": None,
        "email_delivery_mode": "auto",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("overrides", "active_adapter", "ready", "diagnostic"),
    [
        ({}, "fake", False, "AUTO_FAKE_RESEND_CREDENTIALS_MISSING"),
        ({"resend_api_key": "key"}, "fake", False, "AUTO_FAKE_EMAIL_FROM_MISSING"),
        (
            {"email_from": "alerts@example.com"},
            "fake",
            False,
            "AUTO_FAKE_RESEND_API_KEY_MISSING",
        ),
        (
            {"resend_api_key": "key", "email_from": "alerts@example.com"},
            "resend",
            True,
            "RESEND_READY",
        ),
        (
            {
                "email_delivery_mode": "fake",
                "resend_api_key": "key",
                "email_from": "alerts@example.com",
            },
            "fake",
            False,
            "FAKE_EMAIL_MODE",
        ),
        (
            {"email_delivery_mode": "resend"},
            "unavailable",
            False,
            "RESEND_CREDENTIALS_MISSING",
        ),
    ],
)
def test_adapter_selection_is_explicit_and_diagnostic(
    overrides: dict[str, object],
    active_adapter: str,
    ready: bool,
    diagnostic: str,
) -> None:
    selection = select_email_adapter(runtime(**overrides))

    assert selection.active_adapter == active_adapter
    assert selection.live_delivery_ready is ready
    assert selection.diagnostic_code == diagnostic


def test_adapter_builder_matches_the_selected_mode() -> None:
    assert isinstance(build_email_sender(runtime()), FakeEmailSender)
    assert isinstance(
        build_email_sender(runtime(resend_api_key="key", email_from="alerts@example.com")),
        ResendEmailSender,
    )
    assert isinstance(
        build_email_sender(runtime(email_delivery_mode="resend")),
        UnavailableEmailSender,
    )


def test_test_environment_forces_the_credential_free_adapter() -> None:
    settings = runtime(
        app_env="test",
        email_delivery_mode="resend",
        resend_api_key="would-be-live-key",
        email_from="alerts@example.com",
    )

    selection = select_email_adapter(settings)

    assert selection.active_adapter == "fake"
    assert selection.live_delivery_ready is False
    assert selection.diagnostic_code == "TEST_MODE_USES_FAKE_EMAIL"
    assert isinstance(build_email_sender(settings), FakeEmailSender)
