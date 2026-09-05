"""Content-free provider usage counters aggregated across one operation attempt.

Split out of ``services/provider_usage_instrumentation.py``: that module is the
call-boundary adapter (admission, leases, receipts) and this is only the
arithmetic it feeds. Nothing here sees a prompt, a payload, a response body, a
path or an exception -- only stage names, identifiers and numeric counters --
and the shape ``snapshot()`` returns is the strict scalar block the attempt
timing receipt accepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from services.provider_call_timing import ProviderCallTimingLedger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from services.provider_usage_instrumentation import ProviderUsageScope

#: Stage names are receipt keys, so the vocabulary is bounded on the way in.
_STAGE_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789._-"
_STAGE_MAX_LENGTH = 64

#: Every counter ``_ProviderStageUsage.to_dict`` emits, in receipt order. The
#: totals block sums exactly these, so a new counter is added in one place.
STAGE_COUNTER_KEYS = (
    "logicalCalls",
    "physicalCalls",
    "retryCalls",
    "reportedUsageCalls",
    "unavailableUsageCalls",
    "pricedCalls",
    "latencyMs",
    "inputTokens",
    "outputTokens",
    "cachedTokens",
    "actualCostMicrounits",
    "estimatedCostMicrounits",
)


@dataclass(slots=True)
class _ProviderStageUsage:
    logical_keys: set[tuple[int, int]] = field(default_factory=set)
    physical_calls: int = 0
    retry_calls: int = 0
    reported_usage_calls: int = 0
    unavailable_usage_calls: int = 0
    priced_calls: int = 0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: Prompt tokens the provider served from its own cache. Reported only by
    #: providers that expose it; a provider that does not leaves this at zero,
    #: which is why it is never subtracted from ``input_tokens``.
    cached_tokens: int = 0
    actual_cost_microunits: int = 0
    estimated_cost_microunits: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "logicalCalls": len(self.logical_keys),
            "physicalCalls": self.physical_calls,
            "retryCalls": self.retry_calls,
            "reportedUsageCalls": self.reported_usage_calls,
            "unavailableUsageCalls": self.unavailable_usage_calls,
            "pricedCalls": self.priced_calls,
            "latencyMs": self.latency_ms,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "cachedTokens": self.cached_tokens,
            "actualCostMicrounits": self.actual_cost_microunits,
            "estimatedCostMicrounits": self.estimated_cost_microunits,
        }


@dataclass(slots=True)
class ProviderOperationUsage:
    """Content-free logical/physical provider usage for one operation attempt."""

    _stages: dict[str, _ProviderStageUsage] = field(default_factory=dict)
    #: SPEED-12: per-call admission/executor/provider spans, keyed by stage.
    call_timing: ProviderCallTimingLedger = field(
        default_factory=ProviderCallTimingLedger
    )

    def _stage(self, name: str) -> _ProviderStageUsage:
        normalized = str(name).strip().lower()
        if (
            not normalized
            or len(normalized) > _STAGE_MAX_LENGTH
            or any(character not in _STAGE_ALPHABET for character in normalized)
        ):
            raise ValueError("provider_usage_stage_invalid")
        return self._stages.setdefault(normalized, _ProviderStageUsage())

    def record_admission(
        self,
        scope: "ProviderUsageScope",
        *,
        semantic_slot: str,
        is_retry: bool,
        estimated_cost_microunits: int,
    ) -> None:
        stage = self._stage(scope.stage)
        slide = -1 if scope.slide_index is None else scope.slide_index
        stage.logical_keys.add(
            (slide, scope.public_logical_call_ordinal(semantic_slot))
        )
        stage.physical_calls += 1
        stage.retry_calls += int(is_retry)
        stage.estimated_cost_microunits += max(0, estimated_cost_microunits)

    def record_latency(self, stage_name: str, latency_ms: float) -> None:
        self._stage(stage_name).latency_ms += max(0, round(float(latency_ms)))

    def record_usage(
        self,
        stage_name: str,
        *,
        usage_state: str,
        input_tokens: int | None,
        output_tokens: int | None,
        actual_cost_microunits: int | None,
        cached_tokens: int | None = None,
    ) -> None:
        stage = self._stage(stage_name)
        stage.reported_usage_calls += int(usage_state == "reported")
        stage.unavailable_usage_calls += int(usage_state == "unavailable")
        stage.priced_calls += int(actual_cost_microunits is not None)
        stage.input_tokens += input_tokens or 0
        stage.output_tokens += output_tokens or 0
        stage.cached_tokens += cached_tokens or 0
        stage.actual_cost_microunits += actual_cost_microunits or 0

    def snapshot(self) -> dict[str, object]:
        """Return the strict scalar shape accepted by attempt timing receipts."""

        stages = {
            name: usage.to_dict() for name, usage in sorted(self._stages.items())
        }
        totals = {
            key: sum(stage[key] for stage in stages.values())
            for key in STAGE_COUNTER_KEYS
        }
        return {"totals": totals, "stages": stages}


__all__ = [
    "STAGE_COUNTER_KEYS",
    "ProviderOperationUsage",
]
