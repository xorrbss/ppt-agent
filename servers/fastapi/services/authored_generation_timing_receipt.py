"""Immutable authored generation timings bound to QualityReceipt v2."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic

from services.authored_generation_timing_receipt_e4 import (
    fit_summary_entries,
    flagged_slide_entries,
    merged_stage_wall_millis,
    normalization_entries,
    optional_top_level_fields,
    provider_rate_profile as _e4_provider_rate_profile,
    quality_loop_variant_field,
    stage_has_wall,
    stage_wall_millis,
)
from services.authored_quality_receipt_v2 import (
    QualityReceiptV2,
    _manifest_digest,
    _manifest_lock,
    _optional_prefixed_sha256,
    _receipt_digest as _quality_receipt_digest,
    public_attempt_id,
    quality_receipt_manifest_path,
)


GENERATION_TIMING_STAGES = (
    "outline",
    "author",
    "illustration",
    "render",
    "quality",
    "repair",
    "checkpoint",
    "export",
    "publication",
)
LEGACY_GENERATION_TIMING_STAGES = (
    "outline", "author", "repair", "checkpoint", "export", "publication"
)
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
MAX_GENERATION_STAGE_TIMING_MS = 30 * 24 * 60 * 60 * 1000
GENERATION_ATTEMPT_OUTCOMES = frozenset(
    {
        "succeeded",
        "failed",
        "budget_exhausted",
        "cancelled",
        "dead_letter",
        "worker_crash",
    }
)
GENERATION_ATTEMPT_TIMING_STAGES = frozenset(
    (*GENERATION_TIMING_STAGES, "provider")
)
PUBLIC_GENERATION_TIMING_STAGES = (
    "outline",
    "provider",
    "authoring",
    "repair",
    "chromiumReview",
    "publication",
)
_PROVIDER_USAGE_COUNTERS = (
    "logicalCalls",
    "physicalCalls",
    "retryCalls",
    "reportedUsageCalls",
    "unavailableUsageCalls",
    "pricedCalls",
    "latencyMs",
    "inputTokens",
    "outputTokens",
    "actualCostMicrounits",
    "estimatedCostMicrounits",
)
_ATTEMPT_CAUSE_KEYS = ("queueReason", "stageReason", "retryReason")


def _attempt_causes(value: object | None) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping) or set(value) != set(_ATTEMPT_CAUSE_KEYS):
        raise ValueError("generation_attempt_causes_invalid")
    output: list[tuple[str, str]] = []
    for key in _ATTEMPT_CAUSE_KEYS:
        reason = str(value.get(key) or "").strip().lower()
        if (
            not reason
            or len(reason) > 64
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in reason
            )
        ):
            raise ValueError("generation_attempt_causes_invalid")
        output.append((key, reason))
    return tuple(output)


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


def generation_timing_receipt_path(job_id: object) -> Path:
    """Return the job-scoped timing sidecar beside its QualityReceipt ledger."""

    manifest = quality_receipt_manifest_path(job_id)
    return manifest.with_name(f"{manifest.stem}-timing.json")


def generation_attempt_timing_receipt_path(job_id: object) -> Path:
    """Return the append-only attempt ledger used before quality succeeds."""

    manifest = quality_receipt_manifest_path(job_id)
    return manifest.with_name(f"{manifest.stem}-attempt-timing.json")


def _bounded_timing_ms(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"generation_timing_{field_name}_invalid")
    numeric = float(value)
    if not (numeric >= 0.0 and numeric < float("inf")):
        raise ValueError(f"generation_timing_{field_name}_invalid")
    return min(round(numeric), MAX_GENERATION_STAGE_TIMING_MS)


def _stage_timings(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping):
        raise ValueError("generation_timing_stages_invalid")
    stages = (
        GENERATION_TIMING_STAGES
        if set(value) == set(GENERATION_TIMING_STAGES)
        else LEGACY_GENERATION_TIMING_STAGES
        if set(value) == set(LEGACY_GENERATION_TIMING_STAGES)
        else None
    )
    if stages is None:
        raise ValueError("generation_timing_stages_invalid")
    return tuple(
        (stage, _bounded_timing_ms(value[stage], stage))
        for stage in stages
    )


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


def _required_sha256(value: object, field_name: str) -> str:
    digest = _optional_prefixed_sha256(value, field_name)
    if digest is None:
        raise ValueError(f"generation_timing_{field_name}_invalid")
    return digest


def _partial_stage_timings(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("generation_attempt_timing_stages_invalid")
    normalized: dict[str, int] = {}
    for raw_stage, raw_value in value.items():
        stage = str(raw_stage).strip().lower()
        if stage not in GENERATION_ATTEMPT_TIMING_STAGES:
            raise ValueError("generation_attempt_timing_stages_invalid")
        normalized[stage] = _bounded_timing_ms(raw_value, stage)
    return tuple(
        (stage, normalized[stage])
        for stage in (*GENERATION_TIMING_STAGES, "provider")
        if stage in normalized
    )


def _provider_usage_counters(value: object, field_name: str) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(_PROVIDER_USAGE_COUNTERS):
        raise ValueError(f"generation_attempt_{field_name}_invalid")
    return {
        name: _bounded_timing_ms(value[name], f"{field_name}_{name}")
        for name in _PROVIDER_USAGE_COUNTERS
    }


def _provider_usage(value: object | None) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"totals", "stages"}:
        raise ValueError("generation_attempt_provider_usage_invalid")
    raw_stages = value.get("stages")
    if not isinstance(raw_stages, Mapping) or len(raw_stages) > 64:
        raise ValueError("generation_attempt_provider_usage_invalid")
    stages: dict[str, dict[str, int]] = {}
    for raw_stage, counters in sorted(raw_stages.items(), key=lambda item: str(item[0])):
        stage = str(raw_stage).strip().lower()
        if (
            not stage
            or len(stage) > 64
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in stage
            )
        ):
            raise ValueError("generation_attempt_provider_usage_invalid")
        stages[stage] = _provider_usage_counters(counters, f"provider_stage_{stage}")
    return {
        "totals": _provider_usage_counters(value.get("totals"), "provider_totals"),
        "stages": stages,
    }


def _checkpoint_signals(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > 64:
        raise ValueError("generation_attempt_checkpoint_signals_invalid")
    output: list[dict[str, object]] = []
    required = {
        "stage",
        "enabled",
        "reused",
        "outcome",
        "lookupMs",
        "resolveMs",
        "commitMs",
        "totalMs",
    }
    for item in value:
        if not isinstance(item, Mapping) or set(item) != required:
            raise ValueError("generation_attempt_checkpoint_signals_invalid")
        stage = str(item.get("stage") or "").strip().lower()
        enabled = item.get("enabled")
        reused = item.get("reused")
        outcome = item.get("outcome")
        if stage not in {"deck_plan", "chapter_plan", "header", "illustration"}:
            raise ValueError("generation_attempt_checkpoint_signals_invalid")
        if (
            not isinstance(enabled, bool)
            or not isinstance(reused, bool)
            or outcome not in {"succeeded", "failed"}
        ):
            raise ValueError("generation_attempt_checkpoint_signals_invalid")
        if reused and not enabled:
            raise ValueError("generation_attempt_checkpoint_signals_invalid")
        output.append(
            {
                "stage": stage,
                "enabled": enabled,
                "reused": reused,
                "outcome": outcome,
                "lookupMs": _bounded_timing_ms(item.get("lookupMs"), "checkpoint_lookup"),
                "resolveMs": _bounded_timing_ms(item.get("resolveMs"), "checkpoint_resolve"),
                "commitMs": _bounded_timing_ms(item.get("commitMs"), "checkpoint_commit"),
                "totalMs": _bounded_timing_ms(item.get("totalMs"), "checkpoint_total"),
            }
        )
    return tuple(output)


@dataclass(frozen=True, slots=True)
class GenerationTimingReceipt:
    schema_version: str
    attempt_id: str
    attempt_number: int
    revision: int
    quality_receipt_digest: str
    requested_release_policy_digest: str
    pre_export_evaluation_policy_digest: str
    stage_timings_ms: tuple[tuple[str, int], ...]
    total_ms: int
    observability_metrics: tuple[tuple[str, dict[str, object]], ...] = ()
    timing_receipt_digest: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = {
            "schemaVersion": self.schema_version,
            "attemptId": self.attempt_id,
            "attemptNumber": self.attempt_number,
            "revision": self.revision,
            "qualityReceiptDigest": self.quality_receipt_digest,
            "requestedReleasePolicyDigest": self.requested_release_policy_digest,
            "preExportEvaluationPolicyDigest": self.pre_export_evaluation_policy_digest,
            "stagesMs": dict(self.stage_timings_ms),
            "totalMs": self.total_ms,
            "timingReceiptDigest": self.timing_receipt_digest,
        }
        if self.observability_metrics:
            payload["observability"] = {
                "schemaVersion": "generation-observability/v1",
                "metrics": dict(self.observability_metrics),
            }
        return payload


def _receipt_digest(receipt: GenerationTimingReceipt) -> str:
    payload = receipt.to_dict()
    payload.pop("timingReceiptDigest", None)
    return "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _from_dict(value: object) -> GenerationTimingReceipt:
    if not isinstance(value, Mapping):
        raise ValueError("generation_timing_receipt_invalid")
    attempt_number = value.get("attemptNumber")
    revision = value.get("revision")
    if (
        value.get("schemaVersion") != "generation-timing-receipt/v1"
        or isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
    ):
        raise ValueError("generation_timing_receipt_invalid")
    observability = value.get("observability")
    observability_metrics = ()
    if observability is not None:
        if not isinstance(observability, Mapping) or observability.get("schemaVersion") != "generation-observability/v1":
            raise ValueError("generation_observability_receipt_invalid")
        observability_metrics = _observability_metrics(observability.get("metrics"))
    receipt = GenerationTimingReceipt(
        schema_version="generation-timing-receipt/v1",
        attempt_id=public_attempt_id(value.get("attemptId")),
        attempt_number=attempt_number,
        revision=revision,
        quality_receipt_digest=_required_sha256(
            value.get("qualityReceiptDigest"), "quality_receipt_digest"
        ),
        requested_release_policy_digest=_required_sha256(
            value.get("requestedReleasePolicyDigest"),
            "requested_release_policy_digest",
        ),
        pre_export_evaluation_policy_digest=_required_sha256(
            value.get("preExportEvaluationPolicyDigest"),
            "pre_export_evaluation_policy_digest",
        ),
        stage_timings_ms=_stage_timings(value.get("stagesMs")),
        total_ms=_bounded_timing_ms(value.get("totalMs"), "total"),
        observability_metrics=observability_metrics,
        timing_receipt_digest=_required_sha256(
            value.get("timingReceiptDigest"), "timing_receipt_digest"
        ),
    )
    if receipt.timing_receipt_digest != _receipt_digest(receipt):
        raise ValueError("generation_timing_receipt_digest_mismatch")
    return receipt


def _load_records(path: Path) -> dict[str, GenerationTimingReceipt]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("generation_timing_manifest_corrupt") from error
    records = payload.get("receipts") if isinstance(payload, Mapping) else None
    if (
        not isinstance(payload, Mapping)
        or payload.get("schemaVersion") != "generation-timing-manifest/v1"
        or not isinstance(records, Mapping)
        or payload.get("manifestDigest") != _manifest_digest(records)
    ):
        raise ValueError("generation_timing_manifest_corrupt")
    parsed: dict[str, GenerationTimingReceipt] = {}
    for key, item in records.items():
        receipt = _from_dict(item)
        if key != f"{receipt.attempt_id}#{receipt.revision}":
            raise ValueError("generation_timing_manifest_key_invalid")
        parsed[key] = receipt
    return parsed


def _persist_records(
    path: Path, records: Mapping[str, GenerationTimingReceipt]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = {key: receipt.to_dict() for key, receipt in sorted(records.items())}
    envelope = {
        "schemaVersion": "generation-timing-manifest/v1",
        "receipts": serialized,
        "manifestDigest": _manifest_digest(serialized),
    }
    encoded = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except (AttributeError, OSError):
            pass
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def create_generation_timing_receipt(
    *,
    job_id: object,
    quality_receipt: QualityReceiptV2,
    attempt_number: int,
    stage_timings_ms: Mapping[str, object],
    total_ms: object,
    observability_metrics: Mapping[str, object] | None = None,
) -> GenerationTimingReceipt:
    """Atomically create one digest-bound timing receipt for a successful attempt."""

    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
    ):
        raise ValueError("generation_timing_attempt_number_invalid")
    if quality_receipt.receipt_digest != _quality_receipt_digest(quality_receipt):
        raise ValueError("generation_timing_quality_receipt_digest_mismatch")
    receipt = GenerationTimingReceipt(
        schema_version="generation-timing-receipt/v1",
        attempt_id=quality_receipt.attempt_id,
        attempt_number=attempt_number,
        revision=quality_receipt.revision,
        quality_receipt_digest=quality_receipt.receipt_digest,
        requested_release_policy_digest=quality_receipt.requested_release_policy_digest,
        pre_export_evaluation_policy_digest=quality_receipt.pre_export_evaluation_policy_digest,
        stage_timings_ms=_stage_timings(stage_timings_ms),
        total_ms=_bounded_timing_ms(total_ms, "total"),
        observability_metrics=(
            _observability_metrics(observability_metrics)
            if observability_metrics is not None
            else ()
        ),
    )
    receipt = replace(receipt, timing_receipt_digest=_receipt_digest(receipt))
    path = generation_timing_receipt_path(job_id)
    with _manifest_lock(path):
        records = _load_records(path)
        key = f"{receipt.attempt_id}#{receipt.revision}"
        existing = records.get(key)
        if existing is not None:
            if existing != receipt:
                raise ValueError("generation_timing_receipt_binding_changed")
            return existing
        records[key] = receipt
        _persist_records(path, records)
    return receipt


def load_generation_timing_receipt(job_id: object) -> GenerationTimingReceipt | None:
    path = generation_timing_receipt_path(job_id)
    with _manifest_lock(path):
        records = _load_records(path)
    if not records:
        return None
    return max(
        records.values(),
        key=lambda receipt: (
            receipt.attempt_number,
            receipt.revision,
            receipt.attempt_id,
        ),
    )


@dataclass(frozen=True, slots=True)
class GenerationAttemptTimingReceipt:
    """Immutable terminal timing for one operation attempt.

    This receipt intentionally does not require a QualityReceipt. It can
    therefore be sealed from an exception/finally path while preserving only
    opaque identity digests, bounded timings and scalar usage counters.
    """

    schema_version: str
    attempt_id: str
    attempt_number: int
    revision: int
    outcome: str
    failure_stage: str | None
    stage_timings_ms: tuple[tuple[str, int], ...]
    total_ms: int
    quality_receipt_digest: str | None = None
    provider_usage: dict[str, object] | None = None
    checkpoint_signals: tuple[dict[str, object], ...] = ()
    causes: tuple[tuple[str, str], ...] = ()
    receipt_digest: str = ""

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schemaVersion": self.schema_version,
            "attemptId": self.attempt_id,
            "attemptNumber": self.attempt_number,
            "revision": self.revision,
            "outcome": self.outcome,
            "failureStage": self.failure_stage,
            "stagesMs": dict(self.stage_timings_ms),
            "totalMs": self.total_ms,
            "receiptDigest": self.receipt_digest,
        }
        if self.quality_receipt_digest is not None:
            payload["qualityReceiptDigest"] = self.quality_receipt_digest
        if self.provider_usage is not None:
            payload["providerUsage"] = self.provider_usage
        if self.checkpoint_signals:
            payload["checkpointSignals"] = list(self.checkpoint_signals)
        if self.causes:
            payload["causes"] = dict(self.causes)
        return payload


@dataclass(frozen=True, slots=True)
class GenerationAttemptTimingReceiptV2:
    """Content-free terminal attempt receipt with explicit stage validity."""

    schema_version: str
    attempt_id: str
    attempt_number: int
    revision: int
    outcome: str
    stages: tuple[tuple[str, dict[str, object]], ...]
    queue_wait_ms: int
    wall_ms: int
    logical_calls: int
    retry_reason: str | None
    token_availability: str
    cost_availability: str
    worker: str
    terminal_error: str | None
    validity: str
    invalid_reasons: tuple[str, ...] = ()
    provider_usage: dict[str, object] | None = None
    #: E4 additions (``authored_generation_timing_receipt_e4.py`` module
    #: docstring has the full field-by-field description and the digest
    #: backward-compatibility rationale for their conditional serialization).
    provider_rate_profile: str | None = None
    normalizations: tuple[dict[str, object], ...] = ()
    quality_loop_variant: str | None = None
    flagged_slides: tuple[dict[str, object], ...] = ()
    #: M1: per-slide fit magnitude for the slides that failed the composition
    #: safety gate. Empty on a succeeded attempt, and then absent from the
    #: serialized form for the same digest reason as the fields above.
    fit_summaries: tuple[dict[str, object], ...] = ()
    receipt_digest: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "attemptId": self.attempt_id,
            "attemptNumber": self.attempt_number,
            "revision": self.revision,
            "outcome": self.outcome,
            "stages": dict(self.stages),
            "queueWaitMillis": self.queue_wait_ms,
            "wallMillis": self.wall_ms,
            "logicalCalls": self.logical_calls,
            "retryReason": self.retry_reason,
            "tokenAvailability": self.token_availability,
            "costAvailability": self.cost_availability,
            "worker": self.worker,
            "terminalError": self.terminal_error,
            "validity": self.validity,
            "invalidReasons": list(self.invalid_reasons),
            "providerUsage": self.provider_usage,
            **optional_top_level_fields(
                provider_rate_profile=self.provider_rate_profile,
                normalizations=self.normalizations,
                quality_loop_variant=self.quality_loop_variant,
                flagged_slides=self.flagged_slides,
                fit_summaries=self.fit_summaries,
            ),
            "receiptDigest": self.receipt_digest,
        }


def _attempt_receipt_digest(receipt: GenerationAttemptTimingReceipt) -> str:
    payload = receipt.to_dict()
    payload.pop("receiptDigest", None)
    return "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _attempt_receipt_v2_digest(receipt: GenerationAttemptTimingReceiptV2) -> str:
    payload = receipt.to_dict()
    payload.pop("receiptDigest", None)
    return "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _public_stage_receipts(
    value: object,
) -> tuple[tuple[str, dict[str, object]], ...]:
    if not isinstance(value, Mapping) or set(value) != set(PUBLIC_GENERATION_TIMING_STAGES):
        raise ValueError("generation_attempt_v2_stages_invalid")
    output: list[tuple[str, dict[str, object]]] = []
    for name in PUBLIC_GENERATION_TIMING_STAGES:
        raw = value.get(name)
        if not isinstance(raw, Mapping):
            raise ValueError("generation_attempt_v2_stages_invalid")
        state = raw.get("state")
        if state == "observed":
            # ``wallMillis`` (D13/E4, authored_generation_timing_receipt_e4.py)
            # is optional so a receipt written before it existed still loads.
            has_wall = stage_has_wall(raw)
            required_keys = {"state", "startMillis", "endMillis", "durationMillis"}
            if set(raw) != required_keys | ({"wallMillis"} if has_wall else set()):
                raise ValueError("generation_attempt_v2_stages_invalid")
            start = _bounded_timing_ms(raw.get("startMillis"), f"{name}_start")
            end = _bounded_timing_ms(raw.get("endMillis"), f"{name}_end")
            duration = _bounded_timing_ms(raw.get("durationMillis"), name)
            if end < start:
                raise ValueError("generation_attempt_v2_stages_invalid")
            normalized = {
                "state": "observed",
                "startMillis": start,
                "endMillis": end,
                "durationMillis": duration,
            }
            wall = stage_wall_millis(
                raw, stage_name=name, bounded_timing_ms=_bounded_timing_ms
            )
            if wall is not None:
                normalized["wallMillis"] = wall
        elif state == "not_started" and set(raw) == {"state"}:
            normalized = {"state": "not_started"}
        elif (
            state == "invalid"
            and set(raw) == {"state", "reason"}
            and raw.get("reason") == "executed_duration_missing"
        ):
            normalized = {
                "state": "invalid",
                "reason": "executed_duration_missing",
            }
        else:
            raise ValueError("generation_attempt_v2_stages_invalid")
        output.append((name, normalized))
    return tuple(output)


def _availability(value: object, field_name: str) -> str:
    if value not in {"available", "partial", "unavailable", "not_started"}:
        raise ValueError(f"generation_attempt_v2_{field_name}_invalid")
    return str(value)


def _optional_reason(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 160:
        raise ValueError(f"generation_attempt_v2_{field_name}_invalid")
    return value.strip()


def _attempt_receipt_v2_from_dict(
    value: object,
) -> GenerationAttemptTimingReceiptV2:
    if not isinstance(value, Mapping):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    attempt_number = value.get("attemptNumber")
    revision = value.get("revision")
    outcome = value.get("outcome")
    validity = value.get("validity")
    invalid_reasons = value.get("invalidReasons")
    if (
        value.get("schemaVersion") != "generation-attempt-timing-receipt/v2"
        or isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or outcome not in GENERATION_ATTEMPT_OUTCOMES
        or validity not in {"valid", "rc_invalid"}
        or not isinstance(invalid_reasons, Sequence)
        or isinstance(invalid_reasons, (str, bytes))
        or any(not isinstance(item, str) or not item for item in invalid_reasons)
    ):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    stages = _public_stage_receipts(value.get("stages"))
    derived_reasons = tuple(
        f"{name}:executed_duration_missing"
        for name, stage in stages
        if stage["state"] == "invalid"
    )
    reasons = tuple(str(item) for item in invalid_reasons)
    if reasons != derived_reasons or (validity == "valid") != (not reasons):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    terminal_error = _optional_reason(value.get("terminalError"), "terminal_error")
    if (outcome == "succeeded") != (terminal_error is None):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    worker = _optional_reason(value.get("worker"), "worker")
    if worker is None:
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    # E4 fields (providerRateProfile/normalizations/qualityLoop). Absent on a
    # receipt written before E4 landed -- ``None``/empty then, not invented.
    receipt = GenerationAttemptTimingReceiptV2(
        schema_version="generation-attempt-timing-receipt/v2",
        attempt_id=public_attempt_id(value.get("attemptId")),
        attempt_number=attempt_number,
        revision=revision,
        outcome=str(outcome),
        stages=stages,
        queue_wait_ms=_bounded_timing_ms(value.get("queueWaitMillis"), "queue_wait"),
        wall_ms=_bounded_timing_ms(value.get("wallMillis"), "wall"),
        logical_calls=_bounded_timing_ms(value.get("logicalCalls"), "logical_calls"),
        retry_reason=_optional_reason(value.get("retryReason"), "retry_reason"),
        token_availability=_availability(value.get("tokenAvailability"), "tokens"),
        cost_availability=_availability(value.get("costAvailability"), "cost"),
        worker=worker,
        terminal_error=terminal_error,
        validity=str(validity),
        invalid_reasons=reasons,
        provider_usage=_provider_usage(value.get("providerUsage")),
        provider_rate_profile=_e4_provider_rate_profile(
            value.get("providerRateProfile")
        ),
        normalizations=normalization_entries(value.get("normalizations")),
        quality_loop_variant=quality_loop_variant_field(
            value.get("qualityLoop"), optional_reason=_optional_reason
        ),
        flagged_slides=flagged_slide_entries(value.get("flaggedSlides")),
        fit_summaries=fit_summary_entries(value.get("fitSummary")),
        receipt_digest=_required_sha256(value.get("receiptDigest"), "receipt_digest"),
    )
    if receipt.receipt_digest != _attempt_receipt_v2_digest(receipt):
        raise ValueError("generation_attempt_timing_receipt_v2_digest_mismatch")
    return receipt


def _attempt_receipt_from_dict(value: object) -> GenerationAttemptTimingReceipt:
    if not isinstance(value, Mapping):
        raise ValueError("generation_attempt_timing_receipt_invalid")
    attempt_number = value.get("attemptNumber")
    revision = value.get("revision")
    outcome = value.get("outcome")
    failure_stage = value.get("failureStage")
    if (
        value.get("schemaVersion") != "generation-attempt-timing-receipt/v1"
        or isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or outcome not in GENERATION_ATTEMPT_OUTCOMES
        or (
            failure_stage is not None
            and failure_stage not in GENERATION_ATTEMPT_TIMING_STAGES
        )
        or (outcome == "succeeded" and failure_stage is not None)
    ):
        raise ValueError("generation_attempt_timing_receipt_invalid")
    quality_digest = value.get("qualityReceiptDigest")
    receipt = GenerationAttemptTimingReceipt(
        schema_version="generation-attempt-timing-receipt/v1",
        attempt_id=public_attempt_id(value.get("attemptId")),
        attempt_number=attempt_number,
        revision=revision,
        outcome=str(outcome),
        failure_stage=failure_stage,
        stage_timings_ms=_partial_stage_timings(value.get("stagesMs")),
        total_ms=_bounded_timing_ms(value.get("totalMs"), "attempt_total"),
        quality_receipt_digest=(
            _required_sha256(quality_digest, "quality_receipt_digest")
            if quality_digest is not None
            else None
        ),
        provider_usage=_provider_usage(value.get("providerUsage")),
        checkpoint_signals=_checkpoint_signals(value.get("checkpointSignals", ())),
        causes=_attempt_causes(value.get("causes")),
        receipt_digest=_required_sha256(value.get("receiptDigest"), "receipt_digest"),
    )
    if receipt.receipt_digest != _attempt_receipt_digest(receipt):
        raise ValueError("generation_attempt_timing_receipt_digest_mismatch")
    return receipt


GenerationAttemptTimingReceiptAny = (
    GenerationAttemptTimingReceipt | GenerationAttemptTimingReceiptV2
)


def _load_attempt_records(path: Path) -> dict[str, GenerationAttemptTimingReceiptAny]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("generation_attempt_timing_manifest_corrupt") from error
    records = payload.get("receipts") if isinstance(payload, Mapping) else None
    if (
        not isinstance(payload, Mapping)
        or payload.get("schemaVersion") != "generation-attempt-timing-manifest/v1"
        or not isinstance(records, Mapping)
        or payload.get("manifestDigest") != _manifest_digest(records)
    ):
        raise ValueError("generation_attempt_timing_manifest_corrupt")
    parsed: dict[str, GenerationAttemptTimingReceiptAny] = {}
    for key, item in records.items():
        receipt = (
            _attempt_receipt_v2_from_dict(item)
            if isinstance(item, Mapping)
            and item.get("schemaVersion") == "generation-attempt-timing-receipt/v2"
            else _attempt_receipt_from_dict(item)
        )
        expected = f"{receipt.attempt_id}#{receipt.attempt_number}#{receipt.revision}"
        if isinstance(receipt, GenerationAttemptTimingReceiptV2):
            expected += "#v2"
        if key != expected:
            raise ValueError("generation_attempt_timing_manifest_key_invalid")
        parsed[key] = receipt
    return parsed


def _persist_attempt_records(
    path: Path, records: Mapping[str, GenerationAttemptTimingReceiptAny]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = {key: receipt.to_dict() for key, receipt in sorted(records.items())}
    envelope = {
        "schemaVersion": "generation-attempt-timing-manifest/v1",
        "receipts": serialized,
        "manifestDigest": _manifest_digest(serialized),
    }
    encoded = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def create_generation_attempt_timing_receipt(
    *,
    job_id: object,
    attempt_id: object,
    attempt_number: int,
    revision: int,
    outcome: str,
    stage_timings_ms: Mapping[str, object],
    total_ms: object,
    failure_stage: str | None = None,
    quality_receipt: QualityReceiptV2 | None = None,
    provider_usage: Mapping[str, object] | None = None,
    checkpoint_signals: Sequence[Mapping[str, object]] = (),
    causes: Mapping[str, object] | None = None,
) -> GenerationAttemptTimingReceipt:
    """Atomically append one immutable success or failure attempt receipt."""

    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or outcome not in GENERATION_ATTEMPT_OUTCOMES
        or (
            failure_stage is not None
            and failure_stage not in GENERATION_ATTEMPT_TIMING_STAGES
        )
        or (outcome == "succeeded" and failure_stage is not None)
    ):
        raise ValueError("generation_attempt_timing_receipt_invalid")
    opaque_attempt_id = public_attempt_id(attempt_id)
    quality_digest = None
    if quality_receipt is not None:
        if quality_receipt.receipt_digest != _quality_receipt_digest(quality_receipt):
            raise ValueError("generation_timing_quality_receipt_digest_mismatch")
        if quality_receipt.attempt_id != opaque_attempt_id:
            raise ValueError("generation_attempt_quality_receipt_attempt_mismatch")
        quality_digest = quality_receipt.receipt_digest
    receipt = GenerationAttemptTimingReceipt(
        schema_version="generation-attempt-timing-receipt/v1",
        attempt_id=opaque_attempt_id,
        attempt_number=attempt_number,
        revision=revision,
        outcome=outcome,
        failure_stage=failure_stage,
        stage_timings_ms=_partial_stage_timings(stage_timings_ms),
        total_ms=_bounded_timing_ms(total_ms, "attempt_total"),
        quality_receipt_digest=quality_digest,
        provider_usage=_provider_usage(provider_usage),
        checkpoint_signals=_checkpoint_signals(checkpoint_signals),
        causes=_attempt_causes(causes),
    )
    receipt = replace(receipt, receipt_digest=_attempt_receipt_digest(receipt))
    path = generation_attempt_timing_receipt_path(job_id)
    with _manifest_lock(path):
        records = _load_attempt_records(path)
        key = f"{receipt.attempt_id}#{receipt.attempt_number}#{receipt.revision}"
        existing = records.get(key)
        if existing is not None:
            if existing != receipt:
                raise ValueError("generation_attempt_timing_receipt_binding_changed")
            return existing
        records[key] = receipt
        _persist_attempt_records(path, records)
    return receipt


def create_generation_attempt_timing_receipt_v2(
    *,
    job_id: object,
    attempt_id: object,
    attempt_number: int,
    revision: int,
    outcome: str,
    stage_observations: Mapping[str, Mapping[str, object]],
    executed_stages: Sequence[str],
    queue_wait_ms: object,
    wall_ms: object,
    provider_usage: Mapping[str, object],
    retry_reason: str | None,
    worker: str,
    terminal_error: str | None,
    provider_rate_profile: str | None = None,
    normalizations: Sequence[Mapping[str, object]] = (),
    quality_loop_variant: str | None = None,
    flagged_slides: Sequence[Mapping[str, object]] = (),
    fit_summaries: Sequence[Mapping[str, object]] | Mapping[object, object] = (),
) -> GenerationAttemptTimingReceiptV2:
    """Seal one v2 terminal receipt without prompt, content, or provider traces.

    ``provider_rate_profile``, ``normalizations`` and ``quality_loop_variant``
    are E4 additions (section 8 of the design doc) and all default to the
    absent/empty state a caller that does not supply them yet already
    produces: this stays backward compatible for a caller that has not been
    updated.
    """

    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or outcome not in GENERATION_ATTEMPT_OUTCOMES
    ):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    executed = frozenset(executed_stages)
    if not executed.issubset(PUBLIC_GENERATION_TIMING_STAGES):
        raise ValueError("generation_attempt_v2_executed_stages_invalid")
    normalized_observations: dict[str, dict[str, int]] = {}
    for raw_name, raw_observation in stage_observations.items():
        name = str(raw_name)
        if name not in GENERATION_ATTEMPT_TIMING_STAGES:
            raise ValueError("generation_attempt_v2_stage_observation_invalid")
        if not isinstance(raw_observation, Mapping):
            raise ValueError("generation_attempt_v2_stage_observation_invalid")
        start = _bounded_timing_ms(raw_observation.get("startMillis"), f"{name}_start")
        end = _bounded_timing_ms(raw_observation.get("endMillis"), f"{name}_end")
        duration = _bounded_timing_ms(raw_observation.get("durationMillis"), name)
        if end < start:
            raise ValueError("generation_attempt_v2_stage_observation_invalid")
        normalized_observations[name] = {
            "startMillis": start,
            "endMillis": end,
            "durationMillis": duration,
        }

    public_sources = {
        "outline": ("outline",),
        "provider": ("provider",),
        "authoring": ("author",),
        "repair": ("repair",),
        "chromiumReview": ("render", "quality"),
        "publication": ("publication",),
    }
    public_stages: dict[str, dict[str, object]] = {}
    invalid_reasons: list[str] = []
    for public_name in PUBLIC_GENERATION_TIMING_STAGES:
        observations = [
            normalized_observations[source_name]
            for source_name in public_sources[public_name]
            if source_name in normalized_observations
        ]
        if observations:
            merged_start = min(item["startMillis"] for item in observations)
            merged_end = max(item["endMillis"] for item in observations)
            public_stages[public_name] = {
                "state": "observed",
                "startMillis": merged_start,
                "endMillis": merged_end,
                "durationMillis": sum(item["durationMillis"] for item in observations),
                # D13: the merged window's real wall span, next to the summed
                # per-source work above (authored_generation_timing_receipt_e4.py).
                "wallMillis": merged_stage_wall_millis(merged_start, merged_end),
            }
        elif public_name in executed:
            public_stages[public_name] = {
                "state": "invalid",
                "reason": "executed_duration_missing",
            }
            invalid_reasons.append(f"{public_name}:executed_duration_missing")
        else:
            public_stages[public_name] = {"state": "not_started"}

    safe_usage = _provider_usage(provider_usage)
    if safe_usage is None:
        raise ValueError("generation_attempt_provider_usage_invalid")
    totals = safe_usage["totals"]
    assert isinstance(totals, Mapping)
    logical_calls = int(totals["logicalCalls"])
    reported_calls = int(totals["reportedUsageCalls"])
    unavailable_calls = int(totals["unavailableUsageCalls"])
    priced_calls = int(totals["pricedCalls"])
    estimated_cost = int(totals["estimatedCostMicrounits"])
    if logical_calls == 0:
        token_availability = "not_started"
        cost_availability = "not_started"
    else:
        token_availability = (
            "available"
            if reported_calls >= logical_calls and unavailable_calls == 0
            else "partial"
            if reported_calls > 0
            else "unavailable"
        )
        cost_availability = (
            "available"
            if priced_calls >= logical_calls
            else "partial"
            if priced_calls > 0 or estimated_cost > 0
            else "unavailable"
        )

    receipt = GenerationAttemptTimingReceiptV2(
        schema_version="generation-attempt-timing-receipt/v2",
        attempt_id=public_attempt_id(attempt_id),
        attempt_number=attempt_number,
        revision=revision,
        outcome=outcome,
        stages=_public_stage_receipts(public_stages),
        queue_wait_ms=_bounded_timing_ms(queue_wait_ms, "queue_wait"),
        wall_ms=_bounded_timing_ms(wall_ms, "wall"),
        logical_calls=logical_calls,
        retry_reason=_optional_reason(retry_reason, "retry_reason"),
        token_availability=token_availability,
        cost_availability=cost_availability,
        worker=_optional_reason(worker, "worker") or "workspace-generation-worker",
        terminal_error=_optional_reason(terminal_error, "terminal_error"),
        validity="rc_invalid" if invalid_reasons else "valid",
        invalid_reasons=tuple(invalid_reasons),
        provider_usage=safe_usage,
        provider_rate_profile=_e4_provider_rate_profile(provider_rate_profile),
        normalizations=normalization_entries(normalizations),
        quality_loop_variant=_optional_reason(
            quality_loop_variant, "quality_loop_variant"
        ),
        flagged_slides=flagged_slide_entries(flagged_slides),
        fit_summaries=fit_summary_entries(fit_summaries),
    )
    if (outcome == "succeeded") != (receipt.terminal_error is None):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    receipt = replace(receipt, receipt_digest=_attempt_receipt_v2_digest(receipt))
    path = generation_attempt_timing_receipt_path(job_id)
    with _manifest_lock(path):
        records = _load_attempt_records(path)
        key = f"{receipt.attempt_id}#{receipt.attempt_number}#{receipt.revision}#v2"
        existing = records.get(key)
        if existing is not None:
            if existing != receipt:
                raise ValueError("generation_attempt_timing_receipt_binding_changed")
            assert isinstance(existing, GenerationAttemptTimingReceiptV2)
            return existing
        records[key] = receipt
        _persist_attempt_records(path, records)
    return receipt


def load_generation_attempt_timing_receipts(
    job_id: object,
) -> tuple[GenerationAttemptTimingReceiptAny, ...]:
    """Load every verified attempt receipt in deterministic attempt order."""

    path = generation_attempt_timing_receipt_path(job_id)
    with _manifest_lock(path):
        records = _load_attempt_records(path)
    return tuple(
        sorted(
            records.values(),
            key=lambda receipt: (
                receipt.attempt_number,
                receipt.revision,
                receipt.attempt_id,
            ),
        )
    )


@dataclass(slots=True)
class GenerationTimingRecorder:
    """Monotonic, exception-safe stage collector for a final attempt receipt."""

    started_at: float = field(default_factory=monotonic)
    _stage_timings_ms: dict[str, int] = field(default_factory=dict)
    _stage_observations: dict[str, dict[str, int]] = field(default_factory=dict)
    _checkpoint_signals: list[dict[str, object]] = field(default_factory=list)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if name not in GENERATION_ATTEMPT_TIMING_STAGES:
            raise ValueError("generation_attempt_timing_stage_invalid")
        started = monotonic()
        start_ms = _bounded_timing_ms(
            (started - self.started_at) * 1000,
            f"{name}_start",
        )
        try:
            yield
        finally:
            ended = monotonic()
            elapsed = _bounded_timing_ms(
                (ended - started) * 1000,
                name,
            )
            self._stage_timings_ms[name] = min(
                MAX_GENERATION_STAGE_TIMING_MS,
                self._stage_timings_ms.get(name, 0) + elapsed,
            )
            previous = self._stage_observations.get(name)
            self._stage_observations[name] = {
                "startMillis": min(
                    start_ms,
                    previous["startMillis"] if previous is not None else start_ms,
                ),
                "endMillis": _bounded_timing_ms(
                    (ended - self.started_at) * 1000,
                    f"{name}_end",
                ),
                "durationMillis": self._stage_timings_ms[name],
            }

    def record_stage_ms(self, name: str, value: object) -> None:
        if name not in GENERATION_ATTEMPT_TIMING_STAGES:
            raise ValueError("generation_attempt_timing_stage_invalid")
        duration = _bounded_timing_ms(value, name)
        end_ms = self.elapsed_ms
        self._stage_timings_ms[name] = duration
        self._stage_observations[name] = {
            "startMillis": max(0, end_ms - duration),
            "endMillis": end_ms,
            "durationMillis": duration,
        }

    def record_checkpoint_signal(self, signal: Mapping[str, object]) -> None:
        self._checkpoint_signals.append(dict(_checkpoint_signals((signal,))[0]))

    def snapshot_stage_timings_ms(self) -> dict[str, int]:
        return dict(self._stage_timings_ms)

    def stage_observations(self) -> dict[str, dict[str, int]]:
        return {
            name: dict(observation)
            for name, observation in self._stage_observations.items()
        }

    def checkpoint_signals(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(signal) for signal in self._checkpoint_signals)

    @property
    def elapsed_ms(self) -> int:
        return _bounded_timing_ms(
            (monotonic() - self.started_at) * 1000,
            "attempt_total",
        )


__all__ = [
    "GENERATION_ATTEMPT_OUTCOMES",
    "GENERATION_ATTEMPT_TIMING_STAGES",
    "GenerationAttemptTimingReceipt",
    "GenerationAttemptTimingReceiptV2",
    "GenerationTimingRecorder",
    "GenerationTimingReceipt",
    "create_generation_attempt_timing_receipt",
    "create_generation_attempt_timing_receipt_v2",
    "create_generation_timing_receipt",
    "generation_attempt_timing_receipt_path",
    "generation_timing_receipt_path",
    "load_generation_attempt_timing_receipts",
    "load_generation_timing_receipt",
]
