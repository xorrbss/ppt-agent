"""Scheduler-facing product work items for trusted render/export execution.

This module deliberately owns only the in-process handoff from an already
admitted product work item to a trusted-executor launcher.  It does not claim
the database lease, artifact staging, worker stdin/stdout protocol, or publish
authority.  Those remain at the product seam documented beside this module.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from services.trusted_executor.contracts import SandboxLaunchSpec
from services.trusted_executor.weighted_scheduler import (
    Reservation,
    ScheduledOperation,
    WeightedScheduler,
)


class ProductAdapterError(RuntimeError):
    """A product work item cannot safely enter trusted execution."""


class ProductWorkItemNotFound(ProductAdapterError):
    """The scheduler returned an operation that has no registered work item."""


ProductExecutionStatus = Literal[
    "completed",
    "cancelled",
    "deadline_exceeded",
    "memory_limit_exceeded",
]


@dataclass(frozen=True, slots=True)
class TrustedExecutorWorkItem:
    """One render/export launch bound to the admission identity and scheduler."""

    scheduling: ScheduledOperation
    launch_spec: SandboxLaunchSpec

    def __post_init__(self) -> None:
        identity = self.launch_spec.identity
        if self.scheduling.operation_id != str(identity.operation_id):
            raise ProductAdapterError("scheduled operation does not match launch identity")
        if self.scheduling.tenant_id != identity.tenant_id:
            raise ProductAdapterError("scheduled tenant does not match launch identity")
        if self.scheduling.owner_id != str(identity.owner_id):
            raise ProductAdapterError("scheduled owner does not match launch identity")
        if self.scheduling.presentation_id != str(identity.presentation_id):
            raise ProductAdapterError("scheduled presentation does not match launch identity")
        if self.launch_spec.role not in {"render", "export"}:
            raise ProductAdapterError("trusted executor role is invalid")


@dataclass(frozen=True, slots=True)
class ProductExecutionResult:
    """Bounded process outcome; worker output remains on the product wire seam."""

    status: ProductExecutionStatus
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class ExecutedProductWorkItem:
    work_item: TrustedExecutorWorkItem
    reservation: Reservation
    result: ProductExecutionResult


ProductWorkItemExecutor = Callable[
    [TrustedExecutorWorkItem], Awaitable[ProductExecutionResult]
]


class TrustedExecutorProductAdapter:
    """Register and dispatch product work strictly through ``WeightedScheduler``.

    A dispatcher invokes :meth:`execute_next`; it may execute any eligible
    queued item, which is essential for fairness.  Product callers must not
    submit an item and immediately launch it out of order.
    """

    def __init__(self, scheduler: WeightedScheduler) -> None:
        self._scheduler = scheduler
        self._work_items: dict[str, TrustedExecutorWorkItem] = {}

    def submit(self, work_item: TrustedExecutorWorkItem) -> None:
        operation_id = work_item.scheduling.operation_id
        if operation_id in self._work_items:
            raise ProductAdapterError("product work item is already registered")
        self._scheduler.submit(work_item.scheduling)
        self._work_items[operation_id] = work_item

    def cancel_pending(self, operation_id: str) -> bool:
        """Remove only queued work; launched process cancellation is launcher-owned."""

        cancelled = self._scheduler.cancel_pending(operation_id)
        if cancelled:
            self._work_items.pop(operation_id, None)
        return cancelled

    async def execute_next(
        self,
        executor: ProductWorkItemExecutor,
    ) -> ExecutedProductWorkItem | None:
        """Reserve one fair, cap-eligible item and release it on every exit path."""

        reservation = self._scheduler.reserve_next()
        if reservation is None:
            return None
        operation_id = reservation.operation.operation_id
        work_item = self._work_items.pop(operation_id, None)
        if work_item is None:
            self._scheduler.release(reservation)
            raise ProductWorkItemNotFound("scheduled product work item was not registered")
        try:
            result = await executor(work_item)
            return ExecutedProductWorkItem(
                work_item=work_item,
                reservation=reservation,
                result=result,
            )
        finally:
            if not self._scheduler.release(reservation):
                raise ProductAdapterError("trusted executor reservation release failed")


__all__ = [
    "ExecutedProductWorkItem",
    "ProductAdapterError",
    "ProductExecutionResult",
    "ProductExecutionStatus",
    "ProductWorkItemExecutor",
    "ProductWorkItemNotFound",
    "TrustedExecutorProductAdapter",
    "TrustedExecutorWorkItem",
]
