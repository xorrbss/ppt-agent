"""Bounded authored-provider overload retries and durable quota pauses."""

from __future__ import annotations

import re
import math
import threading
import time
from contextvars import ContextVar
from typing import Any, Callable, Mapping, Protocol

from services.authored_batch_policy import (
    OVERLOAD_COOLDOWN_BASE_SECONDS,
    OVERLOAD_COOLDOWN_MAX_SECONDS,
    _RATE_LIMIT_PATTERN,
    _TIMEOUT_PATTERN,
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

_SAFE_ERROR_CODES = frozenset({
    "server_error", "rate_limit_exceeded", "usage_limit_reached", "insufficient_quota",
    "invalid_request_error", "authentication_error", "permission_denied", "model_not_found",
    "model_not_supported",
    "unsupported_model",
    "invalid_model",
    "invalid_api_key",
    "context_length_exceeded", "overloaded", "timeout", "request_timeout", "internal_error",
    "internal_server_error", "service_unavailable", "content_filter", "max_output_tokens",
})
_SAFE_EXCEPTION_TYPES = frozenset({
    "ProviderError", "ProviderOutcomeError", "ProviderQuotaPause", "RuntimeError", "ValueError",
    "TypeError", "KeyError", "AttributeError", "UnicodeDecodeError",
    "TimeoutError", "ConnectionError", "OSError", "APIError", "APIStatusError", "APIConnectionError",
    "APITimeoutError", "RateLimitError", "InternalServerError", "BadRequestError",
    "AuthenticationError", "PermissionDeniedError", "NotFoundError", "UnprocessableEntityError",
    "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
    "LLMAPIError", "LLMError", "LLMResponseError",
})
_SAFE_ERROR_TYPES = frozenset({
    "invalid_request_error", "authentication_error", "permission_error",
    "not_found_error", "rate_limit_error", "server_error", "timeout_error",
})
_SAFE_REQUEST_PARAMETERS = frozenset({
    "model", "reasoning", "reasoning.effort", "reasoning.summary", "temperature",
    "top_p", "max_tokens", "max_output_tokens", "stream", "store", "instructions",
    "input", "input.role", "input.content", "input.content.type", "input.content.text",
    "input.content.image_url", "input.content.file_id", "input.content.file_data",
    "tools", "tool_choice", "parallel_tool_calls", "text", "text.format",
    "text.verbosity", "response_format", "include", "metadata", "service_tier",
    "prompt_cache_key", "prompt_cache_retention",
})
_DIAGNOSTIC_ENUMS = {
    "exceptionType": _SAFE_EXCEPTION_TYPES,
    "errorCode": _SAFE_ERROR_CODES,
    "causeExceptionType": _SAFE_EXCEPTION_TYPES,
    "providerErrorCode": _SAFE_ERROR_CODES,
    "providerErrorType": _SAFE_ERROR_TYPES,
    "rejectedParameter": _SAFE_REQUEST_PARAMETERS,
    "requestRejectionReason": frozenset({
        "unsupported_parameter", "unsupported_value", "model_not_supported",
        "instructions_required", "store_false_required", "stream_true_required",
    }),
    "streamTermination": frozenset(
        {"raised_exception", "stream_exhausted_without_completion"}
    ),
    "terminalEvent": frozenset({"response.completed", "response.incomplete", "response.failed", "response.cancelled", "error"}),
    "failureKind": frozenset({"stream_interrupted", "result_unknown", "terminal_failed", "terminal_cancelled", "terminal_error", "incomplete", "overload", "timeout", "bridge_exception", "empty_output", "local_postresponse", "dispatch_stopped"}),
    "streamStage": frozenset({"before_stream", "stream_opened", "response_created", "output_delta", "terminal_event", "post_response"}),
    "statusOrigin": frozenset({"http_response", "local_stream", "transport", "stream_event"}),
    "incompleteReason": frozenset({"max_output_tokens", "content_filter", "incomplete"}),
    # ``wall`` is the opt-in per-call stream cap; the others name the httpx
    # transport phase whose timeout fired.
    "timeoutPhase": frozenset({"connect", "read", "write", "pool", "wall"}),
}
#: Stream timing measured by the Codex stream observer, in milliseconds or
#: events.  Non-negative integers only.
_DIAGNOSTIC_COUNTS = ("firstEventMs", "maxEventGapMs", "eventCount")
_MAX_DIAGNOSTIC_COUNT = 10**12
_SAFE_REQUEST_ID = re.compile(r"(?:req[_-])?(?:[0-9a-fA-F]{16,64}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z")
_SAFE_RESPONSE_ID = re.compile(r"resp_[A-Za-z0-9_-]{1,128}\Z")
_USAGE_FIELDS = frozenset({"input_tokens", "output_tokens", "total_tokens", "reasoning_tokens", "cached_tokens"})


_PROVIDER_CALL_DEADLINE = ContextVar("provider_call_absolute_deadline", default=None)


def current_provider_call_remaining_seconds():
    bound = _PROVIDER_CALL_DEADLINE.get()
    if bound is None or bound[0] is None:
        return None
    deadline, clock = bound
    return max(0.0, deadline - clock())


def _structured_provider_error_fields(error: BaseException) -> dict[str, object]:
    """Select SDK error fields without retaining its message or response body."""
    body = getattr(error, "body", None)
    details: Mapping[str, Any] = body if isinstance(body, Mapping) else {}
    inner = details.get("error")
    if isinstance(inner, Mapping):
        details = inner
    fields = {
        key: getattr(error, key, None) or details.get(key)
        for key in ("code", "param", "type")
    }
    # Some Codex rejections have no code/param fields. Classify only known
    # protocol messages; do not copy their text, quoted values, or model ID.
    message = details.get("message") or details.get("detail")
    if message is None:
        message = inner if isinstance(inner, str) else body if isinstance(body, str) else None
    if isinstance(message, str) and len(message) <= 4096:
        rejected = re.match(r"\AUnsupported parameter:\s*['\"]([a-zA-Z_][a-zA-Z0-9_.]{0,79})['\"](?:[.\s:]|$)", message)
        if rejected:
            fields["requestRejectionReason"] = "unsupported_parameter"
            if not fields["param"] and rejected[1] in _SAFE_REQUEST_PARAMETERS:
                fields["param"] = rejected[1]
        elif re.match(r"\AUnsupported value:\s", message):
            fields["requestRejectionReason"] = "unsupported_value"
        elif re.match(r"\A(?:(?:The )?['\"]gpt-[a-zA-Z0-9_.-]{1,128}['\"] model|Model ['\"]gpt-[a-zA-Z0-9_.-]{1,128}['\"]) is not supported\b", message):
            fields["requestRejectionReason"] = "model_not_supported"
        else:
            for prefix, reason in (
                ("Instructions are required", "instructions_required"),
                ("Store must be set to false", "store_false_required"),
                ("Stream must be set to true", "stream_true_required"),
            ):
                if re.match(re.escape(prefix) + r"(?:[.\s:]|$)", message, re.IGNORECASE):
                    fields["requestRejectionReason"] = reason
                    break
    return fields


def safe_provider_diagnostics(
    error: BaseException | None = None,
    *,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Keep only closed-vocabulary metadata and bounded numeric measurements.

    No message, body, header, dynamic class name or arbitrary SDK field is
    persisted. SDK error code, type and parameter are selected only when their
    values are in the closed vocabularies. Unknown values are never retained.
    """
    candidates: dict[str, Any] = {}
    nested = getattr(error, "cause", None) or getattr(error, "__cause__", None)
    synthetic_stream_status = (
        error is not None
        and type(error).__module__.startswith("llmai.")
        and provider_status_code(error) == 500
        and isinstance(nested, BaseException)
        and provider_status_code(nested) is None
    )
    current = error
    visited: set[int] = set()
    for depth in range(6):
        if current is None or id(current) in visited:
            break
        visited.add(id(current))
        fields = _structured_provider_error_fields(current)
        rejection_reason = fields.get("requestRejectionReason")
        if rejection_reason is not None:
            candidates.setdefault("requestRejectionReason", rejection_reason)
        raw_error_code = fields["code"]
        if isinstance(raw_error_code, str) and raw_error_code in _SAFE_ERROR_CODES:
            candidates.setdefault("errorCode", raw_error_code)
        elif isinstance(raw_error_code, str) and raw_error_code:
            candidates["unrecognizedErrorCode"] = True
        error_type = fields["type"]
        if isinstance(error_type, str) and error_type in _SAFE_ERROR_TYPES:
            candidates.setdefault("providerErrorType", error_type)
        parameter = fields["param"]
        if isinstance(parameter, str) and parameter:
            normalized = re.sub(r"\[\d{1,4}\]", "", parameter) if len(parameter) <= 128 else ""
            if normalized in _SAFE_REQUEST_PARAMETERS:
                candidates.setdefault("rejectedParameter", normalized)
            else:
                candidates["unrecognizedParameter"] = True
        name = type(current).__name__
        if "exceptionType" not in candidates:
            candidates["exceptionType"] = name if name in _SAFE_EXCEPTION_TYPES else "ProviderError"
        if depth and name in _SAFE_EXCEPTION_TYPES:
            candidates.setdefault("causeExceptionType", name)
        if depth and isinstance(raw_error_code, str) and raw_error_code in _SAFE_ERROR_CODES:
            candidates.setdefault("providerErrorCode", raw_error_code)
        status = provider_status_code(current)
        if status is not None and "httpStatus" not in candidates:
            candidates["httpStatus"] = status
            candidates["statusOrigin"] = "http_response"
        for attribute, key in (("code", "errorCode"), ("request_id", "requestId"), ("retry_after_seconds", "retryAfterSeconds")):
            value = getattr(current, attribute, None)
            if value is not None:
                candidates.setdefault(key, value)
        existing = getattr(current, "diagnostics", None)
        if isinstance(existing, Mapping):
            candidates.update(existing)
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    if isinstance(diagnostics, Mapping):
        candidates.update(diagnostics)
    if synthetic_stream_status:
        candidates["statusOrigin"] = "local_stream"
        candidates.setdefault("failureKind", "result_unknown")
    safe: dict[str, object] = {}
    for key, allowed in _DIAGNOSTIC_ENUMS.items():
        value = candidates.get(key)
        if isinstance(value, str) and value in allowed:
            safe[key] = value
    for key in ("unrecognizedErrorCode", "unrecognizedParameter"):
        if candidates.get(key) is True:
            safe[key] = True
    request_id = candidates.get("requestId")
    if isinstance(request_id, str) and _SAFE_REQUEST_ID.fullmatch(request_id):
        safe["requestId"] = request_id
    response_id = candidates.get("responseId")
    if isinstance(response_id, str) and _SAFE_RESPONSE_ID.fullmatch(response_id):
        safe["responseId"] = response_id
    stream_status = candidates.get("streamHttpStatus")
    if isinstance(stream_status, int) and not isinstance(stream_status, bool) and 100 <= stream_status <= 599:
        safe["streamHttpStatus"] = stream_status
    status = candidates.get("httpStatus")
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 and safe.get("statusOrigin") not in {"local_stream", "stream_event"}:
        safe["httpStatus"] = status
    for key in _DIAGNOSTIC_COUNTS:
        count = candidates.get(key)
        if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= _MAX_DIAGNOSTIC_COUNT:
            safe[key] = count
    retry = candidates.get("retryAfterSeconds")
    if isinstance(retry, (int, float)) and not isinstance(retry, bool) and 0 <= retry <= 604800 and math.isfinite(retry):
        safe["retryAfterSeconds"] = retry
    usage = candidates.get("usage")
    if isinstance(usage, Mapping):
        counts = {key: usage[key] for key in _USAGE_FIELDS if isinstance(usage.get(key), int) and not isinstance(usage[key], bool) and 0 <= usage[key] <= 10**12}
        if counts:
            safe["usage"] = counts
    return safe


class ProviderOutcomeError(RuntimeError):
    """Content-free provider outcome that survives the authored job boundary."""

    def __init__(
        self,
        code: str,
        *,
        recovery_allowed: bool,
        overload: bool = False,
        cooldown_seconds: float | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.retryable = False
        self.recovery_allowed = recovery_allowed
        self.overload = overload
        self.cooldown_seconds = cooldown_seconds
        self.diagnostics = safe_provider_diagnostics(diagnostics=diagnostics)
        super().__init__(code)


class ProviderQuotaPause(ProviderOutcomeError, WorkspaceOperationDeferred):
    """Whole-account usage exhaustion returned to the durable operation queue."""

    def __init__(self, *, reset_at: int, observed_at: int | None = None, diagnostics: Mapping[str, Any] | None = None) -> None:
        now = int(time.time()) if observed_at is None else int(observed_at)
        self.code = "public_provider_usage_limit_paused"
        self.retryable = False
        self.recovery_allowed = False
        self.overload = True
        self.cooldown_seconds = None
        self.diagnostics = safe_provider_diagnostics(diagnostics=diagnostics)
        self.reset_at = int(reset_at)
        self.retry_after_seconds = max(0.05, float(self.reset_at - now))
        self.detail = {
            "provider_quota_reset_at": self.reset_at,
            "provider_quota_resume_semantics": "coordinator_requeues_after_reset",
        }
        RuntimeError.__init__(self, self.code)


def is_page_recoverable_overload(error: BaseException) -> bool:
    """True when the producer marked this outcome as overload and replayable for the same page."""

    # The producer is the only authority: a transport timeout is replayable,
    # the deadline-exhausted timeout of ``BoundedProviderCall`` is not, and a
    # quota pause belongs to the durable operation queue, never to a page.
    return (
        isinstance(error, ProviderOutcomeError)
        and not isinstance(error, ProviderQuotaPause)
        and bool(error.overload)
        and bool(error.recovery_allowed)
    )


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


def _overload_message_signal(error: BaseException) -> bool:
    """Rate-limit / usage-limit / timeout text signals only -- never the bare
    HTTP-number pattern.

    Called only once a structured status code is already known and is not
    itself an overload code, so a coincidental number elsewhere in the
    message (``max_output_tokens must be <= 500``) must not match. The
    rate-limit and timeout text patterns cannot false-positive on that text
    and remain valid positive signals (e.g. a ``400`` ``usage limit
    reached``, or a ``408`` ``Request timed out.``).
    """

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
        if _TIMEOUT_PATTERN.search(normalized) or _RATE_LIMIT_PATTERN.search(normalized):
            return True
    return False


def is_provider_overload(error: BaseException | None) -> bool:
    """Classify overload from structured signals first, from text only as a fallback.

    A transport that reports a durable outcome or a status code has already
    said what happened, so its message is not read the same way: a rejection
    like ``400 max_output_tokens must be <= 500`` must not match the free-text
    ``500`` pattern and buy an identical, certainly-failing replay. But a
    structured status that is not itself an overload code can still carry an
    unambiguous rate-limit / usage-limit / timeout message (the transport
    encodes those as prose, not as a distinct status), so those two text
    patterns stay live even once a status code is known -- only the bare
    HTTP-number pattern is suppressed.  The scheduling predicate
    (``is_authored_provider_overload``) still owns which statuses and which
    message shapes count, so this narrows the inputs it is given and never
    widens them.
    """

    if error is None:
        return False
    if safe_provider_diagnostics(error).get("statusOrigin") in {"local_stream", "stream_event"}:
        return False
    if isinstance(error, ProviderOutcomeError):
        return bool(error.overload)
    if isinstance(error, TimeoutError):
        return True
    status_code = provider_status_code(error)
    if status_code is not None:
        if is_authored_provider_overload(status_code):
            return True
        return _overload_message_signal(error)
    return is_authored_provider_overload(error)


class ProviderRetryReservation(Protocol):
    """A bounded call's claim on the attempt reservations that let it replay.

    ``claim`` is what actually spends them, and the bounded call makes it as
    its first physical request begins -- so a wrapper that is built and then
    dropped spends nothing, and the reservations stay available to the call
    that does run.  ``pending`` says whether this claim can still authorize a
    replay, which is the receipt's authority for ``replayUnavailable``.
    """

    @property
    def pending(self) -> bool: ...

    def claim(self) -> None: ...

    def __call__(self, physical_ordinal: int) -> None: ...


class BoundedProviderCall:
    """One provider call with a bounded, receipt-visible overload replay."""

    __slots__ = (
        "_run",
        "_abort_event",
        "_terminal",
        "_idempotency_capable",
        "_replay_unavailable",
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
        reserve_retry: ProviderRetryReservation | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        deadline_seconds: float | None = None,
    ) -> None:
        self._run = run
        self._abort_event = abort_event
        self._terminal = terminal
        self._idempotency_capable = idempotency_capable
        self._replay_unavailable = replay_unavailable
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

        if self._replay_unavailable is not None:
            # Declared at construction: a streamed response, or a call scope
            # that owns no slide attempt.  Neither becomes replayable later.
            return self._replay_unavailable
        if not self._idempotency_capable:
            return "transport_idempotency_unconfirmed"
        if self._reserve_retry is None or not self._reserve_retry.pending:
            return "attempt_reservation_unavailable"
        if int(self._receipt["physicalCallCount"]) >= _MAX_OVERLOAD_PHYSICAL_CALLS:
            return "physical_call_budget_exhausted"
        return None

    def _claim_reservation(self) -> None:
        """Spend this call's attempt reservations as its physical call starts.

        Idempotent, and deliberately not done when the wrapper is built: a
        wrapper that is never invoked must leave the reservations for the call
        that is.
        """

        if self._reserve_retry is not None:
            self._reserve_retry.claim()

    def _run_with_deadline(self, remaining, deadline):
        token = _PROVIDER_CALL_DEADLINE.set((deadline, self._clock))
        try:
            return self._run(remaining)
        finally:
            _PROVIDER_CALL_DEADLINE.reset(token)

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
            self._claim_reservation()
            self._receipt["physicalCallCount"] = (
                int(self._receipt["physicalCallCount"]) + 1
            )
            try:
                return self._run_with_deadline(remaining, deadline)
            except ProviderQuotaPause as pause:
                self._receipt["quotaPauseResetAt"] = pause.reset_at
                self._receipt.setdefault("failureDiagnostics", []).append(safe_provider_diagnostics(pause))
                raise
            except Exception as error:
                self._receipt.setdefault("failureDiagnostics", []).append(safe_provider_diagnostics(error))
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
    "ProviderRetryReservation",
    "is_page_recoverable_overload",
    "is_provider_overload",
    "provider_status_code",
    "usage_limit_reset_epoch",
]
