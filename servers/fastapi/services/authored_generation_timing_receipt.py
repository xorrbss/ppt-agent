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
    "repair",
    "checkpoint",
    "export",
    "publication",
)
MAX_GENERATION_STAGE_TIMING_MS = 30 * 24 * 60 * 60 * 1000


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
    if not isinstance(value, Mapping) or set(value) != set(GENERATION_TIMING_STAGES):
        raise ValueError("generation_timing_stages_invalid")
    return tuple(
        (stage, _bounded_timing_ms(value[stage], stage))
        for stage in GENERATION_TIMING_STAGES
    )


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
    timing_receipt_digest: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
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
