"""Repo-local stable-idempotency adapter for confirmed llmai/OpenAI clients."""

from __future__ import annotations

import asyncio
from copy import copy
import logging
import threading
import time
from types import MethodType
from typing import Any, Callable, Mapping

import httpx

from services.authored_batch_policy import OVERLOAD_COOLDOWN_BASE_SECONDS
from utils.provider_attempt_reservations import (
    ProviderAttemptReservationLease,
    lease_provider_attempt_reservations,
)
from utils.provider_overload import (
    BoundedProviderCall,
    ProviderOutcomeError,
    ProviderQuotaPause,
    ProviderRetryReservation,
    is_provider_overload,
    provider_retry_after_seconds,
    provider_status_code,
    safe_provider_diagnostics,
    usage_limit_reset_epoch,
)
from utils.provider_stream_guard import (
    GuardedResponsesClient as _GuardedResponsesClient,
    ProviderCallAborted as ProviderCallAborted,
)
from utils.provider_terminal_outcome import (
    ProviderTerminalOutcomeRecorder,
)


LOGGER = logging.getLogger(__name__)

_SUPPORTED_PROVIDER_IDS = frozenset({"openai"})
_LLMAI_OPENAI_MODULE_PREFIX = "llmai.openai."
_LLMAI_CHATGPT_MODULE_PREFIX = "llmai.chatgpt."
#: One authored slide attempt: it may hold an attempt reservation of its own.
_AUTHORED_OUTCOME_SCOPE = "authored"
#: A deck-level authored call (the vision advisory) -- same outcome mapping and
#: quota pause, but no slide attempt, so it must never spend a slide's
#: reservation.  Stated here, at the only constructor of a bounded call, rather
#: than as a guard each deck-level caller could forget.
_AUTHORED_DECK_OUTCOME_SCOPE = "authored_deck"
_AUTHORED_OUTCOME_SCOPES = frozenset(
    {_AUTHORED_OUTCOME_SCOPE, _AUTHORED_DECK_OUTCOME_SCOPE}
)

# One provider call must not outlive its slice of the job budget.  Measured
# three-slide authoring calls land between ~170s and ~240s, so this only fires
# on a call that is genuinely stuck.  The whole-job budgets are enforced
# elsewhere and carry their own codes -- ``public_deadline_exceeded`` from
# admission and ``public_worker_job_expired`` from the isolated worker -- so a
# call that trips this limit is never confused with either.  A caller with a
# stage deadline of its own (the content brief, whose measured wall is 325-455
# s) passes it as ``timeout_seconds`` and it replaces this generic guard.
PROVIDER_CALL_TIMEOUT_SECONDS = 360.0
# The value above (or the caller's own deadline) is the *read* timeout: httpx
# applies it to every gap between two received bytes, so it bounds a stuck
# stream, not a long one.  The other phases get their own short limits -- a
# connection that cannot be opened in 30 s will not open in 360 s, and waiting
# that long only delays the page's recovery.  None of them may exceed the
# read value, so a caller's short deadline stays the tightest bound.
PROVIDER_CONNECT_TIMEOUT_SECONDS = 30.0
PROVIDER_WRITE_TIMEOUT_SECONDS = 60.0
PROVIDER_POOL_TIMEOUT_SECONDS = 30.0


_TIMEOUT_UNENFORCEABLE_PROVIDERS: set[str] = set()


def _warn_timeout_unenforceable(provider_id: str, reason: str) -> None:
    """Say once, per provider, that this transport cannot bound a call."""

    if provider_id in _TIMEOUT_UNENFORCEABLE_PROVIDERS:
        return
    _TIMEOUT_UNENFORCEABLE_PROVIDERS.add(provider_id)
    LOGGER.warning(
        "provider_call_timeout_not_enforced provider=%s reason=%s seconds=%s",
        provider_id,
        reason,
        PROVIDER_CALL_TIMEOUT_SECONDS,
    )


_WALL_TIMEOUT_UNENFORCEABLE_PROVIDERS: set[str] = set()


def _warn_wall_timeout_unenforceable(provider_id: str, reason: str) -> None:
    """Say once, per provider, that this transport cannot cap a call's wall time."""

    if provider_id in _WALL_TIMEOUT_UNENFORCEABLE_PROVIDERS:
        return
    _WALL_TIMEOUT_UNENFORCEABLE_PROVIDERS.add(provider_id)
    LOGGER.warning(
        "provider_call_wall_timeout_not_enforced provider=%s reason=%s",
        provider_id,
        reason,
    )


_PHASE_TIMEOUT_UNSUPPORTED_PROVIDERS: set[str] = set()


def _warn_phase_timeout_unsupported(provider_id: str) -> None:
    """Say once, per provider, that this transport takes no per-phase timeout."""

    if provider_id in _PHASE_TIMEOUT_UNSUPPORTED_PROVIDERS:
        return
    _PHASE_TIMEOUT_UNSUPPORTED_PROVIDERS.add(provider_id)
    LOGGER.warning("provider_phase_timeout_not_supported provider=%s", provider_id)


def _phase_timeout(read_seconds: float) -> httpx.Timeout:
    """One request's per-phase transport timeouts around its read timeout."""

    return httpx.Timeout(
        connect=min(PROVIDER_CONNECT_TIMEOUT_SECONDS, read_seconds),
        read=read_seconds,
        write=min(PROVIDER_WRITE_TIMEOUT_SECONDS, read_seconds),
        pool=min(PROVIDER_POOL_TIMEOUT_SECONDS, read_seconds),
    )


def _clone_raw_client(
    raw_client: Any,
    options: Mapping[str, Any],
    *,
    provider_id: str,
    timeout_seconds: float | None = None,
) -> Any:
    """Clone the transport with a request timeout, degrading loudly if refused."""

    read_timeout = (
        PROVIDER_CALL_TIMEOUT_SECONDS
        if timeout_seconds is None
        else max(0.05, float(timeout_seconds))
    )
    try:
        return raw_client.with_options(**options, timeout=_phase_timeout(read_timeout))
    except TypeError:
        _warn_phase_timeout_unsupported(provider_id)
    try:
        # A transport that takes only one number still gets the read bound,
        # applied to every phase as before.
        return raw_client.with_options(**options, timeout=read_timeout)
    except TypeError:
        # This transport takes no request timeout.  Keep the existing
        # retry/idempotency contract rather than failing the call, and say so.
        _warn_timeout_unenforceable(provider_id, "with_options_rejected_timeout")
        return raw_client.with_options(**options) if options else raw_client


def _is_call_timeout(error: BaseException) -> bool:
    """Recognize a transport timeout across provider SDKs without importing them."""

    current: BaseException | None = error
    visited: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in visited:
            return False
        visited.add(id(current))
        if isinstance(current, TimeoutError):
            return True
        if "timeout" in type(current).__name__.casefold():
            return True
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return False


_TIMEOUT_PHASES: tuple[tuple[type[BaseException], str], ...] = (
    (httpx.ConnectTimeout, "connect"),
    (httpx.ReadTimeout, "read"),
    (httpx.WriteTimeout, "write"),
    (httpx.PoolTimeout, "pool"),
)


def _timeout_phase(error: BaseException) -> str | None:
    """Name the httpx phase whose timeout fired, or ``None`` when unknown."""

    current: BaseException | None = error
    visited: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in visited:
            return None
        visited.add(id(current))
        for timeout_type, phase in _TIMEOUT_PHASES:
            if isinstance(current, timeout_type):
                return phase
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return None


def _provider_outcome_cause(error: BaseException) -> ProviderOutcomeError | None:
    current: BaseException | None = error
    visited: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in visited:
            return None
        visited.add(id(current))
        if isinstance(current, ProviderOutcomeError):
            return current
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return None


def _content_has_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(
            _content_has_text(value.get(key)) for key in ("text", "content")
            if key in value
        )
    if isinstance(value, (list, tuple)):
        return any(_content_has_text(item) for item in value)
    for attribute in ("text", "content"):
        nested = getattr(value, attribute, None)
        if nested is not None and _content_has_text(nested):
            return True
    return False


def supports_provider_idempotency(client: Any, *, provider_id: str) -> bool:
    """Declare support only for the inspected llmai OpenAI SDK transport."""

    raw_client = getattr(client, "_client", None)
    return bool(
        provider_id in _SUPPORTED_PROVIDER_IDS
        and type(client).__module__.startswith(_LLMAI_OPENAI_MODULE_PREFIX)
        and callable(getattr(raw_client, "with_options", None))
    )


class _AuthoredRetryReservation:
    """This one bounded call's claim on the authored attempt reservations.

    A reservation authorizes a single physical replay of the call that spends
    it, so it must leave the context with that call -- otherwise the next call
    in the same task (a different slide, a fallback request, an unrelated
    structured call) could reserve a physical ordinal against an attempt token
    that is not its own.  But it must leave *when that call starts*, not when
    this wrapper is built: a wrapper that an early return or a cancelled
    executor hand-off then drops would otherwise spend a reservation for a
    physical call that never happened, and leave the call that does run with
    nothing to replay.

    ``claim`` is therefore made by :class:`BoundedProviderCall` as the physical
    request begins, from the SDK thread it runs on.  That thread cannot rebind
    the caller's ``ContextVar``, which is why the ledger keeps the reservations
    in a shared holder and hands out leases against it.
    """

    __slots__ = ("_lease", "_loop", "_reserve")

    def __init__(
        self,
        lease: ProviderAttemptReservationLease,
        loop: asyncio.AbstractEventLoop,
        reserve: Callable[..., Any],
    ) -> None:
        self._lease = lease
        self._loop = loop
        self._reserve = reserve

    @property
    def pending(self) -> bool:
        return self._lease.available

    def claim(self) -> None:
        self._lease.claim()

    def __call__(self, physical_ordinal: int) -> None:
        reservations = self._lease.claim()
        if not reservations:
            raise RuntimeError("authored_provider_attempt_reservation_unavailable")
        future = asyncio.run_coroutine_threadsafe(
            self._reserve(reservations, physical_ordinal=physical_ordinal),
            self._loop,
        )
        future.result()


def _authored_retry_reserver() -> _AuthoredRetryReservation | None:
    """Lease this authored call's attempt reservations, spending none yet.

    The ledger coroutine is resolved here, on the caller's loop thread: the
    lease is later claimed and spent from the authoring executor's SDK thread,
    and a first import taken there could contend with the main thread's.
    """

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    lease = lease_provider_attempt_reservations()
    if lease is None:
        return None
    from services.authored_checkpoint_state import reserve_provider_overload_retry

    return _AuthoredRetryReservation(lease, loop, reserve_provider_overload_retry)


def provider_generate_call(
    client: Any,
    *,
    provider_id: str,
    kwargs: Mapping[str, Any],
    idempotency_key: str | None,
    outcome_scope: str | None = None,
    timeout_seconds: float | None = None,
    retry_reserver: ProviderRetryReservation | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    wall_timeout_seconds: float | None = None,
) -> BoundedProviderCall:
    """Return one bounded provider call with optional authored outcome guards.

    The shared llmai client is never mutated, which keeps parallel slide calls
    from leaking one operation's key into another request.

    ``timeout_seconds`` is the caller's own deadline for this one request.  It
    replaces the generic ``PROVIDER_CALL_TIMEOUT_SECONDS`` guard on the cloned
    transport and also pins ``max_retries`` to zero: a bounded call that the
    SDK silently replayed after a timeout would run past its deadline and make
    a second physical request under one admission.

    ``wall_timeout_seconds`` is an opt-in cap on how long one physical call may
    keep streaming, measured from the start of that request and checked on
    every stream event of a Responses transport.  It changes nothing else --
    not the per-phase transport timeouts, not the bounded-call deadline, not
    ``max_retries`` -- so a gap between two events is still bounded only by the
    read timeout and the worst case is wall plus read.  A call that exceeds it
    fails as a replayable ``public_provider_call_timeout`` with
    ``timeoutPhase=wall``.
    """

    if outcome_scope is not None and outcome_scope not in _AUTHORED_OUTCOME_SCOPES:
        raise ValueError("provider outcome scope is unsupported")
    wall_seconds = (
        None if wall_timeout_seconds is None else max(0.05, float(wall_timeout_seconds))
    )
    generate_kwargs = dict(kwargs)
    # ``outcome_scope`` is the only authority for the authored guards.  A
    # pending attempt reservation is not one: the streaming author path omits
    # the scope deliberately (the guard reads a document's text and a stream
    # returns an iterator), and every unrelated call a task makes while an
    # authored attempt is open would otherwise inherit the authored outcome
    # mapping, the quota pause and another slide's retry reservation.
    authored_request = outcome_scope in _AUTHORED_OUTCOME_SCOPES
    # Only a slide attempt owns a reservation.  A deck-level authored call runs
    # while a slide's reservation may still be pending in the same task, so
    # leasing one here would reserve a physical replay against another slide's
    # attempt token.
    slide_attempt_request = outcome_scope == _AUTHORED_OUTCOME_SCOPE
    # ``generate(stream=True)`` returns a generator: the request is issued at
    # iteration, outside ``BoundedProviderCall.__call__``, so no overload can
    # ever reach its replay branch on any transport.  A wrapper that cannot
    # replay must not hold a reservation either.
    streamed_response = bool(generate_kwargs.get("stream"))
    selected_retry_reserver = retry_reserver
    if selected_retry_reserver is None and slide_attempt_request:
        selected_retry_reserver = _authored_retry_reserver()
    if streamed_response:
        selected_retry_reserver = None
    is_chatgpt = any(
        cls.__module__.startswith(_LLMAI_CHATGPT_MODULE_PREFIX)
        for cls in type(client).__mro__
    )
    can_clone_raw_client = callable(
        getattr(getattr(client, "_client", None), "with_options", None)
    )
    should_adapt = supports_provider_idempotency(client, provider_id=provider_id) or (
        authored_request
        and is_chatgpt
        and can_clone_raw_client
    )
    adapted_client = client
    abort_event = threading.Event()
    # Filled by the stream wrapper from ``response.completed`` /
    # ``response.incomplete``; read back through ``call.terminal_outcome``.
    terminal = ProviderTerminalOutcomeRecorder()
    options: dict[str, Any] = {}
    if should_adapt or timeout_seconds is not None or authored_request:
        options["max_retries"] = 0
    if should_adapt and idempotency_key and supports_provider_idempotency(
        client, provider_id=provider_id
    ):
        options["extra_headers"] = {"Idempotency-Key": idempotency_key}
    if can_clone_raw_client:
        # Clone for the request timeout even when nothing else needs adapting.
        # A stuck call otherwise holds one of the bounded authoring executor
        # threads for the whole job: that executor runs the provider SDK
        # synchronously, so cancelling the awaiting task frees nothing.
        raw_client = _clone_raw_client(
            client._client,
            options,
            provider_id=provider_id,
            timeout_seconds=timeout_seconds,
        )
        adapted_client = copy(client)
        # Every transport that exposes a Responses resource gets the abortable
        # stream wrapper: the chatgpt client streams even a "non-streaming"
        # generate() and would otherwise run to completion behind a cancelled
        # await.  The terminal-event outcome guards stay authored-only.
        adapted_client._client = (
            _GuardedResponsesClient(
                raw_client,
                abort_event=abort_event,
                guard_outcomes=(
                    authored_request and is_chatgpt
                ),
                terminal=terminal,
                wall_timeout_seconds=wall_seconds,
                clock=clock,
            )
            if getattr(raw_client, "responses", None) is not None
            else raw_client
        )
        if wall_seconds is not None and getattr(raw_client, "responses", None) is None:
            _warn_wall_timeout_unenforceable(provider_id, "no_responses_stream")
    else:
        _warn_timeout_unenforceable(provider_id, "raw_client_not_cloneable")
        if wall_seconds is not None:
            _warn_wall_timeout_unenforceable(provider_id, "raw_client_not_cloneable")
    if callable(getattr(adapted_client, "_get_openai_chat_max_tokens_kwargs", None)):
        # llmai 0.3.7 normally retries a rejected output-token parameter once
        # inside `_create_chat_completion`. That hidden second network request
        # would bypass public admission. The cloned request client therefore
        # uses the already selected field exactly once; a 400/422 is surfaced
        # to the explicit, bounded public retry policy.
        def create_chat_completion_once(
            adapted_self: Any,
            *,
            model: str,
            max_tokens: int | None,
            **chat_kwargs: object,
        ) -> Any:
            token_kwargs = adapted_self._get_openai_chat_max_tokens_kwargs(
                max_tokens,
                model=model,
            )
            return adapted_self._client.chat.completions.create(
                model=model,
                **chat_kwargs,
                **token_kwargs,
            )

        adapted_client._create_chat_completion = MethodType(
            create_chat_completion_once,
            adapted_client,
        )
    def generate_once(_remaining_seconds: float | None) -> Any:
        if can_clone_raw_client and _remaining_seconds is not None:
            request_raw_client = _clone_raw_client(
                client._client,
                options,
                provider_id=provider_id,
                timeout_seconds=_remaining_seconds,
            )
            adapted_client._client = (
                _GuardedResponsesClient(
                    request_raw_client,
                    abort_event=abort_event,
                    guard_outcomes=(authored_request and is_chatgpt),
                    terminal=terminal,
                    wall_timeout_seconds=wall_seconds,
                    clock=clock,
                )
                if getattr(request_raw_client, "responses", None) is not None
                else request_raw_client
            )
        try:
            response = adapted_client.generate(**generate_kwargs)
        except Exception as error:
            provider_outcome = _provider_outcome_cause(error)
            if provider_outcome is not None:
                observed = getattr(provider_outcome, "terminal_outcome", None)
                if observed is not None:
                    terminal.outcome = observed
                provider_outcome.diagnostics = safe_provider_diagnostics(
                    error,
                    diagnostics={**terminal.diagnostics, **provider_outcome.diagnostics},
                )
                if provider_outcome is error:
                    raise
                # The bridge already points at this outcome. Pointing it back
                # at the bridge would create a cause cycle and hide the source.
                raise provider_outcome from provider_outcome.__cause__
            if not authored_request:
                # Structured calls keep the SDK exception and its existing
                # retry policy, but must not lose a terminal event observed
                # before the SDK's local parser failed.
                if terminal.diagnostics:
                    try:
                        error.diagnostics = safe_provider_diagnostics(
                            error, diagnostics=terminal.diagnostics,
                        )
                    except Exception:
                        # Diagnostic attachment cannot replace the failure.
                        pass
                raise
            reset_at = usage_limit_reset_epoch(error)
            if reset_at is not None:
                raise ProviderQuotaPause(reset_at=reset_at, diagnostics=safe_provider_diagnostics(error)) from error
            if _is_call_timeout(error):
                # Distinct from the generic bridge failure so the batch policy
                # can treat a stuck call as overload and the failed slides
                # still get their one recovery call.
                timeout_diagnostics: dict[str, object] = {"failureKind": "timeout"}
                phase = _timeout_phase(error)
                if phase is not None:
                    timeout_diagnostics["timeoutPhase"] = phase
                raise ProviderOutcomeError(
                    "public_provider_call_timeout",
                    recovery_allowed=True,
                    overload=True,
                    diagnostics=safe_provider_diagnostics(error, diagnostics=timeout_diagnostics),
                ) from error
            # llmai's local stream wrapper assigns a synthetic HTTP 500 to an
            # exception with no HTTP evidence. It is not an overload receipt.
            nested = getattr(error, "cause", None) or getattr(error, "__cause__", None)
            synthetic_stream_status = (
                type(error).__module__.startswith("llmai.")
                and provider_status_code(error) == 500
                and isinstance(nested, BaseException)
                and provider_status_code(nested) is None
            )
            if synthetic_stream_status or safe_provider_diagnostics(error).get("statusOrigin") in {"local_stream", "stream_event"}:
                raise ProviderOutcomeError(
                    "public_provider_result_unknown",
                    recovery_allowed=False,
                    diagnostics=safe_provider_diagnostics(nested if synthetic_stream_status else error, diagnostics={"statusOrigin": "local_stream", "failureKind": "result_unknown"}),
                ) from error
            if is_provider_overload(error):
                retry_after = provider_retry_after_seconds(error)
                raise ProviderOutcomeError(
                    "public_provider_overload",
                    recovery_allowed=True,
                    overload=True,
                    cooldown_seconds=OVERLOAD_COOLDOWN_BASE_SECONDS if retry_after is None else retry_after,
                    cooldown_from_retry_after=retry_after is not None,
                    diagnostics=safe_provider_diagnostics(error, diagnostics={"failureKind": "overload"}),
                ) from error
            raise ProviderOutcomeError(
                "public_provider_bridge_exception",
                recovery_allowed=False,
                diagnostics=safe_provider_diagnostics(error, diagnostics={"failureKind": "bridge_exception"}),
            ) from error
        content = getattr(response, "content", None)
        # llmai returns parsed JSON objects for structured responses. An empty
        # array inside one of those objects is still a real model answer.
        structured_output = isinstance(content, Mapping) and bool(
            generate_kwargs.get("response_format")
        )
        if authored_request and not (structured_output or _content_has_text(content)):
            raise ProviderOutcomeError(
                "public_provider_empty_output",
                recovery_allowed=True,
                diagnostics=safe_provider_diagnostics(diagnostics={**terminal.diagnostics, "failureKind": "empty_output"}),
            )
        return response

    idempotency_capable = authored_request and supports_provider_idempotency(
        client, provider_id=provider_id
    )
    replay_unavailable: str | None = None
    if streamed_response:
        # Structural, not transport-specific: the overload surfaces at
        # iteration, where the bounded wrapper has already returned.  Saying so
        # at construction is the only way the receipt can distinguish this from
        # a call that simply never needed a replay.
        replay_unavailable = "stream_response_not_replayable"
    elif outcome_scope == _AUTHORED_DECK_OUTCOME_SCOPE:
        replay_unavailable = "deck_scope_no_slide_attempt"
    elif authored_request and not idempotency_capable:
        # The chatgpt/codex transport confirms no request-level idempotency, so
        # replaying an overload could bill and publish a second document.  The
        # call still runs once -- the rescue keeps its one bounded attempt --
        # and the receipt names the reason instead of reading as a call that
        # simply never needed a replay.
        replay_unavailable = "transport_idempotency_unconfirmed"
    elif authored_request and selected_retry_reserver is None:
        replay_unavailable = "attempt_reservation_unavailable"
    return BoundedProviderCall(
        generate_once,
        abort_event,
        terminal,
        idempotency_capable=idempotency_capable,
        replay_unavailable=replay_unavailable,
        reserve_retry=selected_retry_reserver,
        clock=clock,
        sleep=sleep,
        deadline_seconds=(
            PROVIDER_CALL_TIMEOUT_SECONDS
            if timeout_seconds is None
            else max(0.05, float(timeout_seconds))
        ),
    )


#: Deck-level authored calls declare this instead of the slide scope.
AUTHORED_DECK_OUTCOME_SCOPE = _AUTHORED_DECK_OUTCOME_SCOPE


__all__ = [
    "AUTHORED_DECK_OUTCOME_SCOPE",
    "PROVIDER_CALL_TIMEOUT_SECONDS",
    "PROVIDER_CONNECT_TIMEOUT_SECONDS",
    "PROVIDER_POOL_TIMEOUT_SECONDS",
    "PROVIDER_WRITE_TIMEOUT_SECONDS",
    "BoundedProviderCall",
    "ProviderCallAborted",
    "ProviderOutcomeError",
    "ProviderQuotaPause",
    "provider_generate_call",
    "supports_provider_idempotency",
]
