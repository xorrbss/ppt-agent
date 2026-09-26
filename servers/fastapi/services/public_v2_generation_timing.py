"""Projection of public generation timing receipts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from api.v2.contracts import (
    PublicGenerationStageDurationV1,
    PublicGenerationStageTimingsV1,
    PublicGenerationTimingV1,
    PublicGenerationUsageAvailabilityV1,
    PublicGenerationWorkerTimingV1,
)
from models.sql.workspace import WorkspaceOperation
from services.public_v2_operation_lifecycle import (
    PUBLIC_GENERATION_KIND as _GENERATION_KIND,
    aware_datetime as _aware,
    public_operation_status as _operation_status,
)


def _timing_counter(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if numeric < 0 or numeric == float("inf") or numeric != numeric:
        return None
    return round(numeric)


def _stage_duration(
    stages: Mapping[str, object], *names: str
) -> PublicGenerationStageDurationV1 | str:
    values = [_timing_counter(stages.get(name)) for name in names]
    observed = [value for value in values if value is not None]
    if not observed:
        return "not_started"
    return PublicGenerationStageDurationV1(duration_millis=sum(observed))


def _receipt_v2_stage_duration(
    stages: Mapping[str, object], name: str
) -> PublicGenerationStageDurationV1 | str:
    stage = stages.get(name)
    if not isinstance(stage, Mapping) or stage.get("state") != "observed":
        return "not_started"
    duration = _timing_counter(stage.get("durationMillis"))
    if duration is None:
        return "not_started"
    return PublicGenerationStageDurationV1(duration_millis=duration)


def _observability_metric(
    receipt: Mapping[str, object], name: str
) -> Mapping[str, object] | None:
    observability = receipt.get("observability")
    metrics = (
        observability.get("metrics")
        if isinstance(observability, Mapping)
        else None
    )
    metric = metrics.get(name) if isinstance(metrics, Mapping) else None
    return metric if isinstance(metric, Mapping) else None


def _observed_metric_counter(
    receipt: Mapping[str, object], name: str
) -> int | None:
    metric = _observability_metric(receipt, name)
    if metric is None or metric.get("state") != "observed":
        return None
    return _timing_counter(metric.get("value"))


def _availability_from_metric(
    receipt: Mapping[str, object], *, actual: str, estimated: str
) -> str:
    actual_metric = _observability_metric(receipt, actual)
    estimated_metric = _observability_metric(receipt, estimated)
    if actual_metric is not None and actual_metric.get("state") == "observed":
        return "available"
    if estimated_metric is not None and estimated_metric.get("state") == "observed":
        return "partial"
    if actual_metric is not None or estimated_metric is not None:
        return "unavailable"
    return "not_started"


def _operation_elapsed_millis(
    started: object, finished: object
) -> int | None:
    if not isinstance(started, datetime) or not isinstance(finished, datetime):
        return None
    return max(0, round((_aware(finished) - _aware(started)).total_seconds() * 1000))


def _public_generation_timing(
    operation: WorkspaceOperation,
    receipt: Mapping[str, object] | None,
) -> PublicGenerationTimingV1 | None:
    """Normalize a durable terminal result into the six public timing stages."""

    public_status = _operation_status(operation.state)
    if operation.kind != _GENERATION_KIND or public_status not in {
        "succeeded",
        "failed",
        "cancelled",
    }:
        return None

    source = receipt or {}
    is_v2 = source.get("schemaVersion") == "generation-attempt-timing-receipt/v2"
    raw_stages = source.get("stages" if is_v2 else "stagesMs")
    stages = raw_stages if isinstance(raw_stages, Mapping) else {}
    if is_v2:
        stage_timings = PublicGenerationStageTimingsV1(
            outline=_receipt_v2_stage_duration(stages, "outline"),
            provider=_receipt_v2_stage_duration(stages, "provider"),
            authoring=_receipt_v2_stage_duration(stages, "authoring"),
            repair=_receipt_v2_stage_duration(stages, "repair"),
            chromium_review=_receipt_v2_stage_duration(stages, "chromiumReview"),
            publication=_receipt_v2_stage_duration(stages, "publication"),
        )
    else:
        stage_timings = PublicGenerationStageTimingsV1(
            outline=_stage_duration(stages, "outline"),
            provider=_stage_duration(stages, "provider"),
            authoring=_stage_duration(stages, "author"),
            repair=_stage_duration(stages, "repair"),
            chromium_review=_stage_duration(stages, "render", "quality"),
            publication=_stage_duration(stages, "publication"),
        )

    raw_attempt = source.get("attemptNumber")
    attempt_number = (
        raw_attempt
        if isinstance(raw_attempt, int)
        and not isinstance(raw_attempt, bool)
        and raw_attempt >= 0
        else max(0, int(getattr(operation, "attempt_number", 0)))
    )
    raw_outcome = source.get("outcome")
    allowed_outcomes = {
        "succeeded",
        "failed",
        "budget_exhausted",
        "cancelled",
        "dead_letter",
        "worker_crash",
    }
    outcome = (
        str(raw_outcome)
        if raw_outcome in allowed_outcomes
        else "succeeded"
        if public_status == "succeeded"
        else "cancelled"
        if public_status == "cancelled"
        else "dead_letter"
        if operation.state == "dead_letter"
        else "failed"
    )

    queue_wait = (
        _timing_counter(source.get("queueWaitMillis"))
        if is_v2
        else _operation_elapsed_millis(
            getattr(operation, "created_at", None),
            getattr(operation, "started_at", None),
        )
    )
    raw_total = _timing_counter(source.get("wallMillis" if is_v2 else "totalMs"))
    wall_millis = raw_total
    if wall_millis is None:
        terminal_at = getattr(operation, "completed_at", None) or getattr(
            operation, "updated_at", None
        )
        wall_millis = _operation_elapsed_millis(
            getattr(operation, "created_at", None), terminal_at
        )

    provider_usage = source.get("providerUsage")
    provider_totals = (
        provider_usage.get("totals")
        if isinstance(provider_usage, Mapping)
        else None
    )
    logical_calls = _timing_counter(source.get("logicalCalls")) if is_v2 else None
    if logical_calls is None:
        logical_calls = (
            _timing_counter(provider_totals.get("logicalCalls"))
            if isinstance(provider_totals, Mapping)
            else None
        )
    if logical_calls is None:
        initial_calls = _observed_metric_counter(source, "providerInitialCalls")
        retry_calls = _observed_metric_counter(source, "providerRetryCalls")
        if initial_calls is not None or retry_calls is not None:
            logical_calls = (initial_calls or 0) + (retry_calls or 0)

    if is_v2:
        token_availability = str(source.get("tokenAvailability") or "not_started")
        cost_availability = str(source.get("costAvailability") or "not_started")
    elif isinstance(provider_totals, Mapping):
        reported = _timing_counter(provider_totals.get("reportedUsageCalls")) or 0
        unavailable = (
            _timing_counter(provider_totals.get("unavailableUsageCalls")) or 0
        )
        priced = _timing_counter(provider_totals.get("pricedCalls")) or 0
        if not logical_calls:
            token_availability = "not_started"
            cost_availability = "not_started"
        else:
            token_availability = (
                "available"
                if reported >= logical_calls and unavailable == 0
                else "partial"
                if reported > 0
                else "unavailable"
            )
            cost_availability = (
                "available"
                if priced >= logical_calls
                else "partial"
                if priced > 0
                else "unavailable"
            )
    else:
        token_availability = _availability_from_metric(
            source, actual="actualTokens", estimated="estimatedTokens"
        )
        cost_availability = _availability_from_metric(
            source, actual="actualCost", estimated="estimatedCost"
        )

    causes = source.get("causes")
    retry_reason = (
        str(source.get("retryReason"))
        if is_v2 and source.get("retryReason")
        else str(causes.get("retryReason"))
        if isinstance(causes, Mapping) and causes.get("retryReason")
        else None
    )
    if retry_reason is None and isinstance(operation.error_detail, Mapping):
        candidate = operation.error_detail.get("retry_reason") or operation.error_detail.get(
            "retryReason"
        )
        if isinstance(candidate, str) and candidate.strip():
            retry_reason = candidate.strip()[:160]
    if retry_reason is None and int(getattr(operation, "retry_count", 0)) > 0:
        retry_reason = "retry_reason_unavailable"

    progress = operation.progress if isinstance(operation.progress, Mapping) else {}
    result = operation.result if isinstance(operation.result, Mapping) else {}
    raw_worker = (
        source.get("worker")
        or result.get("worker")
        or result.get("worker_id")
        or progress.get("worker")
        or progress.get("worker_id")
    )
    worker_name = (
        raw_worker.strip()[:160]
        if isinstance(raw_worker, str) and raw_worker.strip()
        else None
    )
    peak_memory = _observed_metric_counter(source, "workerPeakMemory")
    peak_handles = _observed_metric_counter(source, "workerPeakHandles")
    worker_observed = bool(
        worker_name is not None or peak_memory is not None or peak_handles is not None
    )
    worker_started = any(value != "not_started" for value in (
        stage_timings.outline,
        stage_timings.provider,
        stage_timings.authoring,
        stage_timings.repair,
        stage_timings.chromium_review,
        stage_timings.publication,
    )) or getattr(operation, "started_at", None) is not None
    worker = PublicGenerationWorkerTimingV1(
        status="observed" if worker_observed else "unavailable" if worker_started else "not_started",
        worker=worker_name,
        peak_memory=peak_memory,
        peak_handles=peak_handles,
    )

    terminal_error = source.get("terminalError") if is_v2 else None
    if not isinstance(terminal_error, str) or not terminal_error.strip():
        terminal_error = None
    if outcome != "succeeded":
        terminal_error = terminal_error or (
            "deadline_exceeded"
            if operation.state == "deadline_exceeded"
            else "cancelled_by_user"
            if operation.state == "cancelled"
            else operation.error_code or "operation_failed"
        )
    source_digest = source.get("receiptDigest") or source.get("timingReceiptDigest")
    if not isinstance(source_digest, str):
        source_digest = None

    return PublicGenerationTimingV1(
        attempt_number=attempt_number,
        outcome=outcome,  # type: ignore[arg-type]
        stages=stage_timings,
        queue_wait_millis=queue_wait if queue_wait is not None else "not_started",
        wall_millis=wall_millis if wall_millis is not None else "not_started",
        logical_calls=logical_calls if logical_calls is not None else "not_started",
        retry_reason=retry_reason,
        usage_availability=PublicGenerationUsageAvailabilityV1(
            tokens=token_availability,  # type: ignore[arg-type]
            cost=cost_availability,  # type: ignore[arg-type]
        ),
        worker=worker,
        terminal_error=terminal_error,
        source_receipt_digest=source_digest,
    )
