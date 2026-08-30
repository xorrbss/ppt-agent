from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

from services.trusted_executor.contracts import (
    ExecutionIdentity,
    ResourceLimits,
    SandboxLaunchSpec,
    SandboxPolicy,
)
from services.trusted_executor.isolation_policy import SandboxFilesystemSpec
from services.trusted_executor.product_adapter import (
    ProductExecutionResult,
    TrustedExecutorProductAdapter,
    TrustedExecutorWorkItem,
)
from services.trusted_executor.weighted_scheduler import (
    ScheduledOperation,
    SchedulerCaps,
    SchedulerPolicy,
    WeightedScheduler,
)


def _work_item(tmp_path, *, operation_id: uuid.UUID, owner_id: uuid.UUID, presentation_id: uuid.UUID, weight: int) -> TrustedExecutorWorkItem:
    sandbox_root = tmp_path / str(operation_id)
    input_root = sandbox_root / "inputs"
    staged_input = input_root / "input.json"
    filesystem = SandboxFilesystemSpec(
        sandbox_root=sandbox_root,
        input_root=input_root,
        temporary_root=sandbox_root / "tmp",
        staged_inputs=(staged_input,),
    )
    identity = ExecutionIdentity(
        operation_id=operation_id,
        tenant_id="tenant-a",
        owner_id=owner_id,
        presentation_id=presentation_id,
    )
    return TrustedExecutorWorkItem(
        scheduling=ScheduledOperation(
            operation_id=str(operation_id),
            node_id="node-a",
            tenant_id="tenant-a",
            owner_id=str(owner_id),
            presentation_id=str(presentation_id),
            weight=weight,
        ),
        launch_spec=SandboxLaunchSpec(
            identity=identity,
            role="render",
            executable=Path(sys.executable).resolve(),
            arguments=(),
            limits=ResourceLimits(
                memory_bytes=256 * 1024 * 1024,
                process_limit=2,
                deadline_seconds=30,
            ),
            policy=SandboxPolicy(),
            filesystem=filesystem,
        ),
    )


def test_product_adapter_dispatches_product_render_work_through_weighted_scheduler(tmp_path) -> None:
    adapter = TrustedExecutorProductAdapter(
        WeightedScheduler(
            SchedulerPolicy(
                caps=SchedulerCaps(
                    global_cap=1,
                    node_cap=1,
                    tenant_cap=1,
                    owner_cap=1,
                    presentation_cap=1,
                )
            )
        )
    )
    lower_share = _work_item(
        tmp_path,
        operation_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
        weight=1,
    )
    higher_share = _work_item(
        tmp_path,
        operation_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
        weight=100,
    )
    adapter.submit(lower_share)
    adapter.submit(higher_share)
    executed: list[str] = []

    async def execute(work_item: TrustedExecutorWorkItem) -> ProductExecutionResult:
        executed.append(work_item.scheduling.operation_id)
        return ProductExecutionResult(status="completed", exit_code=0)

    first = asyncio.run(adapter.execute_next(execute))
    second = asyncio.run(adapter.execute_next(execute))

    assert first is not None
    assert second is not None
    assert [item.work_item.scheduling.operation_id for item in (first, second)] == [
        higher_share.scheduling.operation_id,
        lower_share.scheduling.operation_id,
    ]
    assert executed == [
        higher_share.scheduling.operation_id,
        lower_share.scheduling.operation_id,
    ]
    assert first.reservation.operation.operation_id == higher_share.scheduling.operation_id
    assert adapter.cancel_pending(lower_share.scheduling.operation_id) is False
