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


def _load_attempt_records(path: Path) -> dict[str, GenerationAttemptTimingReceipt]:
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
    parsed: dict[str, GenerationAttemptTimingReceipt] = {}
    for key, item in records.items():
        receipt = _attempt_receipt_from_dict(item)
        expected = (
            f"{receipt.attempt_id}#{receipt.attempt_number}#{receipt.revision}"
        )
        if key != expected:
            raise ValueError("generation_attempt_timing_manifest_key_invalid")
        parsed[key] = receipt
    return parsed


def _persist_attempt_records(
    path: Path, records: Mapping[str, GenerationAttemptTimingReceipt]
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


def load_generation_attempt_timing_receipts(
    job_id: object,
) -> tuple[GenerationAttemptTimingReceipt, ...]:
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
    _checkpoint_signals: list[dict[str, object]] = field(default_factory=list)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if name not in GENERATION_ATTEMPT_TIMING_STAGES:
            raise ValueError("generation_attempt_timing_stage_invalid")
        started = monotonic()
        try:
            yield
        finally:
            elapsed = _bounded_timing_ms(
                (monotonic() - started) * 1000,
                name,
            )
            self._stage_timings_ms[name] = min(
                MAX_GENERATION_STAGE_TIMING_MS,
                self._stage_timings_ms.get(name, 0) + elapsed,
            )

    def record_stage_ms(self, name: str, value: object) -> None:
        if name not in GENERATION_ATTEMPT_TIMING_STAGES:
            raise ValueError("generation_attempt_timing_stage_invalid")
        self._stage_timings_ms[name] = _bounded_timing_ms(value, name)

    def record_checkpoint_signal(self, signal: Mapping[str, object]) -> None:
        self._checkpoint_signals.append(dict(_checkpoint_signals((signal,))[0]))

    def snapshot_stage_timings_ms(self) -> dict[str, int]:
        # Attempt receipts must never expose null/missing core stage timing.
        return {
            stage: self._stage_timings_ms.get(stage, 0)
            for stage in (*GENERATION_TIMING_STAGES, "provider")
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
    "GenerationTimingRecorder",
    "GenerationTimingReceipt",
    "create_generation_attempt_timing_receipt",
    "create_generation_timing_receipt",
    "generation_attempt_timing_receipt_path",
    "generation_timing_receipt_path",
    "load_generation_attempt_timing_receipts",
    "load_generation_timing_receipt",
]
