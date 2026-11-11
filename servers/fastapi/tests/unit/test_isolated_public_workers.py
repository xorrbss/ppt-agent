from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

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
from workers.public_export_worker import execute_export
from workers.public_svg_export_worker import SVG_EXPORT_SCHEMA
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
        role="export",
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

    assert "AUTHORED_RENDERER_WORKER" not in environment

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
        role="export",
        executable=Path(sys.executable),
        arguments=("-m", "workers.public_export_worker"),
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
            role="export", operation_id=operation_id,
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
            exit_code = asyncio.run(public_worker_runtime.run_worker("export", execute))
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
            "export", incoming, trusted_dispatch=local_development,
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


def test_worker_never_evicts_the_artifact_authority_named_by_the_legacy_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for published template material vanishing from the
    local artifact store.

    A dispatcher that runs the worker without staging (the fake-LLM harness)
    hands the child the controller's authority root as
    ``PUBLIC_WORKER_OBJECT_STORE_ROOT``. The worker used that root as its own
    transport cache: writing one export output evicted every authority object
    older than the cache TTL, including content-addressed material that newer
    rows still referenced, and reading an aged input deleted that input.
    """

    from models.sql.artifact_keys import artifact_storage_key
    from models.sql.public_artifact import ArtifactRef as ProductArtifactRef
    from services.artifact_store import LocalDevelopmentArtifactObjectStore

    authority_root = tmp_path / "public-artifacts"
    authority = LocalDevelopmentArtifactObjectStore(authority_root)
    owner_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    tenant_id = "tenant-a"
    aged = time.time() - 2 * 3600

    def authority_object(payload: bytes, *, kind: str, media_type: str) -> ProductArtifactRef:
        digest = hashlib.sha256(payload).hexdigest()
        reference = ProductArtifactRef(
            artifact_id=uuid.uuid4(), owner_id=owner_id, tenant_id=tenant_id,
            kind=kind, sha256=digest, size_bytes=len(payload), media_type=media_type,
            storage_key=artifact_storage_key(owner_id=owner_id, tenant_id=tenant_id, digest=digest),
            created_by_operation_id=uuid.uuid4(), retention_class="published",
        )
        asyncio.run(authority.put_bytes(reference, payload))
        return reference

    # Template material an older registration wrote first; a newer published
    # template's row points at the same content-addressed object.
    material_payload = b'{"adaptation":"frame-6"}'
    material = authority_object(material_payload, kind="source-manifest",
                                media_type="application/json")
    os.utime(authority._path(material), (aged, aged))
    signing_key = b"h" * 32
    key_file = tmp_path / "capability.key"
    key_file.write_bytes(signing_key)
    environment = {
        **worker_environment("export", {
            "PATH": os.environ.get("PATH", ""),
            "PUBLIC_TRUSTED_WORKER_LOCAL_POLICY": "development",
            "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "linux-local",
            "PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(key_file),
        }, trusted_dispatch=True),
        # Exactly what scripts/fake_llm_e2e/fake_llm_backend.py adds.
        "PUBLIC_WORKER_OBJECT_STORE_ROOT": str(authority_root),
        "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES": "2147483648",
        "PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS": "3600",
        "PPT_WORKER_OUTPUT_LIMIT_BYTES": "536870912",
    }

    def run_export(bundle: ProductArtifactRef, bundle_payload: bytes, deck: bytes) -> dict:
        operation_id = uuid.uuid4()
        job_input = ArtifactRef(
            artifact_id=bundle.artifact_id, owner_id=owner_id, tenant_id=tenant_id,
            kind="export-bundle", sha256=bundle.sha256, size_bytes=bundle.size_bytes,
            media_type=bundle.media_type, storage_key=bundle.storage_key,
            created_by_operation_id=operation_id, retention_class="staged",
        )

        async def execute(job, store):
            assert store.read(job.input_artifact) == bundle_payload
            return store.put(
                deck, source=job.input_artifact, operation_id=job.operation_id,
                kind="presentation-pptx", media_type="application/vnd.ms-powerpoint",
            )

        job = IsolatedWorkerJob(
            role="export", operation_id=operation_id,
            attempt_token="attempt-token-1234567890", fencing_token=1,
            input_digest=job_input.sha256, input_artifact=job_input,
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
                stdin=SimpleNamespace(buffer=stdin), stdout=SimpleNamespace(buffer=stdout),
            ))
            patch.setattr(public_worker_runtime, "_REPLAY_GUARD", CapabilityReplayGuard())
            exit_code = asyncio.run(public_worker_runtime.run_worker("export", execute))
        result = json.loads(stdout.getvalue())
        assert exit_code == 0 and result["ok"] is True, result["errorCode"]
        output = ArtifactRef.from_wire(result["outputArtifact"])
        assert (authority_root / output.storage_key).read_bytes() == deck
        return result

    # A fresh export bundle: writing the deck must not evict the aged material.
    fresh_payload = b'{"bundle":"fresh export input"}'
    fresh = authority_object(fresh_payload, kind="export-bundle",
                             media_type="application/vnd.presenton.export-bundle+json")
    run_export(fresh, fresh_payload, b"exported deck")
    assert asyncio.run(authority.read_bytes(material)) == material_payload
    # The authority's objects keep their own timestamps.
    assert authority._path(material).stat().st_mtime == pytest.approx(aged)

    # An aged export bundle is an authority object, not an expired cache entry.
    aged_payload = b'{"bundle":"aged export input"}'
    aged_bundle = authority_object(aged_payload, kind="export-bundle",
                                   media_type="application/vnd.presenton.export-bundle+json")
    os.utime(authority._path(aged_bundle), (aged, aged))
    run_export(aged_bundle, aged_payload, b"second exported deck")
    assert asyncio.run(authority.read_bytes(aged_bundle)) == aged_payload
    assert asyncio.run(authority.read_bytes(material)) == material_payload


@pytest.fixture
def worker_cache_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """The transport cache budget the launcher supplies to a real worker."""

    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", str(64 * 1024 * 1024))
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", "900")


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
    operation_id = uuid.uuid4()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"/>'
    slide = _artifact(
        operation_id=operation_id,
        payload=svg,
        storage_key="tenant/owner/resource-slide.svg",
        kind="slide-svg",
        media_type="image/svg+xml",
    )
    _store_payload(tmp_path, slide, svg)
    bundle_payload = json.dumps(
        {
            # Opaque to the child below, which never parses its input.
            "schemaVersion": SVG_EXPORT_SCHEMA,
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
    # The authored export's durable resource projection (_resource_summary)
    # was removed with authored generation (P3 B4c).


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


def test_next_export_route_admits_without_executing_it() -> None:
    """The route only admits; the isolated worker is the only executor.

    The editable-hybrid lane was removed with the authored strategy, so the
    admission no longer names a PPTX mode and nothing launches an assembler.
    """

    nextjs = Path(__file__).resolve().parents[3] / "nextjs"
    route = (
        nextjs / "app" / "api" / "export-presentation" / "route.ts"
    ).read_text(encoding="utf-8")
    assert "/api/v1/ppt/workspace/presentations/" in route
    assert "runBundledPresentationExport" not in route
    assert "runAuthoredHybridPresentationExport" not in route
    assert "child_process" not in route
    assert "export_task.json" not in route
    assert "context: admission.context" in route
    admission = (nextjs / "lib" / "public-export-admission.ts").read_text(
        encoding="utf-8"
    )
    assert '"hybrid"' not in admission and "pptx_mode" not in admission
    assert not (nextjs / "scripts" / "public-hybrid-export-worker.mjs").exists()
    for durable in (
        "services/workspace_quality_export_operation_handlers.py",
        "services/public_export_operation.py",
        "services/workspace_service.py",
        "workers/public_export_worker.py",
    ):
        source = (Path(__file__).resolve().parents[2] / durable).read_text(
            encoding="utf-8"
        )
        assert "create_subprocess_exec" not in source


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schemaVersion":"public-export-bundle/v1","exportFormat":"pptx","slides":[]}',
        b'{"schemaVersion":"public-export-bundle/v2","exportMode":"editable-hybrid"}',
        b"not json",
    ],
)
def test_export_worker_refuses_a_bundle_that_is_not_an_svg_export(
    tmp_path: Path, worker_cache_limits: None, payload: bytes
) -> None:
    operation_id = uuid.uuid4()
    bundle = _artifact(
        operation_id=operation_id,
        payload=payload,
        storage_key="tenant/owner/raster-bundle.json",
        kind="export-bundle",
        media_type="application/vnd.presenton.export-bundle+json",
    )
    _store_payload(tmp_path, bundle, payload)
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

    with pytest.raises(public_worker_runtime.PublicWorkerExecutionError) as refused:
        asyncio.run(execute_export(job, LocalContentAddressedStore(tmp_path)))

    assert refused.value.code == "public_export_bundle_schema_unsupported"
