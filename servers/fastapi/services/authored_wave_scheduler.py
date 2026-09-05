"""F3: authoring admission without the wave barrier.

``docs/authored-quality-speed-programme-design-20260905.md`` section 4 (F3).
Initial authoring used to run as ``while pending: gather(wave)`` -- every slide
in a wave waited for the slowest call in that wave before the next call could
start, which on a 20-slide deck is eleven synchronised stalls. With
``AUTHORED_WAVE_BARRIER_DISABLED`` the same calls run under an admission rule
instead: a finished call immediately admits the next pending slide, and the
admission width is re-read from the batch policy on every admission so an
overload that halves ``concurrency_cap`` narrows the live scheduler too.

The barrier path is kept here as well, so the pipeline holds one call site for
both and neither implementation drifts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Sequence

from services.authored_batch_policy import (
    AuthoredBatchPolicyState,
    advance_authored_batch_policy,
    plan_authored_batch_wave,
)
from utils.get_env_programme import is_authored_wave_barrier_disabled

logger = logging.getLogger(__name__)

# One provider call, given its slide indexes; returns the batch result or
# raises. Exceptions are handed to ``record`` rather than propagated.
RunBatch = Callable[[tuple[int, ...]], Awaitable[Any]]
# Books one finished call into the caller's own state and says whether the call
# succeeded, which is what the batch policy is advanced with.
RecordBatch = Callable[[tuple[int, ...], Any], bool]


class AuthoredWaveSchedule:
    """Mutable scheduling state for one deck's initial authoring pass."""

    def __init__(
        self,
        policy: AuthoredBatchPolicyState,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        deadline: float | None = None,
    ) -> None:
        self.policy = policy
        self.cooldown_wait_seconds = 0.0
        self._clock = clock
        # Resolved at call time, not bound here: a default argument would
        # capture the ``asyncio.sleep`` that existed when this module was
        # imported, so a caller (or a test) that replaces it later would be
        # ignored and the cooldown would be slept out for real.
        self._sleep = sleep
        self._deadline = deadline

    async def wait_out_cooldown(self, label: str) -> float:
        """Sleep out an open provider cooldown before the next admission.

        Returns the seconds actually waited. The wait is bounded by the
        cooldown the policy itself opened (at most five minutes) and, when the
        caller supplied one, by the time left before the job deadline. It is an
        ordinary cancellable ``sleep``.
        """

        now = self._clock()
        remaining = self.policy.cooldown_remaining(now)
        if remaining <= 0.0:
            return 0.0
        if self._deadline is not None:
            remaining = min(remaining, max(0.0, self._deadline - now))
        if remaining <= 0.0:
            return 0.0
        logger.info(
            "authored_batch_cooldown_wait admission=%s seconds=%.1f",
            label,
            remaining,
        )
        await (self._sleep or asyncio.sleep)(remaining)
        self.cooldown_wait_seconds += remaining
        return remaining

    def advance(self, outcome: Any, *, succeeded: bool) -> None:
        self.policy = advance_authored_batch_policy(
            self.policy,
            succeeded=succeeded,
            error=outcome if isinstance(outcome, BaseException) else None,
        )


async def run_initial_authoring(
    pending: Sequence[int],
    schedule: AuthoredWaveSchedule,
    *,
    run_batch: RunBatch,
    record: RecordBatch,
    barrier_disabled: bool | None = None,
) -> None:
    """Author every pending slide exactly once, then return.

    ``record`` owns the caller's success/failure bookkeeping and returns whether
    the call succeeded; the batch policy is advanced here from that answer plus
    the raised exception, if any.
    """

    indexes = tuple(pending)
    if not indexes:
        return
    disabled = (
        is_authored_wave_barrier_disabled()
        if barrier_disabled is None
        else barrier_disabled
    )
    if disabled:
        await _run_admission(indexes, schedule, run_batch=run_batch, record=record)
        return
    await _run_waves(indexes, schedule, run_batch=run_batch, record=record)


async def _run_waves(
    indexes: tuple[int, ...],
    schedule: AuthoredWaveSchedule,
    *,
    run_batch: RunBatch,
    record: RecordBatch,
) -> None:
    """The historic barrier: plan a wave, gather it, plan the next."""

    remaining = list(indexes)
    wave_ordinal = 0
    while remaining:
        wave_ordinal += 1
        await schedule.wait_out_cooldown(f"wave-{wave_ordinal}")
        wave = plan_authored_batch_wave(remaining, schedule.policy)
        outcomes = await asyncio.gather(
            *(run_batch(batch.slide_indices) for batch in wave.batches),
            return_exceptions=True,
        )
        consumed: set[int] = set()
        for batch, outcome in zip(wave.batches, outcomes, strict=True):
            consumed.update(batch.slide_indices)
            schedule.advance(
                outcome, succeeded=record(batch.slide_indices, outcome)
            )
        remaining = [index for index in remaining if index not in consumed]


async def _run_admission(
    indexes: tuple[int, ...],
    schedule: AuthoredWaveSchedule,
    *,
    run_batch: RunBatch,
    record: RecordBatch,
) -> None:
    """Admit the next call the moment one finishes, at the live policy width."""

    size = max(1, int(schedule.policy.profile.slides_per_call))
    queue = [
        indexes[offset : offset + size] for offset in range(0, len(indexes), size)
    ]
    position = 0
    admitted = 0
    inflight: dict[asyncio.Task, tuple[int, ...]] = {}
    while position < len(queue) or inflight:
        # The width is re-read here, not captured: an overload that halved the
        # cap between two admissions narrows the scheduler immediately, and a
        # restored cap widens it again without rebuilding anything.
        while position < len(queue) and len(inflight) < schedule.policy.concurrency:
            admitted += 1
            await schedule.wait_out_cooldown(f"admission-{admitted}")
            batch = queue[position]
            position += 1
            inflight[asyncio.ensure_future(run_batch(batch))] = batch
        # ``concurrency`` never falls below 1, so a pending queue always admits
        # at least one call above and this wait can never spin on an empty set.
        done, _ = await asyncio.wait(
            set(inflight), return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            batch = inflight.pop(task)
            error = task.exception()
            outcome = error if error is not None else task.result()
            schedule.advance(outcome, succeeded=record(batch, outcome))


__all__ = [
    "AuthoredWaveSchedule",
    "run_initial_authoring",
]
