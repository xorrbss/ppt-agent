"""Power requests follow attempt ownership, including thread and task teardown."""

import asyncio
from dataclasses import replace
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services import workspace_operation_power as power
from services import workspace_operation_runner as runner
from services import workspace_operation_heartbeat_thread as heartbeat
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationBlocked,
    WorkspaceOperationCancelled,
    WorkspaceOperationDeferred,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
)
from tests.unit.test_workspace_operation_lease import _claim


@pytest.fixture
def held(monkeypatch):
    api = SimpleNamespace(PowerClearRequest=Mock(return_value=True), CloseHandle=Mock())
    create = Mock(return_value=(api, 123))
    monkeypatch.setattr(power, "_create_request", create)
    hold = power.OperationPowerHold()
    monkeypatch.setattr(runner, "operation_power_hold", hold)
    monkeypatch.setattr(heartbeat, "operation_power_hold", hold)
    yield hold, api, create
    hold.close()


def test_concurrent_attempts_share_one_request_and_stale_release_is_idempotent(held):
    hold, api, create = held
    claims = [replace(_claim(), attempt_token=str(index)) for index in range(8)]
    acquired = threading.Barrier(len(claims) + 1)
    released = threading.Event()

    def own(claim):
        hold.acquire(claim)
        hold.acquire(claim)
        acquired.wait(timeout=3)
        assert released.wait(3)
        hold.release(claim)
        hold.release(claim)

    threads = [threading.Thread(target=own, args=(claim,)) for claim in claims]
    for thread in threads:
        thread.start()
    try:
        acquired.wait(timeout=3)
        create.assert_called_once()
        api.PowerClearRequest.assert_not_called()
    finally:
        released.set()
        for thread in threads:
            thread.join(3)
            assert not thread.is_alive()
    api.PowerClearRequest.assert_called_once_with(123, power.SYSTEM_REQUIRED)
    api.CloseHandle.assert_called_once_with(123)


def test_non_windows_is_logged_noop(monkeypatch, caplog):
    monkeypatch.setattr(power, "os", SimpleNamespace(name="posix"))
    with caplog.at_level(logging.DEBUG, logger=power.__name__):
        hold = power.OperationPowerHold()
        hold.acquire(_claim())
        hold.close()
    assert "unavailable on this platform" in caplog.text


def test_native_request_uses_system_required_without_display_or_thread_state(monkeypatch):
    api = SimpleNamespace(PowerCreateRequest=Mock(return_value=123), PowerSetRequest=Mock(return_value=True),
        PowerClearRequest=Mock(return_value=True), CloseHandle=Mock(return_value=True))
    monkeypatch.setattr(power, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(power.ctypes, "WinDLL", Mock(return_value=api), raising=False)
    hold = power.OperationPowerHold()
    claim = _claim()
    hold.acquire(claim)
    context = api.PowerCreateRequest.call_args.args[0]._obj
    assert context.version == 0 and context.flags == 1
    assert context.simple == "Presenton operation lease is active"
    api.PowerSetRequest.assert_called_once_with(123, 1)
    hold.release(claim)
    api.PowerClearRequest.assert_called_once_with(123, 1)
    api.CloseHandle.assert_called_once_with(123)


def test_unavailable_api_never_fails_the_operation(monkeypatch, caplog):
    monkeypatch.setattr(power, "_create_request", Mock(side_effect=OSError("unavailable")))
    hold = power.OperationPowerHold()
    claim = _claim()
    hold.acquire(claim)
    hold.release(claim)
    assert not hold._attempts
    assert "power request unavailable" in caplog.text


def test_shutdown_closes_handle_even_if_clear_fails(held):
    hold, api, _ = held
    hold.acquire(_claim())
    api.PowerClearRequest.side_effect = OSError("unavailable")
    hold.close()
    hold.close()
    api.CloseHandle.assert_called_once_with(123)


@pytest.mark.parametrize("outcome", [
    "success", "unsupported", "revision", "failure", "unexpected", "deferred",
    "blocked", "cancelled", "lease_lost", "shutdown", "local_cancel",
])
def test_default_runner_releases_all_attempt_end_paths(monkeypatch, held, outcome):
    hold, api, _ = held
    worker = runner.WorkspaceOperationRunner(registry=SimpleNamespace(resolve=lambda _: object()))
    claim = _claim()
    errors = {
        "failure": NonRetryableWorkspaceOperationError("test_failure"),
        "unexpected": ValueError("test_failure"),
        "deferred": WorkspaceOperationDeferred("test_deferred"),
        "blocked": WorkspaceOperationBlocked("test_blocked"),
        "cancelled": WorkspaceOperationCancelled(),
        "lease_lost": WorkspaceOperationLeaseLost(),
        "shutdown": asyncio.CancelledError(),
        "local_cancel": asyncio.CancelledError(),
    }

    async def guard(_claim, execute, **kwargs):
        await execute()

    async def keep_heartbeat(*args):
        await asyncio.Future()

    async def handler(*args):
        assert hold._attempts
        api.PowerClearRequest.assert_not_called()
        if outcome in errors:
            raise errors[outcome]
        return WorkspaceOperationResult()

    monkeypatch.setattr(runner, "run_guarded", guard)
    monkeypatch.setattr(worker, "_call_handler", handler)
    monkeypatch.setattr(worker, "_heartbeat_loop", keep_heartbeat)
    monkeypatch.setattr(worker, "_validate_claim_revision", AsyncMock())
    if outcome == "unsupported":
        worker.registry.resolve = lambda _: None
    if outcome == "revision":
        worker._validate_claim_revision.side_effect = NonRetryableWorkspaceOperationError("revision_conflict")
    for name in ("finalize_workspace_operation_success", "finalize_workspace_operation_cancelled",
                 "_transition_claim_failure", "_transition_claim_blocked", "defer_workspace_operation_claim",
                 "release_workspace_operation_claim"):
        monkeypatch.setattr(runner, name, AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "_operation_cancel_requested", AsyncMock(return_value=outcome == "local_cancel"))
    monkeypatch.setattr(runner, "_observe_claim", Mock())

    async def scenario():
        if outcome in {"shutdown", "local_cancel"}:
            with pytest.raises(asyncio.CancelledError):
                await worker._execute(claim)
        else:
            await worker._execute(claim)
    asyncio.run(scenario())
    assert not hold._attempts
    api.PowerClearRequest.assert_called_once()


def test_dispatch_cancelled_before_first_task_instruction_releases(held, monkeypatch):
    hold, api, _ = held
    worker = runner.WorkspaceOperationRunner(max_concurrency=1)
    monkeypatch.setattr(runner, "claim_next_workspace_operation", AsyncMock(return_value=_claim()))

    async def scenario():
        assert await worker.dispatch_once() == 1
        assert hold._attempts
        task = next(iter(worker._active.values()))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
    asyncio.run(scenario())
    assert not hold._attempts
    api.PowerClearRequest.assert_called_once()


def test_heartbeat_lease_loss_releases_before_database_disposal(held, monkeypatch):
    hold, api, _ = held
    claim = _claim()
    hold.acquire(claim)

    async def dispose():
        assert not hold._attempts
        api.PowerClearRequest.assert_called_once()

    monkeypatch.setattr(heartbeat, "_heartbeat_session_factory", lambda _: (
        SimpleNamespace(dispose=dispose), object()))
    monkeypatch.setattr(heartbeat, "run_attempt_heartbeat_loop", AsyncMock(side_effect=WorkspaceOperationLeaseLost()))

    async def scenario():
        with pytest.raises(WorkspaceOperationLeaseLost):
            await heartbeat.run_threaded_attempt_heartbeat(claim, session_factory=object(),
                heartbeat_seconds=1, poll_seconds=1, lease_seconds=lambda: 300)
    asyncio.run(scenario())
    hold.release(claim)
    api.CloseHandle.assert_called_once()
