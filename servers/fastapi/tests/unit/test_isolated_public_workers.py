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
from PIL import Image

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
    worker_environment,
)
from services.trusted_executor.contracts import (
    ExecutionIdentity,
    ResourceLimits,
    SandboxLaunchSpec,
    SandboxPolicy,
)
from workers import public_worker_runtime
from workers.public_export_worker import (
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


def test_export_worker_builds_digest_verified_pptx_from_artifact_refs(
    tmp_path: Path,
) -> None:
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
    bundle_payload = json.dumps(
        {
            "schemaVersion": EXPORT_CHECKPOINT_BUNDLE_SCHEMA,
            "canonicalExportIdentity": hashlib.sha256(b"identity").hexdigest(),
            "exportFormat": "pptx",
            "exportMode": "fidelity-raster",
            "renderAdapter": "chromium-raster-v2",
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

    output = asyncio.run(execute_export(job, store))

    assert output.kind == "presentation-pptx"
    assert output.owner_id == bundle.owner_id
    assert output.created_by_operation_id == operation_id
    payload = store.read(output)
    assert hashlib.sha256(payload).hexdigest() == output.sha256
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert "ppt/presentation.xml" in archive.namelist()


def test_export_subprocess_limit_kills_descendants_and_reports_recycle(
    tmp_path: Path,
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
    job = IsolatedWorkerJob(
        role="export",
        operation_id=operation_id,
        attempt_token="resource-attempt-1234567890",
        fencing_token=1,
        input_digest=bundle.sha256,
        input_artifact=bundle,
        capability="pending",
        deadline_epoch_seconds=now + 60,
    )
    job = replace(
        job,
        capability=issue_worker_capability(
            job,
            signing_key=key_path.read_bytes(),
            ttl_seconds=60,
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

    child_script = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "handles=[open(sys.executable,'rb') for _ in range(32)];"
        "sys.stdin.buffer.read();time.sleep(60)"
    )
    with pytest.raises(IsolatedWorkerProcessError) as raised:
        asyncio.run(
            execute_isolated_worker_job(
                job,
                source_environment=environment,
                command=[sys.executable, "-c", child_script],
            )
        )

    assert "resource_limit_exceeded" in raised.value.code
    assert raised.value.resource_metrics["resource_limits_enforced"] is True
    assert raised.value.resource_metrics["worker_limit_triggered"] is True
    assert raised.value.resource_metrics["worker_peak_handle_count"] > 8
    assert raised.value.resource_metrics["worker_peak_process_count"] >= 2
    assert raised.value.resource_metrics["worker_final_process_count"] == 0
    assert raised.value.resource_metrics["worker_recycle_failure_count"] == 0


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


def test_next_export_route_is_admission_only() -> None:
    route = (
        Path(__file__).resolve().parents[3]
        / "nextjs"
        / "app"
        / "api"
        / "export-presentation"
        / "route.ts"
    ).read_text(encoding="utf-8")
    assert "/api/v1/ppt/workspace/presentations/" in route
    assert "runBundledPresentationExport" not in route
    assert "runAuthoredHybridPresentationExport" not in route
    assert "child_process" not in route
    assert "export_task.json" not in route
