from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

import pytest

from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
from services.trusted_executor.container_composition import (
    create_container_trusted_worker_executor_runtime,
)
from services.trusted_executor.container_isolation_evidence import (
    ContainerIsolationExpectation,
    build_container_isolation_report,
)
from services.trusted_executor.container_runtime import (
    ContainerCreateRequest,
    ContainerImageSettings,
    ContainerPathMapping,
    ContainerRuntimeError,
    build_container_create_request,
)
from services.trusted_executor.container_stdio import AttachDemultiplexer, encode_attach_frame
from services.trusted_executor.container_supervisor import ContainerProcessSupervisor
from services.trusted_executor.contracts import (
    ExecutionIdentity,
    ResourceLimits,
    SandboxFilesystemSpec,
    SandboxLaunchSpec,
    SandboxPolicy,
)
from services.trusted_executor.isolation_policy import (
    EnforcementStatus,
    IsolationControl,
    IsolationUnavailableError,
)
from services.trusted_executor.process_supervisor import (
    ProcessDeadlineExceeded,
    ProcessMemoryLimitExceeded,
    ProcessSupervisorUnavailable,
)
from services.trusted_executor.worker_dispatcher import TrustedWorkerWireError


IMAGE = "registry.example/ppt-worker@sha256:" + "a" * 64
MOUNT_ROOT = PurePosixPath("/srv/trusted-staging")


# ----- fake container runtime -------------------------------------------------


class _FakeAttachStream:
    def __init__(self) -> None:
        self._out = bytearray()
        self._out_closed = False
        self._condition = threading.Condition()
        self.stdin = bytearray()
        self.stdin_closed = threading.Event()
        self.closed = False

    def read(self, size: int) -> bytes:
        with self._condition:
            while not self._out and not self._out_closed:
                self._condition.wait()
            chunk = bytes(self._out[:size])
            del self._out[:size]
            return chunk

    def write(self, data: bytes) -> None:
        self.stdin.extend(data)

    def close_write(self) -> None:
        self.stdin_closed.set()

    def close(self) -> None:
        self.closed = True

    def emit(self, stream_type: int, payload: bytes) -> None:
        with self._condition:
            self._out.extend(encode_attach_frame(stream_type, payload))
            self._condition.notify_all()

    def finish(self) -> None:
        with self._condition:
            self._out_closed = True
            self._condition.notify_all()


Behaviour = Callable[["_FakeContainer"], int]


class _FakeContainer:
    def __init__(self, client: "_FakeClient", request: ContainerCreateRequest) -> None:
        self.client = client
        self.request = request
        self.id = uuid.uuid4().hex
        self.stream = _FakeAttachStream()
        self.started = False
        self.killed = False
        self.removed = False
        self.oom_killed = False
        self.exit_code: int | None = None
        self.exited = threading.Event()
        self._thread: threading.Thread | None = None

    # helpers for behaviours
    def read_stdin(self) -> bytes:
        self.stream.stdin_closed.wait(5.0)
        return bytes(self.stream.stdin)

    def parent_path(self, container_path: str) -> Path:
        for bind in self.request.host_config["Binds"]:
            source, destination, _mode = bind.rsplit(":", 2)
            if container_path == destination or container_path.startswith(destination + "/"):
                return Path(source) / Path(container_path[len(destination) :].lstrip("/"))
        raise AssertionError(f"{container_path} is not mounted")

    # ContainerHandle protocol
    def inspect(self) -> Mapping[str, object]:
        mounts = []
        for bind in self.request.host_config["Binds"]:
            source, destination, mode = bind.rsplit(":", 2)
            mounts.append({"Type": "bind", "Source": source, "Destination": destination, "RW": mode == "rw"})
        value: dict[str, object] = {
            "Id": self.id,
            "State": {
                "Running": self.started and not self.exited.is_set(),
                "ExitCode": self.exit_code if self.exit_code is not None else 0,
                "OOMKilled": self.oom_killed,
            },
            "Config": {"User": self.request.user},
            "HostConfig": copy.deepcopy(dict(self.request.host_config)),
            "Mounts": mounts,
            "NetworkSettings": {"Networks": {"none": {}}},
        }
        self.client.inspect_override(value)
        return value

    def attach(self) -> _FakeAttachStream:
        return self.stream

    def start(self) -> None:
        self.started = True
        self.client.started.append(self)

        def run() -> None:
            try:
                code = self.client.behaviour(self)
            except Exception:  # noqa: BLE001 - a broken fake worker is a failed exit
                code = 70
            if self.exit_code is None:
                self.exit_code = code
            self.exited.set()
            self.stream.finish()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def wait(self, *, timeout: float | None) -> int:
        if not self.exited.wait(timeout):
            raise TimeoutError("fake container still running")
        assert self.exit_code is not None
        return self.exit_code

    def kill(self) -> None:
        if self.exited.is_set():
            raise ContainerRuntimeError("container is not running")
        self.killed = True
        self.exit_code = 137
        self.exited.set()
        self.stream.finish()

    def stats(self) -> Mapping[str, object]:
        return self.client.stats

    def remove(self) -> None:
        self.removed = True


class _FakeClient:
    def __init__(
        self,
        behaviour: Behaviour | None = None,
        *,
        inspect_override: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.behaviour = behaviour or (lambda container: 0)
        self.inspect_override = inspect_override or (lambda value: None)
        self.created: list[_FakeContainer] = []
        self.started: list[_FakeContainer] = []
        self.stats: dict[str, object] = {
            "memory_stats": {"usage": 41 * 1024 * 1024, "max_usage": 64 * 1024 * 1024},
            "pids_stats": {"current": 7},
            "cpu_stats": {"cpu_usage": {"total_usage": 2_500_000_000}},
        }

    def create(self, request: ContainerCreateRequest) -> _FakeContainer:
        container = _FakeContainer(self, request)
        self.created.append(container)
        return container


# ----- fixtures ------------------------------------------------------------------


def _settings() -> ContainerImageSettings:
    return ContainerImageSettings(image=IMAGE, user="10001:10001")


def _limits(deadline: float = 30.0) -> ResourceLimits:
    return ResourceLimits(
        memory_bytes=2 * 1024 * 1024 * 1024,
        process_limit=96,
        deadline_seconds=deadline,
        temporary_bytes=512 * 1024 * 1024,
    )


def _spec(staging_root: Path, *, deadline: float = 30.0) -> SandboxLaunchSpec:
    sandbox_root = staging_root / "op-1" / "render" / "fence-1"
    input_root = sandbox_root / "input"
    for directory in (input_root, sandbox_root / "tmp", sandbox_root / "output"):
        directory.mkdir(parents=True, exist_ok=True)
    (input_root / "source.html").write_bytes(b"<html>")
    return SandboxLaunchSpec(
        identity=ExecutionIdentity(
            operation_id=uuid.uuid4(),
            tenant_id="tenant-a",
            owner_id=uuid.uuid4(),
            presentation_id=uuid.uuid4(),
        ),
        role="render",
        executable=Path(staging_root.anchor) / "usr" / "local" / "bin" / "python",
        arguments=("-m", "workers.public_worker_runtime"),
        limits=_limits(deadline),
        policy=SandboxPolicy(),
        filesystem=SandboxFilesystemSpec(
            sandbox_root=sandbox_root,
            input_root=input_root,
            temporary_root=sandbox_root / "tmp",
            staged_inputs=(input_root / "source.html",),
        ),
    )


def _environment(key_path: Path) -> dict[str, str]:
    return {
        "PATH": "C:/host/bin",
        "PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(key_path),
        "PUBLIC_WORKER_ROLE": "render",
        "PUBLIC_WORKER_OS_ISOLATION": "nonroot-readonly-tmpfs-network-none",
        "PROVIDER_API_KEY": "secret",
    }


def _supervisor(spec: SandboxLaunchSpec, client: _FakeClient, staging_root: Path) -> ContainerProcessSupervisor:
    return ContainerProcessSupervisor(
        spec.identity,
        spec.limits,
        client=client,
        settings=_settings(),
        mapping=ContainerPathMapping(parent_root=staging_root, container_root=MOUNT_ROOT),
        sample_interval_seconds=0.05,
    )


# ----- HostConfig ----------------------------------------------------------------


def test_create_request_builds_the_contract_host_config(tmp_path: Path) -> None:
    staging_root = tmp_path / "staging"
    spec = _spec(staging_root)
    key_copy = spec.filesystem.sandbox_root / "capability.key"
    request = build_container_create_request(
        spec,
        mapping=ContainerPathMapping(parent_root=staging_root, container_root=MOUNT_ROOT),
        settings=_settings(),
        capability_key_parent_path=key_copy,
        environment=_environment(tmp_path / "node.key"),
    )
    sandbox = f"{MOUNT_ROOT}/op-1/render/fence-1"
    parent = spec.filesystem.sandbox_root.as_posix()
    assert request.host_config == {
        "NetworkMode": "none",
        "Memory": 2 * 1024 * 1024 * 1024,
        "MemorySwap": 2 * 1024 * 1024 * 1024,
        "PidsLimit": 96,
        "ReadonlyRootfs": True,
        "Tmpfs": {f"{sandbox}/tmp": "rw,nosuid,nodev,size=536870912,nr_inodes=4096"},
        "ShmSize": 256 * 1024 * 1024,
        "Binds": [
            f"{parent}/input:{sandbox}/input:ro",
            f"{parent}/output:{sandbox}/output:rw",
            f"{parent}/capability.key:{sandbox}/capability.key:ro",
        ],
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges:true"],
        "Init": True,
        "AutoRemove": False,
        "OomKillDisable": False,
    }
    assert request.image == IMAGE and request.user == "10001:10001"
    assert request.working_dir == sandbox
    assert request.command[1:] == ("-m", "workers.public_worker_runtime")
    environment = request.environment
    assert environment["PUBLIC_WORKER_INPUT_STORE_ROOT"] == f"{sandbox}/input"
    assert environment["PUBLIC_WORKER_OUTPUT_STORE_ROOT"] == f"{sandbox}/output"
    assert environment["PUBLIC_WORKER_CAPABILITY_KEY_FILE"] == f"{sandbox}/capability.key"
    assert environment["TMPDIR"] == f"{sandbox}/tmp"
    assert environment["TRUSTED_EXECUTOR_NETWORK"] == "none"
    assert "PATH" not in environment and "PROVIDER_API_KEY" not in environment


def test_cpu_rate_below_full_becomes_a_cfs_quota(tmp_path: Path) -> None:
    spec = _spec(tmp_path / "staging")
    limited = SandboxLaunchSpec(
        identity=spec.identity,
        role=spec.role,
        executable=spec.executable,
        arguments=spec.arguments,
        limits=ResourceLimits(
            memory_bytes=spec.limits.memory_bytes,
            process_limit=spec.limits.process_limit,
            deadline_seconds=spec.limits.deadline_seconds,
            cpu_rate_percent=50,
            temporary_bytes=spec.limits.temporary_bytes,
        ),
        policy=spec.policy,
        filesystem=spec.filesystem,
    )
    request = build_container_create_request(
        limited,
        mapping=ContainerPathMapping(parent_root=tmp_path / "staging", container_root=MOUNT_ROOT),
        settings=_settings(),
        capability_key_parent_path=spec.filesystem.sandbox_root / "capability.key",
        environment={},
        host_cpu_count=4,
    )
    assert request.host_config["CpuPeriod"] == 100_000
    assert request.host_config["CpuQuota"] == 200_000


def test_identity_mapping_requires_posix_root_and_settings_reject_root_user(tmp_path: Path) -> None:
    if os.name == "nt":
        with pytest.raises(ContainerRuntimeError, match="POSIX absolute"):
            ContainerPathMapping.identity(tmp_path)
    else:
        mapping = ContainerPathMapping.identity(Path("/srv/staging"))
        assert mapping.to_container(Path("/srv/staging/op/input")) == PurePosixPath("/srv/staging/op/input")
    explicit = ContainerPathMapping(parent_root=tmp_path, container_root=MOUNT_ROOT)
    assert explicit.to_container(tmp_path / "op" / "input") == MOUNT_ROOT / "op" / "input"
    with pytest.raises(ContainerRuntimeError, match="outside"):
        explicit.to_container(tmp_path.parent / "elsewhere")
    for user in ("0", "root", "0:0", "1000:0", ""):
        with pytest.raises(ContainerRuntimeError):
            ContainerImageSettings(image=IMAGE, user=user)


# ----- inspect -> report ---------------------------------------------------------


def _inspect_and_expectation(tmp_path: Path) -> tuple[dict[str, object], ContainerIsolationExpectation]:
    staging_root = tmp_path / "staging"
    spec = _spec(staging_root)
    client = _FakeClient()
    mapping = ContainerPathMapping(parent_root=staging_root, container_root=MOUNT_ROOT)
    request = build_container_create_request(
        spec,
        mapping=mapping,
        settings=_settings(),
        capability_key_parent_path=spec.filesystem.sandbox_root / "capability.key",
        environment={},
    )
    inspect = dict(client.create(request).inspect())
    expectation = ContainerIsolationExpectation(
        limits=spec.limits,
        policy=spec.policy,
        input_root=mapping.to_container(spec.filesystem.input_root),
        temporary_root=mapping.to_container(spec.filesystem.temporary_root),
        input_source=spec.filesystem.input_root.as_posix(),
    )
    return inspect, expectation


def test_inspect_output_that_proves_every_control_is_production_eligible(tmp_path: Path) -> None:
    inspect, expectation = _inspect_and_expectation(tmp_path)
    report = build_container_isolation_report(inspect, expectation)
    assert report.production_eligible
    assert report.backend == "linux-container-runtime"
    assert all(status is EnforcementStatus.ENFORCED for _control, status, _reason in report.controls)
    assert report.to_attestation()["productionEligible"] is True


def _tmpfs_all(inspect: dict[str, object], options: str) -> None:
    tmpfs = inspect["HostConfig"]["Tmpfs"]
    tmpfs.update({key: options for key in tmpfs})


def _drop_input_mounts(inspect: dict[str, object]) -> None:
    inspect["Mounts"] = []
    inspect["HostConfig"]["Binds"] = []


@pytest.mark.parametrize(
    ("mutate", "control"),
    [
        (lambda i: i["HostConfig"].__setitem__("NetworkMode", "bridge"), IsolationControl.NETWORK_NONE),
        (lambda i: i["NetworkSettings"]["Networks"].__setitem__("bridge", {}), IsolationControl.NETWORK_NONE),
        (lambda i: i["HostConfig"].__setitem__("ReadonlyRootfs", False), IsolationControl.ROOT_FILESYSTEM_READ_ONLY),
        (lambda i: i["HostConfig"].pop("ReadonlyRootfs"), IsolationControl.ROOT_FILESYSTEM_READ_ONLY),
        (lambda i: i["Mounts"][0].__setitem__("RW", True), IsolationControl.STAGED_INPUTS_READ_ONLY),
        (_drop_input_mounts, IsolationControl.STAGED_INPUTS_READ_ONLY),
        (lambda i: i["HostConfig"].__setitem__("Tmpfs", {}), IsolationControl.TEMPORARY_BYTES_BOUNDED),
        (lambda i: _tmpfs_all(i, "rw,size=1g,nr_inodes=4096"), IsolationControl.TEMPORARY_BYTES_BOUNDED),
        (lambda i: _tmpfs_all(i, "rw,size=1m"), IsolationControl.TEMPORARY_FILES_BOUNDED),
        (lambda i: _tmpfs_all(i, "rw,size=1m,nr_inodes=5000"), IsolationControl.TEMPORARY_FILES_BOUNDED),
        (lambda i: i["Config"].__setitem__("User", "root"), IsolationControl.LOW_PRIVILEGE_IDENTITY),
        (lambda i: i["Config"].__setitem__("User", ""), IsolationControl.LOW_PRIVILEGE_IDENTITY),
    ],
)
def test_missing_or_weaker_control_is_unsupported(tmp_path: Path, mutate, control: IsolationControl) -> None:
    inspect, expectation = _inspect_and_expectation(tmp_path)
    mutate(inspect)
    report = build_container_isolation_report(inspect, expectation)
    assert report.status_for(control) is EnforcementStatus.UNSUPPORTED
    assert not report.production_eligible
    with pytest.raises(IsolationUnavailableError, match=control.value):
        report.require((control,))


@pytest.mark.parametrize("field, value", [("Memory", 0), ("Memory", None), ("PidsLimit", 4096), ("PidsLimit", -1)])
def test_unproven_cgroup_limits_are_not_resource_limits(tmp_path: Path, field: str, value: object) -> None:
    inspect, expectation = _inspect_and_expectation(tmp_path)
    inspect["HostConfig"][field] = value
    report = build_container_isolation_report(inspect, expectation)
    assert not report.resource_limits_supported
    assert field in report.resource_limits_reason
    assert not report.production_eligible


# ----- supervisor ------------------------------------------------------------------


def test_supervisor_never_starts_a_container_it_cannot_verify(tmp_path: Path) -> None:
    staging_root = tmp_path / "staging"
    spec = _spec(staging_root)
    key = tmp_path / "node.key"
    key.write_bytes(b"k" * 32)
    client = _FakeClient(inspect_override=lambda value: value["HostConfig"].__setitem__("NetworkMode", "bridge"))
    supervisor = _supervisor(spec, client, staging_root)
    with pytest.raises(ProcessSupervisorUnavailable, match="network-none=unsupported"):
        supervisor.launch(spec, cwd=spec.filesystem.sandbox_root, environment=_environment(key), pipe_stdio=True)
    container = client.created[0]
    assert not container.started
    assert supervisor.termination_reason == "launch_failed"
    assert (spec.filesystem.sandbox_root / "capability.key").read_bytes() == b"k" * 32
    supervisor.close()
    assert container.removed
    assert not (spec.filesystem.sandbox_root / "capability.key").exists()


def test_supervisor_kills_on_deadline_and_reports_it(tmp_path: Path) -> None:
    staging_root = tmp_path / "staging"
    spec = _spec(staging_root, deadline=0.3)
    key = tmp_path / "node.key"
    key.write_bytes(b"k" * 32)

    def hang(container: _FakeContainer) -> int:
        container.read_stdin()
        container.exited.wait(10.0)
        return 0

    client = _FakeClient(hang)
    supervisor = _supervisor(spec, client, staging_root)
    process = supervisor.launch(spec, cwd=spec.filesystem.sandbox_root, environment=_environment(key), pipe_stdio=True)
    process.stdin.write(b"{}")
    process.stdin.close()
    with pytest.raises(ProcessDeadlineExceeded):
        process.wait(timeout=5.0)
    assert client.created[0].killed
    assert supervisor.wait_for_termination(timeout=2.0) == "deadline_exceeded"
    assert supervisor.descendant_zero_verified
    telemetry = supervisor.telemetry()
    assert telemetry["deadlineExceeded"] is True and telemetry["memoryLimitExceeded"] is False
    assert telemetry["containerExitCode"] == 137
    assert telemetry["enforcementLayer"] == "linux-container-cgroup"
    assert telemetry["peakPrivateBytes"] == 64 * 1024 * 1024 and telemetry["peakProcessCount"] == 7
    assert telemetry["peakCpuMillis"] == 2500 and telemetry["resourceLimitsEnforced"] is True
    assert telemetry["containerIsolationEvidence"]["productionEligible"] is True
    supervisor.close()
    assert client.created[0].removed


def test_supervisor_maps_oom_kill_to_memory_limit(tmp_path: Path) -> None:
    staging_root = tmp_path / "staging"
    spec = _spec(staging_root)
    key = tmp_path / "node.key"
    key.write_bytes(b"k" * 32)

    def oom(container: _FakeContainer) -> int:
        container.read_stdin()
        container.oom_killed = True
        return 137

    client = _FakeClient(oom)
    supervisor = _supervisor(spec, client, staging_root)
    process = supervisor.launch(spec, cwd=spec.filesystem.sandbox_root, environment=_environment(key), pipe_stdio=True)
    process.stdin.close()
    with pytest.raises(ProcessMemoryLimitExceeded):
        process.wait(timeout=5.0)
    assert supervisor.memory_limit_exceeded and supervisor.termination_reason == "memory_limit_exceeded"
    assert supervisor.telemetry()["containerOomKilled"] is True
    supervisor.close()


def test_supervisor_cancel_kills_and_proves_zero(tmp_path: Path) -> None:
    staging_root = tmp_path / "staging"
    spec = _spec(staging_root)
    key = tmp_path / "node.key"
    key.write_bytes(b"k" * 32)
    client = _FakeClient(lambda container: container.exited.wait(10.0) or 0)
    supervisor = _supervisor(spec, client, staging_root)
    supervisor.launch(spec, cwd=spec.filesystem.sandbox_root, environment=_environment(key), pipe_stdio=True)
    supervisor.cancel(zero_timeout=2.0)
    assert client.created[0].killed and supervisor.wait_for_zero(timeout=1.0)
    assert supervisor.termination_reason == "cancelled"
    supervisor.close()


def test_supervisor_requires_a_readable_capability_key(tmp_path: Path) -> None:
    staging_root = tmp_path / "staging"
    spec = _spec(staging_root)
    supervisor = _supervisor(spec, _FakeClient(), staging_root)
    environment = _environment(tmp_path / "missing.key")
    with pytest.raises(Exception, match="capability key"):
        supervisor.launch(spec, cwd=spec.filesystem.sandbox_root, environment=environment)
    supervisor.close()


# ----- stdio -----------------------------------------------------------------------


def test_attach_demultiplexer_splits_stdout_and_stderr_frames() -> None:
    stream = _FakeAttachStream()
    demux = AttachDemultiplexer(stream, name="test-attach")
    stream.emit(1, b"out-1")
    stream.emit(2, b"err-1")
    stream.emit(1, b"out-2")
    stream.finish()
    assert demux.stdout.read() == b"out-1out-2"
    assert demux.stderr.read() == b"err-1"
    assert demux.join(timeout=1.0) and demux.error is None
    demux.stdin.write(b"payload")
    demux.stdin.close()
    assert bytes(stream.stdin) == b"payload" and stream.stdin_closed.is_set()
    demux.close()


# ----- transport end to end ----------------------------------------------------------


class _FilesystemAuthority:
    def __init__(self, source: Path, published: Path) -> None:
        self.source = source
        self.published = published

    async def stage(self, *, reference: ArtifactRef, destination: Path) -> None:
        await asyncio.to_thread(_copy_file, self.source, destination)

    async def publish(self, *, reference: ArtifactRef, source: Path) -> None:
        await asyncio.to_thread(_copy_file, source, self.published)


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def _storage_key(owner_id: uuid.UUID, tenant_id: str, digest: str) -> str:
    tenant = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
    return f"v1/tenant/{tenant}/owner/{owner_id}/sha256/{digest[:2]}/{digest}"


def _reference(
    owner_id: uuid.UUID, operation_id: uuid.UUID, payload: bytes, *, kind: str, media_type: str
) -> ArtifactRef:
    digest = hashlib.sha256(payload).hexdigest()
    return ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-container",
        kind=kind,
        sha256=digest,
        size_bytes=len(payload),
        media_type=media_type,
        storage_key=_storage_key(owner_id, "tenant-container", digest),
        created_by_operation_id=operation_id,
        retention_class="ephemeral",
    )


def _run_transport(tmp_path: Path, *, worker_output: bytes, declared_output: bytes) -> tuple[_FakeClient, object]:
    owner_id, operation_id = uuid.uuid4(), uuid.uuid4()
    input_payload = b"<html>container input</html>"
    input_path = tmp_path / "authority-input.html"
    input_path.write_bytes(input_payload)
    input_reference = _reference(owner_id, operation_id, input_payload, kind="slide-html", media_type="text/html")
    output_reference = _reference(owner_id, operation_id, declared_output, kind="slide-png", media_type="image/png")
    job = IsolatedWorkerJob(
        role="render",
        operation_id=operation_id,
        attempt_token="container-attempt",
        fencing_token=5,
        input_digest=input_reference.sha256,
        input_artifact=input_reference,
        capability="test.capability",
        deadline_epoch_seconds=int(time.time()) + 30,
    )
    result = {
        "schemaVersion": "ppt-isolated-worker-result/v1",
        "role": job.role,
        "operationId": str(job.operation_id),
        "attemptTokenSha256": hashlib.sha256(b"container-attempt").hexdigest(),
        "fencingToken": job.fencing_token,
        "inputDigest": job.input_digest,
        "ok": True,
        "errorCode": None,
        "outputArtifact": output_reference.to_wire(),
        "executionEvidence": {},
    }
    seen: dict[str, object] = {}

    def worker(container: _FakeContainer) -> int:
        environment = dict(container.request.environment)
        wire = json.loads(container.read_stdin())
        assert wire["operationId"] == str(operation_id)
        input_root = environment["PUBLIC_WORKER_INPUT_STORE_ROOT"]
        staged = container.parent_path(input_root + "/" + wire["inputArtifact"]["storageKey"])
        assert staged.read_bytes() == input_payload
        key_copy = container.parent_path(environment["PUBLIC_WORKER_CAPABILITY_KEY_FILE"])
        seen["key"] = key_copy.read_bytes()
        seen["key_path"] = key_copy
        output_root = environment["PUBLIC_WORKER_OUTPUT_STORE_ROOT"]
        output = container.parent_path(output_root + "/" + output_reference.storage_key)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(worker_output)
        container.stream.emit(2, b"e" * (1024 * 1024 + 4096))
        container.stream.emit(1, json.dumps(result, separators=(",", ":")).encode())
        return 0

    client = _FakeClient(worker)
    key = tmp_path / "node.key"
    key.write_bytes(b"n" * 48)
    published = tmp_path / "published" / "output.png"
    runtime = create_container_trusted_worker_executor_runtime(
        staging_root=tmp_path / "staging",
        node_id="node-container",
        artifact_authority=_FilesystemAuthority(input_path, published),
        environment={
            "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "container",
            "PUBLIC_TRUSTED_WORKER_CONTAINER_IMAGE": IMAGE,
            "PUBLIC_TRUSTED_WORKER_CONTAINER_USER": "10001:10001",
            "PUBLIC_TRUSTED_WORKER_CONTAINER_STAGING_MOUNT_ROOT": str(MOUNT_ROOT),
        },
        runtime_client=client,
    )

    async def run() -> object:
        try:
            return await runtime.dispatcher.execute(
                job=job,
                presentation_id=uuid.uuid4(),
                payload=json.dumps(job.to_wire(), separators=(",", ":")).encode(),
                command=(
                    str(Path(tmp_path.anchor) / "usr" / "local" / "bin" / "python"),
                    "-m",
                    "workers.public_worker_runtime",
                ),
                environment=_environment(key),
            )
        finally:
            await runtime.close()

    try:
        outcome: object = asyncio.run(run())
    except TrustedWorkerWireError as error:
        outcome = error
    seen["published"] = published
    seen["runtime"] = runtime
    return client, (outcome, seen)


def test_container_transport_publishes_only_digest_verified_output(tmp_path: Path) -> None:
    payload = b"container-output"
    client, (result, seen) = _run_transport(tmp_path, worker_output=payload, declared_output=payload)
    assert not isinstance(result, Exception), result
    assert json.loads(result.stdout)["ok"] is True
    assert seen["published"].read_bytes() == payload
    assert seen["key"] == b"n" * 48
    assert not seen["key_path"].exists(), "per-job key copy must be unlinked at close"
    probe, job_container = client.created
    assert not probe.started and probe.removed
    assert job_container.started and job_container.removed
    assert job_container.request.labels["presenton.role"] == "render"
    metrics = result.resource_metrics
    assert metrics["enforcementLayer"] == "linux-container-cgroup"
    assert metrics["isolationAttestation"]["productionEligible"] is True
    assert metrics["isolationAttestation"]["backend"] == "linux-container-runtime"
    assert metrics["containerIsolationEvidence"]["productionEligible"] is True
    assert seen["runtime"].isolation_attestation["productionEligible"] is True


def test_container_transport_rejects_output_that_does_not_match_the_digest(tmp_path: Path) -> None:
    client, (result, seen) = _run_transport(tmp_path, worker_output=b"tampered", declared_output=b"declared")
    assert isinstance(result, TrustedWorkerWireError)
    assert result.code == "trusted_worker_artifact_digest_mismatch"
    assert not seen["published"].exists()
    assert all(container.removed for container in client.created)


# ----- composition ---------------------------------------------------------------------


def _composition_environment(**overrides: str) -> dict[str, str]:
    return {
        "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "container",
        "PUBLIC_TRUSTED_WORKER_CONTAINER_IMAGE": IMAGE,
        "PUBLIC_TRUSTED_WORKER_CONTAINER_USER": "10001:10001",
        "PUBLIC_TRUSTED_WORKER_CONTAINER_STAGING_MOUNT_ROOT": str(MOUNT_ROOT),
        **overrides,
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "windows-local"}, "SANDBOX_BACKEND=container"),
        ({"PUBLIC_TRUSTED_WORKER_CONTAINER_IMAGE": "ppt-worker:latest"}, "digest-pinned"),
        ({"PUBLIC_TRUSTED_WORKER_CONTAINER_USER": "0:0"}, "non-root"),
        ({"PUBLIC_TRUSTED_WORKER_LOCAL_POLICY": "development"}, "local development policy"),
        ({"PUBLIC_TRUSTED_WORKER_CONTAINER_STAGING_MOUNT_ROOT": "relative/root"}, "staging mount is invalid"),
    ],
)
def test_container_composition_fails_closed_on_configuration(
    tmp_path: Path, overrides: dict[str, str], message: str
) -> None:
    with pytest.raises(IsolationUnavailableError, match=message):
        create_container_trusted_worker_executor_runtime(
            staging_root=tmp_path / "staging",
            node_id="node",
            artifact_authority=_FilesystemAuthority(tmp_path / "in", tmp_path / "out"),
            environment=_composition_environment(**overrides),
            runtime_client=_FakeClient(),
        )


def test_container_composition_refuses_a_runtime_whose_probe_is_unproven(tmp_path: Path) -> None:
    client = _FakeClient(inspect_override=lambda value: value["HostConfig"].__setitem__("ReadonlyRootfs", False))
    with pytest.raises(IsolationUnavailableError, match="root-filesystem-read-only=unsupported"):
        create_container_trusted_worker_executor_runtime(
            staging_root=tmp_path / "staging",
            node_id="node",
            artifact_authority=_FilesystemAuthority(tmp_path / "in", tmp_path / "out"),
            environment=_composition_environment(),
            runtime_client=client,
        )
    assert client.created[0].removed and not client.created[0].started
    assert not (tmp_path / "staging" / ".isolation-probe" / "probe.key").exists()


def test_windows_identity_mapping_is_rejected_without_an_explicit_mount_root(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows drive-letter staging roots only exist on Windows")
    environment = _composition_environment()
    environment.pop("PUBLIC_TRUSTED_WORKER_CONTAINER_STAGING_MOUNT_ROOT")
    with pytest.raises(IsolationUnavailableError, match="POSIX absolute"):
        create_container_trusted_worker_executor_runtime(
            staging_root=tmp_path / "staging",
            node_id="node",
            artifact_authority=_FilesystemAuthority(tmp_path / "in", tmp_path / "out"),
            environment=environment,
            runtime_client=_FakeClient(),
        )
