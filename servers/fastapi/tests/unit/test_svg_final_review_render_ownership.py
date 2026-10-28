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
            checkerCodeDigest=sha256_hex(Path(office.__file__).read_bytes()), renderIsolation="windows-job-object",
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


@pytest.mark.parametrize("shared,fail", [(False, False), (False, True), (True, False)])
def test_office_opens_only_after_containment_and_quits_only_owned_instance(tmp_path, monkeypatch, shared, fail):
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

    document = SimpleNamespace(Slides=Slides(), Close=lambda: events.append("close"))
    def open_document(*args, **kwargs):
        assert "assign" in events
        events.append("open")
        return document
    app = SimpleNamespace(HWND=1, Version="test", Presentations=SimpleNamespace(Open=open_document),
        Quit=lambda: events.append("quit"))
    app._oleobj_ = SimpleNamespace(
        GetIDsOfNames=lambda _lcid, name: 42,
        Invoke=lambda *_args: app.HWND,
    )
    modules = {
        "pythoncom": SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: events.append("uninit"),
            com_error=OSError, DISPATCH_METHOD=1, DISPATCH_PROPERTYGET=2),
        "win32com": SimpleNamespace(client=SimpleNamespace(GetActiveObject=lambda _: app)),
        "win32com.client": SimpleNamespace(GetActiveObject=lambda _: app),
        "win32api": SimpleNamespace(OpenProcess=lambda *args: handle),
        "win32process": SimpleNamespace(EnumProcesses=lambda: [1],
            GetWindowThreadProcessId=lambda _: (0, 1 if shared else 2)),
        "win32job": SimpleNamespace(JOB_OBJECT_ASSIGN_PROCESS=1, OpenJobObject=lambda *args: handle,
            AssignProcessToJobObject=lambda *args: events.append("assign")),
    }
    for key, module in modules.items():
        monkeypatch.setitem(sys.modules, key, module)

    @contextmanager
    def owned_process(request):
        events.append("assign")
        yield 2

    monkeypatch.setattr(office, "_owned_powerpoint", owned_process)
    monkeypatch.setattr(office, "_available_memory", lambda: office.MINIMUM_AVAILABLE_MEMORY)
    target = tmp_path / "artifact.pptx"
    target.write_bytes(b"pptx")
    request = dict(pptx=str(target), outputDirectory=str(tmp_path), widthPx=16, heightPx=9, pageCount=1,
        jobName="test", rendererVersion="test", activationTimeout=1)
    if shared or fail:
        with pytest.raises(ValueError):
            office._render(request)
    else:
        assert office._render(request)["rendererQuit"] is True
    if shared:
        assert events == ["assign", "uninit"]
    else:
        assert events == ["assign", "open", "close", "quit", "uninit"]
