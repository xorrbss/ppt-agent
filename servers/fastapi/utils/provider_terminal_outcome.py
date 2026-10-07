"""The terminal verdict of one Responses API call, read off the raw SDK events.

``llmai==0.3.7`` carries no finish/stop reason on its stream chunks, and its
chatgpt client handles only ``response.completed``: a stream that OpenAI ends
with ``response.incomplete`` (``incomplete_details.reason ==
"max_output_tokens"``) is silently treated as finished, its completion chunk
arrives with ``usage=None``, and the document it cut mid-tag falls into
renderer admission as "HTML contains unclosed elements".

The guarded Responses wrapper in ``utils/provider_idempotency.py`` already
iterates the raw SDK events, so this module gives it one recorder to hand the
terminal event to. The recorded outcome is transport-neutral: a normalised
``finish_reason`` plus the terminal response's own usage, exposed on the
bounded call for the authored stream reader to act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

#: The normalised ``finish_reason`` values, after the OpenAI stop vocabulary.
FINISH_STOP = "stop"
FINISH_LENGTH = "length"
FINISH_CONTENT_FILTER = "content_filter"

_TERMINAL_EVENT_TYPES = frozenset({"response.completed", "response.incomplete", "response.failed", "response.cancelled", "error"})
_INCOMPLETE_REASONS = {
    "max_output_tokens": FINISH_LENGTH,
    "content_filter": FINISH_CONTENT_FILTER,
}
_COUNTED_FIELDS = ("input_tokens", "output_tokens", "total_tokens")


@dataclass(frozen=True, slots=True)
class ProviderTerminalUsage:
    """The terminal response's token counts, shaped like ``llmai``'s usage.

    ``input_tokens``/``output_tokens`` and ``details`` are the fields the
    receipt readers (``response_usage``, ``cached_tokens_from_usage``) already
    look for, so this object can stand in for a missing ``usage`` directly.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    #: The SDK's remaining usage fields (the nested ``*_tokens_details``).
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderTerminalOutcome:
    """How one Responses API call ended, and what it was billed."""

    finish_reason: str
    #: The provider's own ``incomplete_details.reason``; ``None`` on completion.
    raw_reason: str | None = None
    usage: ProviderTerminalUsage | None = None

    @property
    def output_truncated(self) -> bool:
        return self.finish_reason == FINISH_LENGTH


class ProviderTerminalOutcomeRecorder:
    """Keeps the last terminal outcome seen on one bounded provider call."""

    __slots__ = ("outcome", "diagnostics")

    def __init__(self) -> None:
        self.outcome: ProviderTerminalOutcome | None = None
        self.diagnostics: Mapping[str, object] = {}

    def observe_event(self, event: Any) -> None:
        """Record a raw stream event when it is the call's terminal event."""

        event_type = _get(event, "type")
        if event_type not in _TERMINAL_EVENT_TYPES:
            return
        response = _get(event, "response")
        self._record_diagnostics(response, event_type, event)
        if event_type in {"response.completed", "response.incomplete"}:
            self.outcome = terminal_outcome_from_response(response)

    def observe_response(self, response: Any) -> None:
        """Record a non-streamed ``Response`` from its ``status``."""

        status = _get(response, "status")
        if status in {"completed", "incomplete", "failed", "cancelled"}:
            self._record_diagnostics(response, f"response.{status}")
        if status in {"completed", "incomplete"}:
            self.outcome = terminal_outcome_from_response(response)

    def _record_diagnostics(self, response: Any, event_type: str, event: Any = None) -> None:
        from utils.provider_overload import safe_provider_diagnostics

        usage = _get(response, "usage")
        error = _get(response, "error") or _get(event, "error") or event
        kind = {"response.failed": "terminal_failed", "response.cancelled": "terminal_cancelled", "response.incomplete": "incomplete", "error": "terminal_error"}.get(event_type)
        self.diagnostics = safe_provider_diagnostics(diagnostics={
            "terminalEvent": event_type,
            "statusOrigin": "local_stream",
            "failureKind": kind,
            "errorCode": _get(error, "code"),
            "requestId": _get(response, "request_id") or _get(event, "request_id"),
            "incompleteReason": _get(_get(response, "incomplete_details"), "reason"),
            "usage": {
                **{name: _get(usage, name) for name in _COUNTED_FIELDS},
                "reasoning_tokens": _get(_get(usage, "output_tokens_details"), "reasoning_tokens"),
                "cached_tokens": _get(_get(usage, "input_tokens_details"), "cached_tokens"),
            },
        })


def terminal_outcome_from_response(response: Any) -> ProviderTerminalOutcome:
    """Normalise one terminal ``Response`` (from an event or a plain call)."""

    details = _get(response, "incomplete_details")
    raw_reason = _get(details, "reason") if details is not None else None
    if _get(response, "status") == "completed" or (
        details is None and raw_reason is None
    ):
        return ProviderTerminalOutcome(FINISH_STOP, usage=_usage(_get(response, "usage")))
    raw = str(raw_reason) if raw_reason is not None else "incomplete"
    return ProviderTerminalOutcome(
        _INCOMPLETE_REASONS.get(raw, raw),
        raw_reason=raw,
        usage=_usage(_get(response, "usage")),
    )


def _usage(usage: Any) -> ProviderTerminalUsage | None:
    if usage is None:
        return None
    counts = {name: _count(_get(usage, name)) for name in _COUNTED_FIELDS}
    dumped = _dump(usage)
    details = {key: value for key, value in dumped.items() if key not in _COUNTED_FIELDS}
    reasoning = _count(_get(_get(usage, "output_tokens_details"), "reasoning_tokens"))
    if all(value is None for value in counts.values()) and not details:
        return None
    return ProviderTerminalUsage(reasoning_tokens=reasoning, details=details, **counts)


def _dump(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            dumped = dump(exclude_none=True)
        except TypeError:
            dumped = dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _get(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


__all__ = [
    "FINISH_CONTENT_FILTER",
    "FINISH_LENGTH",
    "FINISH_STOP",
    "ProviderTerminalOutcome",
    "ProviderTerminalOutcomeRecorder",
    "ProviderTerminalUsage",
    "terminal_outcome_from_response",
]
