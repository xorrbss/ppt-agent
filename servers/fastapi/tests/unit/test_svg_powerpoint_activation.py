"""Office ownership is established before execution, never inferred from a ROT result."""

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.workspace_operation_handler_registry import RetryableWorkspaceOperationError
from services.svg_final_pptx_review import render_final_review_inputs
from tests.unit.test_svg_final_review_render_availability import _inputs, office_host
from workers import public_svg_pptx_render as office


@pytest.mark.parametrize("value,expected", [
    (101, 101),
    (-2147483648, 2147483648),
    (0xFFFFFFFF, 0xFFFFFFFF),
    (0x100000000, 0x100000000),
])
def test_hwnd_normalization_preserves_handle_bits(value, expected):
    assert office._normalize_hwnd(value) == expected


@pytest.mark.parametrize("value", [None, object()])
def test_hwnd_normalization_fails_closed_for_unconvertible_values(value):
    with pytest.raises(ValueError, match="svg_final_pptx_hwnd_unavailable"):
        office._normalize_hwnd(value)


@pytest.mark.parametrize("error_factory", [
    lambda: TypeError("C:\\private\\deck.pptx leaked message"),
    lambda: __import__("pythoncom").com_error(
        -2147220992, "C:\\private\\deck.pptx leaked message", None, 5
    ),
])
def test_attach_failure_writes_bounded_diagnostic_without_message_or_path(
    tmp_path, monkeypatch, error_factory
):
    error = error_factory()

    def failed_attach(_request):
        office._mark_render_stage("hwnd_pid_match")
        raise error

    monkeypatch.setattr(office, "_render", failed_attach)
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text("{}", encoding="utf-8")
    office._render_child(request_path, result_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["reasonCode"] == "svg_final_pptx_render_failed"
    assert result["retryable"] is True
    assert result["diagnostic"]["stage"] == "hwnd_pid_match"
    assert result["diagnostic"]["exceptionClass"] == type(error).__name__
    assert set(result["diagnostic"]) <= {"stage", "exceptionClass", "hresult", "winerror"}
    assert "private" not in json.dumps(result)
    assert "deck.pptx" not in json.dumps(result)

__all__ = ["office_host"]


@pytest.fixture
def activation_host(monkeypatch):
    events = []
    state = SimpleNamespace(existing=[], attached_pid=20, alive={10, 20}, inherited=False)

    def handle(pid):
        return SimpleNamespace(pid=pid, Close=lambda: events.append(("close", pid)))

    def create(executable, command, _process, _thread, inherit, flags, env, cwd, startup):
        assert flags & 4 and not inherit
        assert "/automation" in command
        assert startup.wShowWindow == 0
        events.append(("create-suspended", 20))
        return handle(20), handle(21), 20, 21

    def assign(job, process):
        events.append(("assign", process.pid))

    def terminate(process, _code):
        events.append(("terminate", process.pid))
        state.alive.discard(process.pid)

    app = SimpleNamespace(HWND=1, Presentations=SimpleNamespace(Open=Mock()), Quit=Mock())
    client = SimpleNamespace(GetActiveObject=Mock(return_value=app),
        Dispatch=Mock(side_effect=AssertionError("DCOM activation forbidden")),
        DispatchEx=Mock(side_effect=AssertionError("DCOM activation forbidden")))
    modules = {
        "pythoncom": SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None, com_error=OSError),
        "win32com": SimpleNamespace(client=client), "win32com.client": client,
        "pywintypes": SimpleNamespace(error=OSError),
        "win32api": SimpleNamespace(OpenProcess=lambda rights, inherit, pid: handle(pid)),
        "win32process": SimpleNamespace(EnumProcesses=lambda: state.existing,
            GetModuleFileNameEx=lambda *_: "POWERPNT.EXE",
            GetWindowThreadProcessId=lambda _: (0, state.attached_pid), STARTUPINFO=lambda: SimpleNamespace(dwFlags=0),
            CreateProcess=create, ResumeThread=lambda _: events.append(("resume", 20)), TerminateProcess=terminate),
        "win32event": SimpleNamespace(WAIT_TIMEOUT=258,
            WaitForSingleObject=lambda process, _: 258 if process.pid in state.alive else 0),
        "win32job": SimpleNamespace(JOB_OBJECT_ASSIGN_PROCESS=1, JOB_OBJECT_QUERY=4,
            OpenJobObject=lambda *_: handle("job"), IsProcessInJob=lambda *_: state.inherited,
            AssignProcessToJobObject=assign),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(office, "_powerpoint_executable", lambda: "POWERPNT.EXE")
    monkeypatch.setattr(office, "_available_memory", lambda: office.MINIMUM_AVAILABLE_MEMORY)
    return SimpleNamespace(events=events, state=state, app=app, client=client)


@pytest.mark.parametrize("inherited", [False, True])
def test_explicit_process_is_contained_before_resume_and_always_reaped(activation_host, inherited):
    host = activation_host
    host.state.inherited = inherited
    with office._owned_powerpoint({"jobName": "test"}) as pid:
        assert pid == 20
        assert host.events[0] == ("create-suspended", 20)
        if not inherited:
            assert host.events.index(("assign", 20)) < host.events.index(("resume", 20))
        assert host.events.index(("close", "job")) < host.events.index(("resume", 20))
    assert host.state.alive == {10}
    assert ("terminate", 10) not in host.events


def test_preexisting_user_powerpoint_defers_without_launch(activation_host):
    host = activation_host
    host.state.existing = [10]
    with pytest.raises(office.SharedPowerPointInstance):
        with office._owned_powerpoint({"jobName": "test"}):
            pytest.fail("Must not launch")
    assert not any(event[0] in {"create-suspended", "terminate"} for event in host.events)
    host.client.GetActiveObject.assert_not_called()


def test_rot_race_never_opens_quits_or_terminates_foreign_application(activation_host, tmp_path):
    host = activation_host
    host.state.attached_pid = 10  # User opened Office after the preflight snapshot.
    target = tmp_path / "artifact.pptx"
    target.write_bytes(b"pptx")
    with pytest.raises(office.SharedPowerPointInstance):
        office._render(dict(pptx=str(target), jobName="test", outputDirectory=str(tmp_path), activationTimeout=1))
    host.app.Presentations.Open.assert_not_called()
    host.app.Quit.assert_not_called()
    host.client.Dispatch.assert_not_called()
    host.client.DispatchEx.assert_not_called()
    assert host.state.alive == {10}
    assert not (tmp_path / "activated").exists()


def test_attachment_polling_deadline_reaps_only_launched_pid(activation_host, monkeypatch):
    host = activation_host
    host.client.GetActiveObject.side_effect = OSError("ROT not ready")
    clock = [0.0]
    monkeypatch.setattr(office, "monotonic", lambda: clock[0])
    monkeypatch.setattr(office, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    with pytest.raises(office.PowerPointActivationUnavailable):
        with office._owned_powerpoint({"jobName": "test"}) as pid:
            office._attach_powerpoint(pid, 0.2)
    assert clock[0] == pytest.approx(0.2)
    assert host.state.alive == {10}
    host.app.Quit.assert_not_called()


def test_failed_job_assignment_never_resumes_or_leaks_process(activation_host, monkeypatch):
    monkeypatch.setattr(sys.modules["win32job"], "AssignProcessToJobObject", Mock(side_effect=OSError("job failed")))
    with pytest.raises(OSError):
        with office._owned_powerpoint({"jobName": "test"}):
            pytest.fail("Must not resume")
    assert ("resume", 20) not in activation_host.events
    assert activation_host.state.alive == {10}


def test_hung_activation_parent_deadline_terminates_job_before_unlock(office_host, monkeypatch):
    callbacks = []
    timers = []
    alive = {10, 20}  # User PID 10 is outside the job containing our PID 20.

    class Timer:
        def __init__(self, seconds, callback):
            timers.append(seconds)
            callbacks.append(callback)

        def start(self):
            pass

        def cancel(self):
            pass

        def join(self):
            pass

    def communicate(child, **kwargs):
        callbacks[0]()  # COM never returned; simulate expiry of the parent clock.
        child.returncode = 1

    def terminate(*args):
        alive.discard(20)
        office_host.events.append("terminate")

    monkeypatch.setattr(office.threading, "Timer", Timer)
    monkeypatch.setattr(office.subprocess.Popen, "communicate", communicate)
    monkeypatch.setattr(sys.modules["win32job"], "TerminateJobObject", terminate)
    claim, inputs = _inputs()
    with pytest.raises(RetryableWorkspaceOperationError) as caught:
        asyncio.run(render_final_review_inputs(inputs, claim))
    assert caught.value.code == "svg_final_pptx_render_timeout"
    assert caught.value.retryable is True
    assert timers == [office.ACTIVATION_TIMEOUT_SECONDS]
    assert alive == {10}
    assert office_host.events.index("terminate") < office_host.events.index("unlock")


def test_failed_activation_remains_typed_retryable_through_review(office_host, monkeypatch):
    monkeypatch.setattr(office, "_render", Mock(side_effect=office.PowerPointActivationUnavailable()))
    claim, inputs = _inputs()
    with pytest.raises(RetryableWorkspaceOperationError) as caught:
        asyncio.run(render_final_review_inputs(inputs, claim))
    assert caught.value.code == "svg_final_pptx_render_failed" and caught.value.retryable


def test_reused_directory_cannot_mask_activation_timeout(office_host, monkeypatch, tmp_path):
    def communicate(child, **kwargs):
        assert not (tmp_path / "activated").exists()
        raise office.subprocess.TimeoutExpired("child", 1)

    monkeypatch.setattr(office.subprocess.Popen, "communicate", communicate)
    (tmp_path / "activated").write_text("old-pid")
    (tmp_path / "artifact.pptx").write_bytes(b"pptx")
    result = office.render_final_pptx(tmp_path / "artifact.pptx", tmp_path,
        page_count=1, width_px=16, height_px=9, timeout=1)
    assert result["reasonCode"] == "svg_final_pptx_render_timeout" and result["retryable"]
