"""Pure comparisons for source-preserving structural repair admission."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from services.authored_local_fit_policy import FONT_FLOOR_PT, GEOMETRY_TOLERANCE_PX
from services.source_content.source_runs import parse_source_binding_id
from utils.authored_dom_quality import normalize_browser_metrics
from utils.authored_dom_text_escape import find_text_escapes

SOURCE_REPAIR_FONT_FLOOR_PT = max(9.5, FONT_FLOOR_PT)
_DECORATION_FIELDS = (
    "fontFamily", "fontWeight", "color", "backgroundColor", "backgroundImage",
    "borderTopStyle", "borderRightStyle", "borderBottomStyle", "borderLeftStyle",
    "borderTopColor", "borderRightColor", "borderBottomColor", "borderLeftColor",
    "borderTopLeftRadius", "borderTopRightRadius", "borderBottomLeftRadius",
    "borderBottomRightRadius", "boxShadow", "textShadow", "textAlign",
    "scale", "zoom", "rotate",
)


def _field(value: Mapping[str, Any], snake: str, camel: str) -> Any:
    return value.get(snake, value.get(camel))

def _number(value: Any) -> float | None:
    try:
        number = float(str(value).removesuffix("px"))
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None

def _box(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, Mapping):
        return None
    coords = [_number(value.get(key)) for key in ("x", "y", "width", "height")]
    if any(item is None for item in coords):
        return None
    x, y, width, height = coords
    if width <= 0 or height <= 0:
        return None
    return x, y, x + width, y + height

def _measured_geometry(value: Any) -> tuple[float, float, float, float] | None:
    """Return measured x/y/width/height, retaining valid zero-area nodes."""
    if not isinstance(value, Mapping):
        return None
    coords = tuple(
        _number(value.get(key)) for key in ("x", "y", "width", "height")
    )
    if any(item is None for item in coords):
        return None
    x, y, width, height = coords
    if width < 0 or height < 0:
        return None
    return x, y, width, height

def _intersection(first, second):
    result = (max(first[0], second[0]), max(first[1], second[1]), min(first[2], second[2]), min(first[3], second[3]))
    return result if result[2] > result[0] and result[3] > result[1] else None

def _union_area(boxes) -> float:
    """Exact union of axis-aligned browser line rectangles (avoid double count)."""
    edges = sorted({edge for box in boxes for edge in (box[0], box[2])})
    area = 0.0
    for left, right in zip(edges, edges[1:]):
        intervals = sorted((box[1], box[3]) for box in boxes if box[0] < right and box[2] > left)
        bottom = -math.inf
        height = 0.0
        for top, end in intervals:
            height += max(0.0, end - max(top, bottom))
            bottom = max(bottom, end)
        area += (right - left) * height
    return area

def _visibility(metrics: Mapping[str, Any], failures: tuple[str, ...]) -> dict[str, Any]:
    """Keep separate per-block maxima; never trade one block against another."""
    normalized = normalize_browser_metrics(metrics)
    escapes = {item.qid: max(item.escape_px_right, item.escape_px_bottom) for item in find_text_escapes(normalized["elements"])}
    width, height = _number(metrics.get("width")), _number(metrics.get("height"))
    result: dict[str, Any] = {}
    for element in metrics.get("elements", ()):
        raw_id = _field(element, "source_block_id", "sourceBlockId")
        if not raw_id:
            continue
        block_id = parse_source_binding_id(str(raw_id))[0]
        box = _box(element.get("bbox"))
        clip_flags = [_field(element, snake, camel) for snake, camel in (("clipped", "clipped"), ("glyph_clipped", "glyphClipped"), ("line_box_clipped", "lineBoxClipped"))]
        clipped = any(value is True for value in clip_flags)
        clipping_fraction: float | None = 0.0 if not clipped and all(isinstance(value, bool) for value in clip_flags) else None
        bounds = _field(element, "source_clip_bounds", "sourceClipBounds")
        fragments = _field(element, "text_fragments", "textFragments")
        if bounds is not None and (
            clipped or not all(isinstance(value, bool) for value in clip_flags)
        ):
            clipping_fraction = None
        if isinstance(bounds, Mapping):
            clip = tuple(_number(bounds.get(key)) for key in ("left", "top", "right", "bottom"))
            raw_boxes = fragments if isinstance(fragments, (list, tuple)) else []
            if not raw_boxes and str(element.get("tag", "")).lower() == "img":
                raw_boxes = [element.get("bbox")]
            boxes = [_box(item) for item in raw_boxes]
            if all(value is not None for value in clip) and boxes and all(item is not None for item in boxes):
                total = _union_area(boxes)
                visible = _union_area([intersection for item in boxes if (intersection := _intersection(item, clip)) is not None])
                clipping_fraction = max(0.0, 1.0 - visible / total) if total else None
        observed = {
            "viewport_escape_px": max(0.0, -box[0], -box[1], box[2] - width, box[3] - height) if box and width is not None and height is not None else None,
            "card_escape_px": escapes.get(str(element.get("qid")), 0.0) if box else None,
            "clipping_fraction": clipping_fraction,
            "occluded_fraction": _number(_field(element, "occluded_fraction", "occludedFraction")),
            "continuous_occluded_fraction": _number(_field(element, "continuous_occluded_fraction", "continuousOccludedFraction")),
            "invisible": _field(element, "css_visible", "cssVisible") is not True,
            "clipped": clipped,
            "failed": block_id in failures,
        }
        current = result.get(block_id)
        if current is None:
            result[block_id] = observed
        else:
            for key, value in observed.items():
                current[key] = None if current[key] is None or value is None else max(current[key], value)
    return result

def _visibility_comparison(baseline: dict, candidate: dict) -> tuple[bool, bool]:
    if set(baseline) != set(candidate):
        return False, False
    improved = False
    for block_id, before in baseline.items():
        after = candidate[block_id]
        for key in ("failed", "clipped", "invisible"):
            if after[key] and not before[key]:
                return False, False
            improved = improved or (before[key] and not after[key])
        for key in ("viewport_escape_px", "card_escape_px", "clipping_fraction", "occluded_fraction", "continuous_occluded_fraction"):
            old, new = before[key], after[key]
            # A measured zero is a lower bound even if the previous clipped
            # amount was unknown. Positive/missing amounts cannot prove that.
            if old is None and new == 0 and key == "clipping_fraction" and before["clipped"]:
                continue
            if old is None or new is None:
                return False, False
            tolerance = GEOMETRY_TOLERANCE_PX if key.endswith("_px") else 0.0
            if new > old + tolerance:
                return False, False
            minimum = max(2 * tolerance, old * 0.05) if key.endswith("_px") else max(0.01, old * 0.05)
            improved = improved or (old > new and old - new >= minimum)
    return True, improved

def _composition_comparison(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> tuple[bool, bool]:
    """Match defect identity before comparing counts or known physical units."""
    def buckets(report):
        grouped = {}
        for issue in report.get("violations", ()):
            if not isinstance(issue, Mapping):
                issue = {"category": str(issue)}
            identity = (
                str(issue.get("category", "")), tuple(issue.get("qids") or ()),
                str(_field(issue, "cause_qid", "causeQid") or ""),
                str(_field(issue, "target_qid", "targetQid") or ""),
                str(_field(issue, "source_block_id", "sourceBlockId") or issue.get("sourceId") or ""),
            )
            grouped.setdefault(identity, []).append(issue)
        return grouped

    old_buckets, new_buckets = buckets(baseline), buckets(candidate)
    improved = False
    ranks = {"low": 1, "medium": 2, "high": 3}
    # These are the existing DOM gate's measured miss distances. Ratios with
    # changing denominators are retained in receipts, not used as scores.
    pixel_fields = {"excess_px_x", "excess_px_y", "overshoot_px_left", "overshoot_px_top", "overshoot_px_right", "overshoot_px_bottom"}

    def compare(before, after):
        if ranks.get(after.get("severity"), 3) > ranks.get(before.get("severity"), 3):
            return False, False
        old_mag, new_mag = before.get("magnitude") or {}, after.get("magnitude") or {}
        better = False
        for key in pixel_fields.intersection(old_mag.keys() | new_mag.keys()):
            old, new = _number(old_mag.get(key)), _number(new_mag.get(key))
            if old is None or new is None or new > old + GEOMETRY_TOLERANCE_PX:
                return False, False
            better = better or old - new >= max(2 * GEOMETRY_TOLERANCE_PX, old * 0.05)
        if "hidden_fraction" in old_mag or "hidden_fraction" in new_mag:
            old, new = _number(old_mag.get("hidden_fraction")), _number(new_mag.get("hidden_fraction"))
            if old is None or new is None or new > old:
                return False, False
            better = better or old - new >= max(0.01, old * 0.05)
        return True, better

    for identity, candidates in new_buckets.items():
        originals = old_buckets.get(identity, [])
        if len(candidates) > len(originals):
            return False, False
        # Duplicate identities are matched by feasibility, never array order.
        edges = [[index for index, before in enumerate(originals) if compare(before, after)[0]] for after in candidates]
        matched = {}

        def assign(index, seen):
            for old_index in edges[index]:
                if old_index in seen:
                    continue
                seen.add(old_index)
                if old_index not in matched or assign(matched[old_index], seen):
                    matched[old_index] = index
                    return True
            return False

        if any(not assign(index, set()) for index in range(len(candidates))):
            return False, False
        # A continuous improvement claim is unambiguous for a single identity.
        if len(originals) == len(candidates) == 1:
            improved = improved or compare(originals[0], candidates[0])[1]
    improved = improved or sum(map(len, new_buckets.values())) < sum(map(len, old_buckets.values()))
    return True, improved

def _style_report(baseline: Mapping[str, Any], candidate: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    baseline_nodes = {str(item.get("qid")): item for item in baseline.get("elements", ()) if item.get("qid")}
    candidate_nodes = {str(item.get("qid")): item for item in candidate.get("elements", ()) if item.get("qid")}
    failures: list[str] = []
    title_fonts: list[tuple[float, float]] = []
    body_fonts: list[tuple[float, float]] = []
    for qid, before in baseline_nodes.items():
        after = candidate_nodes.get(qid)
        if after is None:
            failures.append(f"repair_missing_measured_qid:{qid}")
            continue
        before_style = _field(before, "computed_style", "computedStyle") or {}
        after_style = _field(after, "computed_style", "computedStyle") or {}
        for key in _DECORATION_FIELDS:
            if key in before_style and before_style[key] != after_style.get(key):
                failures.append(f"repair_style_changed:{qid}:{key}")
        before_role = _field(before, "semantic_role", "semanticRole")
        if before_role != _field(after, "semantic_role", "semanticRole"):
            failures.append(f"repair_role_changed:{qid}")
        if _field(before, "occlusion_probe_version", "occlusionProbeVersion") != _field(after, "occlusion_probe_version", "occlusionProbeVersion"):
            failures.append(f"repair_measurement_version_changed:{qid}")
        old_font = _number(before.get("font_size", before_style.get("fontSize")))
        new_font = _number(after.get("font_size", after_style.get("fontSize")))
        if old_font is not None and new_font is None:
            failures.append(f"repair_font_unmeasured:{qid}")
        if new_font is not None and str(_field(after, "rendered_text", "renderedText") or "").strip():
            if new_font * 0.75 < SOURCE_REPAIR_FONT_FLOOR_PT:
                failures.append(f"repair_font_floor:{qid}")
            if old_font is not None:
                if "title" in str(before_role or "").lower():
                    title_fonts.append((old_font, new_font))
                elif str(before_role or "").lower() in {"body", "text", "bullet"}:
                    body_fonts.append((old_font, new_font))
        if qid in context.get("protected_qids", ()):
            old_box = _measured_geometry(before.get("bbox"))
            new_box = _measured_geometry(after.get("bbox"))
            if old_box is None or new_box is None or any(abs(old - new) > GEOMETRY_TOLERANCE_PX for old, new in zip(old_box, new_box)):
                failures.append(f"repair_protected_geometry_changed:{qid}")
    if title_fonts and body_fonts:
        if min(item[0] for item in title_fonts) >= max(item[0] for item in body_fonts) and min(item[1] for item in title_fonts) < max(item[1] for item in body_fonts):
            failures.append("repair_title_body_hierarchy_changed")
    return {"passed": not failures, "violations": failures}

def protected_scope_violations(
    baseline_html: str, candidate_html: str,
    baseline_metrics: Mapping[str, Any], candidate_metrics: Mapping[str, Any],
    protected_qids,
) -> tuple[str, ...]:
    """Reject direct protected edits and measured indirect reflow before history advances."""
    from services.authored_structural_repair import (
        _ImmutableDom,
        source_repair_html_violations,
    )

    reasons = list(source_repair_html_violations(baseline_html, candidate_html))
    original, candidate = _ImmutableDom(baseline_html), _ImmutableDom(candidate_html)
    for qid in protected_qids:
        if original.inline_styles.get(qid) != candidate.inline_styles.get(qid):
            reasons.append(f"repair_protected_style_changed:{qid}")
    style = _style_report(baseline_metrics, candidate_metrics, {"protected_qids": tuple(protected_qids)})
    reasons.extend(style["violations"])
    return tuple(dict.fromkeys(reasons))
