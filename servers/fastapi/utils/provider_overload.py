"""Bounded authored-provider overload retries and durable quota pauses."""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Mapping

from services.authored_batch_policy import (
    OVERLOAD_COOLDOWN_BASE_SECONDS,
    OVERLOAD_COOLDOWN_MAX_SECONDS,
    is_authored_provider_overload,
)
from services.workspace_operation_handler_registry import WorkspaceOperationDeferred
from utils.provider_terminal_outcome import (
    ProviderTerminalOutcome,
    ProviderTerminalOutcomeRecorder,
)


_MAX_OVERLOAD_PHYSICAL_CALLS = 2
_USAGE_LIMIT_CODE = "usage_limit_reached"
_RESET_AT_PATTERN = re.compile(r'''["']resets_at["']\s*:\s*(\d+)''')
_RESET_IN_PATTERN = re.compile(r'''["']resets_in_seconds["']\s*:\s*(\d+)''')


class ProviderOutcomeError(RuntimeError):
    """Content-free provider outcome that survives the authored job boundary."""

    def __init__(
        self,
        code: str,
        *,
        recovery_allowed: bool,
        overload: bool = False,
        cooldown_seconds: float | None = None,
    ) -> None:
        self.code = code
        self.retryable = False
        self.recovery_allowed = recovery_allowed
        self.overload = overload
        self.cooldown_seconds = cooldown_seconds
        super().__init__(code)


class ProviderQuotaPause(ProviderOutcomeError, WorkspaceOperationDeferred):
    """Whole-account usage exhaustion returned to the durable operation queue."""

    def __init__(self, *, reset_at: int, observed_at: int | None = None) -> None:
        now = int(time.time()) if observed_at is None else int(observed_at)
        self.code = "public_provider_usage_limit_paused"
        self.retryable = False
        self.recovery_allowed = False
        self.overload = True
        self.cooldown_seconds = None
        self.reset_at = int(reset_at)
        self.retry_after_seconds = max(0.05, float(self.reset_at - now))
        self.detail = {
            "provider_quota_reset_at": self.reset_at,
            "provider_quota_resume_semantics": "coordinator_requeues_after_reset",
        }
        RuntimeError.__init__(self, self.code)


def provider_status_code(error: BaseException | None) -> int | None:
    """Return the HTTP-like status a provider SDK exposes, or ``None``.

    ``None`` means the transport said nothing structured about this failure --
    only then may a caller fall back to reading the message.
    """

    for candidate in (error, getattr(error, "response", None)):
        if candidate is None:
            continue
        value = getattr(candidate, "status_code", None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def is_provider_overload(error: BaseException | None) -> bool:
    """Classify overload from structured signals first, from text only as a fallback.

    A transport that reports a durable outcome or a status code has already
    said what happened, so its message is not read as well: a rejection like
    ``400 max_output_tokens must be <= 500`` otherwise matches the free-text
    ``500`` pattern and buys an identical, certainly-failing replay.  The
    scheduling predicate (``is_authored_provider_overload``) still owns which
    statuses and which message shapes count, so this narrows the inputs it is
    given and never widens them.
    """

    if error is None:
        return False
    if isinstance(error, ProviderOutcomeError):
        return bool(error.overload)
    if isinstance(error, TimeoutError):
        return True
    status_code = provider_status_code(error)
    if status_code is not None:
        return is_authored_provider_overload(status_code)
    return is_authored_provider_overload(error)


class BoundedProviderCall:
    """One provider call with a bounded, receipt-visible overload replay."""

    __slots__ = (
        "_run",
        "_abort_event",
        "_terminal",
        "_idempotency_capable",
        "_reserve_retry",
        "_clock",
        "_sleep",
        "_deadline_seconds",
        "_receipt",
    )

    def __init__(
        self,
        run: Callable[[float | None], Any],
        abort_event: threading.Event,
        terminal: ProviderTerminalOutcomeRecorder | None = None,
        *,
        idempotency_capable: bool = False,
        replay_unavailable: str | None = None,
        reserve_retry: Callable[[int], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        deadline_seconds: float | None = None,
    ) -> None:
        self._run = run
        self._abort_event = abort_event
        self._terminal = terminal
        self._idempotency_capable = idempotency_capable
        self._reserve_retry = reserve_retry
        self._clock = clock
        self._sleep = sleep
        self._deadline_seconds = deadline_seconds
        self._receipt: dict[str, object] = {
            "schemaVersion": 1,
            "physicalCallCount": 0,
            "overloadRetryCount": 0,
            "cooldownWaitSeconds": 0.0,
            "deadlineExhausted": False,
            "quotaPauseResetAt": None,
            # A transport whose idempotency nobody confirmed still gets its one
            # bounded call; it just never gets a second.  Both facts are stated
            # here so a run that never replayed is not read as a run that had
            # nothing to replay.
            "idempotencyCapable": bool(idempotency_capable),
            "replayUnavailable": replay_unavailable,
        }

    def _replay_declined_reason(self) -> str | None:
        """Why this overload cannot be replayed, or ``None`` when it can be."""

        if not self._idempotency_capable:
            return "transport_idempotency_unconfirmed"
        if self._reserve_retry is None:
            return "attempt_reservation_unavailable"
        if int(self._receipt["physicalCallCount"]) >= _MAX_OVERLOAD_PHYSICAL_CALLS:
            return "physical_call_budget_exhausted"
        return None

    def __call__(self) -> Any:
        deadline = (
            self._clock() + self._deadline_seconds
            if self._deadline_seconds is not None
            else None
        )
        while True:
            remaining = None if deadline is None else max(0.0, deadline - self._clock())
            if remaining is not None and remaining <= 0.0:
                self._receipt["deadlineExhausted"] = True
                raise ProviderOutcomeError(
                    "public_provider_call_timeout",
                    recovery_allowed=False,
                    overload=True,
                )
            self._receipt["physicalCallCount"] = (
                int(self._receipt["physicalCallCount"]) + 1
            )
            try:
                return self._run(remaining)
            except ProviderQuotaPause as pause:
                self._receipt["quotaPauseResetAt"] = pause.reset_at
                raise
            except Exception as error:
                if not is_provider_overload(error):
                    raise
                declined = self._replay_declined_reason()
                if declined is not None:
                    self._receipt["replayUnavailable"] = declined
                    raise
                retry_ordinal = int(self._receipt["physicalCallCount"]) + 1
                cooldown = min(
                    OVERLOAD_COOLDOWN_BASE_SECONDS * (2 ** (retry_ordinal - 2)),
                    OVERLOAD_COOLDOWN_MAX_SECONDS,
                )
                if deadline is not None and self._clock() + cooldown >= deadline:
                    self._receipt["deadlineExhausted"] = True
                    self._receipt["replayUnavailable"] = "deadline_exhausted"
                    raise
                self._reserve_retry(retry_ordinal)
                self._sleep(cooldown)
                self._receipt["overloadRetryCount"] = (
                    int(self._receipt["overloadRetryCount"]) + 1
                )
                self._receipt["cooldownWaitSeconds"] = (
                    float(self._receipt["cooldownWaitSeconds"]) + cooldown
                )

    def abort(self) -> None:
        self._abort_event.set()

    @property
    def abort_requested(self) -> bool:
        return self._abort_event.is_set()

    @property
    def terminal_outcome(self) -> ProviderTerminalOutcome | None:
        return self._terminal.outcome if self._terminal is not None else None

    @property
    def receipt(self) -> Mapping[str, object]:
        """Content-free record of physical attempts and overload waiting."""

        return dict(self._receipt)


def _bounded_reset_epoch(error: BaseException, *, observed_at: int) -> int | None:
    candidates: list[object] = [error]
    response = getattr(error, "response", None)
    if response is not None:
        candidates.append(response)
    for candidate in candidates:
        for attribute in ("body", "error", "detail"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, Mapping):
                nested = value.get("error")
                payload = nested if isinstance(nested, Mapping) else value
                reset_at = payload.get("resets_at")
                if isinstance(reset_at, int) and not isinstance(reset_at, bool):
                    return max(reset_at, observed_at)
                reset_in = payload.get("resets_in_seconds")
                if isinstance(reset_in, int) and not isinstance(reset_in, bool):
                    return observed_at + max(0, reset_in)
    text = str(error)
    reset_at_match = _RESET_AT_PATTERN.search(text)
    if reset_at_match:
        return max(observed_at, int(reset_at_match.group(1)))
    reset_in_match = _RESET_IN_PATTERN.search(text)
    if reset_in_match:
        return observed_at + max(0, int(reset_in_match.group(1)))
    return None


def usage_limit_reset_epoch(error: BaseException) -> int | None:
    """Return the numeric reset only for an explicit usage_limit_reached cause."""

    current: BaseException | None = error
    visited: set[int] = set()
    observed_at = int(time.time())
    for _ in range(6):
        if current is None or id(current) in visited:
            return None
        visited.add(id(current))
        code = getattr(current, "code", None)
        if (
            (isinstance(code, str) and code.casefold() == _USAGE_LIMIT_CODE)
            or _USAGE_LIMIT_CODE in str(current).casefold()
        ):
            return _bounded_reset_epoch(current, observed_at=observed_at)
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return None


__all__ = [
    "BoundedProviderCall",
    "ProviderOutcomeError",
    "ProviderQuotaPause",
    "is_provider_overload",
    "provider_status_code",
    "usage_limit_reset_epoch",
]
