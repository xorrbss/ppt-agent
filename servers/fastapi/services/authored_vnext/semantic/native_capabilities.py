"""Derive native capability admission/readiness from the canonical Scene.

Owner decisions are admission facts.  They are never readiness evidence: a
native owner becomes ready only when a separately verified receipt digest is
provided for the exact versioned capability.
"""

from __future__ import annotations

from collections import Counter
from typing import Mapping

from models.authored_vnext.deck import DeckSceneIR
from models.authored_vnext.semantic.native_capabilities import (
    NativeCapabilityAdmissionV1,
    NativeCapabilityProjectionV1,
    NativeCapabilityReadinessV1,
)


NATIVE_CAPABILITY_IDS_V1: tuple[str, ...] = tuple(
    sorted(
        {
            "css.background-plane.v1",
            "css.clip-path.v1",
            "css.filter-blend.v1",
            "css.gradient.v1",
            "css.grid-flex.v1",
            "css.paint-closure.v1",
            "css.shadow.v1",
            "editable.chart.line-pie.v1",
            # Retained only for read compatibility with pre-v1 raster receipts.
            "editable.chart.raster.v1",
            "editable.image.advanced-crop-mask.v1",
            "editable.image.rect-crop.v1",
            "editable.raster-island-integrity.v1",
            "editable.shape.advanced.v1",
            "editable.shape.solid-basic.v1",
            "editable.table.advanced.v1",
            "editable.table.basic.v1",
            "editable.text.advanced.v1",
            "editable.text.ja-zh.v1",
            "editable.text.ko-en.v1",
        }
    )
)

EDITABLE_MVP_CAPABILITY_IDS_V1: frozenset[str] = frozenset(
    {
        "editable.image.rect-crop.v1",
        "editable.shape.solid-basic.v1",
        "editable.table.basic.v1",
        "editable.text.ko-en.v1",
    }
)


def capability_version(capability_id: str) -> str:
    version = capability_id.rsplit(".", 1)[-1]
    if not version.startswith("v") or not version[1:].isdigit():
        raise ValueError("native capability ID is not versioned")
    return version


def build_native_capability_projection_v1(
    scene: DeckSceneIR | None,
    *,
    ready_receipts: Mapping[str, str] | None = None,
) -> NativeCapabilityProjectionV1:
    """Build one deterministic fail-closed projection for the current Scene."""

    receipt_by_capability = dict(ready_receipts or {})
    unknown_receipts = set(receipt_by_capability).difference(NATIVE_CAPABILITY_IDS_V1)
    if unknown_receipts:
        raise ValueError("native capability receipt uses an unknown capability ID")

    admissions: list[NativeCapabilityAdmissionV1] = []
    if scene is not None:
        for slide in scene.slides:
            plan = slide.hybrid_export
            if plan is None:
                continue
            for decision in plan.owner_decisions:
                admissions.append(
                    NativeCapabilityAdmissionV1(
                        slide_id=slide.slide_id,
                        owner_id=decision.owner_id,
                        capability_id=decision.capability_id,
                        version=capability_version(decision.capability_id),
                        admission=decision.decision,
                        owner_domain={
                            "nativeEditable": "native",
                            "rasterIsland": "raster",
                            "publicationBlocked": "blocked",
                        }[decision.decision],
                        reason_code=decision.reason_code,
                    )
                )
    admissions.sort(key=lambda item: (item.slide_id, item.owner_id))

    counts: dict[str, Counter[str]] = {
        capability_id: Counter() for capability_id in NATIVE_CAPABILITY_IDS_V1
    }
    for admission in admissions:
        if admission.capability_id not in counts:
            raise ValueError("Scene owner uses an unknown versioned capability")
        counts[admission.capability_id][admission.admission] += 1

    capabilities: list[NativeCapabilityReadinessV1] = []
    for capability_id in NATIVE_CAPABILITY_IDS_V1:
        capability_counts = counts[capability_id]
        native_count = capability_counts["nativeEditable"]
        raster_count = capability_counts["rasterIsland"]
        blocked_count = capability_counts["publicationBlocked"]
        receipt_digest = (
            receipt_by_capability.get(capability_id)
            if native_count and not raster_count and not blocked_count
            else None
        )
        if blocked_count:
            status, reason = "blocked", "capability_publication_blocked"
        elif raster_count:
            status, reason = "fidelityFallback", "capability_fidelity_fallback"
        elif native_count and receipt_digest is not None:
            status, reason = "ready", "capability_receipt_current"
        elif native_count:
            status, reason = "notVerified", "capability_receipt_missing"
        else:
            status, reason = "notEvaluated", "capability_not_present"
        capabilities.append(
            NativeCapabilityReadinessV1(
                capability_id=capability_id,
                version=capability_version(capability_id),
                status=status,
                ready=status == "ready",
                reason_code=reason,
                native_owner_count=native_count,
                raster_owner_count=raster_count,
                blocked_owner_count=blocked_count,
                receipt_digest=receipt_digest,
            )
        )

    return NativeCapabilityProjectionV1(
        scene_digest=scene.scene_digest if scene is not None else None,
        admissions=admissions,
        capabilities=capabilities,
    )


__all__ = [
    "EDITABLE_MVP_CAPABILITY_IDS_V1",
    "NATIVE_CAPABILITY_IDS_V1",
    "build_native_capability_projection_v1",
    "capability_version",
]
