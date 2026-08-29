"""Validated scalar primitives shared by provider admission implementations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import re


IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
OUTCOMES = frozenset(("success", "failure", "retryable_failure"))
SNAPSHOT_VERSION = 1


class RuntimeControlError(RuntimeError):
    """Base class for safe, payload-free admission failures."""


class BackpressureError(RuntimeControlError):
    """Raised when the bounded waiting queue is full."""


class CircuitOpenError(RuntimeControlError):
    """Raised while the provider circuit is open."""


class BudgetExceededError(RuntimeControlError):
    """Raised when a per-job estimated or actual ceiling is crossed."""

    def __init__(self, *, kind: str, limit: int, observed: int) -> None:
        self.kind = kind
        self.limit = limit
        self.observed = observed
        super().__init__(f"{kind}_budget_exceeded:{observed}>{limit}")


class RetryAmplificationError(RuntimeControlError):
    """Raised when planned retries would exceed the configured multiplier."""


class CancellationRequested(RuntimeControlError):
    """Raised at an admission boundary after a job kill condition fires."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"provider_job_cancelled:{reason}")


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    """Provider-wide queue, rate, retry, and breaker limits."""

    max_queue_size: int = 128
    max_concurrency: int = 8
    requests_per_minute: int = 60
    rpm_window_seconds: float = 60.0
    # Structured generation performs at most four physical attempts for one
    # logical call.  Keep the in-process secondary guard aligned with that
    # bounded retry contract; the durable public reservation remains the hard
    # per-operation request/cost authority.
    retry_amplification_ceiling: float = 4.0
    circuit_failure_threshold: int = 5
    circuit_reset_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name in (
            "max_queue_size",
            "max_concurrency",
            "requests_per_minute",
            "circuit_failure_threshold",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in (
            "rpm_window_seconds",
            "retry_amplification_ceiling",
            "circuit_reset_seconds",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")
        if self.retry_amplification_ceiling < 1:
            raise ValueError("retry_amplification_ceiling must be at least 1")


@dataclass(frozen=True, slots=True)
class JobKillConditions:
    """Portable kill conditions evaluated before and after every call."""

    deadline_seconds: float | None = None
    max_total_requests: int | None = None
    max_retry_requests: int | None = None
    max_consecutive_failures: int | None = None

    def __post_init__(self) -> None:
        if self.deadline_seconds is not None and (
            isinstance(self.deadline_seconds, bool)
            or not isinstance(self.deadline_seconds, (int, float))
            or not math.isfinite(float(self.deadline_seconds))
            or self.deadline_seconds <= 0
        ):
            raise ValueError("deadline_seconds must be finite and positive")
        for name in (
            "max_total_requests",
            "max_retry_requests",
            "max_consecutive_failures",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")


class CancellationToken:
    """Cooperative cancellation token with a non-sensitive reason code."""

    __slots__ = ("_event", "_reason")

    def __init__(self, reason: str | None = None) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None
        if reason is not None:
            self.cancel(reason)

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str = "operator_cancelled") -> None:
        validate_reason(reason)
        if not self.cancelled:
            self._reason = reason
            self._event.set()

    async def wait(self) -> str:
        await self._event.wait()
        return self._reason or "operator_cancelled"

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise CancellationRequested(self._reason or "operator_cancelled")


def validate_identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a bounded safe identifier")


def validate_reason(value: object) -> None:
    if not isinstance(value, str) or not SAFE_REASON_RE.fullmatch(value):
        raise ValueError("cancellation reason must be a bounded safe code")


def validate_nonnegative(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def validate_optional_nonnegative(value: object, name: str) -> None:
    if value is not None:
        validate_nonnegative(value, name)


def required_nonnegative_int(value: object, name: str) -> int:
    validate_nonnegative(value, name)
    return value


def optional_nonnegative_int(
    value: object, name: str, *, positive: bool = False
) -> int | None:
    if value is None:
        return None
    validate_nonnegative(value, name)
    if positive and value == 0:
        raise ValueError(f"{name} must be positive")
    return value


def required_nonnegative_number(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{name} must be finite and non-negative")
    return float(value)


def require_dict(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return value


def require_keys(value: dict[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError("runtime snapshot fields do not match the allowlist")
