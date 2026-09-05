from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.pool import NullPool

from app.db.session import get_worker_engine
from app.discovery.failures import discovery_error_code
from app.workers import tasks
from app.workers.celery_app import celery_app


class _AsyncSessionContext:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_worker_engine_does_not_retain_async_connections() -> None:
    get_worker_engine.cache_clear()
    engine = get_worker_engine()

    assert isinstance(engine.sync_engine.pool, NullPool)

    get_worker_engine.cache_clear()


@pytest.mark.asyncio
async def test_discovery_task_guard_records_uncaught_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid4()
    error = RuntimeError("controlled pre-workflow failure")
    runner = AsyncMock(side_effect=error)
    recorder = AsyncMock()
    monkeypatch.setattr(tasks, "_run_company_discovery", runner)
    monkeypatch.setattr(tasks, "_record_discovery_failure", recorder)

    with pytest.raises(RuntimeError, match="controlled pre-workflow failure"):
        await tasks._run_company_discovery_guarded(run_id)

    runner.assert_awaited_once_with(run_id)
    recorder.assert_awaited_once_with(run_id, error)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("OPENAI_API_KEY is required"), "OPENAI_NOT_CONFIGURED"),
        (
            RuntimeError("Future attached to a different loop"),
            "WORKER_DATABASE_ERROR",
        ),
        (RuntimeError("OpenAI request failed"), "OPENAI_REQUEST_FAILED"),
        (RuntimeError("unclassified"), "DISCOVERY_FAILED"),
    ],
)
def test_discovery_worker_errors_have_stable_codes(error: RuntimeError, expected: str) -> None:
    assert discovery_error_code(error) == expected


@pytest.mark.asyncio
async def test_due_review_reminder_task_delegates_to_domain_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object()
    enqueue = AsyncMock(return_value=2)
    service = Mock(enqueue_due_notifications=enqueue)
    monkeypatch.setattr(tasks, "ReviewReminderService", Mock(return_value=service))
    monkeypatch.setattr(
        tasks,
        "get_worker_session_factory",
        lambda: lambda: _AsyncSessionContext(session),
    )

    await tasks._enqueue_due_review_reminders()

    tasks.ReviewReminderService.assert_called_once_with(session)
    enqueue.assert_awaited_once_with()


def test_beat_schedules_due_review_reminders_frequently() -> None:
    entries = [
        entry
        for entry in celery_app.conf.beat_schedule.values()
        if entry["task"] == "app.workers.tasks.enqueue_due_review_reminders"
    ]

    assert len(entries) == 1
    assert float(entries[0]["schedule"]) <= 60.0
