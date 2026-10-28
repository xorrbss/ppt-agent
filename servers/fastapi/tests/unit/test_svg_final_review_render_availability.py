"""Review render admission serializes Office and bounds the stored projection."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

from PIL import Image
import pytest

from services.svg_final_pptx_review import render_final_review_inputs
from services.svg_final_pptx_review import FINAL_PPTX_OFFICE_BUSY_MAX_WAIT_SECONDS
from services.trusted_executor.wire_bundle_references import _final_pptx_render_references
from services.trusted_executor.worker_dispatcher import TrustedWorkerWireError
from services.workspace_operation_handler_registry import (
    WorkspaceOperationDeferred,
)
from utils.hashing import canonical_json_sha256, sha256_hex
from workers import public_svg_pptx_render as office


def _inputs():
    claim = SimpleNamespace(operation_id=uuid.uuid4(), owner_id=uuid.uuid4(), presentation_id=uuid.uuid4(),
        expected_revision=1, owner_scope="owner")
    payload = b"immutable-pptx"
    inputs = dict(artifactId=str(uuid.uuid4()), artifactDigest="sha256:" + sha256_hex(payload),
        artifactBytes=payload, parentExportOperationId=str(uuid.uuid4()), sourceDigests=["a" * 64],
        ownerId=str(claim.owner_id), tenantId="tenant", presentationId=str(claim.presentation_id),
        sourceRevision=1, widthPx=16, heightPx=9)
    return claim, inputs


@pytest.fixture
def office_host(monkeypatch):
    host = SimpleNamespace(events=[], launches=0, inflight=0, max_inflight=0, block_first=False,
        second_waiting=threading.Event(), mutex=threading.Lock(), wait_count=0, timeouts=[])

    def create_mutex(*args):
        assert args == (None, False, office.RENDER_MUTEX_NAME)
        return SimpleNamespace(Close=lambda: host.events.append("mutex-close"))

    def wait(*args):
        host.wait_count += 1
        if host.wait_count == 2:
            host.second_waiting.set()
        return 0 if host.mutex.acquire(timeout=args[1] / 1000) else 258

    def release(*args):
        host.events.append("unlock")
        host.mutex.release()

    def render(request):
        if host.block_first and host.launches == 1:
            assert host.second_waiting.wait(5), "Second review never reached the render lock"
            assert host.launches == 1
        target = Path(request["pptx"])
        png = Path(request["outputDirectory"]) / "page-000.png"
        Image.new("RGB", (16, 9)).save(png)
        digest = "sha256:" + sha256_hex(target.read_bytes())
        return dict(schemaVersion=office.SCHEMA, status="measured", artifactDigest=digest,
            artifactDigestAfter=digest, hashInvariant=True, openReadOnly=True, openWithWindow=False,
            availablePhysicalMemoryBytes=office.MINIMUM_AVAILABLE_MEMORY, pageCount=1,
            checkerCodeDigest=sha256_hex(Path(office.__file__).read_bytes()),
            renderIsolation="windows-job-object", rendererQuit=True,
            pages=[dict(index=0, pngSha256=sha256_hex(png.read_bytes()), widthPx=16, heightPx=9)])

    class Child:
        def __init__(self, args, **kwargs):
            assert args[1:3] == ["-m", "workers.public_svg_pptx_render"]
            assert kwargs["cwd"] == Path(office.__file__).resolve().parents[1]
            assert all(Path(path).is_absolute() for path in args[3:])
            host.launches += 1
            host.inflight += 1
            host.max_inflight = max(host.max_inflight, host.inflight)
            host.events.append("launch")
            self.args, self.pid, self.returncode = args, host.launches, None
            self.stdin = SimpleNamespace(close=lambda: None)

        def communicate(self, *, input, timeout):
            assert input == b"render\n"
            host.timeouts.append(timeout)
            try:
                office._render_child(*self.args[3:])
                self.returncode = 0
            except Exception:
                self.returncode = 1

        def poll(self):
            return self.returncode

        def kill(self):
            host.events.append("kill")

        def wait(self):
            host.events.append("reap")
            host.inflight -= 1

    monkeypatch.setitem(sys.modules, "win32event", SimpleNamespace(CreateMutex=create_mutex,
        WaitForSingleObject=wait, ReleaseMutex=release, WAIT_OBJECT_0=0, WAIT_ABANDONED=128, WAIT_TIMEOUT=258))
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(
        OpenProcess=lambda *args: SimpleNamespace(Close=lambda: None)))
    monkeypatch.setitem(sys.modules, "win32job", SimpleNamespace(
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=8192, JobObjectExtendedLimitInformation=9,
        CreateJobObject=lambda *args: SimpleNamespace(Close=lambda: host.events.append("job-close")),
        QueryInformationJobObject=lambda *args: {"BasicLimitInformation": {"LimitFlags": 0}},
        SetInformationJobObject=lambda *args: None, AssignProcessToJobObject=lambda *args: None,
        TerminateJobObject=lambda *args: host.events.append("terminate")))
    monkeypatch.setattr(office, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(office, "_render", Mock(side_effect=render))
    monkeypatch.setattr(office.subprocess, "Popen", Child)
    return host


def test_concurrent_final_reviews_wait_for_cleanup_and_both_succeed(office_host):
    office_host.block_first = True
    first_claim, first_inputs = _inputs()
    second_claim, second_inputs = _inputs()

    async def reviews():
        return await asyncio.gather(render_final_review_inputs(first_inputs, first_claim),
            render_final_review_inputs(second_inputs, second_claim))

    results = asyncio.run(reviews())
    assert [result["proof"]["operationId"] for result in results] == [
        str(first_claim.operation_id), str(second_claim.operation_id)]
    assert all(result["proof"]["status"] == "measured" for result in results)
    assert office_host.launches == 2 and office_host.max_inflight == 1
    events = [event for event in office_host.events if event != "mutex-close"]
    assert events == ["launch", "terminate", "job-close", "reap", "unlock"] * 2


def test_shared_instance_survives_child_boundary_as_deferred_review(office_host, monkeypatch):
    monkeypatch.setattr(office, "_render", Mock(side_effect=office.SharedPowerPointInstance()))
    claim, inputs = _inputs()
    with pytest.raises(WorkspaceOperationDeferred) as caught:
        asyncio.run(render_final_review_inputs(inputs, claim))
    assert caught.value.code == office.SHARED_INSTANCE_REASON
    assert caught.value.retry_after_seconds == 60
    assert office_host.launches == 1 and not office_host.mutex.locked()


def test_render_failure_diagnostic_reaches_internal_operation_detail(monkeypatch):
    claim, inputs = _inputs()
    diagnostic = {"stage": "hwnd_pid_match", "exceptionClass": "TypeError"}
    monkeypatch.setattr(office, "render_final_pptx", lambda *args, **kwargs: {
        "schemaVersion": office.SCHEMA,
        "status": "unavailable",
        "reasonCode": "svg_final_pptx_render_failed",
        "retryable": True,
        "diagnostic": diagnostic,
    })
    from services.workspace_operation_handler_registry import RetryableWorkspaceOperationError

    with pytest.raises(RetryableWorkspaceOperationError) as caught:
        asyncio.run(render_final_review_inputs(inputs, claim))
    assert caught.value.code == "svg_final_pptx_render_failed"
    assert caught.value.retryable is True
    assert caught.value.detail == {"diagnostic": diagnostic}
    from services.workspace_operation_runner import sanitize_workspace_event_payload

    assert sanitize_workspace_event_payload({
        "diagnostic": {**diagnostic, "message": "private path C:\\secret\\deck.pptx"},
        "commandLine": "private command",
    }) == {"diagnostic": diagnostic}


def test_shared_instance_busy_past_bound_is_terminal(office_host, monkeypatch):
    monkeypatch.setattr(office, "_render", Mock(side_effect=office.SharedPowerPointInstance()))
    claim, inputs = _inputs()
    claim.progress = {
        "svg_final_pptx_busy_since": (
            datetime.now(timezone.utc)
            - timedelta(seconds=FINAL_PPTX_OFFICE_BUSY_MAX_WAIT_SECONDS + 1)
        ).isoformat(),
    }
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError
    with pytest.raises(NonRetryableWorkspaceOperationError) as caught:
        asyncio.run(render_final_review_inputs(inputs, claim))
    assert caught.value.code == office.SHARED_INSTANCE_REASON
    assert caught.value.retryable is False


def test_render_lock_timeout_defers_without_launching_office(office_host, tmp_path):
    office_host.mutex.acquire()
    try:
        result = office.render_final_pptx(tmp_path / "absent.pptx", tmp_path / "render",
            page_count=1, width_px=16, height_px=9, timeout=0.01)
    finally:
        office_host.mutex.release()
    assert result["reasonCode"] == "svg_final_pptx_render_busy"
    assert result["retryable"] is True
    assert office_host.launches == 0


def test_child_uses_backend_cwd_and_absolute_request_paths(office_host, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("artifact.pptx").write_bytes(b"pptx")
    result = office.render_final_pptx(Path("artifact.pptx"), Path("relative-render"),
        page_count=1, width_px=16, height_px=9, timeout=2)
    assert result["status"] == "measured"
    assert office_host.launches == 1


def test_wait_consumes_render_deadline(office_host, tmp_path, monkeypatch):
    times = iter([10, 11.5])
    monkeypatch.setattr(office, "monotonic", lambda: next(times))
    (tmp_path / "artifact.pptx").write_bytes(b"pptx")
    result = office.render_final_pptx(tmp_path / "artifact.pptx", tmp_path / "render",
        page_count=1, width_px=16, height_px=9, timeout=2)
    assert result["status"] == "measured"
    assert office_host.timeouts == [0.5]


@pytest.mark.parametrize("proof", [None, {}, {"status": "unavailable"}, {"status": "measured", "pages": []}])
def test_export_stdout_cannot_publish_any_final_render_proof(proof):
    stdout = json.dumps(dict(executionEvidence=dict(structuralReadback=dict(finalPptxRender=proof))))
    with pytest.raises(TrustedWorkerWireError, match="trusted_worker_final_pptx_render_not_allowed"):
        _final_pptx_render_references(SimpleNamespace(role="export"),
            output_reference=SimpleNamespace(kind="presentation-pptx"), stdout=stdout)


def test_export_without_final_render_keeps_its_existing_output_contract():
    assert _final_pptx_render_references(SimpleNamespace(role="export"),
        output_reference=SimpleNamespace(kind="presentation-pptx"),
        stdout=json.dumps(dict(executionEvidence=dict(structuralReadback={})))) == ()


def test_even_valid_legacy_export_proof_cannot_publish_png_children():
    from tests.unit.test_trusted_executor_wire_transport import _artifact_reference
    owner_id, operation_id = uuid.uuid4(), uuid.uuid4()
    output = _artifact_reference(owner_id=owner_id, operation_id=operation_id, tenant_id="tenant",
        kind="presentation-pptx",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        payload=b"pptx")
    png = _artifact_reference(owner_id=owner_id, operation_id=operation_id, tenant_id="tenant",
        kind="slide-png", media_type="image/png", payload=b"png")
    proof = dict(schemaVersion=office.SCHEMA, status="measured", artifactDigest="sha256:" + output.sha256,
        artifactDigestAfter="sha256:" + output.sha256, hashInvariant=True, openReadOnly=True, openWithWindow=False,
        operationId=str(operation_id), ownerId=str(owner_id), tenantId="tenant", pageCount=1,
        pages=[dict(index=0, pngSha256=png.sha256, artifactRef=png.to_wire())])
    proof["evidenceDigest"] = "sha256:" + canonical_json_sha256(proof)
    job = SimpleNamespace(role="export", operation_id=operation_id, input_artifact=output)
    with pytest.raises(TrustedWorkerWireError, match="trusted_worker_final_pptx_render_not_allowed"):
        _final_pptx_render_references(job, output_reference=output,
            stdout=json.dumps(dict(executionEvidence=dict(structuralReadback=dict(finalPptxRender=proof)))))


def test_final_review_writer_keeps_only_latest_proof_and_source_review_preserves_it(monkeypatch):
    from services import svg_final_pptx_review as final
    from tests.unit.test_svg_review_pages import _review_execution
    claim, inputs = _inputs()
    module, factory, context, presentation, _, _ = _review_execution(monkeypatch)
    context.claim.request_payload["context"].update(target="final_pptx", artifact_id=inputs["artifactId"])
    inputs.update(proof={"operationId": str(claim.operation_id)}, pngs=[b"png"])
    load = AsyncMock(side_effect=lambda *args, **kwargs: inputs)
    monkeypatch.setattr(final, "load_final_review_inputs", load)
    monkeypatch.setattr(final, "render_final_review_inputs", AsyncMock(return_value=inputs))
    monkeypatch.setattr(final, "persist_final_review_render", AsyncMock())
    monkeypatch.setattr(final, "seal_final_review", lambda *args: {"latest": inputs["artifactDigest"]})
    monkeypatch.setattr("services.svg_export_operation.require_current_header_revision_authority",
        AsyncMock(return_value=None))
    presentation.theme = {"unrelated": "preserved", "svg": {"finalPptxReviews": {
        f"{index:064x}": {"largeHistoricalProof": {"pages": ["artifactRef"] * 100}} for index in range(100)}}}
    for artifact in ("a", "a", "b"):
        inputs["artifactDigest"] = "sha256:" + artifact * 64
        asyncio.run(module.execute_svg_quality_review(context, factory))
        assert presentation.theme["svg"]["finalPptxReviews"] == {
            artifact * 64: {"latest": inputs["artifactDigest"]}}
        assert presentation.theme["unrelated"] == "preserved"
    context.claim.request_payload["context"].update(target="source_svg", artifact_id=None)
    asyncio.run(module.execute_svg_quality_review(context, factory))
    assert presentation.theme["svg"]["finalPptxReviews"] == {"b" * 64: {"latest": "sha256:" + "b" * 64}}
