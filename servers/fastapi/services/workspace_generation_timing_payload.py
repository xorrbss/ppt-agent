"""Bounded public projection for authored generation timing receipts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


GENERATION_TIMING_STAGES = (
    "outline",
    "author",
    "repair",
    "checkpoint",
    "export",
    "publication",
)
MAX_GENERATION_TIMING_MS = 30 * 24 * 60 * 60 * 1000
GENERATION_TIMING_RESULT_KEYS = frozenset(
    {
        "stage_timings_ms",
        "total_ms",
        "generation_timing_receipt",
    }
)


def _generation_timing_ms(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return None
    return min(round(numeric), MAX_GENERATION_TIMING_MS)


def sanitize_generation_stage_timings(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, int] = {}
    for stage in GENERATION_TIMING_STAGES:
        duration = _generation_timing_ms(value.get(stage))
        if duration is None:
            return {}
        safe[stage] = duration
    return safe


def sanitize_generation_timing_receipt(value: Any) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    stages = sanitize_generation_stage_timings(value.get("stagesMs"))
    total_ms = _generation_timing_ms(value.get("totalMs"))
    attempt_number = value.get("attemptNumber")
    revision = value.get("revision")
    if (
        value.get("schemaVersion") != "generation-timing-receipt/v1"
        or not stages
        or total_ms is None
        or isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
    ):
        return {}

    raw_attempt_id = value.get("attemptId")
    if not isinstance(raw_attempt_id, str):
        return {}
    attempt_hex = (
        raw_attempt_id[7:]
        if raw_attempt_id.startswith("sha256:")
        else raw_attempt_id
    )
    if len(attempt_hex) not in {32, 64} or any(
        char not in "0123456789abcdefABCDEF" for char in attempt_hex
    ):
        return {}
    attempt_id = (
        f"sha256:{attempt_hex.lower()}"
        if len(attempt_hex) == 64
        else attempt_hex.lower()
    )

    digests: dict[str, str] = {}
    for key in (
        "qualityReceiptDigest",
        "requestedReleasePolicyDigest",
        "preExportEvaluationPolicyDigest",
        "timingReceiptDigest",
    ):
        raw = value.get(key)
        if not isinstance(raw, str):
            return {}
        candidate = raw if raw.startswith("sha256:") else f"sha256:{raw}"
        hexadecimal = candidate[7:]
        if len(hexadecimal) != 64 or any(
            char not in "0123456789abcdefABCDEF" for char in hexadecimal
        ):
            return {}
        digests[key] = candidate.lower()

    return {
        "schemaVersion": "generation-timing-receipt/v1",
        "attemptId": attempt_id,
        "attemptNumber": attempt_number,
        "revision": revision,
        "qualityReceiptDigest": digests["qualityReceiptDigest"],
        "requestedReleasePolicyDigest": digests[
            "requestedReleasePolicyDigest"
        ],
        "preExportEvaluationPolicyDigest": digests[
            "preExportEvaluationPolicyDigest"
        ],
        "stagesMs": stages,
        "totalMs": total_ms,
        "timingReceiptDigest": digests["timingReceiptDigest"],
    }


def generation_timing_result_projection(value: Any) -> dict[str, object]:
    receipt = sanitize_generation_timing_receipt(value)
    if not receipt:
        return {}
    return {
        "stage_timings_ms": dict(receipt["stagesMs"]),
        "total_ms": receipt["totalMs"],
        "generation_timing_receipt": receipt,
    }


def sanitize_generation_timing_result(payload: Mapping[str, Any]) -> dict[str, object]:
    """Keep only independently valid public generation timing fields."""

    safe: dict[str, object] = {}
    stage_timings = sanitize_generation_stage_timings(payload.get("stage_timings_ms"))
    if stage_timings:
        safe["stage_timings_ms"] = stage_timings
    receipt = sanitize_generation_timing_receipt(
        payload.get("generation_timing_receipt")
    )
    if receipt:
        safe["generation_timing_receipt"] = receipt
        if "total_ms" in payload:
            safe["total_ms"] = receipt["totalMs"]
    return safe


__all__ = [
    "GENERATION_TIMING_STAGES",
    "GENERATION_TIMING_RESULT_KEYS",
    "MAX_GENERATION_TIMING_MS",
    "generation_timing_result_projection",
    "sanitize_generation_stage_timings",
    "sanitize_generation_timing_receipt",
    "sanitize_generation_timing_result",
]
