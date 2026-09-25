from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import time
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import pypdfium2 as pdfium
from PIL import Image
from pptx import Presentation

from services.isolated_worker_contracts import (
    ARTIFACT_REF_SCHEMA,
    ArtifactRef,
    CapabilityReplayGuard,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
    issue_worker_capability,
    verify_and_consume_worker_capability,
)
from services.isolated_worker_process import (
    IsolatedWorkerProcessError,
    NETWORK_NONE_ATTESTATION,
    execute_isolated_worker_job,
    install_trusted_worker_dispatcher,
    worker_environment,
)
from services.trusted_executor.composition import (
    create_trusted_worker_executor_runtime,
)
from services.trusted_executor.contracts import (
    ExecutionIdentity,
    ResourceLimits,
    SandboxLaunchSpec,
    SandboxPolicy,
)
from workers import public_worker_runtime
from workers.public_export_worker import (
    EXPORT_BUNDLE_SCHEMA,
    EXPORT_CHECKPOINT_BUNDLE_SCHEMA,
    execute_export,
)
from workers.public_worker_runtime import LocalContentAddressedStore


def _artifact(
    *,
    operation_id: uuid.UUID,
    payload: bytes,
    storage_key: str,
    kind: str,
    media_type: str,
) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=uuid.UUID("10000000-0000-4000-8000-000000000001"),
        tenant_id="tenant-a",
        kind=kind,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type=media_type,
        storage_key=storage_key,
        created_by_operation_id=operation_id,
        retention_class="staged",
    )


def _store_payload(root: Path, reference: ArtifactRef, payload: bytes) -> None:
    path = root / reference.storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_capability_is_attempt_digest_scoped_and_one_shot() -> None:
    now = int(time.time())
    operation_id = uuid.uuid4()
    payload = b"sealed input"
    artifact = _artifact(
        operation_id=operation_id,
        payload=payload,
        storage_key="tenant/owner/aa/input",
        kind="render-html",
        media_type="text/html",
    )
    job = IsolatedWorkerJob(
        role="render",
        operation_id=operation_id,
        attempt_token="attempt-token-1234567890",
        fencing_token=3,
        input_digest=artifact.sha256,
        input_artifact=artifact,
        capability="pending",
        deadline_epoch_seconds=now + 300,
    )
    signing_key = b"k" * 32
    job = replace(
        job,
        capability=issue_worker_capability(
            job,
            signing_key=signing_key,
            ttl_seconds=120,
            now=now,
            nonce="a" * 32,
        ),
    )
    parsed = IsolatedWorkerJob.from_wire(job.to_wire())
    guard = CapabilityReplayGuard()

    claims = verify_and_consume_worker_capability(
        parsed,
        signing_key=signing_key,
        replay_guard=guard,
        now=now,
    )
    assert claims.operation_id == operation_id
    assert claims.input_digest == artifact.sha256
    with pytest.raises(IsolatedWorkerContractError, match="already consumed"):
        verify_and_consume_worker_capability(
            parsed,
            signing_key=signing_key,
            replay_guard=guard,
            now=now,
        )


def test_worker_environment_is_allowlisted_and_requires_network_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil
    import subprocess

    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell 7 is required for the launcher limit contract")
    launcher = Path(__file__).resolve().parents[4] / "scripts" / "start-public-v2-rc.ps1"
    defaults = {
        "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES": "1073741824",
        "PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS": "900",
        "PPT_WORKER_OUTPUT_LIMIT_BYTES": "536870912",
    }
    custom = dict(zip(defaults, ("8192", "0750", " 4096 "), strict=True))
    cases = [{}, custom, *[
        {key: value} for key in defaults for value in ("", " ", "invalid", "0", "-1", "1.5")
    ]]
    # Evaluate only the pure function's AST, never the launcher's side effects.
    probe = r"""
$ErrorActionPreference = 'Stop'
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json -AsHashtable
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($payload.launcher, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'launcher parse failed' }
$definition = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Resolve-WorkerRuntimeLimits' }, $true)
if ($null -eq $definition) { throw 'limit helper missing' }
. ([scriptblock]::Create($definition.Extent.Text))
$results = @(foreach ($source in $payload.cases) {
    try { @{ ok = $true; limits = (Resolve-WorkerRuntimeLimits $source) } }
    catch { @{ ok = $false } }
})
ConvertTo-Json -InputObject $results -Depth 5 -Compress
"""
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", probe],
        input=json.dumps({"launcher": str(launcher), "cases": cases}),
        capture_output=True, text=True, timeout=20, check=True,
    )
    limit_results = json.loads(completed.stdout)
    assert limit_results[0] == {"ok": True, "limits": defaults}
    assert limit_results[1] == {"ok": True, "limits": custom}
    assert all(result == {"ok": False} for result in limit_results[2:])
    launcher_source = launcher.read_text(encoding="utf-8")
    assert launcher_source.index("$workerRuntimeLimits = Resolve-WorkerRuntimeLimits") < launcher_source.index("$runId =")
    for key in defaults:
        assert f'"{key}" = $workerRuntimeLimits["{key}"]' in launcher_source
    assert (
        '$syncEnvironment["PRESENTON_PROVIDER_CONFIG_IMPORT_PATH"] = '
        "$ProviderConfigPath"
    ) in launcher_source
    assert '$syncEnvironment["USER_CONFIG_PATH"] = $ProviderConfigPath' not in launcher_source

    source = {
        "PATH": "C:/runtime",
        "PUBLIC_WORKER_OS_ISOLATION": NETWORK_NONE_ATTESTATION,
        "PUBLIC_WORKER_CAPABILITY_KEY_FILE": "C:/run/secrets/capability.key",
        "PUBLIC_WORKER_OBJECT_STORE_ROOT": "C:/object-store",
        "OPENAI_API_KEY": "must-not-cross",
        "DATABASE_URL": "must-not-cross",
        "COOKIE": "must-not-cross",
    }
    environment = worker_environment("export", source)
    assert environment["PUBLIC_WORKER_SESSIONLESS"] == "1"
    assert environment["PUBLIC_WORKER_ROLE"] == "export"
    assert "OPENAI_API_KEY" not in environment
    assert "DATABASE_URL" not in environment
    assert "COOKIE" not in environment

    render_environment = worker_environment("render", source)
    assert render_environment["AUTHORED_RENDERER_WORKER"] == "1"
    assert "OPENAI_API_KEY" not in render_environment

    operation_id = uuid.uuid4()
    payload = b"bounded worker input"
    reference = _artifact(
        operation_id=operation_id,
        payload=payload,
        storage_key="tenant/owner/input.html",
        kind="slide-html",
        media_type="text/html",
    )
    input_root, output_root = tmp_path / "input", tmp_path / "output"
    _store_payload(input_root, reference, payload)
    signing_key = b"k" * 32
    key_file = tmp_path / "capability.key"
    key_file.write_bytes(signing_key)
    policy_environment = {
        "PPT_WORKER_NETWORK_MODE": "none",
        "PPT_WORKER_ROOT_FILESYSTEM": "read-only",
        "PPT_WORKER_SCRATCH": "tmpfs",
        "PPT_WORKER_RUN_AS_NON_ROOT": "true",
        "PPT_WORKER_SECCOMP_PROFILE": "default",
        "PPT_WORKER_REPLICAS": "1",
        "PPT_WORKER_MEMORY_LIMIT_BYTES": "536870912",
        "PPT_WORKER_CPU_MILLIS": "600000",
        "PPT_WORKER_PID_LIMIT": "64",
        "PPT_WORKER_OPEN_FILE_LIMIT": "512",
        "PPT_WORKER_WALL_CLOCK_SECONDS": "600",
        **limit_results[0]["limits"],
    }
    spec = SandboxLaunchSpec(
        identity=ExecutionIdentity(
            operation_id=operation_id,
            tenant_id=reference.tenant_id,
            owner_id=reference.owner_id,
            presentation_id=uuid.uuid4(),
        ),
        role="render",
        executable=Path(sys.executable),
        arguments=("-m", "workers.public_render_worker"),
        limits=ResourceLimits(
            memory_bytes=536870912, process_limit=64, deadline_seconds=600,
        ),
        policy=SandboxPolicy(),
    )
    executions: list[uuid.UUID] = []

    async def execute(job, store):
        executions.append(job.operation_id)
        assert store.read(job.input_artifact) == payload
        return store.put(
            b"bounded output",
            source=job.input_artifact,
            operation_id=job.operation_id,
            kind="slide-png",
            media_type="image/png",
        )

    def invoke(environment):
        job = IsolatedWorkerJob(
            role="render", operation_id=operation_id,
            attempt_token="attempt-token-1234567890", fencing_token=3,
            input_digest=reference.sha256, input_artifact=reference,
            capability="pending", deadline_epoch_seconds=int(time.time()) + 300,
        )
        job = replace(job, capability=issue_worker_capability(
            job, signing_key=signing_key, ttl_seconds=120,
        ))
        stdin = io.BytesIO(json.dumps(job.to_wire()).encode("utf-8"))
        stdout = io.BytesIO()
        with monkeypatch.context() as patch:
            patch.setattr(os, "environ", dict(environment))
            patch.setattr(public_worker_runtime, "sys", SimpleNamespace(
                stdin=SimpleNamespace(buffer=stdin),
                stdout=SimpleNamespace(buffer=stdout),
            ))
            patch.setattr(public_worker_runtime, "_REPLAY_GUARD", CapabilityReplayGuard())
            exit_code = asyncio.run(public_worker_runtime.run_worker("render", execute))
        return exit_code, stdin.tell(), stdout.getvalue()

    for local_development in (True, False):
        incoming = {
            **source, **policy_environment,
            **limit_results[0 if local_development else 1]["limits"],
            "PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(key_file),
            "PUBLIC_WORKER_INPUT_STORE_ROOT": str(input_root),
            "PUBLIC_WORKER_OUTPUT_STORE_ROOT": str(output_root),
            "SESSION": "must-not-cross",
            "PUBLIC_WORKER_SESSION_COOKIE": "must-not-cross",
            "PPT_WORKER_UNRECOGNIZED": "must-not-cross",
        }
        if local_development:
            incoming["PUBLIC_TRUSTED_WORKER_LOCAL_POLICY"] = "development"
        child_environment = spec.environment(worker_environment(
            "render", incoming, trusted_dispatch=local_development,
        ))
        assert child_environment["TRUSTED_EXECUTOR_SESSIONLESS"] == "1"
        assert all(child_environment[key] == incoming[key] for key in policy_environment)
        for forbidden in (
            "SESSION", "PUBLIC_WORKER_SESSION_COOKIE", "OPENAI_API_KEY",
            "DATABASE_URL", "COOKIE", "PPT_WORKER_UNRECOGNIZED",
        ):
            assert forbidden not in child_environment
        exit_code, consumed_bytes, stdout = invoke(child_environment)
        assert exit_code == 0 and consumed_bytes > 0
        result = json.loads(stdout)
        assert result["ok"] is True and result["errorCode"] is None
        assert result["operationId"] == str(operation_id)
        assert result["fencingToken"] == 3 and result["inputDigest"] == reference.sha256
        output = ArtifactRef.from_wire(result["outputArtifact"])
        assert output.created_by_operation_id == operation_id
        assert output.owner_id == reference.owner_id and output.tenant_id == reference.tenant_id
        assert (output_root / output.storage_key).read_bytes() == b"bounded output"

        for name, value in (
            ("TRUSTED_EXECUTOR_SESSIONLESS", "0"),
            ("TRUSTED_EXECUTOR_SESSIONLESS", "true"),
            ("TRUSTED_EXECUTOR_SESSIONLESS", " 1"),
            ("TRUSTED_EXECUTOR_SESSIONLESS_COOKIE", "1"),
            ("SESSION", "forbidden"),
            ("OPENAI_API_KEY", "forbidden"),
        ):
            execution_count = len(executions)
            exit_code, consumed_bytes, stdout = invoke({**child_environment, name: value})
            assert (exit_code, consumed_bytes, stdout) == (1, 0, b"")
            assert len(executions) == execution_count
    assert executions == [operation_id, operation_id]


@pytest.fixture
def worker_cache_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """The transport cache budget the launcher supplies to a real worker."""

    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", str(64 * 1024 * 1024))
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", "900")


def _hybrid_export_fixture(tmp_path: Path) -> tuple[IsolatedWorkerJob, ArtifactRef]:
    """One editable-hybrid bundle naming a raster checkpoint and its source."""

    operation_id = uuid.uuid4()
    image = Image.new("RGB", (1280, 720), "#1265E3")
    png_stream = io.BytesIO()
    image.save(png_stream, format="PNG")
    image.close()
    png = png_stream.getvalue()
    slide = _artifact(
        operation_id=operation_id,
        payload=png,
        storage_key="tenant/owner/slide.png",
        kind="slide-png",
        media_type="image/png",
    )
    _store_payload(tmp_path, slide, png)
    html = b"<html><body><h1>Slide</h1></body></html>"
    source = _artifact(
        operation_id=operation_id,
        payload=html,
        storage_key="tenant/owner/slide.html",
        kind="slide-html",
        media_type="text/html",
    )
    _store_payload(tmp_path, source, html)
    bundle_payload = json.dumps(
        {
            "schemaVersion": EXPORT_CHECKPOINT_BUNDLE_SCHEMA,
            "canonicalExportIdentity": hashlib.sha256(b"identity").hexdigest(),
            "exportFormat": "pptx",
            "exportMode": "editable-hybrid",
            "renderAdapter": "public-isolated-hybrid",
            "fontEmbedding": True,
            "slides": [
                {
                    "slideIndex": 0,
                    "artifactRef": slide.to_wire(),
                    "validationDigest": hashlib.sha256(b"validation").hexdigest(),
                }
            ],
            "sourceSlides": [{"slideIndex": 0, "artifactRef": source.to_wire()}],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bundle = _artifact(
        operation_id=operation_id,
        payload=bundle_payload,
        storage_key="tenant/owner/hybrid-bundle.json",
        kind="export-bundle",
        media_type="application/vnd.presenton.export-bundle+json",
    )
    _store_payload(tmp_path, bundle, bundle_payload)
    return (
        IsolatedWorkerJob(
            role="export",
            operation_id=operation_id,
            attempt_token="attempt-token-1234567890",
            fencing_token=1,
            input_digest=bundle.sha256,
            input_artifact=bundle,
            capability="validated-by-runtime",
            deadline_epoch_seconds=int(time.time()) + 300,
        ),
        bundle,
    )


def test_export_worker_keeps_the_fidelity_deck_when_hybrid_assembly_fails(
    tmp_path: Path,
    worker_cache_limits: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed assembly must cost the reason, never the user's export."""

    from workers import public_export_worker

    job, bundle = _hybrid_export_fixture(tmp_path)
    # An assembler command that exits non-zero with a stable reason code.
    monkeypatch.setenv(
        "PUBLIC_EXPORT_HYBRID_COMMAND_JSON",
        json.dumps(
            [
                sys.executable,
                "-c",
                "import sys;sys.stdin.read();"
                'print(\'{"schemaVersion":"public-hybrid-export-result/v1",'
                '"ok":false,"errorCode":"hybrid_chrome_unavailable"}\');'
                "sys.exit(1)",
            ]
        ),
    )
    store = LocalContentAddressedStore(tmp_path)

    output = asyncio.run(public_export_worker.execute_export(job, store))

    assert output.artifact.kind == "presentation-pptx"
    assert output.evidence["requestedExportMode"] == "editable-hybrid"
    assert output.evidence["deliveredExportMode"] == "fidelity-raster"
    assert output.evidence["fallbackReason"] == "hybrid_chrome_unavailable"
    with zipfile.ZipFile(io.BytesIO(store.read(output.artifact))) as archive:
        assert "ppt/slides/slide1.xml" in archive.namelist()
    assert bundle.kind == "export-bundle"


def test_export_worker_publishes_the_assembled_hybrid_deck(
    tmp_path: Path,
    worker_cache_limits: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful assembly replaces the raster deck and reports its evidence."""

    from workers import public_export_worker

    job, _bundle = _hybrid_export_fixture(tmp_path)
    monkeypatch.setenv(
        "PUBLIC_EXPORT_HYBRID_COMMAND_JSON",
        json.dumps(
            [
                sys.executable,
                "-c",
                "import json,sys;job=json.loads(sys.stdin.read());"
                "open(job['outputPath'],'wb').write(b'assembled-hybrid-package');"
                "print(json.dumps({'schemaVersion':'public-hybrid-export-result/v1',"
                "'ok':True,'errorCode':None,"
                "'structural':{'slideCount':1,'nativeShapeElements':4}}))",
            ]
        ),
    )
    store = LocalContentAddressedStore(tmp_path)

    output = asyncio.run(public_export_worker.execute_export(job, store))

    assert output.evidence["deliveredExportMode"] == "editable-hybrid"
    assert output.evidence["fallbackReason"] is None
    assert output.evidence["structural"] == {"slideCount": 1, "nativeShapeElements": 4}
    assert store.read(output.artifact) == b"assembled-hybrid-package"


@pytest.mark.parametrize("export_format", ["pptx", "pdf"])
def test_export_worker_builds_portrait_artifacts_with_exact_canvas(
    tmp_path: Path,
    worker_cache_limits: None,
    export_format: str,
) -> None:
    operation_id = uuid.uuid4()
    image = Image.new("RGB", (720, 1280), "#1265E3")
    png_stream = io.BytesIO()
    image.save(png_stream, format="PNG")
    image.close()
    png = png_stream.getvalue()
    slide = _artifact(
        operation_id=operation_id,
        payload=png,
        storage_key="tenant/owner/slide.png",
        kind="slide-png",
        media_type="image/png",
    )
    _store_payload(tmp_path, slide, png)
    bundle_payload = json.dumps(
        {
            "schemaVersion": EXPORT_CHECKPOINT_BUNDLE_SCHEMA,
            "canonicalExportIdentity": hashlib.sha256(b"identity").hexdigest(),
            "exportFormat": export_format,
            "exportMode": "fidelity-raster",
            "renderAdapter": "chromium-raster-v2",
            "canvas": {
                "contractVersion": "authored-canvas/v1",
                "aspectRatio": "9:16",
                "widthPx": 720,
                "heightPx": 1280,
                "locale": "ko-KR",
                "safeArea": {"x": 64, "y": 64, "width": 592, "height": 1152},
            },
            "slides": [
                {
                    "slideIndex": 0,
                    "artifactRef": slide.to_wire(),
                    "validationDigest": hashlib.sha256(b"validation").hexdigest(),
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bundle = _artifact(
        operation_id=operation_id,
        payload=bundle_payload,
        storage_key="tenant/owner/export-bundle.json",
        kind="export-bundle",
        media_type="application/vnd.presenton.export-bundle+json",
    )
    _store_payload(tmp_path, bundle, bundle_payload)
    job = IsolatedWorkerJob(
        role="export",
        operation_id=operation_id,
        attempt_token="attempt-token-1234567890",
        fencing_token=1,
        input_digest=bundle.sha256,
        input_artifact=bundle,
        capability="validated-by-runtime",
        deadline_epoch_seconds=int(time.time()) + 300,
    )
    store = LocalContentAddressedStore(tmp_path)

    output = asyncio.run(execute_export(job, store)).artifact

    assert output.kind == f"presentation-{export_format}"
    assert output.owner_id == bundle.owner_id
    assert output.created_by_operation_id == operation_id
    payload = store.read(output)
    assert hashlib.sha256(payload).hexdigest() == output.sha256
    if export_format == "pptx":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert "ppt/presentation.xml" in archive.namelist()
        deck = Presentation(io.BytesIO(payload))
        assert (deck.slide_width, deck.slide_height) == (6_858_000, 12_192_000)
    else:
        with pdfium.PdfDocument(store.verified_path(output)) as document:
            assert len(document) == 1
            page = document[0]
            assert page.get_size() == pytest.approx((540, 960), abs=0.01)
            rendered = page.render(scale=1).to_pil().convert("RGB")
            try:
                assert rendered.size == (540, 960)
                for point in ((5, 5), (270, 480), (534, 954)):
                    assert rendered.getpixel(point) == pytest.approx(
                        (18, 101, 227), abs=2
                    )
            finally:
                rendered.close()
                page.close()


def test_render_worker_forwards_portrait_canvas_and_checks_png_size(
    tmp_path: Path,
    worker_cache_limits: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workers import public_render_worker
    from utils import slide_capture

    operation_id = uuid.uuid4()
    html = b"<html><body>portrait</body></html>"
    source = _artifact(
        operation_id=operation_id,
        payload=html,
        storage_key="tenant/owner/portrait.html",
        kind="slide-html",
        media_type="text/html",
    )
    _store_payload(tmp_path, source, html)
    canvas = {
        "contractVersion": "authored-canvas/v1",
        "aspectRatio": "9:16",
        "widthPx": 720,
        "heightPx": 1280,
        "locale": "ko-KR",
        "safeArea": {"x": 64, "y": 64, "width": 592, "height": 1152},
    }
    bundle_payload = json.dumps(
        {
            "schemaVersion": "public-render-bundle/v2",
            "canvas": canvas,
            "slides": [{"slideIndex": 0, "artifactRef": source.to_wire()}],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bundle = _artifact(
        operation_id=operation_id,
        payload=bundle_payload,
        storage_key="tenant/owner/portrait-render-bundle.json",
        kind="export-bundle",
        media_type="application/vnd.presenton.export-bundle+json",
    )
    _store_payload(tmp_path, bundle, bundle_payload)
    job = IsolatedWorkerJob(
        role="render",
        operation_id=operation_id,
        attempt_token="attempt-token-1234567890",
        fencing_token=1,
        input_digest=bundle.sha256,
        input_artifact=bundle,
        capability="validated-by-runtime",
        deadline_epoch_seconds=int(time.time()) + 300,
    )
    captured: dict[str, int] = {}

    def render(admitted, timeout, *, width_px=1280, height_px=720):
        assert len(admitted) == 1 and timeout == 120
        captured.update(width=width_px, height=height_px)
        image = Image.new("RGB", (width_px, height_px), "#1265E3")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        image.close()
        return [(stream.getvalue(), {})]

    monkeypatch.setattr(public_render_worker, "_admit_html", lambda value: value)
    monkeypatch.setattr(slide_capture, "_render_admitted_htmls_over_pipe", render)
    store = LocalContentAddressedStore(tmp_path)

    result = asyncio.run(public_render_worker.execute_render(job, store))

    assert captured == {"width": 720, "height": 1280}
    manifest = json.loads(store.read(result))
    output = ArtifactRef.from_wire(manifest["slides"][0]["artifactRef"])
    with Image.open(store.verified_path(output)) as image:
        assert image.size == (720, 1280)


class _StagedArtifactAuthority:
    """Filesystem byte authority standing in for the product object store.

    The trusted transport takes its bytes from an injected streaming
    authority (in production `ArtifactObjectStoreWireAdapter` over the durable
    object store).  Here the object store is the directory the fixture already
    wrote, so staging is a copy and publishing keeps the bytes addressable.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @staticmethod
    def _copy(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    async def stage(self, *, reference: ArtifactRef, destination: Path) -> None:
        await asyncio.to_thread(
            self._copy, self._root / reference.storage_key, destination
        )

    async def publish(self, *, reference: ArtifactRef, source: Path) -> None:
        await asyncio.to_thread(
            self._copy, source, self._root / "published" / reference.storage_key
        )


@pytest.fixture
def trusted_worker_runtime(tmp_path: Path):
    """Compose the real trusted-worker runtime and install it for one test.

    `execute_isolated_worker_job` fails closed without a dispatcher, so the
    supervision contract can only be exercised through a composed runtime.
    The Windows-local backend is the development composition that supervises
    with a Job Object; the dispatcher is uninstalled again on teardown so no
    other test inherits a process-launching wire port.
    """

    runtime = create_trusted_worker_executor_runtime(
        staging_root=tmp_path / "trusted-staging",
        node_id="isolated-public-worker-test",
        artifact_authority=_StagedArtifactAuthority(tmp_path),
        environment={
            "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "windows-local",
            "PUBLIC_TRUSTED_WORKER_LOCAL_POLICY": "development",
            "ENVIRONMENT": "test",
        },
    )
    install_trusted_worker_dispatcher(runtime.dispatcher)
    try:
        yield runtime
    finally:
        install_trusted_worker_dispatcher(None)
        asyncio.run(runtime.close())


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="the windows-local trusted backend supervises with a Job Object",
)
def test_export_subprocess_limit_kills_descendants_and_reports_recycle(
    tmp_path: Path,
    trusted_worker_runtime: object,
) -> None:
    launcher = (
        Path(__file__).resolve().parents[4] / "scripts" / "start-authored-vnext-dev.ps1"
    ).read_text(encoding="utf-8")
    for variable, value in {
        "PPT_WORKER_MEMORY_LIMIT_BYTES": "1073741824",
        "PPT_WORKER_CPU_MILLIS": "600000",
        "PPT_WORKER_PID_LIMIT": "64",
        "PPT_WORKER_OPEN_FILE_LIMIT": "512",
        "PPT_WORKER_OUTPUT_LIMIT_BYTES": "536870912",
        "PPT_WORKER_WALL_CLOCK_SECONDS": "600",
    }.items():
        assert f'"{variable}" = "{value}"' in launcher
    operation_id = uuid.uuid4()
    image = Image.new("RGB", (320, 180), "#1265E3")
    png_stream = io.BytesIO()
    image.save(png_stream, format="PNG")
    image.close()
    png = png_stream.getvalue()
    slide = _artifact(
        operation_id=operation_id,
        payload=png,
        storage_key="tenant/owner/resource-slide.png",
        kind="slide-png",
        media_type="image/png",
    )
    _store_payload(tmp_path, slide, png)
    bundle_payload = json.dumps(
        {
            "schemaVersion": EXPORT_BUNDLE_SCHEMA,
            "exportFormat": "pptx",
            "slides": [slide.to_wire()],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    bundle = _artifact(
        operation_id=operation_id,
        payload=bundle_payload,
        storage_key="tenant/owner/resource-bundle.json",
        kind="export-bundle",
        media_type="application/vnd.presenton.export-bundle+json",
    )
    _store_payload(tmp_path, bundle, bundle_payload)
    key_path = tmp_path / "capability.key"
    key_path.write_bytes(b"r" * 32)
    now = int(time.time())
    # A short immutable deadline is the limit this backend can actually
    # enforce, and it is what terminates a child that never exits.
    deadline_seconds = 5
    job = IsolatedWorkerJob(
        role="export",
        operation_id=operation_id,
        attempt_token="resource-attempt-1234567890",
        fencing_token=1,
        input_digest=bundle.sha256,
        input_artifact=bundle,
        capability="pending",
        deadline_epoch_seconds=now + deadline_seconds,
    )
    job = replace(
        job,
        capability=issue_worker_capability(
            job,
            signing_key=key_path.read_bytes(),
            ttl_seconds=deadline_seconds,
            now=now,
            nonce="b" * 32,
        ),
    )
    environment = {
        **os.environ,
        "PUBLIC_WORKER_OS_ISOLATION": NETWORK_NONE_ATTESTATION,
        "PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(key_path),
        "PUBLIC_WORKER_OBJECT_STORE_ROOT": str(tmp_path),
        "PPT_WORKER_MEMORY_LIMIT_BYTES": str(1024 * 1024 * 1024),
        "PPT_WORKER_CPU_MILLIS": "60000",
        "PPT_WORKER_PID_LIMIT": "16",
        "PPT_WORKER_OPEN_FILE_LIMIT": "8",
        "PPT_WORKER_OUTPUT_LIMIT_BYTES": str(10 * 1024 * 1024),
        "PPT_WORKER_WALL_CLOCK_SECONDS": "60",
    }

    # A descendant that keeps writing for far longer than the job deadline, so
    # its own file is direct evidence of whether the Job Object recycled it.
    heartbeat = tmp_path / "descendant-heartbeat"
    descendant_script = tmp_path / "descendant.py"
    descendant_script.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "marker = Path(sys.argv[1])\n"
        "for _ in range(10000):\n"
        "    with marker.open('ab') as stream:\n"
        "        stream.write(b'.')\n"
        "    time.sleep(0.02)\n",
        encoding="utf-8",
    )
    child_script = tmp_path / "child.py"
    child_script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
        "handles = [open(sys.executable, 'rb') for _ in range(32)]\n"
        "sys.stdin.buffer.read()\n"
        "time.sleep(600)\n",
        encoding="utf-8",
    )
    with pytest.raises(IsolatedWorkerProcessError) as raised:
        asyncio.run(
            execute_isolated_worker_job(
                job,
                presentation_id=uuid.uuid4(),
                source_environment=environment,
                command=[
                    sys.executable,
                    str(child_script),
                    str(descendant_script),
                    str(heartbeat),
                ],
            )
        )

    from services.public_export_operation import _resource_summary

    # The enforcing limit is the Job Object's immutable deadline. The
    # userspace handle/pid sampler that used to raise
    # `public_worker_resource_limit_exceeded` lived in the deleted
    # `services/public_worker_resource_boundary.py`, alongside the direct
    # subprocess path; the supervisor now reports the kernel's own reason.
    assert raised.value.code == "deadline_exceeded"
    metrics = raised.value.resource_metrics
    assert metrics["resourceLimitsEnforced"] is True
    assert metrics["enforcementLayer"] == "windows-job-object"
    assert metrics["deadlineExceeded"] is True

    # Unchanged intent, proved by the descendant itself rather than by a count:
    # it ran (so the Job really did own a grandchild), and it stopped writing
    # the moment the Job was recycled.
    assert heartbeat.exists(), "the child never started its descendant"
    beats = heartbeat.stat().st_size
    assert beats > 0
    time.sleep(0.5)
    assert heartbeat.stat().st_size == beats, "the descendant outlived its Job"

    # Job accounting agrees: nothing is left to recycle.
    assert metrics["peakProcessCount"] >= 2
    assert metrics["finalProcessCount"] == 0
    assert metrics["recycledWithinBaseline"] is True

    # The durable projection the workspace runner persists keeps the names the
    # operation timeline has always carried.
    summary = _resource_summary([{**metrics, "role": job.role}])
    assert summary["worker_peak_process_count"] >= 2
    assert summary["worker_recycle_failure_count"] == 0
    assert summary["resource_limits_enforced"] is True
    assert summary["worker_enforcement_layer"] == "windows-job-object"
    assert summary["worker_resource_samples"][0]["finalProcessCount"] == 0
    # A Job Object meters processes, memory and CPU, never open handles, so on
    # this backend the durable handle name exists and can only project zero.
    assert summary["worker_peak_handle_count"] == 0


def test_artifact_ref_rejects_paths_and_session_fields() -> None:
    operation_id = uuid.uuid4()
    payload = b"x"
    base = _artifact(
        operation_id=operation_id,
        payload=payload,
        storage_key="tenant/owner/object",
        kind="slide-png",
        media_type="image/png",
    ).to_wire()
    assert base["schemaVersion"] == ARTIFACT_REF_SCHEMA
    with pytest.raises(IsolatedWorkerContractError, match="relative"):
        ArtifactRef.from_wire({**base, "storageKey": "C:/private/input.png"})
    with pytest.raises(IsolatedWorkerContractError, match="credential"):
        ArtifactRef.from_wire({**base, "cookie": "session=secret"})


def test_next_export_route_admits_hybrid_without_executing_it() -> None:
    """The route still only admits; the isolated worker is the only executor.

    An editable-hybrid public export now exists
    (`public-export.hybrid-in-isolated-lane`), so this pins both halves of that
    decision: the browser-facing route hands the requested mode to the durable
    command and runs no exporter of its own, and the process that does run
    Chrome plus the OOXML assembler is the isolated export worker.
    """

    nextjs = Path(__file__).resolve().parents[3] / "nextjs"
    route = (
        nextjs / "app" / "api" / "export-presentation" / "route.ts"
    ).read_text(encoding="utf-8")
    # Unchanged intent: the route performs no export work in the Next process.
    assert "/api/v1/ppt/workspace/presentations/" in route
    assert "runBundledPresentationExport" not in route
    assert "runAuthoredHybridPresentationExport" not in route
    assert "child_process" not in route
    assert "export_task.json" not in route
    # New intent: the requested output mode reaches the durable command, so a
    # hybrid request is admitted rather than silently downgraded at the edge.
    assert "context: admission.context" in route
    admission = (nextjs / "lib" / "public-export-admission.ts").read_text(
        encoding="utf-8"
    )
    assert '"hybrid"' in admission and "font_embedding" in admission

    # The assembler child is launched from the isolated worker package only.
    hybrid = (
        Path(__file__).resolve().parents[2] / "workers" / "public_export_hybrid.py"
    ).read_text(encoding="utf-8")
    assert "create_subprocess_exec" in hybrid
    for durable in (
        "services/workspace_quality_export_operation_handlers.py",
        "services/public_export_operation.py",
        "services/workspace_service.py",
    ):
        source = (Path(__file__).resolve().parents[2] / durable).read_text(
            encoding="utf-8"
        )
        assert "create_subprocess_exec" not in source
        assert "from workers.public_export_hybrid import" not in source


def test_export_worker_rejects_a_hybrid_bundle_without_authored_sources() -> None:
    """Hybrid must name the sources it measures; a bare mode flip is refused."""

    from workers import public_export_worker

    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    reference = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant",
        kind="export-bundle",
        sha256="a" * 64,
        size_bytes=32,
        media_type="application/vnd.presenton.export-bundle+json",
        storage_key="v1/bundle",
        created_by_operation_id=operation_id,
        retention_class="ephemeral",
    )
    job = SimpleNamespace(input_artifact=reference, operation_id=operation_id)

    def store_for(payload):
        class Store:
            def read(self, _reference):
                return json.dumps(payload).encode("utf-8")

        return Store()

    base = {
        "schemaVersion": "public-export-bundle/v2",
        "canonicalExportIdentity": "b" * 64,
        "exportFormat": "pptx",
        "exportMode": "editable-hybrid",
        "renderAdapter": "public-isolated-hybrid",
        "slides": [],
    }
    for payload, expected in (
        (base, "public_export_bundle_identity_invalid"),
        ({**base, "fontEmbedding": True}, "public_export_bundle_invalid_shape"),
    ):
        with pytest.raises(public_export_worker.PublicWorkerExecutionError) as raised:
            public_export_worker._read_bundle(job, store_for(payload))
        assert raised.value.code == expected


def test_hybrid_assembler_budget_reserves_time_to_assemble_and_store(
    tmp_path: Path,
) -> None:
    """The assembler must stop before the worker's own deadline.

    An assembler allowed to spend the entire job budget leaves nothing for
    OOXML assembly and the CAS write, and the worker expires with no file at
    all. It must instead degrade its last slides to raster and still deliver.
    """

    from workers import public_export_hybrid

    echo = tmp_path / "echo-job.py"
    echo.write_text(
        "import json,sys\n"
        "job=json.loads(sys.stdin.read())\n"
        "open(job['outputPath'],'wb').write(b'x')\n"
        "print(json.dumps({'schemaVersion':'public-hybrid-export-result/v1',"
        "'ok':True,'errorCode':None,'structural':{'budgetMillis':job['budgetMillis']}}))\n",
        encoding="utf-8",
    )
    base = tmp_path / "base.pptx"
    base.write_bytes(b"base")
    html = tmp_path / "slide.html"
    html.write_text("<html></html>", encoding="utf-8")
    environment = {
        "PUBLIC_EXPORT_HYBRID_COMMAND_JSON": json.dumps(
            [sys.executable, str(echo)]
        ),
    }
    # A ten-minute export retains two minutes for packaging without starving
    # the final pages with the previous fixed four-minute reserve.
    budget_seconds = 600.0

    outcome = asyncio.run(
        public_export_hybrid.assemble_hybrid_pptx(
            base_pptx=base,
            html_paths=[html],
            scratch=tmp_path,
            font_embedding=False,
            budget_seconds=budget_seconds,
            environment=environment,
        )
    )

    assert outcome.fallback_reason is None
    assembler_millis = outcome.evidence["structural"]["budgetMillis"]
    reserve_millis = 120_000
    assert assembler_millis == int(budget_seconds * 1000) - reserve_millis
    assert reserve_millis >= 60_000

    # A budget that cannot even cover the reserve never starts the assembler.
    exhausted = asyncio.run(
        public_export_hybrid.assemble_hybrid_pptx(
            base_pptx=base,
            html_paths=[html],
            scratch=tmp_path / "second",
            font_embedding=False,
            budget_seconds=float(public_export_hybrid.MIN_ASSEMBLY_RESERVE_SECONDS),
            environment=environment,
        )
    )
    assert exhausted.output_path is None
    assert exhausted.fallback_reason == "hybrid_assembler_budget_exhausted"


def test_hybrid_quality_summary_persists_bounded_slide_fallback_codes() -> None:
    from services.public_export_hybrid_evidence import (
        HYBRID_QUALITY_SUMMARY_SCHEMA,
        MAX_HYBRID_SLIDE_FALLBACKS,
        bounded_hybrid_quality_summary,
        bounded_hybrid_worker_evidence,
    )

    raw = {
        "schemaVersion": HYBRID_QUALITY_SUMMARY_SCHEMA,
        "status": "partially-editable",
        "editableSlides": 7,
        "imageFallbackSlides": 28,
        "rasterFallbackElements": 28,
        "fallbackReasonTally": {
            "authored_hybrid_paint_band_complex_ancestor": 25,
            "hybrid-deck-budget-exhausted": 3,
            "unsafe free-form message": 1,
        },
        "slideFallbacks": [
            {"n": 0, "code": "unsafe free-form message"},
            *[
                {"n": number, "code": "unknown-exception"}
                for number in range(1, MAX_HYBRID_SLIDE_FALLBACKS + 2)
            ],
        ],
        "deckBudgetMillis": 1_500_000,
        "slidesNotAttempted": 3,
        "message": "must not persist",
    }

    quality = bounded_hybrid_quality_summary(raw)
    persisted = bounded_hybrid_worker_evidence(
        {"quality": quality, "deliveredExportMode": "editable-hybrid"}
    )

    assert quality["schemaVersion"] == HYBRID_QUALITY_SUMMARY_SCHEMA
    assert quality["fallbackReasonTally"] == {
        "authored_hybrid_paint_band_complex_ancestor": 25,
        "hybrid-deck-budget-exhausted": 3,
    }
    assert len(quality["slideFallbacks"]) == MAX_HYBRID_SLIDE_FALLBACKS
    assert quality["slideFallbacks"][0] == {"n": 1, "code": "unknown-exception"}
    assert quality["deckBudgetMillis"] == 1_500_000
    assert quality["slidesNotAttempted"] == 3
    assert "message" not in quality
    assert persisted == {
        "quality": quality,
        "deliveredExportMode": "editable-hybrid",
    }


def _hybrid_bundle_payload(**overrides: object) -> dict:
    declared = {
        "schemaVersion": "public-hybrid-declared-semantic/v1",
        "slideIndex": 0,
        "bundleDigest": "sha256:" + "b" * 64,
        "fontSetDigest": "sha256:" + "c" * 64,
        "declaredElementIds": ["q-a"],
        "declaredElements": [
            {"elementId": "q-a", "kind": "shape", "sourceDigest": "sha256:" + "d" * 64}
        ],
    }
    payload = {
        "schemaVersion": EXPORT_CHECKPOINT_BUNDLE_SCHEMA,
        "canonicalExportIdentity": "a" * 64,
        "exportFormat": "pptx",
        "exportMode": "editable-hybrid",
        "renderAdapter": "public-isolated-hybrid",
        "fontEmbedding": True,
        "slides": [],
        "sourceSlides": [],
        "semanticSlides": [declared],
        "exportAttemptId": str(uuid.uuid4()),
        "rendererSourceDigest": "sha256:" + "e" * 64,
    }
    payload.update(overrides)
    return payload


def test_hybrid_recovery_evidence_is_bounded_and_content_free() -> None:
    from services.public_export_hybrid_evidence import bounded_hybrid_worker_evidence
    evidence = bounded_hybrid_worker_evidence({
        "timing": {"schemaVersion": "public-hybrid-timing/v1", "wallMillis": 10,
                   "sourcePath": "private", "slides": [{"n": 1, "conversionMillis": 3, "html": "private"}]},
        "editability": {"schemaVersion": "public-hybrid-editability/v1", "verdict": "unknown",
                        "requiredTargetCount": 1, "imageOnlyPages": [0, 1, 1, 101],
                        "powerpointInteraction": {"status": "passed"},
                        "slides": [{"slideNumber": 1, "verdict": "unknown", "unknownTargetCount": 1,
                                    "targets": [{"normalizedText": "private"}],
                                    "reasons": ["source-role-unknown", "private prose"]}]},
    })
    assert "private" not in json.dumps(evidence)
    assert evidence["editability"]["imageOnlyPages"] == [1]
    assert evidence["editability"]["powerpointInteraction"] == {"status": "not-run"}
    assert evidence["editability"]["slides"][0]["reasons"] == ["source-role-unknown"]
    assert "assemblyMillis" not in evidence["timing"]


def test_hybrid_recovery_wire_preflight_uses_serialized_bytes(tmp_path: Path, monkeypatch) -> None:
    from workers import public_export_hybrid
    captured = []

    class Process:
        returncode = 1
        async def communicate(self, payload):
            captured.append(payload)
            return json.dumps({"schemaVersion": "public-hybrid-export-result/v1", "ok": False,
                               "errorCode": "fixture-stop"}).encode(), b""

    async def launch(*args, **kwargs):
        return Process()

    monkeypatch.setattr(public_export_hybrid.asyncio, "create_subprocess_exec", launch)
    kwargs = dict(base_pptx=tmp_path / "base.pptx", html_paths=[tmp_path / "한글.html"],
                  scratch=tmp_path, font_embedding=False, budget_seconds=600,
                  environment={"PUBLIC_EXPORT_HYBRID_COMMAND_JSON": json.dumps([sys.executable])})
    assert public_export_hybrid.MAX_HYBRID_JOB_BYTES == 8 * 1024 * 1024
    asyncio.run(public_export_hybrid.assemble_hybrid_pptx(**kwargs))
    size = len(captured[0])
    assert b"\\u" in captured[0]
    for limit in (size + 1, size):
        monkeypatch.setattr(public_export_hybrid, "MAX_HYBRID_JOB_BYTES", limit)
        outcome = asyncio.run(public_export_hybrid.assemble_hybrid_pptx(**kwargs))
        assert outcome.fallback_reason == "fixture-stop"
    monkeypatch.setattr(public_export_hybrid, "MAX_HYBRID_JOB_BYTES", size - 1)
    outcome = asyncio.run(public_export_hybrid.assemble_hybrid_pptx(**kwargs))
    assert outcome.fallback_reason == "hybrid_job_wire_limit"
    assert len(captured) == 3


def test_hybrid_recovery_fragment_readback_binds_crop_bytes_and_placement() -> None:
    from services.authored_local_fit_export_evidence import validate_raster_fragments
    image = Image.new("RGBA", (22, 2), (80, 40, 20, 127))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    payload = stream.getvalue()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    placement = {"x": 0.375, "y": 0.375, "widthPt": 8.25, "heightPt": 0.75}
    fragment = {"id": "f0", "order": 0, "rect": {"x": 1, "y": 1, "width": 22, "height": 2},
                "pngDigest": digest, "sizeBytes": len(payload), "placement": placement}
    evidence = {"raster": [{"schemaVersion": "authored-raster-band/v2", "sourceDigest": "sha256:" + "a" * 64,
                            "pngDigest": "sha256:" + "a" * 64, "captureWidthPx": 2560, "captureHeightPx": 1440,
                            "fragments": [fragment]}]}
    kwargs = dict(evidence=evidence, runs=[], manifest={"width_pt": 960, "height_pt": 540}, enforce_font_policy=False)
    assert validate_raster_fragments([(digest, placement, payload)], **kwargs) == {0}
    # Actual assembler edge rounding: round(23 / 2560 * 12192000)
    # minus round(1 / 2560 * 12192000) produces a 104774 EMU extent.
    rounded = {"x": 4763 / 12700, "y": 4763 / 12700,
               "widthPt": 104774 / 12700, "heightPt": 9525 / 12700}
    assert abs(rounded["widthPt"] - placement["widthPt"]) > 1 / 12700
    assert validate_raster_fragments([(digest, rounded, payload)], **kwargs) == {0}
    with pytest.raises(ValueError, match="raster_fragment_placement_mismatch"):
        validate_raster_fragments([(digest, {**rounded, "widthPt": 104773 / 12700}, payload)], **kwargs)
    with pytest.raises(ValueError, match="raster_fragment_placement_mismatch") as shifted:
        validate_raster_fragments([(digest, {**placement, "x": 0}, payload)], **kwargs)
    assert shifted.value.diagnostics["digestMatchCount"] == 1
    assert shifted.value.diagnostics["placementMatchCount"] == 0
    assert shifted.value.diagnostics["expectedDigest"] == digest
    assert shifted.value.diagnostics["candidates"][0]["placementPt"]["x"] == 0
    with pytest.raises(ValueError, match="raster_fragment_digest_mismatch") as changed:
        validate_raster_fragments([("sha256:" + "b" * 64, placement, payload)] * 8, **kwargs)
    assert len(changed.value.diagnostics["candidates"]) == 4
    with pytest.raises(ValueError, match="raster_fragment_missing") as missing:
        validate_raster_fragments([], **kwargs)
    assert missing.value.diagnostics["packagedImageCount"] == 0
    fragment["sizeBytes"] += 1
    with pytest.raises(ValueError, match="raster_fragment_bytes_mismatch"):
        validate_raster_fragments([(digest, placement, payload)], **kwargs)


def test_export_worker_refuses_a_declared_graph_without_its_sealed_identity(
    tmp_path: Path,
    worker_cache_limits: None,
) -> None:
    """The graph is sealed against an attempt and a renderer; both must travel."""

    from workers import public_export_worker

    job, _bundle = _hybrid_export_fixture(tmp_path)

    real_store = LocalContentAddressedStore(tmp_path)

    def store_for(payload):
        class Store:
            def read(self, _reference):
                return json.dumps(payload).encode("utf-8")

            def verified_path(self, reference):
                return real_store.verified_path(reference)

        return Store()

    real = json.loads(real_store.read(job.input_artifact).decode("utf-8"))
    base = _hybrid_bundle_payload(
        slides=real["slides"], sourceSlides=real["sourceSlides"]
    )

    accepted = public_export_worker._read_bundle(job, store_for(base))
    assert len(accepted.semantic_slides) == 1
    assert accepted.export_attempt_id == base["exportAttemptId"]
    assert accepted.renderer_source_digest == base["rendererSourceDigest"]

    for overrides, expected in (
        ({"exportAttemptId": ""}, "public_export_semantic_binding_invalid"),
        ({"rendererSourceDigest": "not-a-digest"}, "public_export_semantic_binding_invalid"),
        ({"semanticSlides": []}, "public_export_semantic_binding_invalid"),
        (
            {"semanticSlides": [{"slideIndex": 7, "declaredElementIds": [], "declaredElements": []}]},
            "public_export_semantic_slide_invalid",
        ),
    ):
        payload = {**base, **overrides}
        with pytest.raises(public_export_worker.PublicWorkerExecutionError) as raised:
            public_export_worker._read_bundle(job, store_for(payload))
        assert raised.value.code == expected

    # Dropping the identity keys entirely is a shape error, not a silent drop.
    without_identity = {key: value for key, value in base.items() if key != "exportAttemptId"}
    with pytest.raises(public_export_worker.PublicWorkerExecutionError) as raised:
        public_export_worker._read_bundle(job, store_for(without_identity))
    assert raised.value.code == "public_export_bundle_invalid_shape"

    # A hybrid bundle with no graph at all stays valid and observed-only.
    observed_only = {
        key: value
        for key, value in base.items()
        if key not in {"semanticSlides", "exportAttemptId", "rendererSourceDigest"}
    }
    assert public_export_worker._read_bundle(job, store_for(observed_only)).semantic_slides == []


def test_hybrid_assembler_puts_the_declared_graph_on_the_child_job(
    tmp_path: Path,
) -> None:
    """The assembler child is the only consumer, and it gets the whole graph."""

    from workers import public_export_hybrid

    echo = tmp_path / "echo-job.py"
    echo.write_text(
        "import json,sys\n"
        "job=json.loads(sys.stdin.read())\n"
        "open(job['outputPath'],'wb').write(b'x')\n"
        "print(json.dumps({'schemaVersion':'public-hybrid-export-result/v1',"
        "'ok':True,'errorCode':None,'structural':{"
        "'semanticSlides':len(job.get('semanticSlides',[])),"
        "'exportAttemptId':job.get('exportAttemptId'),"
        "'rendererSourceDigest':job.get('rendererSourceDigest')}}))\n",
        encoding="utf-8",
    )
    base = tmp_path / "base.pptx"
    base.write_bytes(b"base")
    html = tmp_path / "slide.html"
    html.write_text("<html></html>", encoding="utf-8")
    environment = {
        "PUBLIC_EXPORT_HYBRID_COMMAND_JSON": json.dumps([sys.executable, str(echo)])
    }
    declared = [{"slideIndex": 0, "declaredElementIds": [], "declaredElements": []}]
    attempt_id = str(uuid.uuid4())
    renderer_digest = "sha256:" + "f" * 64

    outcome = asyncio.run(
        public_export_hybrid.assemble_hybrid_pptx(
            base_pptx=base,
            html_paths=[html],
            scratch=tmp_path / "run",
            font_embedding=False,
            budget_seconds=1030.0,
            semantic_slides=declared,
            export_attempt_id=attempt_id,
            renderer_source_digest=renderer_digest,
            environment=environment,
        )
    )

    assert outcome.fallback_reason is None
    assert outcome.evidence["structural"] == {
        "semanticSlides": 1,
        "exportAttemptId": attempt_id,
        "rendererSourceDigest": renderer_digest,
    }

    # A graph that does not cover every slide never reaches the child.
    mismatched = asyncio.run(
        public_export_hybrid.assemble_hybrid_pptx(
            base_pptx=base,
            html_paths=[html],
            scratch=tmp_path / "mismatch",
            font_embedding=False,
            budget_seconds=1030.0,
            semantic_slides=[*declared, {"slideIndex": 1}],
            export_attempt_id=attempt_id,
            renderer_source_digest=renderer_digest,
            environment=environment,
        )
    )
    assert mismatched.output_path is None
    assert mismatched.fallback_reason == "hybrid_assembler_semantic_binding_invalid"
