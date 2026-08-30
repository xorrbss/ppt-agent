"""Fail-closed composition factory for the product trusted-worker wire path."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from collections.abc import Mapping
from typing import Protocol

from services.trusted_executor.isolation_policy import (
    IsolationUnavailableError,
    probe_platform_isolation,
)
from services.trusted_executor.process_supervisor import probe_process_supervisor
from services.trusted_executor.product_adapter import TrustedExecutorProductAdapter
from services.trusted_executor.weighted_scheduler import (
    SchedulerCaps,
    SchedulerPolicy,
    WeightedScheduler,
)
from services.trusted_executor.worker_dispatcher import TrustedWorkerDispatcher
from services.trusted_executor.wire_transport import (
    SupervisedStdioWireTransport,
    TrustedWorkerArtifactAuthority,
)


class _ClosableTransport(Protocol):
    async def close(self) -> None: ...


def default_trusted_worker_scheduler_policy() -> SchedulerPolicy:
    return SchedulerPolicy(
        caps=SchedulerCaps(
            global_cap=4,
            node_cap=4,
            tenant_cap=2,
            owner_cap=2,
            presentation_cap=1,
        )
    )


@dataclass(slots=True)
class TrustedWorkerExecutorRuntime:
    dispatcher: TrustedWorkerDispatcher
    transport: _ClosableTransport
    _closed: bool = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.transport.close()

    async def __aenter__(self) -> "TrustedWorkerExecutorRuntime":
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.close()


def create_trusted_worker_executor_runtime(
    *,
    staging_root: Path,
    node_id: str,
    artifact_authority: TrustedWorkerArtifactAuthority,
    environment: Mapping[str, str] | None = None,
    scheduler_policy: SchedulerPolicy | None = None,
    zero_timeout_seconds: float = 5.0,
) -> TrustedWorkerExecutorRuntime:
    """Build Scheduler -> Adapter -> concrete wire transport -> Dispatcher.

    Windows local execution is available only under the explicit development
    policy.  A normal public profile remains fail-closed because a Job Object
    cannot prove network-none or filesystem namespace controls.
    """

    root = Path(staging_root)
    if not root.is_absolute() or not node_id.strip():
        raise ValueError("trusted worker runtime configuration is invalid")
    source = os.environ if environment is None else environment
    backend = source.get("PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND", "").strip().lower()
    if backend != "windows-local":
        raise IsolationUnavailableError(
            "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND=windows-local is required"
        )
    local_policy = source.get("PUBLIC_TRUSTED_WORKER_LOCAL_POLICY", "").strip().lower()
    if local_policy != "development":
        raise IsolationUnavailableError(
            "windows-local requires PUBLIC_TRUSTED_WORKER_LOCAL_POLICY=development; "
            "normal public profiles require stronger network and filesystem isolation"
        )
    supervisor_capability = probe_process_supervisor()
    if (
        not supervisor_capability.enforcement_ready
        or supervisor_capability.backend != "windows-job-object"
    ):
        raise IsolationUnavailableError(supervisor_capability.reason)
    isolation_report = probe_platform_isolation()
    if isolation_report.backend != "windows-job-object-only":
        raise IsolationUnavailableError(
            "windows-local trusted workers require Windows Job Object supervision"
        )
    root.mkdir(parents=True, exist_ok=True)
    transport = SupervisedStdioWireTransport(
        artifact_authority=artifact_authority,
        isolation_report=isolation_report,
        allow_local_development_isolation_gap=True,
        zero_timeout_seconds=zero_timeout_seconds,
    )
    adapter = TrustedExecutorProductAdapter(
        WeightedScheduler(
            scheduler_policy or default_trusted_worker_scheduler_policy()
        )
    )
    dispatcher = TrustedWorkerDispatcher(
        adapter=adapter,
        transport=transport,
        staging_root=root,
        node_id=node_id,
    )
    return TrustedWorkerExecutorRuntime(
        dispatcher=dispatcher,
        transport=transport,
    )


def create_configured_trusted_worker_executor_runtime(
    *,
    artifact_authority: TrustedWorkerArtifactAuthority,
    environment: Mapping[str, str] | None = None,
    scheduler_policy: SchedulerPolicy | None = None,
    zero_timeout_seconds: float = 10.0,
) -> TrustedWorkerExecutorRuntime:
    source = os.environ if environment is None else environment
    staging_value = source.get("PUBLIC_TRUSTED_WORKER_STAGING_ROOT", "").strip()
    node_id = source.get("PUBLIC_WORKER_NODE_ID", "").strip()
    staging_root = Path(staging_value)
    if not staging_value or not staging_root.is_absolute() or not node_id:
        raise IsolationUnavailableError(
            "absolute PUBLIC_TRUSTED_WORKER_STAGING_ROOT and PUBLIC_WORKER_NODE_ID are required"
        )
    return create_trusted_worker_executor_runtime(
        staging_root=staging_root,
        node_id=node_id,
        artifact_authority=artifact_authority,
        environment=source,
        scheduler_policy=scheduler_policy,
        zero_timeout_seconds=zero_timeout_seconds,
    )
__all__ = [
    "TrustedWorkerExecutorRuntime",
    "create_configured_trusted_worker_executor_runtime",
    "create_trusted_worker_executor_runtime",
    "default_trusted_worker_scheduler_policy",
]
