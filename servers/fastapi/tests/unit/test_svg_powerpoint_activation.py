"""Office ownership is established before execution, never inferred from a ROT result."""

import asyncio
import importlib.util
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.workspace_operation_handler_registry import RetryableWorkspaceOperationError
from services.svg_final_pptx_review import render_final_review_inputs
from tests.unit.test_svg_final_review_render_availability import _inputs, office_host
from workers import public_svg_pptx_render as office

# Tests that drive ``_render_child`` or raise the real pywin32 ``com_error``
# need pywin32; the rest use the faked Windows modules and run on any OS.
_needs_pywin32 = pytest.mark.skipif(
    importlib.util.find_spec("pythoncom") is None, reason="requires pywin32 (Windows)"
)


@pytest.mark.parametrize(
    "existing,in_job,owned",
    [([20], True, True), ([20, 30], True, False), ([30], True, False), ([20], False, False)],
)
def test_attach_proves_single_owned_pid(activation_host, existing, in_job, owned):
    activation_host.state.existing = existing
    activation_host.state.in_job = in_job
    if owned:
        assert office._attach_powerpoint(20, 1, job_name="test") is activation_host.app
    else:
        with pytest.raises(office.SharedPowerPointInstance):
            office._attach_powerpoint(20, 1, job_name="test")


@pytest.mark.parametrize("error_factory", [
    lambda: TypeError("C:\\private\\deck.pptx leaked message"),
    pytest.param(lambda: __import__("pythoncom").com_error(
        -2147220992, "C:\\private\\deck.pptx leaked message", None, 5
    ), marks=_needs_pywin32),
])
@_needs_pywin32
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


def _renderer_request(tmp_path):
    target = tmp_path / "artifact.pptx"
    target.write_bytes(b"pptx")
    return dict(
        pptx=str(target), outputDirectory=str(tmp_path), widthPx=16, heightPx=9,
        pageCount=1, jobName="test", rendererVersion="test", activationTimeout=1,
    )


def _child_result(tmp_path):
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(json.dumps(_renderer_request(tmp_path)), encoding="utf-8")
    office._render_child(request_path, result_path)
    return json.loads(result_path.read_text(encoding="utf-8"))


def test_real_render_records_attach_stage(activation_host, tmp_path):
    activation_host.state.in_job = False
    result = _child_result(tmp_path)
    assert result["diagnostic"]["stage"] == "hwnd_pid_match"
    assert result["reasonCode"] == office.SHARED_INSTANCE_REASON
    assert result["retryable"] is True


def test_real_render_records_open_stage_after_activation(activation_host, tmp_path):
    activation_host.state.in_job = True
    activation_host.app.Presentations.Open.side_effect = ValueError(
        "C:\\private\\deck.pptx"
    )
    result = _child_result(tmp_path)
    assert result["diagnostic"]["stage"] == "open"
    assert result["reasonCode"] == "svg_final_pptx_render_failed"
    assert result.get("retryable", False) is False


@pytest.mark.parametrize(
    "case,stage,error,activated,reason,retryable",
    [
        ("create", "create", OSError("create"), False, "svg_final_pptx_render_failed", True),
        ("assign_job", "assign_job", OSError("assign"), False, "svg_final_pptx_render_failed", True),
        ("resume", "resume", OSError("resume"), False, "svg_final_pptx_render_failed", True),
        pytest.param("get_active_object", "get_active_object",
                     lambda: __import__("pythoncom").com_error(-1, "private", None, 1),
                     False, "svg_final_pptx_render_failed", True, marks=_needs_pywin32),
        ("hwnd_pid_match", "hwnd_pid_match", TypeError("private path"), False, "svg_final_pptx_render_failed", True),
        ("open", "open", ValueError("open"), True, "svg_final_pptx_render_failed", False),
        ("export", "export", OSError("export"), True, "svg_final_pptx_render_failed", False),
        ("close", "close", OSError("close"), True, "svg_final_pptx_render_failed", False),
        ("hash_changed", "close", ValueError("svg_final_pptx_hash_changed"), True, "svg_final_pptx_render_failed", False),
        ("memory_insufficient", "create", ValueError("svg_final_pptx_memory_insufficient"), False, "svg_final_pptx_render_failed", True),
        ("SharedPowerPointInstance", "create", office.SharedPowerPointInstance(), False, office.SHARED_INSTANCE_REASON, True),
    ],
)
@_needs_pywin32
def test_child_failure_table_preserves_baseline_retryability(
    tmp_path, monkeypatch, case, stage, error, activated, reason, retryable
):
    def failed_render(_request):
        office._mark_render_stage(stage)
        if activated:
            (tmp_path / "activated").write_text("20", encoding="ascii")
        raise error() if callable(error) else error

    monkeypatch.setattr(office, "_render", failed_render)
    result = _child_result(tmp_path)
    assert result["reasonCode"] == reason, case
    assert result.get("retryable", False) is retryable, case
    assert result["diagnostic"]["stage"] == stage

__all__ = ["office_host"]


@pytest.fixture
def activation_host(monkeypatch):
    events = []
    state = SimpleNamespace(existing=[], alive={10, 20}, inherited=False,
        in_job=True, checking_attach=False, foreign_on_attach=False)

    def handle(pid):
        return SimpleNamespace(pid=pid, Close=lambda: events.append(("close", pid)))

    def create(executable, command, _process, _thread, inherit, flags, env, cwd, startup):
        assert flags & 4 and not inherit
        assert "/automation" in command
        assert startup.wShowWindow == 0
        events.append(("create-suspended", 20))
        state.existing.append(20)
        return handle(20), handle(21), 20, 21

    def assign(job, process):
        events.append(("assign", process.pid))

    def terminate(process, _code):
        events.append(("terminate", process.pid))
        state.alive.discard(process.pid)

    app = SimpleNamespace(Caption="original", Presentations=SimpleNamespace(Count=0, Open=Mock()), Quit=Mock())
    def get_active_object(_):
        state.checking_attach = True
        if state.foreign_on_attach:
            state.existing.append(10)
        return app

    client = SimpleNamespace(GetActiveObject=Mock(side_effect=get_active_object),
        Dispatch=Mock(side_effect=AssertionError("DCOM activation forbidden")),
        DispatchEx=Mock(side_effect=AssertionError("DCOM activation forbidden")))
    modules = {
        "pythoncom": SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None,
            com_error=OSError),
        "win32com": SimpleNamespace(client=client), "win32com.client": client,
        "pywintypes": SimpleNamespace(error=OSError, HANDLE=lambda value: value),
        "win32api": SimpleNamespace(OpenProcess=lambda rights, inherit, pid: handle(pid)),
        "win32process": SimpleNamespace(EnumProcesses=lambda: state.existing,
            GetWindowThreadProcessId=lambda _: (0, 20),
            GetModuleFileNameEx=lambda *_: "POWERPNT.EXE",
            STARTUPINFO=lambda: SimpleNamespace(dwFlags=0),
            CreateProcess=create, ResumeThread=lambda _: events.append(("resume", 20)), TerminateProcess=terminate),
        "win32gui": SimpleNamespace(EnumWindows=lambda visit, context: visit(100, context),
            GetClassName=lambda _: "PPTFrameClass", GetWindowText=lambda _: app.Caption),
        "win32event": SimpleNamespace(WAIT_TIMEOUT=258,
            WaitForSingleObject=lambda process, _: 258 if process.pid in state.alive else 0),
        "win32job": SimpleNamespace(JOB_OBJECT_ASSIGN_PROCESS=1, JOB_OBJECT_QUERY=4,
            OpenJobObject=lambda *_: handle("job"),
            IsProcessInJob=lambda _process, _job: state.in_job if state.checking_attach else state.inherited,
            AssignProcessToJobObject=assign),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(office, "_powerpoint_executable", lambda: "POWERPNT.EXE")
    monkeypatch.setattr(office, "_process_identity", lambda pid: dict(pid=pid, creation=100))
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
    host.state.foreign_on_attach = True  # User opened Office after the preflight snapshot.
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
