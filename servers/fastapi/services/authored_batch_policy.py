"""Pure scheduling policy for bounded Authored slide-authoring batches.

The authoring caller owns execution and checkpointing.  This module only turns
the remaining slide indexes into provider-call batches and carries the small,
per-job concurrency state needed to avoid repeatedly overloading a provider.

Batch shape and concurrency come from the provider rate profile
(``services/authored_provider_rate_profile.py``); nothing here is a literal.
An overload no longer degrades a job for good: the cap is halved and a
cooldown with exponential backoff starts, and after it ends a run of
consecutive successes restores one step at a time up to the profile cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
import time
from typing import Callable, Iterable

from services.authored_provider_rate_profile import (
    AuthoredRateProfile,
    resolve_authored_rate_profile,
)


OVERLOAD_COOLDOWN_BASE_SECONDS = 30.0
OVERLOAD_COOLDOWN_MAX_SECONDS = 300.0

_OVERLOAD_CODES = frozenset(
    {
        "provider_circuit_open",
        "public_provider_bridge_exception",
        # A call that exhausted its own transport timeout is the strongest
        # overload signal available.  The underscore-joined code does not match
        # the free-text timeout pattern below, so it is named here explicitly.
        "public_provider_call_timeout",
    }
)
_OVERLOAD_STATUS_CODES = frozenset({429, 500, 503})
_HTTP_OVERLOAD_PATTERN = re.compile(r"(?<!\d)(?:429|500|503)(?!\d)")
_TIMEOUT_PATTERN = re.compile(r"\b(?:timeout|timed out|deadline exceeded)\b", re.IGNORECASE)
# What the provider bridges actually surface for a 429: ``openai.RateLimitError``
# (status 429), llmai's ``usage_limit_reached`` / "usage limit" messages,
# Google's RESOURCE_EXHAUSTED and the generic "too many requests" text.
_RATE_LIMIT_PATTERN = re.compile(
    r"rate[\s_-]*limit|usage[\s_-]*limit|too many requests|resource[\s_-]*exhausted",
    re.IGNORECASE,
)

Clock = Callable[[], float]


@dataclass(frozen=True)
class AuthoredBatch:
    """One provider call, containing at most ``slides_per_call`` slide indexes."""

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

    A fresh job runs ``initial_wave_concurrency`` calls per wave until its
    first successful provider call unlocks ``steady_wave_concurrency``.  An
    overload halves ``concurrency_cap`` (floor 1) and opens a cooldown; once
    the cooldown has passed, ``recovery_successes`` consecutive successes
    restore one step (doubling, bounded by the profile cap).  Callers must
    keep the returned state with the job.
    """

    profile: AuthoredRateProfile = field(default_factory=resolve_authored_rate_profile)
    first_success_observed: bool = False
    concurrency_cap: int | None = None
    cooldown_until: float | None = None
    consecutive_successes: int = 0
    consecutive_overloads: int = 0

    def __post_init__(self) -> None:
        if self.concurrency_cap is None:
            object.__setattr__(
                self, "concurrency_cap", self.profile.steady_wave_concurrency
            )
        elif self.concurrency_cap < 1:
            raise ValueError("concurrency_cap must be at least 1")

    @property
    def concurrency(self) -> int:
        base = (
            self.profile.steady_wave_concurrency
            if self.first_success_observed
            else self.profile.initial_wave_concurrency
        )
        return max(1, min(base, self.concurrency_cap))

    @property
    def degraded(self) -> bool:
        """Whether an overload has lowered the cap below the profile value."""

        return self.concurrency_cap < self.profile.steady_wave_concurrency

    def cooldown_remaining(self, now: float) -> float:
        if self.cooldown_until is None:
            return 0.0
        return max(0.0, self.cooldown_until - now)

    def in_cooldown(self, now: float) -> bool:
        return self.cooldown_remaining(now) > 0.0


def new_authored_batch_policy_state(
    profile: AuthoredRateProfile | None = None,
) -> AuthoredBatchPolicyState:
    """Return the initial-wave state for one new Authored generation job."""

    return AuthoredBatchPolicyState(
        profile=profile if profile is not None else resolve_authored_rate_profile()
    )


def plan_authored_batch_wave(
    pending_slide_indices: Iterable[int],
    state: AuthoredBatchPolicyState,
) -> AuthoredBatchWave:
    """Plan the next initial-authoring wave from still-uncompleted slides.

    Input order is preserved.  With the token profile unlocked this produces
    the exact remainder shapes ``3+3``, ``3+2``, ``3+1``, ``3``, ``2`` and
    ``1`` for six through one remaining slides; the api profile emits one
    slide per call, ``concurrency`` calls at a time.  Slides beyond the wave
    are left for the next wave.
    """

    indices = _normalized_indices(pending_slide_indices, label="pending_slide_indices")
    size = state.profile.slides_per_call
    batch_count = min(state.concurrency, _batch_count(indices, size))
    return AuthoredBatchWave(
        batches=tuple(
            AuthoredBatch(indices[offset : offset + size])
            for offset in range(0, batch_count * size, size)
            if indices[offset : offset + size]
        )
    )


def plan_authored_recovery_batches(
    failed_slide_indices: Iterable[int],
    *,
    successful_slide_indices: Iterable[int] = (),
    profile: AuthoredRateProfile | None = None,
) -> tuple[AuthoredBatch, ...]:
    """Return recovery calls containing only failed slides, ``slides_per_call`` at a time.

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
    size = (profile if profile is not None else resolve_authored_rate_profile()).slides_per_call
    return tuple(
        AuthoredBatch(failed[offset : offset + size], recovery=True)
        for offset in range(0, len(failed), size)
    )


def advance_authored_batch_policy(
    state: AuthoredBatchPolicyState,
    *,
    succeeded: bool,
    error: object | None = None,
    now: float | None = None,
    clock: Clock = time.monotonic,
) -> AuthoredBatchPolicyState:
    """Return the job state after one provider-call outcome.

    An overload signal wins even if an integration reports a partial success:
    the cap halves and a cooldown of ``30 s × 2^(n-1)`` (capped at five
    minutes, ``n`` counting consecutive overloads) begins.  A success after
    the cooldown counts toward recovery; a success during it does not.  A
    non-overload failure only breaks the run of consecutive successes.
    """

    moment = clock() if now is None else now
    if is_authored_provider_overload(error):
        overloads = state.consecutive_overloads + 1
        cooldown = min(
            OVERLOAD_COOLDOWN_BASE_SECONDS * (2 ** (overloads - 1)),
            OVERLOAD_COOLDOWN_MAX_SECONDS,
        )
        return replace(
            state,
            first_success_observed=state.first_success_observed or succeeded,
            concurrency_cap=max(1, state.concurrency_cap // 2),
            cooldown_until=moment + cooldown,
            consecutive_successes=0,
            consecutive_overloads=overloads,
        )
    if not succeeded:
        return replace(state, consecutive_successes=0)
    if not state.degraded:
        return replace(state, first_success_observed=True, consecutive_successes=0)
    if state.in_cooldown(moment):
        return replace(state, first_success_observed=True, consecutive_successes=0)
    successes = state.consecutive_successes + 1
    if successes < state.profile.recovery_successes:
        return replace(
            state, first_success_observed=True, consecutive_successes=successes
        )
    restored = min(state.profile.steady_wave_concurrency, state.concurrency_cap * 2)
    fully_restored = restored >= state.profile.steady_wave_concurrency
    return replace(
        state,
        first_success_observed=True,
        concurrency_cap=restored,
        cooldown_until=None,
        consecutive_successes=0,
        consecutive_overloads=0 if fully_restored else state.consecutive_overloads,
    )


def is_authored_provider_overload(error: object | None) -> bool:
    """Recognize failures which must slow a job's provider-call scheduling.

    Covers transport timeouts, HTTP 429/500/503, the durable overload codes,
    and rate-limit / usage-limit / resource-exhausted texts.
    """

    if error is None:
        return False
    if isinstance(error, TimeoutError):
        return True
    if isinstance(error, int) and not isinstance(error, bool):
        return error in _OVERLOAD_STATUS_CODES

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
        if (
            _HTTP_OVERLOAD_PATTERN.search(normalized)
            or _TIMEOUT_PATTERN.search(normalized)
            or _RATE_LIMIT_PATTERN.search(normalized)
        ):
            return True
    return False


def _normalized_indices(indices: Iterable[int], *, label: str) -> tuple[int, ...]:
    result = tuple(indices)
    if any(isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in result):
        raise ValueError(f"{label} must contain non-negative integer slide indexes")
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must not contain duplicate slide indexes")
    return result


def _batch_count(indices: tuple[int, ...], size: int) -> int:
    return (len(indices) + size - 1) // size


__all__ = [
    "AuthoredBatch",
    "AuthoredBatchPolicyState",
    "AuthoredBatchWave",
    "OVERLOAD_COOLDOWN_BASE_SECONDS",
    "OVERLOAD_COOLDOWN_MAX_SECONDS",
    "advance_authored_batch_policy",
    "is_authored_provider_overload",
    "new_authored_batch_policy_state",
    "plan_authored_batch_wave",
    "plan_authored_recovery_batches",
]
