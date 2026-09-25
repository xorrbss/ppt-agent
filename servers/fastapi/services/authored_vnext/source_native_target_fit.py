"""Target-canvas readability evaluation for source-native frame candidates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

from .source_render_geometry import BODY_FONT_DEFAULT_PX, semantic_text_metrics, slot_declared_font_px

SOURCE_ROLE_FONT_EXCEPTIONS = frozenset({
    "title", "heading", "section-heading", "caption", "footnote", "supporting",
    "eyebrow", "kpi-label", "compact-footer", "footer", "decoration", "furniture",
})


def target_typography_slots(slots, *, contain_scale, minimum_effective_font_px, policy_id):
    """Set ordinary body typography in target physical units before allocation.

    Supporting/title styles and dense-source exception retain their own policy.
    The same slots feed capacity estimation, plan identity and actual rendering.
    """
    if policy_id != "source-native/min-body-12-physical-pt":
        return slots
    # The CSS emitter serializes hundredths of a source px. Round upward at
    # that boundary so it cannot turn an exact physical floor into 11.998pt.
    minimum = math.ceil(minimum_effective_font_px / contain_scale * 100) / 100
    result = []
    for slot in slots:
        role = str(slot.get("sourceFlowRole") or slot.get("semanticRole") or "body").lower()
        if role == "body":
            style = dict(slot.get("textStyle") or {})
            style["fontSize"] = max(slot_declared_font_px(slot) or BODY_FONT_DEFAULT_PX, minimum)
            slot = {**slot, "textStyle": style}
        result.append(slot)
    return result


def target_typography_fit(
    *,
    slots: Sequence[Mapping[str, Any]],
    assigned: Mapping[str, Sequence[object]],
    renderer_scale: float,
    contain_scale: float,
    minimum_effective_font_px: float,
    policy_id: str,
) -> dict[str, Any]:
    source_fonts: list[float] = []
    for slot in slots:
        role = str(
            slot.get("sourceFlowRole") or slot.get("semanticRole") or "body"
        ).strip().lower()
        declared = slot_declared_font_px(slot)
        for value in assigned[str(slot["slotId"])]:
            text = str(getattr(value, "text", "") or "")
            if getattr(value, "kind", None) == "image" or not text.strip():
                continue
            semantic_role = str(getattr(value, "semantic_role", "") or role).lower()
            if policy_id == "source-native/min-body-12-physical-pt" and semantic_role in SOURCE_ROLE_FONT_EXCEPTIONS:
                continue
            source_fonts.append(
                semantic_text_metrics(
                    semantic_role=str(getattr(value, "semantic_role", "") or ""),
                    group_kind=str(
                        getattr(value, "semantic_group_kind", "") or ""
                    ),
                    fallback_role=role,
                    declared_font_px=declared,
                    adaptive_scale=renderer_scale,
                ).font_px
            )
    minimum_source = min(source_fonts) if source_fonts else None
    effective = (
        minimum_source * contain_scale if minimum_source is not None else None
    )
    passed = effective is None or effective >= minimum_effective_font_px
    return {
        "targetTypographyFit": passed,
        "targetFontPolicyId": policy_id,
        "targetContainScale": round(contain_scale, 12),
        "minimumSourceFontPx": (
            round(minimum_source, 6) if minimum_source is not None else None
        ),
        "minimumEffectiveFontPx": (
            round(effective, 6) if effective is not None else None
        ),
        "requiredEffectiveFontPx": round(minimum_effective_font_px, 6),
        "targetTypographyRank": [
            round(-effective, 6) if effective is not None else 0.0
        ],
    }


__all__ = ["target_typography_fit"]
