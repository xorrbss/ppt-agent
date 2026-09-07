"""Unit contract for the kill-on-parent-death job binding.

The binding is teardown hardening layered under the existing ``atexit`` path, so
its most important property is that it degrades quietly: off Windows, and on
Windows whenever the Win32 calls refuse, it must return ``False`` and let the
caller proceed. A render must never fail because a job assignment did.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from utils import process_kill_on_parent_death as kill_module


@pytest.fixture(autouse=True)
def _isolate_module_state():
    """Keep the process-wide job cache out of the other tests in this session."""

    saved = (
        kill_module._win32_api,
        kill_module._job_handle,
        kill_module._job_unavailable,
    )
    yield
    (
        kill_module._win32_api,
        kill_module._job_handle,
        kill_module._job_unavailable,
    ) = saved


class _Process:
    def __init__(self, pid: Any) -> None:
        self.pid = pid


def _explode(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("the Win32 layer must not be reached here")


def test_supports_os_enforced_teardown_tracks_the_platform() -> None:
    assert kill_module.supports_os_enforced_teardown() is (os.name == "nt")


def test_binding_is_a_noop_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kill_module, "_is_windows", lambda: False)
    monkeypatch.setattr(kill_module, "_job_object", _explode)

    assert kill_module.bind_to_parent_lifetime(_Process(4242)) is False
    assert kill_module.supports_os_enforced_teardown() is False


@pytest.mark.parametrize("pid", [None, 0, -1, "1234", object()])
def test_binding_rejects_a_process_without_a_usable_pid(
    monkeypatch: pytest.MonkeyPatch, pid: Any
) -> None:
    monkeypatch.setattr(kill_module, "_is_windows", lambda: True)
    monkeypatch.setattr(kill_module, "_job_object", _explode)

    assert kill_module.bind_to_parent_lifetime(_Process(pid)) is False


def test_binding_falls_back_when_the_job_cannot_be_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kill_module, "_is_windows", lambda: True)
    monkeypatch.setattr(kill_module, "_job_object", lambda: None)
    monkeypatch.setattr(kill_module, "_assign_pid_to_job", _explode)

    assert kill_module.bind_to_parent_lifetime(_Process(4242)) is False


def test_binding_falls_back_when_assignment_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AssignProcessToJobObject can fail inside an outer job or a restricted token."""

    monkeypatch.setattr(kill_module, "_is_windows", lambda: True)
    monkeypatch.setattr(kill_module, "_job_object", lambda: 0x1234)
    monkeypatch.setattr(kill_module, "_assign_pid_to_job", lambda _job, _pid: False)

    assert kill_module.bind_to_parent_lifetime(_Process(4242)) is False


def test_binding_never_raises_when_the_win32_layer_explodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kill_module, "_is_windows", lambda: True)
    monkeypatch.setattr(kill_module, "_job_object", lambda: 0x1234)

    def _raise(_job: Any, _pid: int) -> bool:
        raise OSError("kernel32 unavailable")

    monkeypatch.setattr(kill_module, "_assign_pid_to_job", _raise)

    assert kill_module.bind_to_parent_lifetime(_Process(4242)) is False


def test_binding_passes_the_pid_through_and_reports_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[Any, int]] = []

    def _record(job: Any, pid: int) -> bool:
        seen.append((job, pid))
        return True

    monkeypatch.setattr(kill_module, "_is_windows", lambda: True)
    monkeypatch.setattr(kill_module, "_job_object", lambda: 0x1234)
    monkeypatch.setattr(kill_module, "_assign_pid_to_job", _record)

    assert kill_module.bind_to_parent_lifetime(_Process(4242)) is True
    assert kill_module.bind_to_parent_lifetime(4243) is True
    assert seen == [(0x1234, 4242), (0x1234, 4243)]


def test_a_failed_job_creation_is_remembered_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []

    def _fail() -> Any:
        attempts.append(1)
        return None

    kill_module._job_handle = None
    kill_module._job_unavailable = False
    monkeypatch.setattr(kill_module, "_is_windows", lambda: True)
    monkeypatch.setattr(kill_module, "_create_kill_on_close_job", _fail)

    assert kill_module.bind_to_parent_lifetime(_Process(4242)) is False
    assert kill_module.bind_to_parent_lifetime(_Process(4243)) is False
    assert len(attempts) == 1


@pytest.mark.skipif(os.name != "nt", reason="Job Objects are a Win32 facility")
def test_the_real_job_object_is_created_once_and_reused() -> None:
    kill_module._job_handle = None
    kill_module._job_unavailable = False

    first = kill_module._job_object()
    second = kill_module._job_object()

    assert first is not None, "CreateJobObjectW/SetInformationJobObject must succeed"
    assert first == second
