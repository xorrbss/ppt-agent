"""The generation observability metric vocabulary and its receipt validator.

Split out of ``services/authored_generation_timing_receipt.py`` to keep that
module under the file-size ratchet.  Everything here describes one thing: the
closed set of metric names a sealed generation timing receipt may carry, the
states and reasons each metric may report, and the validator that turns a raw
observability mapping into the normalized tuple the receipt stores.

The two historical metric profiles are kept alongside the current one so that
already-sealed receipts stay readable; a receipt must still match exactly one
profile in full, because filling a missing metric would invent evidence.
"""

from __future__ import annotations

from collections.abc import Mapping

from services.authored_generation_timing_values import _bounded_timing_ms


GENERATION_OBSERVABILITY_METRICS = (
    "queueWait", "queuePosition", "outlineDuration", "authorDuration",
    "illustrationDuration", "renderDuration", "qualityDuration", "repairDuration",
    "checkpointDuration", "publicationDuration", "exportDuration",
    "providerInitialCalls", "providerRetryCalls", "providerLogicalOrdinal",
    "estimatedTokens", "actualTokens", "estimatedCost", "actualCost",
    "stageRetryCalls", "stageEstimatedCost", "stageActualCost",
    "attemptNumber", "fencingOutcome", "chromeLaunch", "chromeRestart",
    "blockedRequest", "timeout", "crash", "workerPeakMemory",
    "workerPeakHandles", "artifactBytes", "cacheHit", "cacheMiss",
    "cacheEviction", "objectStoreLatency", "finalOutcome",
)
PRE_REPAIR_GENERATION_OBSERVABILITY_METRICS = tuple(
    name
    for name in GENERATION_OBSERVABILITY_METRICS
    if name != "repairDuration"
)
LEGACY_GENERATION_OBSERVABILITY_METRICS = tuple(
    name
    for name in PRE_REPAIR_GENERATION_OBSERVABILITY_METRICS
    if name not in {"stageRetryCalls", "stageEstimatedCost", "stageActualCost"}
)
_TEXT_METRICS = frozenset(("fencingOutcome", "finalOutcome"))
_STAGE_MAP_METRICS = frozenset(
    {"stageRetryCalls", "stageEstimatedCost", "stageActualCost"}
)
_OBSERVED_TEXT_VALUES = {
    "fencingOutcome": frozenset(
        {"claim_acquired", "transition_committed", "publish_committed", "lease_lost"}
    ),
    "finalOutcome": frozenset(
        {
            "succeeded",
            "budget_exhausted",
            "cancelled",
            "failed",
            "dead_letter",
            "worker_crash",
        }
    ),
}
_METRIC_STATES = frozenset(("observed", "unavailable", "not_applicable"))
_UNAVAILABLE_REASONS = frozenset(
    (
        "legacy_receipt_no_metric",
        "provider_actual_cost_not_reported",
        "provider_token_estimate_not_recorded",
        "provider_token_usage_not_reported",
        "public_admission_not_found",
        "counter_not_implemented",
    )
)
_NOT_APPLICABLE_REASONS = frozenset(
    {
        "stage_not_executed",
        "provider_not_used",
        "artifact_store_not_used",
        "worker_resource_not_applicable",
    }
)


def _stage_metric_values(value: object, field_name: str) -> dict[str, int]:
    if not isinstance(value, Mapping) or not value or len(value) > 128:
        raise ValueError(f"generation_observability_{field_name}_invalid")
    output: dict[str, int] = {}
    for raw_stage, raw_value in sorted(value.items(), key=lambda item: str(item[0])):
        stage = str(raw_stage).strip().lower()
        if (
            not stage
            or len(stage) > 64
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in stage
            )
        ):
            raise ValueError(f"generation_observability_{field_name}_invalid")
        output[stage] = _bounded_timing_ms(raw_value, field_name)
    return output


def _observability_metrics(value: object) -> tuple[tuple[str, dict[str, object]], ...]:
    if not isinstance(value, Mapping):
        raise ValueError("generation_observability_metrics_invalid")
    # Keep the two previously sealed shapes readable without filling missing
    # metrics. Every supported profile still requires its exact complete set.
    metric_names = next(
        (
            names
            for names in (
                GENERATION_OBSERVABILITY_METRICS,
                PRE_REPAIR_GENERATION_OBSERVABILITY_METRICS,
                LEGACY_GENERATION_OBSERVABILITY_METRICS,
            )
            if set(value) == set(names)
        ),
        None,
    )
    if metric_names is None:
        raise ValueError("generation_observability_metrics_invalid")
    output: list[tuple[str, dict[str, object]]] = []
    for name in metric_names:
        metric = value.get(name)
        if not isinstance(metric, Mapping) or metric.get("state") not in _METRIC_STATES:
            raise ValueError(f"generation_observability_{name}_invalid")
        state = str(metric["state"])
        if state == "observed":
            observed = metric.get("value")
            if name in _TEXT_METRICS:
                if observed not in _OBSERVED_TEXT_VALUES[name]:
                    raise ValueError(f"generation_observability_{name}_invalid")
            elif name in _STAGE_MAP_METRICS:
                observed = _stage_metric_values(observed, name)
            else:
                _bounded_timing_ms(observed, name)
            normalized = {"state": state, "value": observed}
        else:
            reason = metric.get("reason")
            allowed_reasons = (
                _UNAVAILABLE_REASONS
                if state == "unavailable"
                else _NOT_APPLICABLE_REASONS
            )
            if reason not in allowed_reasons:
                raise ValueError(f"generation_observability_{name}_invalid")
            normalized = {"state": state, "reason": reason}
        output.append((name, normalized))
    return tuple(output)
