"""Immutable authored generation timings bound to QualityReceipt v2."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

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
    "illustrationDuration", "renderDuration", "qualityDuration",
    "checkpointDuration", "publicationDuration", "exportDuration",
    "providerInitialCalls", "providerRetryCalls", "providerLogicalOrdinal",
    "estimatedTokens", "actualTokens", "estimatedCost", "actualCost",
    "stageRetryCalls", "stageEstimatedCost", "stageActualCost",
    "attemptNumber", "fencingOutcome", "chromeLaunch", "chromeRestart",
    "blockedRequest", "timeout", "crash", "workerPeakMemory",
    "workerPeakHandles", "artifactBytes", "cacheHit", "cacheMiss",
    "cacheEviction", "objectStoreLatency", "finalOutcome",
)
LEGACY_GENERATION_OBSERVABILITY_METRICS = tuple(
    name
    for name in GENERATION_OBSERVABILITY_METRICS
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
    metric_names = (
        GENERATION_OBSERVABILITY_METRICS
        if set(value) == set(GENERATION_OBSERVABILITY_METRICS)
        else LEGACY_GENERATION_OBSERVABILITY_METRICS
        if set(value) == set(LEGACY_GENERATION_OBSERVABILITY_METRICS)
        else None
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


__all__ = [
    "GenerationTimingReceipt",
    "create_generation_timing_receipt",
    "generation_timing_receipt_path",
    "load_generation_timing_receipt",
]
