"""Abortable provider response streams and their terminal-event outcomes.

Split out of ``utils.provider_idempotency`` so the adapter that builds one
bounded provider call stays readable; the behaviour is unchanged and
``provider_idempotency`` remains the only place that installs these wrappers.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from utils.provider_overload import ProviderOutcomeError
from utils.provider_terminal_outcome import ProviderTerminalOutcomeRecorder


LOGGER = logging.getLogger(__name__)


class ProviderCallAborted(RuntimeError):
    """The caller abandoned this call at its deadline and its stream was closed."""


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
    ) -> None:
        self._client = client
        self.responses = GuardedResponsesResource(
            client.responses,
            abort_event=abort_event,
            guard_outcomes=guard_outcomes,
            terminal=terminal,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


__all__ = [
    "GuardedResponsesClient",
    "GuardedResponsesResource",
    "ProviderCallAborted",
    "close_quietly",
    "raise_for_terminal_event",
]
