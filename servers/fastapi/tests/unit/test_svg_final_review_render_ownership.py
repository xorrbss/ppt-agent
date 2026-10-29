"""The review owns Office and export receipts consume its artifact-bound proof."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import uuid

import pytest
from PIL import Image

from services.svg_final_pptx_review import (
    final_pptx_visual_domain, final_render_current, persist_final_review_render,
    render_final_review_inputs, seal_final_review,
)
from services.svg_export_release_gate import require_release_v1_receipt
from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError
from utils.hashing import canonical_json_sha256, sha256_hex
from workers import public_svg_pptx_render as office


def test_export_never_activates_office_and_records_worker_spans(tmp_path, monkeypatch):
    from tests.unit.test_svg_flagged_deck_lifecycle_export import _deck, _export
    render = Mock(side_effect=AssertionError("Export must not render in Office"))
    monkeypatch.setattr(office, "render_final_pptx", render)
    canvas, sources, receipts = _deck()
    _, evidence = _export(tmp_path, canvas, sources, receipts)
    render.assert_not_called()
    assert "finalPptxRender" not in evidence["structuralReadback"]
    assert set(evidence["stageTimingsSeconds"]) == {"bundleRead", "conversion", "readback", "artifactPut"}
    assert all(value >= 0 for value in evidence["stageTimingsSeconds"].values())


def test_worker_timings_are_excluded_from_quality_digests():
    from tests.unit.test_svg_export_quality import evidence_fixture
    from services.svg_export_quality import source_svg_public_quality_receipt
    _, _, slide, receipt, readback = evidence_fixture()
    metrics = dict(public_export_worker_evidence=dict(structuralReadback=dict(sourcePreservation=readback)))
    kwargs = dict(slides=[slide], source_preservation=[receipt], worker_metrics=metrics,
        quality_receipt_id="q", operation_id="op", presentation_id="p", artifact_digest="sha256:" + "a" * 64,
        source_revision=1, scene_digest="sha256:" + "b" * 64, evaluated_at=datetime.now(timezone.utc),
        exporter_version="test")
    before = source_svg_public_quality_receipt(**kwargs)
    metrics["public_export_worker_evidence"]["stageTimingsSeconds"] = dict(
        bundleRead=2, conversion=3, readback=4, artifactPut=5)
    assert source_svg_public_quality_receipt(**kwargs) == before


@pytest.fixture
def rendered_review(monkeypatch):
    claim = SimpleNamespace(operation_id=uuid.uuid4(), owner_id=uuid.uuid4(), presentation_id=uuid.uuid4(),
        expected_revision=1, owner_scope="owner")
    payload = b"immutable-pptx"
    inputs = dict(artifactId=str(uuid.uuid4()), artifactDigest="sha256:" + sha256_hex(payload),
        artifactBytes=payload, parentExportOperationId=str(uuid.uuid4()), sourceDigests=["a" * 64],
        ownerId=str(claim.owner_id), tenantId="tenant", presentationId=str(claim.presentation_id),
        sourceRevision=1, widthPx=16, heightPx=9)

    def render(target, directory, **kwargs):
        assert target.read_bytes() == payload
        png = directory / "page-000.png"
        Image.new("RGB", (16, 9)).save(png)
        return dict(schemaVersion=office.SCHEMA, status="measured", artifactDigest=inputs["artifactDigest"],
            artifactDigestAfter=inputs["artifactDigest"], hashInvariant=True, openReadOnly=True, openWithWindow=False,
            availablePhysicalMemoryBytes=office.MINIMUM_AVAILABLE_MEMORY, pageCount=1,
            checkerCodeDigest=office.checker_digest(), renderIsolation="windows-job-object",
            rendererQuit=True, pages=[dict(index=0, pngSha256=sha256_hex(png.read_bytes()), widthPx=16, heightPx=9)])

    monkeypatch.setattr(office, "render_final_pptx", render)
    result = asyncio.run(render_final_review_inputs(inputs, claim))

    async def persist(session, **kwargs):
        assert kwargs["created_by_operation_id"] == claim.operation_id
        ref = dict(owner_id=str(claim.owner_id), tenant_id="tenant", created_by_operation_id=str(claim.operation_id),
            kind="slide-png", media_type="image/png", sha256=sha256_hex(kwargs["payload"]))
        return SimpleNamespace(model_dump=lambda **_: ref)

    monkeypatch.setattr("services.artifact_store.configured_public_artifact_service",
        lambda: SimpleNamespace(persist_bytes=persist))
    asyncio.run(persist_final_review_render(object(), result, claim))
    vision = dict(promptVersion="svg-required-visual-review/v1", slideCount=1, model="test-model",
        reviewedPngDigests=[result["proof"]["pages"][0]["pngSha256"]], reviewedPageIndices=[0],
        slides=[dict(index=0, issues=[])])
    review = seal_final_review(claim, result, vision)
    return claim, result, review


def _current(claim, inputs, proof):
    return final_render_current(proof, artifact_digest=inputs["artifactDigest"], operation_id=claim.operation_id,
        presentation_id=claim.presentation_id, source_revision=claim.expected_revision,
        source_digests=inputs["sourceDigests"], owner_id=claim.owner_id, tenant_id="tenant")


def _domain(claim, inputs, review):
    source = dict(required=True, status="passed", evidence=dict(outputDigest="sha256:" + "b" * 64,
        measurements=dict(advisoryDigest="sha256:" + "c" * 64)))
    return final_pptx_visual_domain(source, worker_metrics=dict(final_pptx_visual_review=review),
        artifact_digest=inputs["artifactDigest"], operation_id=str(uuid.uuid4()),
        presentation_id=claim.presentation_id, source_revision=claim.expected_revision,
        source_digests=inputs["sourceDigests"])


def test_review_render_is_bound_to_review_and_current_artifact(rendered_review):
    claim, inputs, review = rendered_review
    assert _current(claim, inputs, inputs["proof"])
    assert review["renderOperationId"] == review["operationId"] == str(claim.operation_id)
    assert _domain(claim, inputs, review)["status"] == "passed"
    changed = dict(inputs, artifactDigest="sha256:" + "f" * 64)
    assert not _current(claim, changed, inputs["proof"])
    assert _domain(claim, changed, review)["reasonCode"] == "svg_final_pptx_render_unavailable"


def test_checker_dependency_digest_binds_producer_validator_and_ownership(
    rendered_review, tmp_path, monkeypatch
):
    import hashlib
    import json
    from utils import powerpoint_ownership

    claim, inputs, _ = rendered_review
    baseline = office.checker_digest()
    assert inputs["proof"]["checkerCodeDigest"] == baseline
    assert _current(claim, inputs, inputs["proof"])
    worker = Path(office.__file__).resolve()
    helper = Path(powerpoint_ownership.__file__).resolve()
    expected = hashlib.sha256(b"svg-final-pptx-render-checker/v2\0")
    for name, path in ((b"renderer", worker), (b"ownership", helper)):
        expected.update(name + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    assert baseline == expected.hexdigest()

    captured = []
    handle = SimpleNamespace(Close=lambda: None)
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(OpenProcess=lambda *_: handle))
    monkeypatch.setitem(sys.modules, "win32job", SimpleNamespace(
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=8192, JobObjectExtendedLimitInformation=9,
        CreateJobObject=lambda *_: handle,
        QueryInformationJobObject=lambda *_: {"BasicLimitInformation": {"LimitFlags": 0}},
        SetInformationJobObject=lambda *_: None, AssignProcessToJobObject=lambda *_: None,
        TerminateJobObject=lambda *_: None))

    class Child:
        pid = 20
        returncode = 0
        stdin = SimpleNamespace(close=lambda: None)

        def __init__(self, args, **kwargs):
            self.request_path, self.result_path = map(Path, args[-2:])

        def communicate(self, **kwargs):
            request = json.loads(self.request_path.read_text())
            captured.append(request["rendererVersion"])
            self.result_path.write_text(json.dumps(dict(status="measured",
                artifactDigest="sha256:" + office._sha(request["pptx"]),
                checkerCodeDigest=request["rendererVersion"])))

        def poll(self):
            return 0

        def wait(self):
            return 0

    monkeypatch.setattr(office.subprocess, "Popen", Child)
    target = tmp_path / "immutable.pptx"
    target.write_bytes(b"immutable")
    result = office._render_serialized(target, tmp_path / "render", page_count=1,
        width_px=16, height_px=9, timeout=1)
    assert captured == [baseline] and result["checkerCodeDigest"] == baseline

    historical = deepcopy(inputs["proof"])
    historical["checkerCodeDigest"] = sha256_hex(worker.read_bytes())
    historical.pop("evidenceDigest")
    historical["evidenceDigest"] = "sha256:" + canonical_json_sha256(historical)
    assert not _current(claim, inputs, historical)
    original_read = Path.read_bytes
    for dependency in (worker, helper):
        with monkeypatch.context() as changed:
            changed.setattr(Path, "read_bytes", lambda path: original_read(path)
                + (b"\n# changed dependency\n" if path.resolve() == dependency else b""))
            assert office.checker_digest() != baseline
            assert not _current(claim, inputs, inputs["proof"])
    assert office.checker_digest() == baseline
    assert inputs["proof"]["checkerCodeDigest"] == baseline


@pytest.mark.parametrize("field,value", [
    ("operationId", "another-operation"), ("renderOperationId", "another-operation"),
    ("artifactDigestAfter", "sha256:" + "e" * 64), ("rendererQuit", False),
    ("renderIsolation", "uncontained"), ("sourceDigests", ["e" * 64]),
])
def test_resigning_wrong_render_bindings_cannot_make_them_current(rendered_review, field, value):
    claim, inputs, _ = rendered_review
    proof = deepcopy(inputs["proof"])
    proof[field] = value
    proof.pop("evidenceDigest")
    proof["evidenceDigest"] = "sha256:" + canonical_json_sha256(proof)
    assert not _current(claim, inputs, proof)


def test_release_requires_review_and_accepts_review_owned_render(rendered_review):
    claim, inputs, review = rendered_review
    missing = _domain(claim, inputs, None)
    with pytest.raises(NonRetryableWorkspaceOperationError, match="svg_release_quality_visual_review_not_passed"):
        require_release_v1_receipt(dict(visualReview=missing, releaseReadiness="blocked"))
    passed = _domain(claim, inputs, review)
    require_release_v1_receipt(dict(visualReview=passed, releaseReadiness="ready"))


def test_historical_export_render_remains_readable_but_old_checker_is_stale(rendered_review):
    claim, inputs, _ = rendered_review
    historical = deepcopy(inputs["proof"])
    historical.pop("renderOperationId")
    historical["operationId"] = inputs["parentExportOperationId"]
    historical["checkerCodeDigest"] = "0" * 64
    historical.pop("evidenceDigest")
    historical["evidenceDigest"] = "sha256:" + canonical_json_sha256(historical)
    metrics = dict(public_export_worker_evidence=dict(structuralReadback=dict(finalPptxRender=historical)))
    domain = final_pptx_visual_domain(dict(status="passed"), worker_metrics=metrics,
        artifact_digest=inputs["artifactDigest"], operation_id=inputs["parentExportOperationId"],
        presentation_id=claim.presentation_id, source_revision=1, source_digests=inputs["sourceDigests"])
    assert domain["reasonCode"] == "svg_final_pptx_render_unavailable"
    assert metrics["public_export_worker_evidence"]["structuralReadback"]["finalPptxRender"] == historical


def test_final_review_handler_renders_once_and_persists_after_revalidation(monkeypatch, rendered_review):
    from unittest.mock import AsyncMock
    from tests.unit.test_svg_review_pages import _review_execution
    from services import svg_final_pptx_review as final
    _, inputs, _ = rendered_review
    module, factory, context, presentation, _, _ = _review_execution(monkeypatch)
    context.claim.request_payload["context"].update(target="final_pptx", artifact_id=inputs["artifactId"])
    render = AsyncMock(return_value=inputs)
    load = AsyncMock(return_value={key: value for key, value in inputs.items() if key not in {"proof", "pngs"}})
    persist = AsyncMock()
    monkeypatch.setattr(final, "load_final_review_inputs", load)
    monkeypatch.setattr(final, "render_final_review_inputs", render)
    monkeypatch.setattr(final, "persist_final_review_render", persist)
    monkeypatch.setattr("services.svg_export_operation.require_current_header_revision_authority",
        AsyncMock(return_value=None))
    asyncio.run(module.execute_svg_quality_review(context, factory))
    assert load.await_count == 2
    render.assert_awaited_once()
    persist.assert_awaited_once()
    assert inputs["artifactDigest"][7:] in presentation.theme["svg"]["finalPptxReviews"]


def test_review_admission_loads_verified_artifact_without_export_render(monkeypatch):
    from unittest.mock import AsyncMock
    from models.sql.public_artifact import PublicArtifact
    from services.svg_final_pptx_review import load_final_review_inputs
    owner, presentation_id, export_id, artifact_id = [uuid.uuid4() for _ in range(4)]
    presentation = SimpleNamespace(id=presentation_id, owner_id=owner, revision=1, theme={})
    public = SimpleNamespace(id=artifact_id, owner_id=owner, kind="presentation-pptx", tenant_id="tenant",
        sha256=sha256_hex(b"pptx"), to_ref=lambda: "verified-ref")
    export = SimpleNamespace(id=export_id, state="succeeded", owner_id=owner, presentation_id=presentation_id,
        expected_revision=1, result={})
    session = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, key: public if model is PublicArtifact else export),
        scalar=AsyncMock(return_value=SimpleNamespace(operation_id=export_id)))
    monkeypatch.setattr("services.svg_export_operation.require_current_header_revision_authority",
        AsyncMock(return_value=None))
    monkeypatch.setattr("utils.owner_context.get_current_tenant_id", lambda: "tenant")
    read = AsyncMock(return_value=b"pptx")
    monkeypatch.setattr("services.artifact_store.configured_public_artifact_service",
        lambda: SimpleNamespace(read_ref_bytes=read))
    render = Mock(side_effect=AssertionError("Admission cannot render"))
    monkeypatch.setattr(office, "render_final_pptx", render)
    inputs = asyncio.run(load_final_review_inputs(session, request=SimpleNamespace(artifact_id=artifact_id),
        presentation=presentation, slides=[SimpleNamespace(svg_content="<svg/>")]))
    assert inputs["artifactBytes"] == b"pptx"
    assert inputs["parentExportOperationId"] == str(export_id)
    read.assert_awaited_once_with("verified-ref")
    render.assert_not_called()


def test_render_timeout_terminates_the_job_and_reaps_the_child(tmp_path, monkeypatch):
    import subprocess
    events = []
    handle = SimpleNamespace(Close=lambda: events.append("handle-close"))
    job = SimpleNamespace(Close=lambda: events.append("job-close"))
    child = SimpleNamespace(pid=2, returncode=None, stdin=SimpleNamespace(close=lambda: None))

    def communicate(**kwargs):
        assert "assign" in events
        raise subprocess.TimeoutExpired("render", 1)

    child.communicate = communicate
    child.poll = lambda: None
    child.kill = lambda: events.append("kill")
    child.wait = lambda: events.append("wait")
    monkeypatch.setitem(sys.modules, "win32event", SimpleNamespace(
        CreateMutex=lambda *args: SimpleNamespace(Close=lambda: None),
        WaitForSingleObject=lambda *args: 0, ReleaseMutex=lambda *args: None,
        WAIT_OBJECT_0=0, WAIT_ABANDONED=128, WAIT_TIMEOUT=258))
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(OpenProcess=lambda *args: handle))
    monkeypatch.setitem(sys.modules, "win32job", SimpleNamespace(
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=8192, JobObjectExtendedLimitInformation=9,
        CreateJobObject=lambda *args: job,
        QueryInformationJobObject=lambda *args: {"BasicLimitInformation": {"LimitFlags": 0}},
        SetInformationJobObject=lambda *args: None,
        AssignProcessToJobObject=lambda *args: events.append("assign"),
        TerminateJobObject=lambda *args: events.append("terminate-job")))
    monkeypatch.setattr(office, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(office.subprocess, "Popen", lambda *args, **kwargs: child)
    target = tmp_path / "artifact.pptx"
    target.write_bytes(b"pptx")
    result = office.render_final_pptx(target, tmp_path / "render", page_count=1, width_px=16, height_px=9, timeout=1)
    assert result["reasonCode"] == "svg_final_pptx_render_timeout"
    assert events.index("terminate-job") < events.index("job-close") < events.index("wait")


@pytest.mark.parametrize(
    "shared,fail,cleanup_error",
    [
        (False, False, None),
        (False, False, "document_attribute"),
        (False, False, "document_com"),
        (False, False, "quit_attribute"),
        (False, False, "quit_com"),
        (False, True, None),
        (True, False, None),
    ],
)
def test_office_opens_only_after_containment_and_quits_only_owned_instance(
    tmp_path, monkeypatch, shared, fail, cleanup_error
):
    from contextlib import contextmanager

    events = []
    handle = SimpleNamespace(Close=lambda: None)
    class Slides:
        Count = 1

        def __call__(self, index):
            return self

        def Export(self, path, fmt, width, height):
            if fail:
                raise ValueError("render failed")
            Image.new("RGB", (width, height)).save(path)

    def close_document():
        if cleanup_error == "document_attribute":
            raise AttributeError("close unavailable")
        if cleanup_error == "document_com":
            raise OSError("close unavailable")
        app.Presentations.Count = 0
        events.append("close")

    document = SimpleNamespace(Slides=Slides(), Close=close_document)
    def open_document(*args, **kwargs):
        assert "assign" in events
        app.Presentations.Count = 1
        events.append("open")
        return document
    def quit_application():
        if cleanup_error == "quit_attribute":
            raise AttributeError("quit unavailable")
        if cleanup_error == "quit_com":
            raise OSError("quit unavailable")
        events.append("quit")

    app = SimpleNamespace(Caption="original", Version="test", Presentations=SimpleNamespace(Count=0, Open=open_document),
        Quit=quit_application)
    modules = {
        "pythoncom": SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: events.append("uninit"),
            com_error=OSError),
        "win32com": SimpleNamespace(client=SimpleNamespace(GetActiveObject=lambda _: app)),
        "win32com.client": SimpleNamespace(GetActiveObject=lambda _: app),
        "win32api": SimpleNamespace(OpenProcess=lambda *args: handle),
        "win32process": SimpleNamespace(EnumProcesses=lambda: [1, 2] if shared else [2],
            GetWindowThreadProcessId=lambda _: (0, 2),
            GetModuleFileNameEx=lambda *_: "POWERPNT.EXE"),
        "win32gui": SimpleNamespace(EnumWindows=lambda visit, context: visit(100, context),
            GetClassName=lambda _: "PPTFrameClass", GetWindowText=lambda _: app.Caption),
        "win32job": SimpleNamespace(JOB_OBJECT_ASSIGN_PROCESS=1, JOB_OBJECT_QUERY=4,
            OpenJobObject=lambda *args: handle, IsProcessInJob=lambda *args: True,
            AssignProcessToJobObject=lambda *args: events.append("assign")),
    }
    for key, module in modules.items():
        monkeypatch.setitem(sys.modules, key, module)

    @contextmanager
    def owned_process(request):
        events.append("assign")
        try:
            yield 2
        finally:
            events.append("job-terminate")

    monkeypatch.setattr(office, "_owned_powerpoint", owned_process)
    monkeypatch.setattr(office, "_process_identity", lambda pid: dict(pid=pid, creation=100))
    monkeypatch.setattr(office, "_available_memory", lambda: office.MINIMUM_AVAILABLE_MEMORY)
    target = tmp_path / "artifact.pptx"
    target.write_bytes(b"pptx")
    request = dict(pptx=str(target), outputDirectory=str(tmp_path), widthPx=16, heightPx=9, pageCount=1,
        jobName="test", rendererVersion="test", activationTimeout=1)
    if shared or fail:
        with pytest.raises(ValueError):
            office._render(request)
    else:
        result = office._render(request)
        assert result["rendererQuit"] is True
        expected_cleanup = "job_termination" if cleanup_error else "application_quit"
        assert result["cleanupMethod"] == expected_cleanup
    if shared:
        assert events == ["assign", "job-terminate", "uninit"]
    elif cleanup_error in {"document_attribute", "document_com"}:
        assert events == ["assign", "open", "job-terminate", "uninit"]
    elif cleanup_error in {"quit_attribute", "quit_com"}:
        assert events == ["assign", "open", "close", "job-terminate", "uninit"]
    else:
        assert events == ["assign", "open", "close", "quit", "job-terminate", "uninit"]


@pytest.mark.parametrize("fault", [
    None, "foreign_window", "duplicate_window", "missing_window", "restore", "nonempty",
    "before_open_creation", "before_export_window", "before_export_job", "before_close_window",
    "before_quit_window",
])
def test_com_window_binding_guards_every_document_action(tmp_path, monkeypatch, fault):
    from contextlib import contextmanager
    from services.svg_templates import source_review
    from utils import powerpoint_ownership as ownership

    events = []
    state = SimpleNamespace(pid=2, creation=100, reads=0, in_job=True, caption="original", foreign_alive=True)

    class Application:
        Version = "test"

        def get_caption(self):
            return state.caption

        def set_caption(self, value):
            if fault == "restore" and value == "original":
                raise ValueError("restore refused")
            state.caption = value

        Caption = property(get_caption, set_caption)

        def Quit(self):
            events.append("quit")

    app = Application()

    def open_document(*args, **kwargs):
        assert state.caption == "original" and kwargs["ReadOnly"] is True
        events.append("open")
        app.Presentations.Count = 1
        if fault == "before_export_window":
            state.pid = 30
        if fault == "before_export_job":
            state.in_job = False
        return document

    def export(path, fmt, width, height):
        events.append("export")
        Image.new("RGB", (width, height)).save(path)
        if fault == "before_close_window":
            state.pid = 30

    def close():
        events.append("close")
        app.Presentations.Count = 0
        if fault == "before_quit_window":
            state.pid = 30

    app.Presentations = SimpleNamespace(Count=1 if fault == "nonempty" else 0, Open=open_document)
    document = SimpleNamespace(Slides=SimpleNamespace(Count=1), Close=close)

    class Slides:
        Count = 1

        def __call__(self, index):
            return SimpleNamespace(Export=export)

    document.Slides = Slides()

    def identity(pid):
        state.reads += 1
        if fault == "before_open_creation" and state.reads == 3:
            state.creation += 1
        return dict(pid=pid, creation=state.creation)

    def windows(visit, context):
        if fault != "missing_window":
            visit(100, context)
        if fault == "duplicate_window":
            visit(101, context)

    handle = SimpleNamespace(Close=lambda: None)
    client = SimpleNamespace(GetActiveObject=lambda _: app)
    modules = {
        "pythoncom": SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: events.append("uninit"),
            PumpWaitingMessages=lambda: None, com_error=OSError),
        "win32com": SimpleNamespace(client=client), "win32com.client": client,
        "win32api": SimpleNamespace(OpenProcess=lambda *args: handle),
        "win32process": SimpleNamespace(EnumProcesses=lambda: [2], GetModuleFileNameEx=lambda *_: "POWERPNT.EXE",
            GetWindowThreadProcessId=lambda _: (0, 30 if fault == "foreign_window" else state.pid)),
        "win32gui": SimpleNamespace(EnumWindows=windows, GetClassName=lambda _: "PPTFrameClass",
            GetWindowText=lambda _: app.Caption),
        "win32job": SimpleNamespace(JOB_OBJECT_QUERY=4, OpenJobObject=lambda *_: handle,
            IsProcessInJob=lambda *_: state.in_job),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(ownership, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(office, "_process_identity", identity)
    monkeypatch.setattr(source_review, "_process_identity", identity)
    monkeypatch.setattr(office, "_available_memory", lambda: office.MINIMUM_AVAILABLE_MEMORY)

    @contextmanager
    def owned_process(request):
        events.append("owned-launch")
        try:
            yield 2
        finally:
            events.append("terminate-own-job")

    monkeypatch.setattr(office, "_owned_powerpoint", owned_process)
    if fault is None:
        assert source_review._claim_empty_powerpoint(app, started_at=100) == {"pid": 2, "creation": 100}
        assert app.Caption == "original"
        state.reads = 0
        binding = {}
        office._assert_com_owned(app, 2, "test", binding)
        alien = Application()
        alien.Presentations = app.Presentations
        with pytest.raises(office.SharedPowerPointInstance):
            office._assert_com_owned(alien, 2, "test", binding)
        assert office._cleanup_powerpoint(None, alien, 2, "test", binding) is True
        assert "quit" not in events
        state.reads = 0
    target = tmp_path / "immutable.pptx"
    target.write_bytes(b"immutable")
    request = dict(pptx=str(target), outputDirectory=str(tmp_path), widthPx=16, heightPx=9, pageCount=1,
        jobName="test", rendererVersion="test", activationTimeout=1)
    if fault in {"foreign_window", "duplicate_window", "missing_window", "restore", "nonempty",
                 "before_open_creation", "before_export_window", "before_export_job"}:
        with pytest.raises(ValueError):
            office._render(request)
    else:
        result = office._render(request)
        assert result["hashInvariant"] is True
        assert result["cleanupMethod"] == ("application_quit" if fault is None else "job_termination")
    expected = ["owned-launch"]
    if fault is None or fault.startswith("before_export") or fault.startswith("before_close") or fault.startswith("before_quit"):
        expected.append("open")
    if fault is None or fault in {"before_close_window", "before_quit_window"}:
        expected.append("export")
    if fault is None or fault == "before_quit_window":
        expected.append("close")
    if fault is None:
        expected.append("quit")
    assert events == expected + ["terminate-own-job", "uninit"]
    assert state.foreign_alive is True and target.read_bytes() == b"immutable"
    if fault != "restore":
        assert state.caption == "original"
