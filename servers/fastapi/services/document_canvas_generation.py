"""Physical geometry at generation/export boundaries; never infer A4 from pixels."""
from __future__ import annotations

import math


def export_size_points(canvas: object | None, physical_canvas: object | None = None) -> tuple[float, float]:
    if physical_canvas is not None:
        from services.document_canvas import canvas_physical_size

        width, height = canvas_physical_size(physical_canvas)
        logical = physical_canvas
        if canvas is not None and (
            getattr(canvas, "width_px", None), getattr(canvas, "height_px", None)
        ) != (getattr(logical, "width_px", None), getattr(logical, "height_px", None)):
            raise ValueError("document_canvas_geometry_mismatch")
        return width / 12700, height / 12700
    from services.authored_canvas import canvas_or_default

    width_px, height_px = canvas_or_default(canvas)
    return width_px * 0.75, height_px * 0.75


def generation_font_floor(instructions: str | None, physical_canvas: object | None = None) -> float:
    """Preserve ordinary physical floor and explicit px/pt intent across renderers."""
    from utils.authored_font_policy import MIN_AUTHORED_FONT_SIZE_PX, requested_font_floor_px

    scale = float(getattr(physical_canvas, "point_per_unit", 0.75))
    requested = requested_font_floor_px(instructions, point_per_unit=scale)
    return requested if requested is not None else MIN_AUTHORED_FONT_SIZE_PX * 0.75 / scale


def source_restyle_font_floor(instructions: str | None, physical_canvas: object | None = None) -> float:
    """Fit page-locked source content down to 7 CSS px on the output canvas."""
    from utils.authored_font_policy import requested_font_floor_px

    scale = float(getattr(physical_canvas, "point_per_unit", 0.75))
    requested = requested_font_floor_px(instructions, point_per_unit=scale)
    return max(7.0, requested) if requested is not None else 7.0


SVG_READABILITY_POLICY_VERSION = "svg-physical-readability/v2"
_LEGACY_SVG_READABILITY_POLICY_VERSION = "svg-physical-readability/v1"
_SVG_QUALITY_MINIMUM_FONT_PT = 9.5


def svg_readability_policy(canvas_metadata: object, *, source_restyle: bool = False,
                           instructions: str | None = None) -> dict:
    """Pin physical typography for fresh admission; historical snapshots omit it."""
    from services.document_canvas import canvas_physical_size, parse_canvas_metadata
    from utils.hashing import canonical_json_digest

    canvas = parse_canvas_metadata(canvas_metadata)
    width, height = canvas_physical_size(canvas)
    scale = width / canvas.width_px / 12700
    policy = {
        "version": SVG_READABILITY_POLICY_VERSION,
        "purpose": "source-preserving-page" if source_restyle else "presentation",
        "canvas": canvas.model_dump(mode="json", by_alias=True),
        "minimum_font_pt": max(9.0, generation_font_floor(instructions, canvas) * scale),
        "quality_target_font_pt": max(_SVG_QUALITY_MINIMUM_FONT_PT,
                                      generation_font_floor(instructions, canvas) * scale),
        # Bounded text line-box occupancy is a crowding signal, not a style score.
        "maximum_text_area_ratio": 0.65 if source_restyle else 0.5,
        "width_emu": width, "height_emu": height,
    }
    return {**policy, "digest": canonical_json_digest(policy)}


def validate_svg_readability_policy(value: object) -> dict | None:
    if value is None:
        return None
    from services.document_canvas import canvas_physical_size, parse_canvas_metadata
    from utils.hashing import canonical_json_digest

    keys = {"version", "purpose", "canvas", "minimum_font_pt", "maximum_text_area_ratio",
            "width_emu", "height_emu", "digest"}
    if not isinstance(value, dict):
        raise ValueError("svg_readability_policy_invalid")
    version = value.get("version")
    if version == SVG_READABILITY_POLICY_VERSION:
        keys.add("quality_target_font_pt")
    elif version != _LEGACY_SVG_READABILITY_POLICY_VERSION:
        raise ValueError("svg_readability_policy_invalid")
    if set(value) != keys or value["purpose"] not in {
        "presentation", "source-preserving-page",
    }:
        raise ValueError("svg_readability_policy_invalid")
    bounds = [("minimum_font_pt", 9, 1000), ("maximum_text_area_ratio", 0, 1)]
    if version == SVG_READABILITY_POLICY_VERSION:
        bounds.append(("quality_target_font_pt", _SVG_QUALITY_MINIMUM_FONT_PT, 1000))
    for key, lower, upper in bounds:
        number = value[key]
        if type(number) not in (int, float) or not math.isfinite(number) or not lower <= number <= upper:
            raise ValueError("svg_readability_policy_invalid")
    if (version == SVG_READABILITY_POLICY_VERSION
            and value["quality_target_font_pt"] < value["minimum_font_pt"]):
        raise ValueError("svg_readability_policy_invalid")
    canvas = parse_canvas_metadata(value["canvas"])
    if canvas_physical_size(canvas) != (value["width_emu"], value["height_emu"]):
        raise ValueError("svg_readability_canvas_mismatch")
    if value["digest"] != canonical_json_digest({key: item for key, item in value.items() if key != "digest"}):
        raise ValueError("svg_readability_policy_digest_mismatch")
    return value


def svg_readability_font_floor(policy: object) -> float:
    from services.document_canvas import parse_canvas_metadata

    value = validate_svg_readability_policy(policy)
    canvas = parse_canvas_metadata(value["canvas"])
    scale = value["width_emu"] / canvas.width_px / 12700
    return value["minimum_font_pt"] / scale


def svg_quality_font_floor(policy: object) -> float:
    from services.document_canvas import parse_canvas_metadata

    value = validate_svg_readability_policy(policy)
    canvas = parse_canvas_metadata(value["canvas"])
    scale = value["width_emu"] / canvas.width_px / 12700
    return value.get("quality_target_font_pt", value["minimum_font_pt"]) / scale


def svg_readability_rules(policy: object) -> str:
    from services.document_canvas import parse_canvas_metadata

    value = validate_svg_readability_policy(policy)
    if value is None:
        return ""
    canvas = parse_canvas_metadata(value["canvas"])
    return (
        f"\nPhysical page: {value['width_emu'] / 36000:g} by {value['height_emu'] / 36000:g} mm; "
        f"compose directly at {canvas.width_px}x{canvas.height_px} SVG units with that exact viewBox. "
        f"Purpose: {value['purpose']}. Every content label must print at least "
        f"{value.get('quality_target_font_pt', value['minimum_font_pt']):g} pt "
        f"({svg_quality_font_floor(value):g} SVG units). Allocate space for complete glyphs and line boxes. "
        "Preserve every word, number, unit, data cell, relationship and requested page count. "
        "Repair the smallest failing region first by enlarging its available space, moving associated objects "
        "together or wrapping at natural boundaries; never shrink text or delete content to fit. "
        "If local repair cannot fit readable content, recompose only this page freely on the same canvas; "
        "if that still cannot fit, report a readability failure. Do not fit a wide page inside this canvas.\n"
    )


def measure_svg_readability(policy: object, *, width: int, height: int,
                            text: list[dict], measurement_complete: bool) -> tuple[dict, list[dict]]:
    """Consume the existing browser measurement, in target canvas units, once."""
    from services.document_canvas import parse_canvas_metadata

    value = validate_svg_readability_policy(policy)
    canvas = parse_canvas_metadata(value["canvas"])
    if (width, height) != (canvas.width_px, canvas.height_px):
        raise ValueError("svg_readability_canvas_mismatch")
    scale = value["width_emu"] / width / 12700
    failures, fonts, rectangles = [], [], []
    for item in text:
        font = item.get("font_size")
        if type(font) not in (int, float) or not math.isfinite(font) or font <= 0:
            failures.append({"kind": "physical_font_unmeasured", "qid": item.get("qid")})
        else:
            fonts.append(font * scale)
            target = value.get("quality_target_font_pt", value["minimum_font_pt"])
            if font * scale + 1e-6 < target:
                failures.append({"kind": "physical_font_too_small", "qid": item.get("qid"),
                                 "actual_pt": font * scale, "minimum_pt": target})
        for rect in item.get("text_fragments") or [item.get("bbox", {})]:
            parts = [rect.get("x", rect.get("left")), rect.get("y", rect.get("top")),
                     rect.get("width"), rect.get("height")]
            if any(type(part) not in (int, float) or not math.isfinite(part) for part in parts):
                failures.append({"kind": "text_density_unmeasured", "qid": item.get("qid")})
                continue
            x, y, w, h = parts
            if w < 0 or h < 0:
                failures.append({"kind": "text_density_unmeasured", "qid": item.get("qid")})
                continue
            left, top, right, bottom = max(0, x), max(0, y), min(width, x + w), min(height, y + h)
            if right > left and bottom > top:
                rectangles.append((left, top, right, bottom))
    # Exact rectangle union avoids double counting parent/tspan line boxes.
    edges = sorted({edge for rect in rectangles for edge in (rect[0], rect[2])})
    area = 0.0
    for left, right in zip(edges, edges[1:]):
        spans = sorted((top, bottom) for x, top, end, bottom in rectangles
                       if x < right and end > left and bottom > top)
        end, covered = 0.0, 0.0
        for top, bottom in spans:
            covered += max(0, bottom - max(end, top))
            end = max(end, bottom)
        area += (right - left) * covered
    safe = canvas.safe_area
    ratio = area / (safe.width * safe.height)
    if ratio > value["maximum_text_area_ratio"]:
        failures.append({"kind": "dense_content", "actual_ratio": ratio,
                         "maximum_ratio": value["maximum_text_area_ratio"]})
    if not measurement_complete or not text:
        failures.append({"kind": "physical_readability_unmeasured"})
    quality_target = ({"quality_target_font_pt": value["quality_target_font_pt"]}
                      if "quality_target_font_pt" in value else {})
    return ({"policy": value, "policy_digest": value["digest"],
             "measurement_basis": "browser-target-canvas-owned-text",
             "point_per_unit": scale, "minimum_font_pt": min(fonts) if fonts else None,
             **quality_target,
             "text_area_ratio": ratio, "measurement_complete": measurement_complete,
             "passed": not failures}, failures)


def minimal_svg_readability_repair(svg: str, quality: dict) -> dict:
    """One free, conservative candidate. The normal renderer must remeasure it.

    Only isolated single-line labels can be enlarged/translated locally. A
    dense page, clipped glyphs, overlap, related geometry or native data needs
    same-page free recomposition within the existing provider authorization.
    """
    import xml.etree.ElementTree as ET
    from services.svg_contract import source_digest
    from services.document_canvas import parse_canvas_metadata

    boundary = {"status": "page_recomposition_required", "provider_call_authorized": False,
                "preserve_content_numbers_relations_page_count": True}
    physical = quality.get("physical_readability") or {}
    policy = validate_svg_readability_policy(physical.get("policy"))
    failures = quality.get("failures") or []
    if (policy is None or quality.get("source_digest") != source_digest(svg)
            or quality.get("measurement_complete") is not True or not failures):
        return {**boundary, "reason": "measurement_or_binding_unavailable"}
    if any(item.get("kind") not in {"small_body_text", "physical_font_too_small", "text_off_canvas"}
           for item in failures):
        return {**boundary, "reason": "page_geometry_requires_recomposition"}
    root = ET.fromstring(svg)
    nodes = list(root.iter())
    if any(node.tag.rsplit("}", 1)[-1] in {"line", "polyline", "polygon", "path", "image"}
           or node.get("transform") for node in nodes):
        return {**boundary, "reason": "associated_geometry_requires_recomposition"}
    metrics = (quality.get("browser_metrics") or {}).get("elements") or []
    by_qid = {item.get("qid"): item for item in metrics}
    qids = sorted({item.get("qid") for item in failures if item.get("qid")})
    canvas = parse_canvas_metadata(policy["canvas"])
    floor = svg_quality_font_floor(policy)
    planned, changes = {}, []
    for qid in qids:
        if not qid.startswith("svg-") or not qid[4:].isdigit() or int(qid[4:]) >= len(nodes):
            return {**boundary, "reason": "element_identity_unavailable"}
        node, measured = nodes[int(qid[4:])], by_qid.get(qid, {})
        if node.tag.rsplit("}", 1)[-1] != "text" or len(node) or not (node.text or "").strip():
            return {**boundary, "reason": "multiline_or_non_text_requires_recomposition"}
        box = measured.get("bbox") or {}
        try:
            x, y, font = float(node.get("x")), float(node.get("y")), float(node.get("font-size"))
            bx, by = float(box.get("x", box.get("left"))), float(box.get("y", box.get("top")))
            bw, bh = float(box["width"]), float(box["height"])
            values = (x, y, font, bx, by, bw, bh)
            if not all(math.isfinite(value) for value in values) or min(font, bw, bh) <= 0:
                raise ValueError
        except (TypeError, KeyError, ValueError):
            return {**boundary, "reason": "label_geometry_unavailable"}
        target_font = max(font, math.ceil(floor * 1000) / 1000)
        factor = target_font / font
        left, top, wide, tall = x + (bx - x) * factor, y + (by - y) * factor, bw * factor, bh * factor
        if wide > canvas.width_px or tall > canvas.height_px:
            return {**boundary, "reason": "physical_space_insufficient"}
        dx = min(max(left, 0), canvas.width_px - wide) - left
        dy = min(max(top, 0), canvas.height_px - tall) - top
        planned[qid] = (left + dx, top + dy, wide, tall)
        changes.append({"qid": qid, "font_px_before": font, "font_px_after": target_font,
                        "translate_x": dx, "translate_y": dy})
    # Candidate geometry is deliberately conservative; actual glyphs and PNG
    # are verified by the established renderer before this SVG is published.
    for qid, (x, y, w, h) in planned.items():
        for item in metrics:
            other = item.get("qid")
            if other == qid or not (item.get("has_owned_text") or item.get("tag") == "image"):
                continue
            box = item.get("bbox") or {}
            if other in planned:
                ox, oy, ow, oh = planned[other]
            else:
                try:
                    ox, oy, ow, oh = (float(box[key]) for key in ("x", "y", "width", "height"))
                except (TypeError, KeyError, ValueError):
                    return {**boundary, "reason": "neighbor_geometry_unavailable"}
            if min(x + w, ox + ow) - max(x, ox) > 0.5 and min(y + h, oy + oh) - max(y, oy) > 0.5:
                return {**boundary, "reason": "available_region_insufficient"}
    for change in changes:
        node = nodes[int(change["qid"][4:])]
        node.set("font-size", f"{change['font_px_after']:.6f}".rstrip("0").rstrip("."))
        for key, delta in (("x", change["translate_x"]), ("y", change["translate_y"])):
            if delta:
                node.set(key, f"{float(node.get(key)) + delta:.6f}".rstrip("0").rstrip("."))
    candidate = ET.tostring(root, encoding="unicode")
    return {"status": "candidate_requires_remeasurement", "svg": candidate, "changes": changes,
            "source_digest_before": source_digest(svg), "source_digest_after": source_digest(candidate),
            "provider_calls": 0, "preserve_content_numbers_relations_page_count": True}


def validate_svg_readability_publication(policy: object, quality: object) -> None:
    value = validate_svg_readability_policy(policy)
    if value is None:
        return
    measured = quality.get("physical_readability") if isinstance(quality, dict) else None
    if (not isinstance(measured, dict) or measured.get("policy_digest") != value["digest"]
            or measured.get("policy") != value or measured.get("measurement_complete") is not True
            or measured.get("passed") is not True or quality.get("passed") is not True):
        raise ValueError("svg_readability_publication_failed")
    minimum = measured.get("minimum_font_pt")
    target = value.get("quality_target_font_pt", value["minimum_font_pt"])
    if type(minimum) not in (int, float) or not math.isfinite(minimum) or minimum + 1e-6 < target:
        raise ValueError("svg_readability_publication_failed")
