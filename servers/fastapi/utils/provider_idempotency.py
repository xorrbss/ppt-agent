"""Repo-local stable-idempotency adapter for confirmed llmai/OpenAI clients."""

from __future__ import annotations

import asyncio
from copy import copy
import logging
import threading
import time
from types import MethodType
from typing import Any, Callable, Mapping

from services.authored_batch_policy import OVERLOAD_COOLDOWN_BASE_SECONDS
from utils.provider_overload import (
    BoundedProviderCall,
    ProviderOutcomeError,
    ProviderQuotaPause,
    is_provider_overload,
    usage_limit_reset_epoch,
)
from utils.provider_terminal_outcome import (
    ProviderTerminalOutcomeRecorder,
)


LOGGER = logging.getLogger(__name__)

_SUPPORTED_PROVIDER_IDS = frozenset({"openai"})
_LLMAI_OPENAI_MODULE_PREFIX = "llmai.openai."
_LLMAI_CHATGPT_MODULE_PREFIX = "llmai.chatgpt."
_AUTHORED_OUTCOME_SCOPE = "authored"

# One provider call must not outlive its slice of the job budget.  Measured
# three-slide authoring calls land between ~170s and ~240s, so this only fires
# on a call that is genuinely stuck.  The whole-job budgets are enforced
# elsewhere and carry their own codes -- ``public_deadline_exceeded`` from
# admission and ``public_worker_job_expired`` from the isolated worker -- so a
# call that trips this limit is never confused with either.  A caller with a
# stage deadline of its own (the content brief, whose measured wall is 325-455
# s) passes it as ``timeout_seconds`` and it replaces this generic guard.
PROVIDER_CALL_TIMEOUT_SECONDS = 360.0


class ProviderCallAborted(RuntimeError):
    """The caller abandoned this call at its deadline and its stream was closed."""


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


def _clone_raw_client(
    raw_client: Any,
    options: Mapping[str, Any],
    *,
    provider_id: str,
    timeout_seconds: float | None = None,
) -> Any:
    """Clone the transport with a request timeout, degrading loudly if refused."""

    timeout = (
        PROVIDER_CALL_TIMEOUT_SECONDS
        if timeout_seconds is None
        else max(0.05, float(timeout_seconds))
    )
    try:
        return raw_client.with_options(**options, timeout=timeout)
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


class _GuardedResponsesResource:
    """Wrap ``responses.create`` so a streamed call can be aborted and, for the
    authored scope, so terminal stream events become durable outcomes."""

    def __init__(
        self,
        resource: Any,
        *,
        abort_event: threading.Event,
        guard_outcomes: bool,
        terminal: ProviderTerminalOutcomeRecorder | None = None,
    ) -> None:
        self._resource = resource
        self._abort_event = abort_event
        self._guard_outcomes = guard_outcomes
        self._terminal = terminal or ProviderTerminalOutcomeRecorder()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resource, name)

    def create(self, *args: Any, **kwargs: Any) -> Any:
        stream = self._resource.create(*args, **kwargs)
        if not hasattr(stream, "__next__"):
            # A non-streaming response is complete on return: nothing to abort,
            # and the SDK request timeout has already bounded it.
            self._terminal.observe_response(stream)
            return stream

        def guarded_events():
            try:
                for event in stream:
                    if self._abort_event.is_set():
                        raise ProviderCallAborted("provider_call_aborted")
                    # Recorded for every transport, before the authored-only
                    # guard below can turn the same event into an outcome error.
                    self._terminal.observe_event(event)
                    if self._guard_outcomes:
                        _raise_for_terminal_event(event)
                    yield event
            finally:
                if self._abort_event.is_set():
                    _close_quietly(stream)

        return guarded_events()


def _raise_for_terminal_event(event: Any) -> None:
    event_type = getattr(event, "type", None)
    if event_type == "response.failed":
        raise ProviderOutcomeError(
            "public_provider_response_failed",
            recovery_allowed=True,
        )
    if event_type == "response.incomplete":
        raise ProviderOutcomeError(
            "public_provider_response_incomplete",
            # The Responses API reports incomplete only after it has
            # accepted and bounded the request (for example at its
            # output limit). Replaying the unchanged call is not a
            # transport recovery.
            recovery_allowed=False,
        )


def _close_quietly(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            LOGGER.debug("Failed to close an aborted provider stream", exc_info=True)


class _GuardedResponsesClient:
    def __init__(
        self,
        client: Any,
        *,
        abort_event: threading.Event,
        guard_outcomes: bool,
        terminal: ProviderTerminalOutcomeRecorder | None = None,
    ) -> None:
        self._client = client
        self.responses = _GuardedResponsesResource(
            client.responses,
            abort_event=abort_event,
            guard_outcomes=guard_outcomes,
            terminal=terminal,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def supports_provider_idempotency(client: Any, *, provider_id: str) -> bool:
    """Declare support only for the inspected llmai OpenAI SDK transport."""

    raw_client = getattr(client, "_client", None)
    return bool(
        provider_id in _SUPPORTED_PROVIDER_IDS
        and type(client).__module__.startswith(_LLMAI_OPENAI_MODULE_PREFIX)
        and callable(getattr(raw_client, "with_options", None))
    )


def _authored_retry_reserver() -> Callable[[int], None] | None:
    """Take this authored call's attempt reservations into a retry closure.

    The reservations are consumed, not snapshotted.  They authorize one
    physical replay of the call being built here, so leaving them in the
    context would hand the same physical ordinal to whatever this task calls
    next -- a different slide, a fallback request, or an unrelated structured
    call -- and spend another slide's provider budget under its attempt token.
    Consumption happens here, in the caller's own task, because the returned
    closure runs on an SDK thread whose context is a different one.
    """

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    from services.authored_checkpoint_state import (
        reserve_provider_overload_retry,
        take_provider_attempt_reservations,
    )

    reservations = take_provider_attempt_reservations()
    if not reservations:
        return None

    def reserve(physical_ordinal: int) -> None:
        future = asyncio.run_coroutine_threadsafe(
            reserve_provider_overload_retry(
                reservations,
                physical_ordinal=physical_ordinal,
            ),
            loop,
        )
        future.result()

    return reserve


def provider_generate_call(
    client: Any,
    *,
    provider_id: str,
    kwargs: Mapping[str, Any],
    idempotency_key: str | None,
    outcome_scope: str | None = None,
    timeout_seconds: float | None = None,
    retry_reserver: Callable[[int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> BoundedProviderCall:
    """Return one bounded provider call with optional authored outcome guards.

    The shared llmai client is never mutated, which keeps parallel slide calls
    from leaking one operation's key into another request.

    ``timeout_seconds`` is the caller's own deadline for this one request.  It
    replaces the generic ``PROVIDER_CALL_TIMEOUT_SECONDS`` guard on the cloned
    transport and also pins ``max_retries`` to zero: a bounded call that the
    SDK silently replayed after a timeout would run past its deadline and make
    a second physical request under one admission.
    """

    if outcome_scope not in {None, _AUTHORED_OUTCOME_SCOPE}:
        raise ValueError("provider outcome scope is unsupported")
    generate_kwargs = dict(kwargs)
    # ``outcome_scope`` is the only authority for the authored guards.  A
    # pending attempt reservation is not one: the streaming author path omits
    # the scope deliberately (the guard reads a document's text and a stream
    # returns an iterator), and every unrelated call a task makes while an
    # authored attempt is open would otherwise inherit the authored outcome
    # mapping, the quota pause and another slide's retry reservation.
    authored_request = outcome_scope == _AUTHORED_OUTCOME_SCOPE
    selected_retry_reserver = retry_reserver
    if selected_retry_reserver is None and authored_request:
        selected_retry_reserver = _authored_retry_reserver()
    is_chatgpt = type(client).__module__.startswith(_LLMAI_CHATGPT_MODULE_PREFIX)
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
            )
            if getattr(raw_client, "responses", None) is not None
            else raw_client
        )
    else:
        _warn_timeout_unenforceable(provider_id, "raw_client_not_cloneable")
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
                )
                if getattr(request_raw_client, "responses", None) is not None
                else request_raw_client
            )
        try:
            response = adapted_client.generate(**generate_kwargs)
        except Exception as error:
            provider_outcome = _provider_outcome_cause(error)
            if provider_outcome is not None:
                if provider_outcome is error:
                    raise
                raise provider_outcome from error
            if not authored_request:
                raise
            reset_at = usage_limit_reset_epoch(error)
            if reset_at is not None:
                raise ProviderQuotaPause(reset_at=reset_at) from error
            if _is_call_timeout(error):
                # Distinct from the generic bridge failure so the batch policy
                # can treat a stuck call as overload and the failed slides
                # still get their one recovery call.
                raise ProviderOutcomeError(
                    "public_provider_call_timeout",
                    recovery_allowed=True,
                    overload=True,
                ) from error
            if is_provider_overload(error):
                raise ProviderOutcomeError(
                    "public_provider_overload",
                    recovery_allowed=True,
                    overload=True,
                    cooldown_seconds=OVERLOAD_COOLDOWN_BASE_SECONDS,
                ) from error
            raise ProviderOutcomeError(
                "public_provider_bridge_exception",
                recovery_allowed=False,
            ) from error
        if authored_request and not _content_has_text(
            getattr(response, "content", None)
        ):
            raise ProviderOutcomeError(
                "public_provider_empty_output",
                recovery_allowed=True,
            )
        return response

    idempotency_capable = authored_request and supports_provider_idempotency(
        client, provider_id=provider_id
    )
    replay_unavailable: str | None = None
    if authored_request and not idempotency_capable:
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


__all__ = [
    "PROVIDER_CALL_TIMEOUT_SECONDS",
    "BoundedProviderCall",
    "ProviderCallAborted",
    "ProviderOutcomeError",
    "ProviderQuotaPause",
    "provider_generate_call",
    "supports_provider_idempotency",
]
