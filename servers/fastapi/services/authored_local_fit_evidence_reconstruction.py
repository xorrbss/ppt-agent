"""Element-level comparison between two authored local-fit captures.

Extracted from ``authored_local_fit_evidence``: these helpers read the
qid-indexed element metrics of a browser capture, fail closed when visible
raster/external image text has no evidence, and compare two captures'
element metrics (the fitted render and its independent verification render)
to decide whether they agree closely enough to accept.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.authored_local_fit_evidence_types import AuthoredLocalFitRejected
from services.authored_local_fit_policy import GEOMETRY_TOLERANCE_PX


def _elements(metrics: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = metrics.get("elements")
    if not isinstance(raw, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or "").strip()
        if qid:
            result[qid] = item
    return result


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_image_text_scope(metrics: Mapping[str, Any]) -> None:
    """Fail closed for visible raster/external image text without evidence."""

    for item in _elements(metrics).values():
        evidence = item.get("image_text_evidence")
        if isinstance(evidence, Mapping) and evidence.get("status") in {
            "no_text",
            "font_floor_verified",
        }:
            continue
        style = item.get("computed_style")
        background = (
            str(style.get("backgroundImage") or "")
            if isinstance(style, Mapping)
            else ""
        )
        visible = item.get("css_visible") is not False and (
            _number(item.get("viewport_intersection_ratio")) or 0
        ) > 0
        if visible and (
            str(item.get("tag") or "").lower() == "img"
            or ("url(" in background.lower() and background.lower() != "none")
        ):
            raise AuthoredLocalFitRejected("unverifiable_font_size")


def _compare_reconstruction(
    fitted_metrics: Mapping[str, Any], verify_metrics: Mapping[str, Any]
) -> tuple[float, tuple[str, ...]]:
    first, second = _elements(fitted_metrics), _elements(verify_metrics)
    if not first or set(first) != set(second):
        return 0.0, ("qid_set",)
    mismatches: list[str] = []
    max_delta = 0.0
    for qid in sorted(first):
        left, right = first[qid], second[qid]
        for field in ("owned_text", "rendered_text", "css_visible"):
            if left.get(field) != right.get(field):
                mismatches.append(f"{qid}:{field}")
        if left.get("computed_style") != right.get("computed_style"):
            mismatches.append(f"{qid}:computed_style")
        for field in ("effective_opacity", "viewport_intersection_ratio"):
            a, b = _number(left.get(field)), _number(right.get(field))
            if a is None or b is None or abs(a - b) > 1e-6:
                mismatches.append(f"{qid}:{field}")
        if left.get("owned_text") != right.get("owned_text"):
            mismatches.append(f"{qid}:text")
        left_box = left.get("bbox")
        right_box = right.get("bbox")
        if not isinstance(left_box, Mapping) or not isinstance(right_box, Mapping):
            mismatches.append(f"{qid}:bbox")
            continue
        for field in ("x", "y", "width", "height"):
            a, b = _number(left_box.get(field)), _number(right_box.get(field))
            if a is None or b is None:
                mismatches.append(f"{qid}:{field}")
                continue
            delta = abs(a - b)
            max_delta = max(max_delta, delta)
            if delta > GEOMETRY_TOLERANCE_PX:
                mismatches.append(f"{qid}:{field}")
        a, b = _number(left.get("font_size")), _number(right.get("font_size"))
        # Font floors have no geometry epsilon.  Browser readback itself is
        # compared at a small numeric precision to avoid JSON float noise.
        if a is None or b is None or abs(a - b) > 1e-6:
            mismatches.append(f"{qid}:font_size")
    return max_delta, tuple(dict.fromkeys(mismatches))


__all__ = [
    "_elements",
    "_number",
    "_validate_image_text_scope",
    "_compare_reconstruction",
]
