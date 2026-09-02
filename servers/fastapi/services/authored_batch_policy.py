"""Pure scheduling policy for bounded Authored slide-authoring batches.

The authoring caller owns execution and checkpointing.  This module only turns
the remaining slide indexes into provider-call batches and carries the small,
per-job concurrency state needed to avoid repeatedly overloading a provider.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


MAX_SLIDES_PER_AUTHOR_CALL = 3
INITIAL_WAVE_CONCURRENCY = 1
STEADY_WAVE_CONCURRENCY = 2

_OVERLOAD_CODES = frozenset(
    {
        "provider_circuit_open",
        "public_provider_bridge_exception",
    }
)
_HTTP_OVERLOAD_PATTERN = re.compile(r"(?<!\d)(?:500|503)(?!\d)")
_TIMEOUT_PATTERN = re.compile(r"\b(?:timeout|timed out|deadline exceeded)\b", re.IGNORECASE)


@dataclass(frozen=True)
class AuthoredBatch:
    """One provider call, containing no more than three slide indexes."""

    slide_indices: tuple[int, ...]
    recovery: bool = False


@dataclass(frozen=True)
class AuthoredBatchWave:
    """The calls allowed to run together for one scheduling wave."""

    batches: tuple[AuthoredBatch, ...]

    @property
    def slide_indices(self) -> tuple[int, ...]:
        """Flattened indexes, useful for trace logging and checkpoint claims."""

        return tuple(index for batch in self.batches for index in batch.slide_indices)


@dataclass(frozen=True)
class AuthoredBatchPolicyState:
    """Immutable, job-scoped state for the adaptive concurrency rule.

    A fresh job starts serial.  Its first successful provider call unlocks two
    calls per wave.  An overload-class failure permanently returns that job to
    serial calls; callers must keep the returned state with the job.
    """

    first_success_observed: bool = False
    permanently_serial: bool = False

    @property
    def concurrency(self) -> int:
        if self.permanently_serial or not self.first_success_observed:
            return INITIAL_WAVE_CONCURRENCY
        return STEADY_WAVE_CONCURRENCY


def new_authored_batch_policy_state() -> AuthoredBatchPolicyState:
    """Return the serial-first state for one new Authored generation job."""

    return AuthoredBatchPolicyState()


def plan_authored_batch_wave(
    pending_slide_indices: Iterable[int],
    state: AuthoredBatchPolicyState,
) -> AuthoredBatchWave:
    """Plan the next initial-authoring wave from still-uncompleted slides.

    Input order is preserved.  With an unlocked policy this produces the exact
    remainder shapes ``3+3``, ``3+2``, ``3+1``, ``3``, ``2`` and ``1`` for six
    through one remaining slides.  A fresh or permanently-serial policy emits
    only the leading call, leaving the rest for the next wave.
    """

    indices = _normalized_indices(pending_slide_indices, label="pending_slide_indices")
    batch_count = min(state.concurrency, _batch_count(indices))
    return AuthoredBatchWave(
        batches=tuple(
            AuthoredBatch(indices[offset : offset + MAX_SLIDES_PER_AUTHOR_CALL])
            for offset in range(0, batch_count * MAX_SLIDES_PER_AUTHOR_CALL, MAX_SLIDES_PER_AUTHOR_CALL)
            if indices[offset : offset + MAX_SLIDES_PER_AUTHOR_CALL]
        )
    )


def plan_authored_recovery_batches(
    failed_slide_indices: Iterable[int],
    *,
    successful_slide_indices: Iterable[int] = (),
) -> tuple[AuthoredBatch, ...]:
    """Return recovery calls containing only failed slides, three at a time.

    Successful indexes are accepted solely as a guardrail: an overlap is an
    invalid checkpoint view and raises instead of silently regenerating a slide
    that was already accepted.
    """

    failed = _normalized_indices(failed_slide_indices, label="failed_slide_indices")
    successful = _normalized_indices(
        successful_slide_indices, label="successful_slide_indices"
    )
    overlap = set(failed).intersection(successful)
    if overlap:
        raise ValueError(
            "failed_slide_indices must not include successful slides: "
            f"{sorted(overlap)}"
        )
    return tuple(
        AuthoredBatch(
            failed[offset : offset + MAX_SLIDES_PER_AUTHOR_CALL], recovery=True
        )
        for offset in range(0, len(failed), MAX_SLIDES_PER_AUTHOR_CALL)
    )


def advance_authored_batch_policy(
    state: AuthoredBatchPolicyState,
    *,
    succeeded: bool,
    error: object | None = None,
) -> AuthoredBatchPolicyState:
    """Return the job state after one provider-call outcome.

    An overload signal wins even if an integration reports a partial success.
    Other failures do not alter the concurrency policy, while the first normal
    success unlocks the two-call waves.
    """

    if is_authored_provider_overload(error):
        return AuthoredBatchPolicyState(
            first_success_observed=state.first_success_observed or succeeded,
            permanently_serial=True,
        )
    if succeeded:
        return AuthoredBatchPolicyState(
            first_success_observed=True,
            permanently_serial=state.permanently_serial,
        )
    return state


def is_authored_provider_overload(error: object | None) -> bool:
    """Recognize failures which require permanent serial scheduling for a job."""

    if error is None:
        return False
    if isinstance(error, TimeoutError):
        return True
    if isinstance(error, int) and not isinstance(error, bool):
        return error in {500, 503}

    candidates = [str(error)]
    for attribute in ("code", "status_code"):
        value = getattr(error, attribute, None)
        if value is not None:
            candidates.append(str(value))
    response = getattr(error, "response", None)
    response_status = getattr(response, "status_code", None)
    if response_status is not None:
        candidates.append(str(response_status))

    for candidate in candidates:
        normalized = candidate.strip().casefold()
        if normalized in _OVERLOAD_CODES:
            return True
        if _HTTP_OVERLOAD_PATTERN.search(normalized) or _TIMEOUT_PATTERN.search(normalized):
            return True
    return False


def _normalized_indices(indices: Iterable[int], *, label: str) -> tuple[int, ...]:
    result = tuple(indices)
    if any(isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in result):
        raise ValueError(f"{label} must contain non-negative integer slide indexes")
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must not contain duplicate slide indexes")
    return result


def _batch_count(indices: tuple[int, ...]) -> int:
    return (len(indices) + MAX_SLIDES_PER_AUTHOR_CALL - 1) // MAX_SLIDES_PER_AUTHOR_CALL


__all__ = [
    "AuthoredBatch",
    "AuthoredBatchPolicyState",
    "AuthoredBatchWave",
    "INITIAL_WAVE_CONCURRENCY",
    "MAX_SLIDES_PER_AUTHOR_CALL",
    "STEADY_WAVE_CONCURRENCY",
    "advance_authored_batch_policy",
    "is_authored_provider_overload",
    "new_authored_batch_policy_state",
    "plan_authored_batch_wave",
    "plan_authored_recovery_batches",
]
