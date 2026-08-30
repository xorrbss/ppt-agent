from __future__ import annotations

import pathlib
import uuid

import pytest

from services.trusted_executor.contracts import (
    ExecutionIdentity,
    ResourceLimits,
    SandboxLaunchSpec,
    SandboxPolicy,
    TrustedExecutorContractError,
)
from services.trusted_executor.isolation_policy import (
    IsolationPolicyError,
    IsolationUnavailableError,
    SandboxFilesystemSpec,
    probe_platform_isolation,
)


def _identity() -> ExecutionIdentity:
    return ExecutionIdentity(
        operation_id=uuid.uuid4(),
        tenant_id="tenant-a",
        owner_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
    )


def _filesystem() -> SandboxFilesystemSpec:
    return SandboxFilesystemSpec(
        sandbox_root=pathlib.Path("C:/sandbox/op-a"),
        input_root=pathlib.Path("C:/sandbox/op-a/input"),
        temporary_root=pathlib.Path("C:/sandbox/op-a/tmp"),
        staged_inputs=(pathlib.Path("C:/sandbox/op-a/input/source.json"),),
    )


def test_launch_contract_is_operation_bound_and_strips_secrets() -> None:
    spec = SandboxLaunchSpec(
        identity=_identity(),
        role="render",
        executable=pathlib.Path("C:/Windows/System32/cmd.exe"),
        arguments=("/c", "exit", "0"),
        limits=ResourceLimits(
            memory_bytes=1024 * 1024,
            process_limit=4,
            deadline_seconds=5,
            temporary_bytes=512 * 1024,
        ),
        policy=SandboxPolicy(),
        filesystem=_filesystem(),
    )

    environment = spec.environment(
        {"PATH": "safe", "PROVIDER_API_KEY": "secret", "SESSION": "cookie"}
    )

    assert environment["PATH"] == "safe"
    assert environment["TRUSTED_EXECUTOR_OPERATION_ID"] == str(
        spec.identity.operation_id
    )
    assert "PROVIDER_API_KEY" not in environment
    assert "SESSION" not in environment

    with pytest.raises(TrustedExecutorContractError, match="network"):
        SandboxPolicy(network_mode="bridge")  # type: ignore[arg-type]


def test_isolation_policy_requires_os_evidence_and_rejects_invalid_staging() -> None:
    report = probe_platform_isolation(platform="nt")
    assert report.resource_limits_supported is True
    with pytest.raises(IsolationUnavailableError, match="network-none=unsupported"):
        SandboxPolicy().require_os_enforcement(report)

    with pytest.raises(IsolationPolicyError, match="inside the sandbox root"):
        SandboxFilesystemSpec(
            sandbox_root=pathlib.Path("C:/sandbox/op-a"),
            input_root=pathlib.Path("C:/sandbox/other/input"),
            temporary_root=pathlib.Path("C:/sandbox/op-a/tmp"),
            staged_inputs=(pathlib.Path("C:/sandbox/other/input/source.json"),),
        )
