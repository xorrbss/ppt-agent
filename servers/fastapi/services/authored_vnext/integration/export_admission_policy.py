"""Export admission policy and service error for authored vNext."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from services.authored_vnext.capability_registry import EDITABLE_HYBRID_PROFILE_ID


class ExportServiceError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{6})$")
_BACKGROUND_FURNITURE_ID = "company-standard:background"
_HYBRID_FALLBACK_POLICIES = frozenset({
    "reject",
    "per-slide-raster",
    "whole-deck-fidelity",
})


def _require_explicit_hybrid_fallback_policy(
    scene: Mapping[str, Any], request_payload: Mapping[str, Any]
) -> None:
    """Reject an implicit or unsupported Hybrid fallback before job creation.

    Raster islands are intentional only when the caller selected the per-slide
    policy. This OOXML worker cannot produce a whole-deck Fidelity replacement,
    so accepting that policy here would be a silent mode change.
    """

    if scene.get("hybridProfileId") != EDITABLE_HYBRID_PROFILE_ID:
        return
    policy = request_payload.get("fallbackPolicy")
    if policy not in _HYBRID_FALLBACK_POLICIES:
        raise ExportServiceError("authored_vnext_hybrid_fallback_policy_required")
    if policy == "whole-deck-fidelity":
        raise ExportServiceError("authored_vnext_hybrid_fallback_policy_unsupported")
    if policy != "reject":
        return
    slides = scene.get("slides")
    if not isinstance(slides, list):
        raise ExportServiceError("authored_vnext_hybrid_export_plan_invalid")
    for slide in slides:
        if not isinstance(slide, Mapping):
            raise ExportServiceError("authored_vnext_hybrid_export_plan_invalid")
        plan = slide.get("hybridExport")
        if not isinstance(plan, Mapping):
            raise ExportServiceError("authored_vnext_hybrid_export_plan_invalid")
        decisions = plan.get("ownerDecisions")
        islands = plan.get("rasterIslands")
        if (
            not isinstance(decisions, list)
            or not isinstance(islands, list)
            or islands
            or any(
                not isinstance(decision, Mapping)
                or decision.get("decision") != "nativeEditable"
                for decision in decisions
            )
        ):
            raise ExportServiceError("authored_vnext_hybrid_fallback_rejected")
