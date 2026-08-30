"""Deterministic, in-process admission for trusted executor work.

This scheduler owns only volatile process-local reservations.  A caller that
needs cluster-wide correctness must acquire its durable admission lease/fence
before calling :meth:`reserve_next`; this module intentionally neither stores
leases nor claims to provide cross-process fencing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from threading import RLock
from typing import Mapping


ScopeName = str


def _positive(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")


def _identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class SchedulerCaps:
    """Maximum concurrent reservations at every isolation scope."""

    global_cap: int
    node_cap: int
    tenant_cap: int
    owner_cap: int
    presentation_cap: int

    def __post_init__(self) -> None:
        for field_name in (
            "global_cap",
            "node_cap",
            "tenant_cap",
            "owner_cap",
            "presentation_cap",
        ):
            _positive(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class SchedulerWeights:
    """Static fair-share weights, keyed by the authenticated scope identity."""

    global_weight: int = 1
    node_weights: Mapping[str, int] = field(default_factory=dict)
    tenant_weights: Mapping[str, int] = field(default_factory=dict)
    owner_weights: Mapping[str, int] = field(default_factory=dict)
    presentation_weights: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _positive(self.global_weight, "global_weight")
        for scope_name, weights in (
            ("node", self.node_weights),
            ("tenant", self.tenant_weights),
            ("owner", self.owner_weights),
            ("presentation", self.presentation_weights),
        ):
            for key, value in weights.items():
                _identifier(key, f"{scope_name}_weight key")
                _positive(value, f"{scope_name}_weight[{key!r}]")

    def weight_for(self, scope_name: ScopeName, scope_id: str) -> int:
        if scope_name == "global":
            return self.global_weight
        weights = {
            "node": self.node_weights,
            "tenant": self.tenant_weights,
            "owner": self.owner_weights,
            "presentation": self.presentation_weights,
        }.get(scope_name)
        if weights is None:
            raise ValueError(f"unknown scheduling scope: {scope_name}")
        return weights.get(scope_id, 1)


@dataclass(frozen=True, slots=True)
class SchedulerPolicy:
    caps: SchedulerCaps
    weights: SchedulerWeights = field(default_factory=SchedulerWeights)
    starvation_after_dispatches: int = 32

    def __post_init__(self) -> None:
        _positive(self.starvation_after_dispatches, "starvation_after_dispatches")


@dataclass(frozen=True, slots=True)
class ScheduledOperation:
    """Identity supplied by the authenticated operation admission layer.

    ``weight`` is a positive local fair-share multiplier.  It never changes a
    cap or grants access to a scope that the upstream authority did not assign.
    """

    operation_id: str
    node_id: str
    tenant_id: str
    owner_id: str
    presentation_id: str
    weight: int = 1

    def __post_init__(self) -> None:
        for field_name in (
            "operation_id",
            "node_id",
            "tenant_id",
            "owner_id",
            "presentation_id",
        ):
            _identifier(getattr(self, field_name), field_name)
        _positive(self.weight, "weight")


@dataclass(frozen=True, slots=True)
class Reservation:
    """A volatile reservation token; retain it to make release generation-safe."""

    operation: ScheduledOperation
    generation: int
    dispatch_order: int


@dataclass(frozen=True, slots=True)
class SchedulerSnapshot:
    pending_operations: tuple[str, ...]
    active_operations: tuple[str, ...]
    active_global: int
    active_by_node: Mapping[str, int]
    active_by_tenant: Mapping[str, int]
    active_by_owner: Mapping[str, int]
    active_by_presentation: Mapping[str, int]


class WeightedScheduler:
    """Single-process weighted fair scheduler with cap-safe reservations.

    Requests are compared using projected normalized service across global,
    node, tenant, owner, and presentation scopes.  A request eligible for at
    least ``starvation_after_dispatches`` later selections is selected FIFO
    before normal weighted ordering, preventing an eligible queue item from
    being perpetually bypassed by heavier arrivals.
    """

    def __init__(self, policy: SchedulerPolicy) -> None:
        self._policy = policy
        self._lock = RLock()
        self._pending: dict[str, tuple[ScheduledOperation, int, int]] = {}
        self._active: dict[str, Reservation] = {}
        self._generation_by_operation: Counter[str] = Counter()
        self._active_counts: dict[ScopeName, Counter[str]] = {
            "global": Counter(),
            "node": Counter(),
            "tenant": Counter(),
            "owner": Counter(),
            "presentation": Counter(),
        }
        self._served: dict[ScopeName, Counter[str]] = {
            "global": Counter(),
            "node": Counter(),
            "tenant": Counter(),
            "owner": Counter(),
            "presentation": Counter(),
        }
        self._enqueue_order = 0
        self._dispatch_order = 0

    def submit(self, operation: ScheduledOperation) -> None:
        """Queue one operation; operation identifiers cannot be concurrently reused."""
        with self._lock:
            if operation.operation_id in self._pending or operation.operation_id in self._active:
                raise ValueError(f"operation is already scheduled: {operation.operation_id}")
            self._pending[operation.operation_id] = (
                operation,
                self._enqueue_order,
                self._dispatch_order,
            )
            self._enqueue_order += 1

    def cancel_pending(self, operation_id: str) -> bool:
        """Remove queued work only; active process cancellation belongs to its supervisor."""
        with self._lock:
            return self._pending.pop(operation_id, None) is not None

    def reserve_next(self) -> Reservation | None:
        """Atomically reserve the next cap-eligible operation, if one exists."""
        with self._lock:
            eligible = [entry for entry in self._pending.values() if self._is_eligible(entry[0])]
            if not eligible:
                return None

            starved = [
                entry
                for entry in eligible
                if self._dispatch_order - entry[2] >= self._policy.starvation_after_dispatches
            ]
            if starved:
                chosen = min(starved, key=lambda entry: (entry[1], entry[0].operation_id))
            else:
                chosen = min(
                    eligible,
                    key=lambda entry: (
                        self._projected_fair_share(entry[0]),
                        entry[1],
                        entry[0].operation_id,
                    ),
                )

            operation, _, _ = chosen
            del self._pending[operation.operation_id]
            self._generation_by_operation[operation.operation_id] += 1
            self._dispatch_order += 1
            reservation = Reservation(
                operation=operation,
                generation=self._generation_by_operation[operation.operation_id],
                dispatch_order=self._dispatch_order,
            )
            self._active[operation.operation_id] = reservation
            self._increment_counts(operation)
            self._increment_served(operation)
            return reservation

    def release(self, reservation: Reservation) -> bool:
        """Release exactly the active generation.  A stale token cannot release new work."""
        with self._lock:
            active = self._active.get(reservation.operation.operation_id)
            if active != reservation:
                return False
            del self._active[reservation.operation.operation_id]
            self._decrement_counts(reservation.operation)
            return True

    def snapshot(self) -> SchedulerSnapshot:
        """Return a consistent read-only view suitable for local metrics."""
        with self._lock:
            return SchedulerSnapshot(
                pending_operations=tuple(self._pending),
                active_operations=tuple(self._active),
                active_global=self._active_counts["global"]["global"],
                active_by_node=dict(self._active_counts["node"]),
                active_by_tenant=dict(self._active_counts["tenant"]),
                active_by_owner=dict(self._active_counts["owner"]),
                active_by_presentation=dict(self._active_counts["presentation"]),
            )

    def _is_eligible(self, operation: ScheduledOperation) -> bool:
        caps = self._policy.caps
        return (
            self._active_counts["global"]["global"] < caps.global_cap
            and self._active_counts["node"][operation.node_id] < caps.node_cap
            and self._active_counts["tenant"][operation.tenant_id] < caps.tenant_cap
            and self._active_counts["owner"][operation.owner_id] < caps.owner_cap
            and self._active_counts["presentation"][operation.presentation_id] < caps.presentation_cap
        )

    def _projected_fair_share(self, operation: ScheduledOperation) -> Fraction:
        scope_ids = self._scope_ids(operation)
        return max(
            Fraction(self._served[scope_name][scope_id] + 1, self._effective_weight(scope_name, scope_id, operation))
            for scope_name, scope_id in scope_ids.items()
        )

    def _effective_weight(self, scope_name: ScopeName, scope_id: str, operation: ScheduledOperation) -> int:
        return self._policy.weights.weight_for(scope_name, scope_id) * operation.weight

    @staticmethod
    def _scope_ids(operation: ScheduledOperation) -> dict[ScopeName, str]:
        return {
            "global": "global",
            "node": operation.node_id,
            "tenant": operation.tenant_id,
            "owner": operation.owner_id,
            "presentation": operation.presentation_id,
        }

    def _increment_counts(self, operation: ScheduledOperation) -> None:
        for scope_name, scope_id in self._scope_ids(operation).items():
            self._active_counts[scope_name][scope_id] += 1

    def _decrement_counts(self, operation: ScheduledOperation) -> None:
        for scope_name, scope_id in self._scope_ids(operation).items():
            self._active_counts[scope_name][scope_id] -= 1
            if self._active_counts[scope_name][scope_id] == 0:
                del self._active_counts[scope_name][scope_id]

    def _increment_served(self, operation: ScheduledOperation) -> None:
        for scope_name, scope_id in self._scope_ids(operation).items():
            self._served[scope_name][scope_id] += 1


__all__ = [
    "Reservation",
    "ScheduledOperation",
    "SchedulerCaps",
    "SchedulerPolicy",
    "SchedulerSnapshot",
    "SchedulerWeights",
    "WeightedScheduler",
]
