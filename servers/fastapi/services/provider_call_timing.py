"""Per-stage aggregation of authored provider-call spans (SPEED-12).

``utils/llm_calls/author_call_spans.py`` measures one call as three spans
(admission wait, executor wait, provider time). This module keeps those
records for one operation attempt and folds them into the attempt timing
receipt as ``providerCallTiming``: per stage, the call count, the sum of each
span and its nearest-rank p50/p90, plus the same percentiles for
``outputTokensPerSecond`` over the calls that reported output tokens.

Only scalar millisecond and rate values are kept; no prompt, response or
provider identifiers pass through here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

PROVIDER_CALL_TIMING_SCHEMA = "provider-call-timing/v1"
SPAN_KEYS = (
    "totalMillis",
    "admissionWaitMillis",
    "executorWaitMillis",
    "providerMillis",
)
_RATE_KEY = "outputTokensPerSecond"
_PERCENTILES = (("p50", 50), ("p90", 90))
_MAX_MILLIS = 30 * 24 * 60 * 60 * 1000
_MAX_STAGES = 64


def _percentile(values: Sequence[float], percent: int) -> float:
    """Nearest-rank percentile of a non-empty, already sorted sequence."""
    rank = max(1, -(-len(values) * percent // 100))
    return values[rank - 1]


def _span_millis(record: Mapping[str, object], key: str) -> int:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("provider_call_timing_record_invalid")
    return min(_MAX_MILLIS, round(float(value)))


@dataclass(slots=True)
class ProviderCallTimingLedger:
    """Attempt-local collector for per-call span records, keyed by stage."""

    _calls: dict[str, list[dict[str, float]]] = field(default_factory=dict)

    def record(self, stage: str, record: Mapping[str, object]) -> None:
        normalized = str(stage).strip().lower()
        if not normalized or len(normalized) > 64:
            raise ValueError("provider_call_timing_stage_invalid")
        entry: dict[str, float] = {key: _span_millis(record, key) for key in SPAN_KEYS}
        rate = record.get(_RATE_KEY)
        if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate >= 0:
            entry[_RATE_KEY] = float(rate)
        self._calls.setdefault(normalized, []).append(entry)

    def snapshot(self) -> dict[str, object] | None:
        """The receipt field, or ``None`` when no call was recorded."""
        if not self._calls:
            return None
        stages: dict[str, dict[str, object]] = {}
        for stage, calls in sorted(self._calls.items()):
            summary: dict[str, object] = {"calls": len(calls)}
            for key in SPAN_KEYS:
                values = sorted(call[key] for call in calls)
                summary[key] = {
                    "sum": int(sum(values)),
                    **{name: int(_percentile(values, percent)) for name, percent in _PERCENTILES},
                }
            rates = sorted(call[_RATE_KEY] for call in calls if _RATE_KEY in call)
            if rates:
                summary[_RATE_KEY] = {
                    "calls": len(rates),
                    **{name: round(_percentile(rates, percent), 2) for name, percent in _PERCENTILES},
                }
            stages[stage] = summary
        return {"schemaVersion": PROVIDER_CALL_TIMING_SCHEMA, "stages": stages}


def _validated_summary(value: object, *, key: str, integer: bool) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("generation_attempt_v2_provider_call_timing_invalid")
    expected = {"sum", "p50", "p90"} if integer else {"calls", "p50", "p90"}
    if set(value) != expected:
        raise ValueError("generation_attempt_v2_provider_call_timing_invalid")
    output: dict[str, object] = {}
    for name in sorted(expected):
        number = value.get(name)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or number < 0:
            raise ValueError("generation_attempt_v2_provider_call_timing_invalid")
        output[name] = int(number) if integer or name == "calls" else round(float(number), 2)
    return output


def provider_call_timing_field(value: object) -> dict[str, object] | None:
    """Validate the optional ``providerCallTiming`` receipt field."""
    if value is None:
        return None
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schemaVersion", "stages"}
        or value.get("schemaVersion") != PROVIDER_CALL_TIMING_SCHEMA
        or not isinstance(value.get("stages"), Mapping)
        or not value["stages"]
        or len(value["stages"]) > _MAX_STAGES
    ):
        raise ValueError("generation_attempt_v2_provider_call_timing_invalid")
    stages: dict[str, dict[str, object]] = {}
    for raw_stage, raw_summary in sorted(value["stages"].items(), key=lambda item: str(item[0])):
        stage = str(raw_stage).strip().lower()
        if not stage or len(stage) > 64 or not isinstance(raw_summary, Mapping):
            raise ValueError("generation_attempt_v2_provider_call_timing_invalid")
        allowed = {"calls", *SPAN_KEYS, _RATE_KEY}
        calls = raw_summary.get("calls")
        if (
            not {"calls", *SPAN_KEYS} <= set(raw_summary) <= allowed
            or isinstance(calls, bool)
            or not isinstance(calls, int)
            or calls < 1
        ):
            raise ValueError("generation_attempt_v2_provider_call_timing_invalid")
        summary: dict[str, object] = {"calls": calls}
        for key in SPAN_KEYS:
            summary[key] = _validated_summary(raw_summary.get(key), key=key, integer=True)
        if _RATE_KEY in raw_summary:
            summary[_RATE_KEY] = _validated_summary(raw_summary[_RATE_KEY], key=_RATE_KEY, integer=False)
        stages[stage] = summary
    return {"schemaVersion": PROVIDER_CALL_TIMING_SCHEMA, "stages": stages}


__all__ = [
    "PROVIDER_CALL_TIMING_SCHEMA",
    "ProviderCallTimingLedger",
    "SPAN_KEYS",
    "provider_call_timing_field",
]
