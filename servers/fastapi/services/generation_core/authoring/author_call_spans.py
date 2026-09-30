"""Per-call span capture for authored provider calls (SPEED-12).

One authored provider call is not one span. Before the SDK request leaves the
process it waits for the author rate slot / 429 cooldown and for the provider
admission lease, and after admission it waits for a free thread on the bounded
author executor. The receipt used to record only one number for all of that,
so a slow provider and a saturated executor were indistinguishable.

``ProviderCallSpans`` marks the three boundaries and ``record()`` partitions
the call's wall time into exactly three spans that always sum to the total:

* ``admissionWaitMillis`` -- call start until the admission lease is granted
  (rate slot, cooldown and the provider admission context together);
* ``executorWaitMillis`` -- admission grant until the executor thread actually
  starts running the SDK call;
* ``providerMillis`` -- executor start until the call returns to the caller
  (the SDK round trip, plus the event-loop resume which is a few microseconds).

``outputTokensPerSecond`` is added only when the provider reported an output
token count; nothing is estimated.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class ProviderCallSpans:
    # Looked up at call time so a test can substitute the module clock.
    started_at: float = field(default_factory=lambda: monotonic())
    admitted_at: float | None = None
    executor_started_at: float | None = None
    finished_at: float | None = None

    def mark_admitted(self) -> None:
        """The admission lease was granted; the call may now be dispatched."""
        if self.admitted_at is None:
            self.admitted_at = monotonic()

    def wrap(self, call: Callable[[], T]) -> Callable[[], T]:
        """Return ``call`` wrapped so the executor thread stamps its own start."""

        def run() -> T:
            if self.executor_started_at is None:
                self.executor_started_at = monotonic()
            return call()

        return run

    def finish(self) -> None:
        if self.finished_at is None:
            self.finished_at = monotonic()

    def record(self, *, output_tokens: int | None = None) -> dict[str, object]:
        """Partition the call into three spans whose millis sum to ``totalMillis``."""
        end = self.finished_at if self.finished_at is not None else monotonic()
        total_ms = _millis(end - self.started_at)
        admitted = self.admitted_at
        if admitted is None:
            # Never admitted (rate limit, budget, cancellation): all waiting.
            admission_ms, executor_ms = total_ms, 0
        else:
            admission_ms = min(total_ms, _millis(admitted - self.started_at))
            executor_started = self.executor_started_at
            executor_end = executor_started if executor_started is not None else end
            executor_ms = min(total_ms - admission_ms, _millis(executor_end - admitted))
        provider_ms = max(0, total_ms - admission_ms - executor_ms)
        record: dict[str, object] = {
            "totalMillis": total_ms,
            "admissionWaitMillis": admission_ms,
            "executorWaitMillis": executor_ms,
            "providerMillis": provider_ms,
        }
        if (
            output_tokens is not None
            and not isinstance(output_tokens, bool)
            and isinstance(output_tokens, int)
            and output_tokens >= 0
        ):
            record["outputTokens"] = output_tokens
            if self.executor_started_at is not None:
                provider_seconds = end - self.executor_started_at
                if provider_seconds > 0:
                    record["outputTokensPerSecond"] = round(
                        output_tokens / provider_seconds, 2
                    )
        return record


def _millis(seconds: float) -> int:
    return max(0, round(seconds * 1000.0))


def record_call_spans(
    spans: ProviderCallSpans, response: object | None
) -> dict[str, object]:
    """Seal ``spans`` and hand the record to the active operation ledger.

    The provider-usage instrumentation decides whether an operation ledger is
    active; with no ledger the record is still returned so a caller can log it.
    """
    from services.provider_usage_instrumentation import (
        record_provider_call_timing,
        response_usage,
    )

    spans.finish()
    # ``response_usage`` also reports cached prompt tokens (R2-D); only the
    # output count is a span input, so the rest is discarded here.
    _state, _input_tokens, output_tokens, _cached_tokens = response_usage(response)
    record = spans.record(output_tokens=output_tokens)
    record_provider_call_timing(record)
    return record


__all__ = ["ProviderCallSpans", "record_call_spans"]
