from services.trusted_executor.weighted_scheduler import (
    ScheduledOperation,
    SchedulerCaps,
    SchedulerPolicy,
    SchedulerWeights,
    WeightedScheduler,
)


def _operation(operation_id: str, owner_id: str, *, weight: int = 1) -> ScheduledOperation:
    return ScheduledOperation(
        operation_id=operation_id,
        node_id="node-a",
        tenant_id="tenant-a",
        owner_id=owner_id,
        presentation_id=f"presentation-{owner_id}",
        weight=weight,
    )


def test_weighted_scheduler_enforces_scoped_caps_and_eventually_serves_an_eligible_waiter() -> None:
    capped = WeightedScheduler(
        SchedulerPolicy(
            caps=SchedulerCaps(global_cap=2, node_cap=2, tenant_cap=2, owner_cap=1, presentation_cap=1),
        )
    )
    capped.submit(_operation("owner-a-active", "owner-a", weight=100))
    capped.submit(_operation("owner-a-blocked", "owner-a", weight=100))
    capped.submit(_operation("owner-b-active", "owner-b"))
    capped_first = capped.reserve_next()
    capped_second = capped.reserve_next()
    assert capped_first is not None and capped_second is not None
    assert [capped_first.operation.operation_id, capped_second.operation.operation_id] == [
        "owner-a-active",
        "owner-b-active",
    ]
    assert capped.reserve_next() is None
    capped_snapshot = capped.snapshot()
    assert capped_snapshot.active_global == 2
    assert capped_snapshot.active_by_node == {"node-a": 2}
    assert capped_snapshot.active_by_tenant == {"tenant-a": 2}
    assert capped_snapshot.active_by_owner == {"owner-a": 1, "owner-b": 1}
    assert capped_snapshot.active_by_presentation == {"presentation-owner-a": 1, "presentation-owner-b": 1}
    assert capped.release(capped_first) is True
    assert capped.release(capped_second) is True
    unblocked = capped.reserve_next()
    assert unblocked is not None
    assert unblocked.operation.operation_id == "owner-a-blocked"
    assert capped.release(unblocked) is True

    scheduler = WeightedScheduler(
        SchedulerPolicy(
            caps=SchedulerCaps(global_cap=2, node_cap=2, tenant_cap=2, owner_cap=1, presentation_cap=1),
            starvation_after_dispatches=2,
        )
    )
    scheduler.submit(_operation("owner-b-waiter", "owner-b"))
    scheduler.submit(_operation("owner-a-0", "owner-a", weight=100))

    first = scheduler.reserve_next()
    assert first is not None
    assert first.operation.operation_id == "owner-a-0"
    assert scheduler.release(first) is True

    scheduler.submit(_operation("owner-a-1", "owner-a", weight=100))
    second = scheduler.reserve_next()
    assert second is not None
    assert second.operation.operation_id == "owner-a-1"
    assert scheduler.release(second) is True

    scheduler.submit(_operation("owner-a-2", "owner-a", weight=100))
    third = scheduler.reserve_next()
    assert third is not None
    assert third.operation.operation_id == "owner-b-waiter"

    snapshot = scheduler.snapshot()
    assert snapshot.active_global == 1
    assert snapshot.active_by_node == {"node-a": 1}
    assert snapshot.active_by_tenant == {"tenant-a": 1}
    assert snapshot.active_by_owner == {"owner-b": 1}
    assert snapshot.active_by_presentation == {"presentation-owner-b": 1}
    assert scheduler.release(third) is True

    fourth = scheduler.reserve_next()
    assert fourth is not None
    assert fourth.operation.operation_id == "owner-a-2"
    assert scheduler.release(fourth) is True
    assert scheduler.snapshot().active_global == 0
    assert scheduler.release(fourth) is False
