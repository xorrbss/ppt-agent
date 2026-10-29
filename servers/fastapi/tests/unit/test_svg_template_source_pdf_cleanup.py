"""Source capture must quit only its unchanged, exactly owned source document."""
from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from services.svg_templates import source_pdf_worker as capture
from services.svg_templates.frame_fonts import artifact_digest, bytes_digest
from workers import public_svg_pptx_render as renderer


_SCOPE = dict(operation_id="00000000-0000-0000-0000-000000000001",
              owner_id="00000000-0000-0000-0000-000000000002", tenant_id="cleanup-test")


@pytest.mark.parametrize("fault", [
    None, "window", "creation", "job", "foreign", "count", "document", "path",
    "writable", "unsaved", "mixed_saved", "unexpected_saved", "source", "quit",
])
def test_child_quits_before_window_destruction_and_rejects_lost_ownership(tmp_path, monkeypatch, fault):
    from models.svg_template_frame import SvgTemplatePackage

    source = tmp_path / "source.pptx"
    source.write_bytes(b"immutable source")
    package = SimpleNamespace(source_digest=bytes_digest(source.read_bytes()),
        source_canvas=SimpleNamespace(physical_size=SimpleNamespace(width_emu=12700, height_emu=12700)),
        frames=[SimpleNamespace(source_page=0, elements=[])])
    marker = dict(schema_version="svg-template-source-pdf-capture/v1", **_SCOPE,
        tenant_digest=bytes_digest(b"cleanup-test"), source_digest=package.source_digest, package_digest="test")
    request = dict(marker, package={}, source_path=str(source), typelib={}, capture_code={}, jobName="owned-test")
    request_path, receipt_path = tmp_path / "request.json", tmp_path / "child-receipt.json"
    request_path.write_text(json.dumps(request))
    events = []
    state = SimpleNamespace(alive=False, foreign=False, window_pid=2, creation=100, in_job=True)

    def export(_dispid, _lcid, _method, _return_type, _argument_types, path, *_args):
        events.append("export")
        Path(path).write_bytes(b"%PDF-test")

    def close():
        state.window_pid = 0
        events.append("close-destroys-window")

    document = SimpleNamespace(ReadOnly=-1, Saved=-1, FullName=str(source),
        PageSetup=SimpleNamespace(SlideWidth=1, SlideHeight=1),
        Slides=SimpleNamespace(Count=1, Item=lambda _: SimpleNamespace(Shapes=SimpleNamespace(Count=0))),
        _oleobj_=SimpleNamespace(InvokeTypes=export), Close=close)
    presentations = SimpleNamespace(Count=0, Item=lambda _: document)

    def open_document(*_args, **kwargs):
        assert kwargs == dict(ReadOnly=True, Untitled=False, WithWindow=False)
        presentations.Count = 1
        events.append("open")
        return document

    def quit_application():
        assert state.window_pid == 2 and state.creation == 100 and state.in_job and not state.foreign
        events.append("quit")
        if fault == "quit":
            raise OSError("quit refused")
        state.window_pid, state.alive, presentations.Count = 0, False, 0

    presentations.Open = open_document
    app = SimpleNamespace(Presentations=presentations, Version="test", Build="test", Quit=quit_application)
    handle = SimpleNamespace(Close=lambda: None)
    modules = dict(pythoncom=SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: events.append("uninit"),
        DISPATCH_METHOD=1, Missing=object()),
        win32process=SimpleNamespace(GetWindowThreadProcessId=lambda _: (0, state.window_pid)),
        win32api=SimpleNamespace(OpenProcess=lambda *_: handle),
        win32job=SimpleNamespace(JOB_OBJECT_QUERY=4, OpenJobObject=lambda *_: handle,
            IsProcessInJob=lambda *_: state.in_job), pywintypes=SimpleNamespace(error=OSError))
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(b"capture\n")))
    monkeypatch.setattr(SvgTemplatePackage, "model_validate", lambda _: package)
    monkeypatch.setattr(capture, "_typelib_binding", lambda: {})
    monkeypatch.setattr(capture, "_capture_code_identity", lambda: {})
    monkeypatch.setattr(renderer, "_available_memory", lambda: renderer.MINIMUM_AVAILABLE_MEMORY)
    monkeypatch.setattr(renderer, "_powerpoint_pids",
        lambda: ({2} if state.alive else set()) | ({30} if state.foreign else set()))
    monkeypatch.setattr(renderer, "_process_identity", lambda pid: dict(pid=pid, creation=state.creation))

    @contextmanager
    def owned_process(_request):
        state.alive = True
        try:
            yield 2
        finally:
            state.alive = False
            events.append("owned-containment")

    def attach(pid, _timeout, *, job_name, binding):
        assert pid == 2 and job_name == "owned-test"
        binding.update(application=app, identity=dict(pid=2, creation=100), hwnd=100)
        return app

    def metadata(data, _package):
        if fault == "window":
            state.window_pid = 30
        elif fault == "creation":
            state.creation = 101
        elif fault == "job":
            state.in_job = False
        elif fault == "foreign":
            state.foreign = True
        elif fault == "count":
            presentations.Count = 2
        elif fault == "document":
            presentations.Item = lambda _: SimpleNamespace(_oleobj_=object())
        elif fault == "path":
            document.FullName = str(tmp_path / "foreign.pptx")
        elif fault == "writable":
            document.ReadOnly = 0
        elif fault in {"unsaved", "mixed_saved", "unexpected_saved"}:
            document.Saved = {"unsaved": 0, "mixed_saved": -2, "unexpected_saved": 1}[fault]
        elif fault == "source":
            source.write_bytes(b"changed source")
        return dict(pdf_digest=bytes_digest(data), pdf_bytes=len(data), page_count=1)

    monkeypatch.setattr(renderer, "_owned_powerpoint", owned_process)
    monkeypatch.setattr(renderer, "_attach_powerpoint", attach)
    monkeypatch.setattr(capture, "_pdf_metadata", metadata)
    capture._child(str(request_path), str(receipt_path))
    receipt = json.loads(receipt_path.read_text())
    assert receipt["export_calls"] == 1 and "close-destroys-window" not in events
    assert events[-2:] == ["owned-containment", "uninit"]
    assert source.read_bytes() == (b"changed source" if fault == "source" else b"immutable source")
    assert state.foreign is (fault == "foreign")
    if fault is None:
        assert receipt["status"] == "captured" and receipt["com_cleanup"] == "owned_application_quit"
        assert [entry["stage"] for entry in receipt["ownership"]][-2:] == ["before_quit_source", "before_quit"]
        assert "cleanup_failure" not in receipt and events.count("quit") == 1
    else:
        assert receipt["status"] == "failed_no_retry" and receipt["com_cleanup"] == "owned_job_fallback"
        assert receipt["cleanup_failure"]["stage"] == "guarded_application_quit"
        assert receipt["failure_class"] == receipt["cleanup_failure"]["exception_class"]
        assert events.count("quit") == int(fault == "quit")


@pytest.mark.parametrize("child_delta", [
    {}, {"com_cleanup": "owned_job_fallback"}, {"cleanup_failure": {}},
    {"status": "failed_no_retry", "failure_class": "SharedPowerPointInstance"},
])
def test_parent_accepts_only_normal_child_cleanup(tmp_path, monkeypatch, child_delta):
    from services.svg_templates import frame_glyphs, source_review
    from utils import get_env, renderer_worker_policy

    source = tmp_path / "source.pptx"
    source.write_bytes(b"immutable source")
    package = SimpleNamespace(source_digest=bytes_digest(source.read_bytes()), digest="test", font_manifest=None,
        model_dump=lambda **_: {})
    monkeypatch.setattr(get_env, "get_app_data_directory_env", lambda: str(tmp_path))
    monkeypatch.setattr(source_review, "_require_installed_fonts", lambda _: None)
    monkeypatch.setattr(frame_glyphs, "validate_capture_source_subset", lambda *_: None)
    monkeypatch.setattr(renderer, "_available_memory", lambda: renderer.MINIMUM_AVAILABLE_MEMORY)
    monkeypatch.setattr(renderer, "_powerpoint_pids", lambda: set())
    monkeypatch.setattr(capture, "_typelib_binding", lambda: {})
    monkeypatch.setattr(capture, "_capture_code_identity", lambda: {})
    monkeypatch.setattr(renderer_worker_policy, "build_worker_environment", lambda: {})
    monkeypatch.setattr(renderer_worker_policy, "assert_no_provider_secrets", lambda _: None)
    monkeypatch.setattr(capture, "_pdf_metadata", lambda data, _: dict(
        pdf_digest=bytes_digest(data), pdf_bytes=len(data), page_count=1))
    events = []
    handle = SimpleNamespace(Close=lambda: None)
    modules = dict(win32api=SimpleNamespace(OpenProcess=lambda *_: handle),
        win32event=SimpleNamespace(CreateMutex=lambda *_: handle, WaitForSingleObject=lambda *_: 0,
            WAIT_OBJECT_0=0, WAIT_ABANDONED=128, ReleaseMutex=lambda _: None),
        win32job=SimpleNamespace(CreateJobObject=lambda *_: handle, JobObjectExtendedLimitInformation=9,
            QueryInformationJobObject=lambda *_: {"BasicLimitInformation": {"LimitFlags": 0}},
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=8192, SetInformationJobObject=lambda *_: None,
            AssignProcessToJobObject=lambda *_: None, IsProcessInJob=lambda *_: True,
            TerminateJobObject=lambda *_: events.append("owned-job-closed")))
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    class Child:
        pid, returncode = 2, 0

        def __init__(self, args, **_kwargs):
            self.request = json.loads(Path(args[-2]).read_text())
            self.path = Path(args[-1])

        def communicate(self, *, input, timeout):
            assert input == b"capture\n" and timeout == 60
            data = b"%PDF-test"
            self.path.with_name("source.pdf").write_bytes(data)
            receipt = {**self.request, "status": "captured", "export_calls": 1,
                "com_cleanup": "owned_application_quit", "pdf_digest": bytes_digest(data),
                "producer_version": "test", "producer_build": "test", **child_delta}
            self.path.write_text(json.dumps(receipt))

        def poll(self):
            return 0

        def wait(self, *, timeout):
            return 0

    monkeypatch.setattr(capture.subprocess, "Popen", Child)
    if child_delta:
        with pytest.raises(ValueError, match="capture_failed"):
            capture.capture_source_pdf(source, package, **_SCOPE)
    else:
        data, receipt = capture.capture_source_pdf(source, package, **_SCOPE)
        assert data == b"%PDF-test" and receipt["status"] == "captured"
    directory = tmp_path / "svg-template-source-captures" / bytes_digest(b"cleanup-test")[7:] / _SCOPE["owner_id"] / _SCOPE["operation_id"]
    persisted = json.loads((directory / "receipt.json").read_text())
    assert persisted["source_unchanged"] is True and persisted["owned_job_closed"] is True
    assert (directory / "source.pdf").read_bytes() == b"%PDF-test" and events == ["owned-job-closed"]
    assert source.read_bytes() == b"immutable source"
    if child_delta:
        assert persisted["status"] == "failed_no_retry" and persisted["child_receipt"] == json.loads(
            (directory / "child-receipt.json").read_text())
        assert "pdf_digest" not in persisted


@pytest.mark.parametrize("child_delta", [
    {}, {"com_cleanup": "owned_job_fallback"}, {"cleanup_failure": {}}, {"failure_class": "test"},
])
def test_verifier_rejects_persisted_abnormal_child_cleanup(tmp_path, monkeypatch, child_delta):
    from utils import get_env

    monkeypatch.setattr(get_env, "get_app_data_directory_env", lambda: str(tmp_path))
    digest = bytes_digest(b"%PDF-test")
    directory = tmp_path / "svg-template-source-captures" / bytes_digest(b"cleanup-test")[7:] / _SCOPE["owner_id"] / _SCOPE["operation_id"]
    directory.mkdir(parents=True)
    (directory / "source.pdf").write_bytes(b"%PDF-test")
    receipt = dict(status="captured", operation_id=_SCOPE["operation_id"], owner_id=_SCOPE["owner_id"],
        tenant_digest=bytes_digest(b"cleanup-test"), source_digest="source", pdf_digest=digest,
        source_unchanged=True, owned_job_closed=True,
        child_receipt={"status": "captured", "com_cleanup": "owned_application_quit", **child_delta})
    (directory / "receipt.json").write_text(json.dumps(receipt))
    kwargs = dict(owner_id=_SCOPE["owner_id"], tenant_id=_SCOPE["tenant_id"], expected_digest=artifact_digest(receipt),
        expected_pdf_digest=digest, expected_source_digest="source")
    if child_delta:
        with pytest.raises(ValueError):
            capture.verify_capture_receipt(_SCOPE["operation_id"], **kwargs)
    else:
        assert capture.verify_capture_receipt(_SCOPE["operation_id"], **kwargs) == receipt
