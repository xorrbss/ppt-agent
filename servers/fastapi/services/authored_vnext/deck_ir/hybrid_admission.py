"""WP7 boundary: admit only validated semantic/QID/geometry subsets to Hybrid."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .grammar import contract_mapping, digest

_NATIVE_KINDS = frozenset({"text", "metric", "card", "process", "image", "shape"})


@dataclass(frozen=True)
class HybridAdmission:
    admitted: bool
    reason_code: str | None
    scene_payload: Mapping[str, Any] | None
    receipt: Mapping[str, Any]
    native_editability_ready: bool = False
    promotion_ready: bool = False
    deployment_authorized: bool = False


def admit_validated_hybrid_subset(*, semantic: Mapping[str, Any], browser_observation: Mapping[str, Any], allowed_kinds: Sequence[str] = tuple(sorted(_NATIVE_KINDS))) -> HybridAdmission:
    """Create an inert Scene mapping only after exact semantic/QID/geometry proof.

    This is admission, never native readiness evidence.  Raster-only or generic
    fallback is rejected rather than being reported as successful Hybrid.
    """

    semantic = contract_mapping(semantic)
    browser_observation = contract_mapping(browser_observation)
    allowed = frozenset(allowed_kinds)
    elements = semantic.get("elements")
    geometry = browser_observation.get("geometryByQid")
    reason: str | None = None
    if not isinstance(elements, list) or not isinstance(geometry, Mapping) or browser_observation.get("passed") is not True:
        reason = "hybrid_semantic_geometry_unverified"
    elif any(not isinstance(item, Mapping) or item.get("kind") not in allowed or item.get("kind") not in _NATIVE_KINDS for item in elements):
        reason = "hybrid_component_capability_missing"
    else:
        qids = [item.get("qid") for item in elements]
        if any(not isinstance(qid, str) or not qid for qid in qids) or len(qids) != len(set(qids)) or set(qids) != set(geometry):
            reason = "hybrid_qid_geometry_mismatch"
        else:
            for qid in qids:
                rect = geometry[qid]
                if not isinstance(rect, Mapping) or any(not isinstance(rect.get(key), (int, float)) or isinstance(rect.get(key), bool) for key in ("x", "y", "width", "height")) or rect["width"] <= 0 or rect["height"] <= 0:
                    reason = "hybrid_qid_geometry_mismatch"
                    break
    if reason is not None:
        receipt = {"schemaVersion": "deck-ir-hybrid-admission-receipt/v1", "status": "rejected", "reasonCode": reason, "semanticDigest": digest(semantic), "browserMeasurementDigest": digest(browser_observation), "nativeEditabilityReady": False, "promotionReady": False, "deploymentAuthorized": False}
        return HybridAdmission(False, reason, None, {**receipt, "receiptDigest": digest(receipt)})
    scene = {"schemaVersion": "deck-ir-hybrid-scene-mapping/v1", "semanticDigest": digest(semantic), "elements": [{"qid": item["qid"], "kind": item["kind"], "semanticRole": item.get("semanticRole"), "geometry": dict(geometry[item["qid"]])} for item in elements]}
    receipt = {"schemaVersion": "deck-ir-hybrid-admission-receipt/v1", "status": "admitted", "reasonCode": None, "semanticDigest": digest(semantic), "browserMeasurementDigest": digest(browser_observation), "sceneDigest": digest(scene), "nativeEditabilityReady": False, "promotionReady": False, "deploymentAuthorized": False}
    return HybridAdmission(True, None, scene, {**receipt, "receiptDigest": digest(receipt)})


__all__ = ["HybridAdmission", "admit_validated_hybrid_subset"]
