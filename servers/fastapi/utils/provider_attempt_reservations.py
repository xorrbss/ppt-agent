"""Context-bound provider attempt reservations and the leases that spend them.

A reservation authorises exactly one physical replay of the provider call the
reserving attempt is about to make.  It therefore has to leave the ledger with
that call -- but only at the moment that call actually starts.  Spending it when
the bounded wrapper is merely *built* makes the ledger's proof false in both
directions: a wrapper that an early return, a cancelled executor hand-off or a
different branch then drops reports a replayable physical call that never ran,
and the call that does run is left with nothing to replay.

The reservations therefore live in a mutable holder that the context binds once,
rather than in the ``ContextVar`` value itself, for two reasons:

* a bounded provider call runs on the authoring executor's SDK thread, whose
  context is not the caller's, so it could never rebind a ``ContextVar`` -- but
  it can claim from a shared, lock-guarded holder;
* a holder belongs to the task that created it, so a child task publishing its
  own attempt never mutates the reservations of the task that spawned it, which
  is the isolation the previous per-task ``ContextVar.set`` gave for free.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import threading
import uuid
import weakref


@dataclass(frozen=True, slots=True)
class ProviderAttemptReservation:
    """Authority needed to reserve one overload replay of a physical call."""

    job_id: uuid.UUID
    slide_index: int
    attempt_token: str
    ordinal: int
    fencing_token: int | None


def _current_task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class PendingProviderAttemptReservations:
    """One task's unspent reservations, claimable from any thread."""

    __slots__ = ("_lock", "_owner", "_reservations")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reservations: tuple[ProviderAttemptReservation, ...] = ()
        task = _current_task()
        self._owner = weakref.ref(task) if task is not None else None

    def owned_by_current_task(self) -> bool:
        """Whether the task reading this holder is the one that created it."""

        owner = None if self._owner is None else self._owner()
        return owner is _current_task()

    def snapshot(self) -> tuple[ProviderAttemptReservation, ...]:
        with self._lock:
            return self._reservations

    def publish(self, reservation: ProviderAttemptReservation) -> None:
        """Add one reservation, dropping any that belong to another attempt."""

        group = (
            reservation.job_id,
            reservation.attempt_token,
            reservation.ordinal,
            reservation.fencing_token,
        )
        with self._lock:
            pending = self._reservations
            if pending:
                first = pending[0]
                current_group = (
                    first.job_id,
                    first.attempt_token,
                    first.ordinal,
                    first.fencing_token,
                )
                if current_group != group:
                    pending = ()
            if all(
                existing.slide_index != reservation.slide_index for existing in pending
            ):
                self._reservations = (*pending, reservation)
            else:
                self._reservations = pending

    def claim(self) -> tuple[ProviderAttemptReservation, ...]:
        """Hand the reservations to one caller and empty the holder."""

        with self._lock:
            claimed = self._reservations
            self._reservations = ()
            return claimed


class ProviderAttemptReservationLease:
    """The reservations one provider call may spend, claimed when it starts.

    Holding a lease spends nothing.  The first ``claim`` -- made by the bounded
    call as its physical request begins, from whatever thread that is -- empties
    the holder, so a wrapper that is never invoked leaves the reservations for
    the call that does run, and two wrappers can never spend the same one.
    """

    __slots__ = ("_holder", "_claimed")

    def __init__(self, holder: PendingProviderAttemptReservations) -> None:
        self._holder = holder
        self._claimed: tuple[ProviderAttemptReservation, ...] | None = None

    @property
    def available(self) -> bool:
        """Whether this lease can still authorise a replay."""

        if self._claimed is not None:
            return bool(self._claimed)
        return bool(self._holder.snapshot())

    def claim(self) -> tuple[ProviderAttemptReservation, ...]:
        if self._claimed is None:
            self._claimed = self._holder.claim()
        return self._claimed


_PENDING: ContextVar[PendingProviderAttemptReservations | None] = ContextVar(
    "authored_pending_provider_attempt_reservations", default=None
)


def _holder(*, create: bool) -> PendingProviderAttemptReservations | None:
    """This task's holder; a holder inherited from another task is not ours."""

    holder = _PENDING.get()
    if holder is not None and holder.owned_by_current_task():
        return holder
    if not create:
        return None
    holder = PendingProviderAttemptReservations()
    _PENDING.set(holder)
    return holder


def publish_provider_attempt_reservation(
    reservation: ProviderAttemptReservation,
) -> None:
    """Record a reservation the current task's next physical call may spend."""

    holder = _holder(create=True)
    if holder is not None:
        holder.publish(reservation)


def current_provider_attempt_reservations() -> tuple[ProviderAttemptReservation, ...]:
    """Inspect the pending reservations without spending them (tests, logging)."""

    holder = _holder(create=False)
    return () if holder is None else holder.snapshot()


def take_provider_attempt_reservations() -> tuple[ProviderAttemptReservation, ...]:
    """Spend the pending reservations immediately, in the publishing task.

    Callers that hand the work to another thread must take a lease instead, so
    the reservations are spent by the call that starts rather than by the
    wrapper that was built.
    """

    holder = _holder(create=False)
    return () if holder is None else holder.claim()


def lease_provider_attempt_reservations() -> ProviderAttemptReservationLease | None:
    """Lease this task's reservations for one call, spending nothing yet.

    ``None`` means there is nothing to lease: no reservation was published in
    this task, or an earlier call already claimed them all.
    """

    holder = _holder(create=False)
    if holder is None or not holder.snapshot():
        return None
    return ProviderAttemptReservationLease(holder)


__all__ = [
    "PendingProviderAttemptReservations",
    "ProviderAttemptReservation",
    "ProviderAttemptReservationLease",
    "current_provider_attempt_reservations",
    "lease_provider_attempt_reservations",
    "publish_provider_attempt_reservation",
    "take_provider_attempt_reservations",
]
