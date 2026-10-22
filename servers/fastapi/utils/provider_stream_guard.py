"""Abortable provider response streams and their terminal-event outcomes.

Split out of ``utils.provider_idempotency`` so the adapter that builds one
bounded provider call stays readable; the behaviour is unchanged and
``provider_idempotency`` remains the only place that installs these wrappers.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from utils.provider_overload import ProviderOutcomeError
from utils.provider_terminal_outcome import (
    _TERMINAL_EVENT_TYPES,
    ProviderTerminalOutcomeRecorder,
)


LOGGER = logging.getLogger(__name__)

#: Stream stage reached by a raw Responses event, in the ``streamStage``
#: vocabulary of ``safe_provider_diagnostics``.  Other event types (reasoning
#: deltas, item bookkeeping) leave the stage where it was.
_STREAM_STAGE_BY_EVENT = {
    "response.created": "response_created",
    "response.output_text.delta": "output_delta",
}


class ProviderCallAborted(RuntimeError):
    """The caller abandoned this call at its deadline and its stream was closed."""


def wall_timeout_error(stream_stage: str) -> ProviderOutcomeError:
    """The outcome of a stream that outlived its caller's wall deadline.

    Shaped like the transport timeout ``provider_generate_call`` raises, so a
    consumer that replays a page after a transport timeout treats both alike.
    """

    return ProviderOutcomeError(
        "public_provider_call_timeout",
        recovery_allowed=True,
        overload=True,
        diagnostics={
            "failureKind": "timeout",
            "timeoutPhase": "wall",
            "streamStage": stream_stage,
        },
    )


def raise_for_terminal_event(event: Any) -> None:
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


def close_quietly(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            LOGGER.debug("Failed to close an aborted provider stream", exc_info=True)


class GuardedResponsesResource:
    """Wrap ``responses.create`` so a streamed call can be aborted and, for the
    authored scope, so terminal stream events become durable outcomes."""

    def __init__(
        self,
        resource: Any,
        *,
        abort_event: threading.Event,
        guard_outcomes: bool,
        terminal: ProviderTerminalOutcomeRecorder | None = None,
        wall_timeout_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resource = resource
        self._abort_event = abort_event
        self._guard_outcomes = guard_outcomes
        self._terminal = terminal or ProviderTerminalOutcomeRecorder()
        # Opt-in wall cap on one physical call.  The SDK read timeout bounds
        # only the gap between two events, so a stream that keeps trickling
        # can otherwise run for as long as the provider keeps sending.
        self._wall_timeout_seconds = wall_timeout_seconds
        self._clock = clock

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resource, name)

    def create(self, *args: Any, **kwargs: Any) -> Any:
        # Measured from the start of this physical request, not from the
        # bounded call: an earlier overload replay must not shorten this one.
        wall_deadline = (
            None
            if self._wall_timeout_seconds is None
            else self._clock() + self._wall_timeout_seconds
        )
        stream = self._resource.create(*args, **kwargs)
        if not hasattr(stream, "__next__"):
            # A non-streaming response is complete on return: nothing to abort,
            # and the SDK request timeout has already bounded it.
            self._terminal.observe_response(stream)
            return stream

        def guarded_events():
            stream_stage = "stream_opened"
            try:
                for event in stream:
                    if self._abort_event.is_set():
                        raise ProviderCallAborted("provider_call_aborted")
                    event_type = getattr(event, "type", None)
                    stream_stage = _STREAM_STAGE_BY_EVENT.get(event_type, stream_stage)
                    if (
                        wall_deadline is not None
                        # A terminal event already settled (and billed) the
                        # call; discarding it would only buy a duplicate.
                        and event_type not in _TERMINAL_EVENT_TYPES
                        and self._clock() >= wall_deadline
                    ):
                        close_quietly(stream)
                        raise wall_timeout_error(stream_stage)
                    # Recorded for every transport, before the authored-only
                    # guard below can turn the same event into an outcome error.
                    self._terminal.observe_event(event)
                    if self._guard_outcomes:
                        raise_for_terminal_event(event)
                    yield event
            finally:
                if self._abort_event.is_set():
                    close_quietly(stream)

        return guarded_events()


class GuardedResponsesClient:
    def __init__(
        self,
        client: Any,
        *,
        abort_event: threading.Event,
        guard_outcomes: bool,
        terminal: ProviderTerminalOutcomeRecorder | None = None,
        wall_timeout_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self.responses = GuardedResponsesResource(
            client.responses,
            abort_event=abort_event,
            guard_outcomes=guard_outcomes,
            terminal=terminal,
            wall_timeout_seconds=wall_timeout_seconds,
            clock=clock,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


__all__ = [
    "GuardedResponsesClient",
    "GuardedResponsesResource",
    "ProviderCallAborted",
    "close_quietly",
    "raise_for_terminal_event",
    "wall_timeout_error",
]
