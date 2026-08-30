"""Fail-closed trusted-executor process launcher for product work items."""

from __future__ import annotations

import asyncio
import pathlib
from collections.abc import Callable

from services.trusted_executor.isolation_policy import (
    PlatformIsolationReport,
    probe_platform_isolation,
)
from services.trusted_executor.process_supervisor import (
    OperationProcessSupervisor,
    ProcessDeadlineExceeded,
    ProcessMemoryLimitExceeded,
    ProcessSupervisorError,
)
from services.trusted_executor.product_adapter import (
    ProductExecutionResult,
    TrustedExecutorWorkItem,
)


class TrustedExecutorLaunchError(RuntimeError):
    """A product worker could not be launched with the required controls."""


SupervisorFactory = Callable[..., OperationProcessSupervisor]


class TrustedExecutorProductLauncher:
    """Launch one already-scheduled product work item under OS isolation.

    The platform report is checked before creating a process supervisor.  On
    current Windows hosts this correctly fails closed: Job Objects alone do
    not prove network, read-only filesystem, staged-input, or tmp quotas.
    """

    def __init__(
        self,
        *,
        isolation_report: PlatformIsolationReport | None = None,
        supervisor_factory: SupervisorFactory = OperationProcessSupervisor,
        zero_timeout_seconds: float = 5.0,
    ) -> None:
        if zero_timeout_seconds <= 0:
            raise ValueError("zero timeout must be positive")
        self._isolation_report = isolation_report or probe_platform_isolation()
        self._supervisor_factory = supervisor_factory
        self._zero_timeout_seconds = zero_timeout_seconds

    async def execute(self, work_item: TrustedExecutorWorkItem) -> ProductExecutionResult:
        """Launch then wait for the operation process tree without bypassing policy."""

        spec = work_item.launch_spec
        # ``require_os_enforcement`` also rejects absent filesystem staging.
        spec.require_os_enforcement(self._isolation_report)
        supervisor = self._supervisor_factory(spec.identity, spec.limits)
        process = None
        try:
            process = await asyncio.to_thread(
                supervisor.launch,
                spec,
                cwd=pathlib.Path(spec.filesystem.sandbox_root),
            )
            try:
                exit_code = await asyncio.to_thread(
                    process.wait,
                    spec.limits.deadline_seconds + 1.0,
                )
            except ProcessMemoryLimitExceeded:
                await self._verify_termination(supervisor)
                return ProductExecutionResult(status="memory_limit_exceeded")
            except ProcessDeadlineExceeded:
                await self._verify_termination(supervisor)
                return ProductExecutionResult(status="deadline_exceeded")
            except TimeoutError as error:
                await asyncio.to_thread(supervisor.cancel, zero_timeout=self._zero_timeout_seconds)
                raise TrustedExecutorLaunchError("trusted executor process did not finish by its deadline") from error

            if not await asyncio.to_thread(
                supervisor.wait_for_zero,
                timeout=self._zero_timeout_seconds,
            ):
                await asyncio.to_thread(supervisor.cancel, zero_timeout=self._zero_timeout_seconds)
                raise TrustedExecutorLaunchError("trusted executor descendants did not exit")
            return ProductExecutionResult(status="completed", exit_code=exit_code)
        except asyncio.CancelledError:
            if process is not None:
                await asyncio.to_thread(supervisor.cancel, zero_timeout=self._zero_timeout_seconds)
            raise
        except ProcessSupervisorError as error:
            raise TrustedExecutorLaunchError("trusted executor process supervision failed") from error
        finally:
            await asyncio.to_thread(supervisor.close)

    async def _verify_termination(self, supervisor: OperationProcessSupervisor) -> None:
        reason = await asyncio.to_thread(
            supervisor.wait_for_termination,
            timeout=self._zero_timeout_seconds,
        )
        if reason is None or not supervisor.descendant_zero_verified:
            raise TrustedExecutorLaunchError("trusted executor termination was not proven")


__all__ = [
    "TrustedExecutorLaunchError",
    "TrustedExecutorProductLauncher",
]
