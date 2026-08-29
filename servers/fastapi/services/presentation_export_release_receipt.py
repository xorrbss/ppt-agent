"""Construction of the final Hybrid export readiness receipt."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


def _receipt_digest(payload: dict[str, Any]) -> str:
    return "sha256:" + sha256(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def build_unified_release_receipt(
    *,
    generation_receipt: dict[str, Any],
    atomic_binding: dict[str, Any],
    quality: dict[str, Any],
    visual_parity_status: str,
    powerpoint_qa_status: str,
) -> dict[str, Any]:
    """Build the digest-sealed receipt without widening the release claim."""

    native = quality.get("nativeCoverage")
    native = native if isinstance(native, dict) else {}
    native_status = str(native.get("status", "not_assessed"))
    raw_ratio = native.get("ratio")
    native_ratio = (
        float(raw_ratio)
        if isinstance(raw_ratio, (int, float)) and not isinstance(raw_ratio, bool)
        else None
    )
    blockers: list[str] = []
    if generation_receipt.get("require_source_readback") and generation_receipt.get(
        "source_readback_status"
    ) != "passed":
        blockers.append("source-readback-not-passed")
    if generation_receipt.get("require_visual_review") and visual_parity_status != "passed":
        blockers.append("visual-parity-not-passed")
    if generation_receipt.get("require_performance") and generation_receipt.get(
        "performance_status"
    ) != "passed":
        blockers.append("performance-not-passed")
    minimum_ratio = float(generation_receipt.get("minimum_native_coverage_ratio", 0.0))
    if generation_receipt.get("require_native_coverage") and (
        native_status != "passed"
        or native_ratio is None
        or native_ratio < minimum_ratio
    ):
        blockers.append("native-coverage-not-passed")
    if (
        generation_receipt.get("require_visual_review")
        or generation_receipt.get("require_native_coverage")
    ) and powerpoint_qa_status != "passed":
        blockers.append("powerpoint-qa-not-passed")
    payload = {
        "schemaVersion": "presenton.unified-export-release-receipt/v1",
        "requestedReleaseProfile": generation_receipt["requested_release_profile"],
        "requestedReleasePolicyDigest": generation_receipt[
            "requested_release_policy_digest"
        ],
        "generationReceiptDigest": generation_receipt["receipt_digest"],
        "exportBinding": atomic_binding,
        "artifactSucceeded": True,
        "releaseReady": not blockers,
        "requireSourceReadback": bool(generation_receipt["require_source_readback"]),
        "requireVisualReview": bool(generation_receipt["require_visual_review"]),
        "requirePerformance": bool(generation_receipt["require_performance"]),
        "requireNativeCoverage": bool(generation_receipt["require_native_coverage"]),
        "minimumNativeCoverageRatio": minimum_ratio,
        "sourceReadbackStatus": generation_receipt["source_readback_status"],
        "visualParityStatus": visual_parity_status,
        "performanceStatus": generation_receipt["performance_status"],
        "nativeCoverageStatus": native_status,
        "nativeCoverageRatio": native_ratio,
        "powerpointQaStatus": powerpoint_qa_status,
        "blockers": blockers,
    }
    payload["receiptDigest"] = _receipt_digest(payload)
    return payload
