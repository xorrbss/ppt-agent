"""Production-eligible composition for ``PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND=container``.

Environment knobs (read once at composition time):

- ``PUBLIC_TRUSTED_WORKER_CONTAINER_IMAGE`` — digest-pinned worker image
  (``name@sha256:<64 hex>``); the image must carry python, node, Chrome and the
  ``PUBLIC_<ROLE>_WORKER_COMMAND_JSON`` executables at container paths.
- ``PUBLIC_TRUSTED_WORKER_CONTAINER_USER`` — numeric ``uid[:gid]`` (non-root) the
  worker runs as; the shared output bind must be writable by it.
- ``PUBLIC_TRUSTED_WORKER_CONTAINER_STAGING_MOUNT_ROOT`` — optional container
  path for the staging root.  Omitted in production, where the staging root is
  mounted at the same POSIX absolute path in both parent and container.
- ``PUBLIC_TRUSTED_WORKER_CONTAINER_SHM_BYTES`` — optional ``/dev/shm`` size.

Before any job runs, a probe container is created (never started) with the
same request builder, inspected, and removed.  The report built from that
inspect output is the launch-admission report the transport checks; each job
is additionally verified from its own container's inspect output in
``ContainerProcessSupervisor``.  Nothing declared by configuration can make a
report production eligible.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from services.trusted_executor.composition import (
    TrustedWorkerExecutorRuntime,
    default_trusted_worker_scheduler_policy,
)
from services.trusted_executor.container_isolation_evidence import (
    ContainerIsolationExpectation,
    build_container_isolation_report,
)
from services.trusted_executor.container_runtime import (
    ContainerImageSettings,
    ContainerPathMapping,
    ContainerRuntimeClient,
    ContainerRuntimeError,
    build_container_create_request,
)
from services.trusted_executor.container_supervisor import ContainerSupervisorFactory
from services.trusted_executor.container_supervisor_support import unproven_controls
from services.trusted_executor.contracts import (
    ExecutionIdentity,
    ResourceLimits,
    SandboxFilesystemSpec,
    SandboxLaunchSpec,
    SandboxPolicy,
)
from services.trusted_executor.isolation_policy import (
    IsolationUnavailableError,
    PlatformIsolationReport,
)
from services.trusted_executor.product_adapter import TrustedExecutorProductAdapter
from services.trusted_executor.weighted_scheduler import SchedulerPolicy, WeightedScheduler
from services.trusted_executor.wire_transport import (
    SupervisedStdioWireTransport,
    TrustedWorkerArtifactAuthority,
)
from services.trusted_executor.worker_dispatcher import TrustedWorkerDispatcher


CONTAINER_BACKEND_VALUE = "container"
IMAGE_ENV = "PUBLIC_TRUSTED_WORKER_CONTAINER_IMAGE"
USER_ENV = "PUBLIC_TRUSTED_WORKER_CONTAINER_USER"
MOUNT_ROOT_ENV = "PUBLIC_TRUSTED_WORKER_CONTAINER_STAGING_MOUNT_ROOT"
SHM_BYTES_ENV = "PUBLIC_TRUSTED_WORKER_CONTAINER_SHM_BYTES"
_PINNED_IMAGE = re.compile(r"\A[^\s@]+@sha256:[0-9a-f]{64}\Z")
_PROBE_DIRECTORY = ".isolation-probe"


def container_image_settings_from_environment(
    source: Mapping[str, str],
) -> ContainerImageSettings:
    image = source.get(IMAGE_ENV, "").strip()
    if not _PINNED_IMAGE.match(image):
        raise IsolationUnavailableError(
            f"{IMAGE_ENV} must be a digest-pinned image reference (name@sha256:<digest>)"
        )
    user = source.get(USER_ENV, "").strip()
    shm_value = source.get(SHM_BYTES_ENV, "").strip()
    try:
        settings = ContainerImageSettings(
            image=image,
            user=user,
            **({"shm_bytes": int(shm_value)} if shm_value else {}),
        )
    except (ContainerRuntimeError, ValueError) as error:
        raise IsolationUnavailableError(
            f"{USER_ENV} must be a numeric non-root uid[:gid] and {SHM_BYTES_ENV} a positive integer"
        ) from error
    return settings


def container_path_mapping_from_environment(
    source: Mapping[str, str], staging_root: Path
) -> ContainerPathMapping:
    mount_root = source.get(MOUNT_ROOT_ENV, "").strip()
    try:
        if mount_root:
            return ContainerPathMapping(
                parent_root=staging_root, container_root=PurePosixPath(mount_root)
            )
        return ContainerPathMapping.identity(staging_root)
    except ContainerRuntimeError as error:
        raise IsolationUnavailableError(
            f"container staging mount is invalid: {error}"
        ) from error


def probe_container_isolation(
    *,
    client: ContainerRuntimeClient,
    settings: ContainerImageSettings,
    mapping: ContainerPathMapping,
    staging_root: Path,
) -> PlatformIsolationReport:
    """Create, inspect and remove a never-started container to obtain evidence."""

    sandbox_root = Path(staging_root) / _PROBE_DIRECTORY
    input_root = sandbox_root / "input"
    temporary_root = sandbox_root / "tmp"
    for directory in (input_root, temporary_root, sandbox_root / "output"):
        directory.mkdir(parents=True, exist_ok=True)
    key_path = sandbox_root / "probe.key"
    key_path.write_bytes(b"\0" * 32)
    limits = ResourceLimits(
        memory_bytes=256 * 1024 * 1024,
        process_limit=8,
        deadline_seconds=30,
        temporary_bytes=16 * 1024 * 1024,
    )
    spec = SandboxLaunchSpec(
        identity=ExecutionIdentity(
            operation_id=uuid.uuid4(),
            tenant_id="isolation-probe",
            owner_id=uuid.uuid4(),
            presentation_id=uuid.uuid4(),
        ),
        role="render",
        # Never started: the command only has to be a syntactically absolute path.
        executable=Path(Path(staging_root).anchor) / "bin" / "true",
        arguments=(),
        limits=limits,
        policy=SandboxPolicy(),
        filesystem=SandboxFilesystemSpec(
            sandbox_root=sandbox_root,
            input_root=input_root,
            temporary_root=temporary_root,
            staged_inputs=(input_root / "probe",),
        ),
    )
    request = build_container_create_request(
        spec,
        mapping=mapping,
        settings=settings,
        capability_key_parent_path=key_path,
        environment={},
        labels={"presenton.isolation-probe": "1"},
    )
    try:
        handle = client.create(request)
    except ContainerRuntimeError as error:
        raise IsolationUnavailableError("container runtime probe create failed") from error
    try:
        inspect = handle.inspect()
    except ContainerRuntimeError as error:
        raise IsolationUnavailableError("container runtime probe inspect failed") from error
    finally:
        try:
            handle.remove()
        except ContainerRuntimeError:
            pass
        key_path.unlink(missing_ok=True)
    return build_container_isolation_report(
        inspect,
        ContainerIsolationExpectation(
            limits=limits,
            policy=spec.policy,
            input_root=mapping.to_container(input_root),
            temporary_root=mapping.to_container(temporary_root),
            input_source=input_root.as_posix(),
        ),
    )


def create_container_trusted_worker_executor_runtime(
    *,
    staging_root: Path,
    node_id: str,
    artifact_authority: TrustedWorkerArtifactAuthority,
    environment: Mapping[str, str] | None = None,
    scheduler_policy: SchedulerPolicy | None = None,
    zero_timeout_seconds: float = 5.0,
    runtime_client: ContainerRuntimeClient | None = None,
) -> TrustedWorkerExecutorRuntime:
    """Build Scheduler -> Adapter -> container-backed transport -> Dispatcher."""

    root = Path(staging_root)
    if not root.is_absolute() or not node_id.strip():
        raise ValueError("trusted worker runtime configuration is invalid")
    source = os.environ if environment is None else environment
    backend = source.get("PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND", "").strip().lower()
    if backend != CONTAINER_BACKEND_VALUE:
        raise IsolationUnavailableError(
            f"PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND={CONTAINER_BACKEND_VALUE} is required"
        )
    if source.get("PUBLIC_TRUSTED_WORKER_LOCAL_POLICY", "").strip():
        raise IsolationUnavailableError(
            "the container backend does not accept a local development policy"
        )
    settings = container_image_settings_from_environment(source)
    mapping = container_path_mapping_from_environment(source, root)
    if runtime_client is None:
        from services.trusted_executor.container_runtime_docker import (
            DockerSdkContainerRuntime,
        )

        try:
            runtime_client = DockerSdkContainerRuntime.from_environment(source)
        except ContainerRuntimeError as error:
            raise IsolationUnavailableError(str(error)) from error
    root.mkdir(parents=True, exist_ok=True)
    isolation_report = probe_container_isolation(
        client=runtime_client, settings=settings, mapping=mapping, staging_root=root
    )
    if not isolation_report.production_eligible:
        raise IsolationUnavailableError(
            "container runtime did not prove every isolation control: "
            + "; ".join(unproven_controls(isolation_report))
        )
    transport = SupervisedStdioWireTransport(
        artifact_authority=artifact_authority,
        isolation_report=isolation_report,
        allow_local_development_isolation_gap=False,
        zero_timeout_seconds=zero_timeout_seconds,
        supervisor_factory=ContainerSupervisorFactory(
            client=runtime_client, settings=settings, mapping=mapping
        ),
    )
    adapter = TrustedExecutorProductAdapter(
        WeightedScheduler(scheduler_policy or default_trusted_worker_scheduler_policy())
    )
    dispatcher = TrustedWorkerDispatcher(
        adapter=adapter, transport=transport, staging_root=root, node_id=node_id
    )
    return TrustedWorkerExecutorRuntime(
        dispatcher=dispatcher, transport=transport, isolation_report=isolation_report
    )


__all__ = [
    "CONTAINER_BACKEND_VALUE",
    "container_image_settings_from_environment",
    "container_path_mapping_from_environment",
    "create_container_trusted_worker_executor_runtime",
    "probe_container_isolation",
]
