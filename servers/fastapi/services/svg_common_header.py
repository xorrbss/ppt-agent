"""Frozen SVG-only header geometry and separate, measured page title bindings.

This adapter deliberately does not change AuthoredHeaderContract v2. It never
shortens titles, shrinks individual pages, or injects defaults into old jobs.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from functools import lru_cache

from models.authored_header_contract import HeaderTitleSpec
from models.authored_header_metrics import LABEL_LINE_RATIO, round_px
from services.svg_master_furniture import master_furniture
from services.svg_text_advance import text_advance
from services.svg_trusted_fonts import bundled_svg_font_family
from utils.hashing import canonical_json_digest
from utils.svg_template_fonts import FONT_FAMILY, font_material, measure_font

VERSION = "svg-common-header/v1"
PAGE_VERSION = "svg-common-header-page/v1"
MEASUREMENT_VERSION = "svg-common-header-glyph/v1"
GROUP_ID = "svg-system-common-header"
TITLE_ID = GROUP_ID + "-title"
SVG_NS = "http://www.w3.org/2000/svg"
_MISSING = object()


def _digest(value: object) -> str:
    return canonical_json_digest(value)


def header_snapshot_base(snapshot: dict | None) -> dict | None:
    """Remove only the new header extension; absent legacy snapshots are exact."""
    if not isinstance(snapshot, dict) or ("common_header" not in snapshot
            and (snapshot.get("common_header_selection") or {}).get("version") != "svg-common-header-fallback/v1"):
        return snapshot
    base = {key: value for key, value in snapshot.items() if key not in {"common_header", "common_header_selection"}}
    base["digest"] = canonical_json_digest({key: value for key, value in base.items() if key != "digest"},
                                           allow_nan=True)
    return base


def validate_common_header_outline(snapshot: dict | None, outline) -> None:
    """Prove every frozen title binding against its own outline page."""
    contract = (snapshot or {}).get("common_header")
    if contract is None:
        slides = outline if isinstance(outline, (list, tuple)) else _value(outline, "slides", [])
        if any(_value(slide, "common_header") is not None for slide in (slides or [])):
            raise ValueError("svg_common_header_digest_mismatch")
        return
    slides = _slides(outline)
    for index, slide in enumerate(slides):
        validate_common_header_binding(contract, _value(slide, "common_header"), index=index, count=len(slides),
            content=_value(slide, "content", ""), title=_value(slide, "title"), page_id=_value(slide, "page_id"))


def common_header_digest(contract: dict | None) -> str | None:
    """Hash the complete frozen style, excluding only its own digest."""
    return None if contract is None else _digest({k: v for k, v in contract.items() if k != "digest"})


def common_header_input_digest(contract: dict | None, page_binding: dict | None) -> str | None:
    """The same page identity is usable by initial generation and recovery."""
    validate_common_header_binding(contract, page_binding)
    if contract is None:
        return None
    return _digest({"header_digest": contract["digest"], "page_digest": page_binding["digest"]})


def common_header_source_digest(content: str) -> str:
    """Canonical source-content identity shared by pinning and worker recovery."""
    if not isinstance(content, str):
        raise ValueError("svg_common_header_source_invalid")
    return _digest(content)


def _value(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _slides(outline) -> list:
    slides = outline if isinstance(outline, (list, tuple)) else _value(outline, "slides")
    if not isinstance(slides, (list, tuple)) or not slides:
        raise ValueError("svg_common_header_titles_missing")
    return list(slides)


def _canvas(canvas) -> dict:
    if hasattr(canvas, "model_dump"):
        canvas = canvas.model_dump(mode="json", by_alias=True)
    if not isinstance(canvas, dict):
        raise ValueError("svg_common_header_canvas_invalid")
    if "logicalSize" in canvas or "logical_size" in canvas:
        # DocumentCanvasMetadata carries its logical and physical sizes apart.
        logical = canvas.get("logicalSize", canvas.get("logical_size"))
        if not isinstance(logical, dict):
            raise ValueError("svg_common_header_canvas_invalid")
        width, height = logical.get("width"), logical.get("height")
    elif "widthPx" in canvas or "width_px" in canvas:
        # AuthoredCanvasMetadata and CanvasContract use this established pair.
        width = canvas.get("widthPx", canvas.get("width_px"))
        height = canvas.get("heightPx", canvas.get("height_px"))
    else:
        width, height = canvas.get("width"), canvas.get("height")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
               for v in (width, height)):
        raise ValueError("svg_common_header_canvas_invalid")
    result = {"width": width, "height": height}
    physical = canvas.get("physicalSize", canvas.get("physical_size"))
    if physical:
        result["physical_size"] = physical
    return result


def _number(options, key, default, *, minimum=0):
    value = options.get(key, default)
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < minimum):
        raise ValueError("svg_common_header_geometry_invalid")
    return value


def _color(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError("svg_common_header_color_invalid")
    return value


def _contrast(first, second):
    def luminance(color):
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
        return sum(value * factor for value, factor in zip(linear, (.2126, .7152, .0722), strict=False))
    low, high = sorted((luminance(first), luminance(second)))
    return (high + .05) / (low + .05)


def _font(family, weight, size):
    if family == "Pretendard":
        from services.svg_renderer import _trusted_font_materials

        if weight not in (400, 700):
            raise ValueError("svg_common_header_font_identity_invalid")
        digest = next(d for w, d, _ in _trusted_font_materials() if w == weight)
    else:
        digest, _ = font_material(family, weight)
    return {"family": family, "weight": weight, "size_px": size, "digest": digest}


def _advance(text, font):
    width = text_advance(text, font["size_px"], str(font["weight"]), font["family"])
    if width is None:
        raise ValueError("svg_common_header_font_glyph_unavailable")
    return width


def _wrap(title, font, width, max_lines=2):
    """Prefer whitespace boundaries; retain every character and explicit break."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("svg_common_header_title_missing")
    lines = []
    for paragraph in title.split("\n"):
        if not paragraph.strip():
            raise ValueError("svg_common_header_title_invalid")
        remaining = paragraph
        while remaining:
            if _advance(remaining, font) <= width:
                lines.append(remaining)
                break
            fit = 0
            for end in range(1, len(remaining) + 1):
                if _advance(remaining[:end], font) > width:
                    break
                fit = end
            if not fit:
                raise ValueError("svg_common_header_title_overflow")
            # CJK can wrap between characters. Other words use the last space
            # when available, with the separating whitespace kept in the line.
            spaces = [match.end() for match in re.finditer(r"\s+", remaining[:fit])]
            end = spaces[-1] if spaces and remaining[:spaces[-1]].strip() else fit
            lines.append(remaining[:end])
            remaining = remaining[end:]
            if len(lines) >= max_lines:
                raise ValueError("svg_common_header_title_overflow")
        if len(lines) > max_lines:
            raise ValueError("svg_common_header_title_overflow")
    return lines


@lru_cache(maxsize=4096)
def _measured_lines(lines, family, weight, size, box_values, baseline, line_height, alignment):
    font = {"family": family, "weight": weight, "size_px": size}
    box = dict(zip(("x", "y", "width", "height"), box_values, strict=False))
    face = measure_font(family, weight, 1000)
    scale = size / 1000
    widths, boxes = [], []
    for index, text in enumerate(lines):
        width = _advance(text, font)
        x = box["x"] + ({"left": 0, "center": (box["width"] - width) / 2,
                        "right": box["width"] - width}[alignment])
        y = box["y"] + baseline + index * line_height
        left, top, right, bottom = face.getbbox(text, anchor="ls")
        glyph = {"x": x + left * scale, "y": y + top * scale,
                 "width": (right - left) * scale, "height": (bottom - top) * scale}
        if (glyph["x"] < box["x"] - .5 or glyph["y"] < box["y"] - .5
                or glyph["x"] + glyph["width"] > box["x"] + box["width"] + .5
                or glyph["y"] + glyph["height"] > box["y"] + box["height"] + .5):
            raise ValueError("svg_common_header_title_overflow")
        widths.append(width)
        boxes.append(tuple(glyph[key] for key in ("x", "y", "width", "height")))
    return tuple(widths), tuple(boxes)


def _line_evidence(lines, font, box, baseline, line_height, alignment):
    widths, boxes = _measured_lines(tuple(lines), font["family"], font["weight"], font["size_px"],
        tuple(box[key] for key in ("x", "y", "width", "height")), baseline, line_height, alignment)
    return list(widths), [dict(zip(("x", "y", "width", "height"), values, strict=False)) for values in boxes]


def prepare_common_header(snapshot: dict, outline, canvas, *, mode="uniform", cover_exception=False):
    """Return style and title bindings without mutating snapshot or outline.

    Callers atomically store the first value in snapshot.common_header and each
    second value in slide.common_header before dispatching body authoring.
    A frozen contract is validated and reused, never remeasured into new defaults.
    """
    if mode in ("free", "page_free"):
        if snapshot.get("common_header"):
            raise ValueError("svg_common_header_digest_mismatch")
        return None, []
    if mode != "uniform":
        raise ValueError("svg_common_header_mode_invalid")
    slides, geometry = _slides(outline), _canvas(canvas)
    frozen = snapshot.get("common_header")
    if isinstance(frozen, dict) and frozen.get("version"):
        if frozen.get("canvas") != geometry or frozen.get("cover_exception") != cover_exception:
            raise ValueError("svg_common_header_digest_mismatch")
        bindings = [_value(slide, "common_header") for slide in slides]
        for index, binding in enumerate(bindings):
            validate_common_header_binding(frozen, binding, index=index, count=len(slides),
                content=_value(slides[index], "content", ""), title=_value(slides[index], "title"),
                page_id=_value(slides[index], "page_id"))
        return frozen, bindings
    options = frozen or {}
    if not isinstance(options, dict):
        raise ValueError("svg_common_header_contract_invalid")
    tokens = snapshot.get("tokens", {})
    width, height = geometry["width"], geometry["height"]
    spec = HeaderTitleSpec()
    family = options.get("font_family", bundled_svg_font_family(tokens.get("heading_font")))
    size = _number(options, "font_size_px", spec.font_size_px, minimum=20)
    font = _font(family, options.get("font_weight", spec.weight if family == FONT_FAMILY else 700), size)
    line_height = _number(options, "line_height_px", round_px(size * spec.line_height), minimum=size)
    face = measure_font(font["family"], font["weight"], 1000)
    ascent, descent = face.getmetrics()
    glyph_height = (ascent + descent) * size / 1000
    line_height = max(line_height, glyph_height)
    baseline = ascent * size / 1000
    margin = _number(options, "margin_x_px", 64, minimum=0)
    x, top = margin, _number(options, "top_px", 32)
    right = width - margin
    reservations = []
    # Reserve a common lane for brand elements, irrespective of cover visibility.
    for item in master_furniture(snapshot).get("elements", []):
        if item.get("visibility") == "hidden" or item.get("kind") == "pageNumber":
            continue
        ix, iy, iw, ih = (float(item.get(k, 0)) for k in ("x", "y", "width", "height"))
        reservations.append({"x": ix, "y": iy, "width": iw, "height": ih})
        if iy + ih > top and iy < top + 2 * line_height:
            if ix + iw / 2 >= width / 2:
                right = min(right, ix - 12)
            else:
                x = max(x, ix + iw + 12)
    available = right - x
    title_width = min(available, _number(options, "title_width_px", available, minimum=1))
    if title_width <= 0:
        raise ValueError("svg_common_header_brand_overlap")
    alignment = options.get("alignment", "left")
    if alignment not in ("left", "center", "right"):
        raise ValueError("svg_common_header_alignment_invalid")
    titles = [_value(slide, "title") for slide in slides]
    applied = [not (cover_exception and index == 0) for index in range(len(slides))]
    page_lines = [_wrap(title, font, title_width) if applies else []
                  for title, applies in zip(titles, applied, strict=False)]
    slots = max((len(lines) for lines in page_lines), default=1) or 1
    optional = {}
    y = top
    for name in ("eyebrow",):
        setting = options.get(name, {})
        if not isinstance(setting, dict):
            raise ValueError("svg_common_header_contract_invalid")
        enabled = setting.get("enabled", False)
        optional[name] = {"enabled": bool(enabled)}
        if enabled:
            label_size = _number(setting, "font_size_px", 13, minimum=12)
            label_font = _font(family, 700, label_size)
            row = label_size * LABEL_LINE_RATIO
            optional[name].update(font=label_font, color=_color(setting.get("color", "#64748B")),
                                  box={"x": x, "y": y, "width": title_width, "height": row})
            y += row + _number(setting, "gap_px", 12)
    title_box = {"x": x, "y": y, "width": title_width, "height": slots * line_height}
    y += title_box["height"]
    subline = options.get("subline", {})
    if not isinstance(subline, dict):
        raise ValueError("svg_common_header_contract_invalid")
    optional["subline"] = {"enabled": bool(subline.get("enabled", False))}
    if optional["subline"]["enabled"]:
        label_size = _number(subline, "font_size_px", 15, minimum=12)
        row = label_size * LABEL_LINE_RATIO
        y += _number(subline, "gap_px", 8)
        optional["subline"].update(font=_font(family, 400, label_size),
                                   color=_color(subline.get("color", "#64748B")),
                                   box={"x": x, "y": y, "width": title_width, "height": row})
        y += row
    y += _number(options, "title_gap_px", 16)
    divider = options.get("divider", {})
    if not isinstance(divider, dict):
        raise ValueError("svg_common_header_contract_invalid")
    optional["divider"] = {"enabled": bool(divider.get("enabled", False))}
    if optional["divider"]["enabled"]:
        thickness = _number(divider, "thickness_px", 1, minimum=.5)
        optional["divider"].update(x1=x, x2=right, y=y, thickness_px=thickness,
                                   color=_color(divider.get("color", "#E2E8F0")))
        y += thickness
    body_top = y + _number(options, "content_gap_px", 20)
    body_bottom = height - _number(options, "bottom_px", 48)
    for item in reservations:
        if item["y"] >= body_top:
            body_bottom = min(body_bottom, item["y"] - 12)
    minimum_body = _number(options, "minimum_body_height_px", 120, minimum=1)
    if body_bottom - body_top < minimum_body:
        raise ValueError("svg_common_header_body_space_unresolved")
    background = _color(options.get("background", tokens.get("background", "#FFFFFF")))
    color = _color(options.get("color", tokens.get("foreground", spec.color)))
    if _contrast(color, background) < 3:
        if "color" in options:
            raise ValueError("svg_common_header_contrast_invalid")
        color = max(("#0F172A", "#FFFFFF"), key=lambda candidate: _contrast(candidate, background))
    contract = {"version": VERSION, "origin": "generated-common-header", "canvas": geometry,
                "measurement_version": MEASUREMENT_VERSION, "font": font,
                "color": color, "background": background,
                "alignment": alignment, "title_box": title_box, "line_height_px": line_height,
                "baseline_offset_px": baseline, "title_slot_lines": slots,
                "content_top_px": body_top,
                "body_box": {"x": margin, "y": body_top, "width": width - 2 * margin,
                             "height": body_bottom - body_top},
                "cover_exception": bool(cover_exception), "brand_reservations": reservations, **optional}
    contract["digest"] = common_header_digest(contract)
    bindings = []
    for index, (slide, title, lines, applies) in enumerate(zip(slides, titles, page_lines, applied, strict=False)):
        if not isinstance(title, str) or not title.strip():
            raise ValueError("svg_common_header_title_missing")
        widths, boxes = _line_evidence(lines, font, title_box, baseline, line_height, alignment)
        binding = {"version": PAGE_VERSION, "header_digest": contract["digest"],
                   "page_index": index, "applied": applies, "title": title, "lines": lines,
                   "title_digest": _digest(title), "content_digest": common_header_source_digest(_value(slide, "content", "")),
                   "source_digest": common_header_source_digest(_value(slide, "content", "")),
                   "line_widths": widths, "glyph_boxes": boxes}
        if _value(slide, "title_source_digest"):
            binding["title_source_digest"] = _value(slide, "title_source_digest")
        for name in ("eyebrow", "subline"):
            if contract[name]["enabled"]:
                text = _value(slide, name, "") or ""
                part = contract[name]
                if not isinstance(text, str) or "\n" in text or _advance(text, part["font"]) > part["box"]["width"]:
                    raise ValueError("svg_common_header_label_overflow")
                binding[name] = text
        binding["digest"] = _digest(binding)
        bindings.append(binding)
    return contract, bindings


def validate_common_header_binding(contract: dict | None, page_binding: dict | None, *, index=None, count=None,
                                   content=_MISSING, title=_MISSING, page_id=_MISSING):
    """Fail closed for partial or altered frozen data; absent legacy data is inert."""
    if isinstance(contract, dict) and contract.get("version") == "svg-common-header/v2":
        from services.svg_common_header_v2 import validate_common_header_binding_v2
        return validate_common_header_binding_v2(contract, page_binding, index=index, count=count,
                                                 content=content, title=title, page_id=page_id)
    if contract is None and page_binding is None:
        return
    if (not isinstance(contract, dict) or contract.get("version") != VERSION
            or not {"font", "canvas", "title_box", "baseline_offset_px", "line_height_px", "title_slot_lines",
                    "content_top_px", "body_box", "cover_exception", "alignment", "eyebrow", "subline", "divider",
                    "color", "background", "origin"} <= contract.keys()
            or contract.get("digest") != common_header_digest(contract)
            or contract.get("measurement_version") != MEASUREMENT_VERSION
            or not isinstance(page_binding, dict) or page_binding.get("version") != PAGE_VERSION
            or page_binding.get("header_digest") != contract["digest"]
            or page_binding.get("digest") != _digest({k: v for k, v in page_binding.items() if k != "digest"})):
        raise ValueError("svg_common_header_digest_mismatch")
    page_index = page_binding.get("page_index")
    frozen_title, lines = page_binding.get("title"), page_binding.get("lines")
    if (not isinstance(page_index, int) or isinstance(page_index, bool) or page_index < 0
            or (index is not None and index != page_index)
            or (count is not None and not 0 <= page_index < count)
            or not isinstance(frozen_title, str) or not frozen_title.strip() or page_binding.get("title_digest") != _digest(frozen_title)
            or not isinstance(lines, list) or any(not isinstance(line, str) for line in lines)
            or not page_binding.get("content_digest") or not page_binding.get("source_digest")):
        raise ValueError("svg_common_header_binding_invalid")
    applied = not (contract.get("cover_exception") and page_index == 0)
    if page_binding.get("applied") != applied:
        raise ValueError("svg_common_header_binding_invalid")
    if content is not _MISSING and (page_binding["content_digest"] != common_header_source_digest(content)
                                    or page_binding["source_digest"] != common_header_source_digest(content)):
        raise ValueError("svg_common_header_source_mismatch")
    if title is not _MISSING and title != page_binding["title"]:
        raise ValueError("svg_common_header_title_mismatch")
    if (applied and (not 1 <= len(lines) <= contract["title_slot_lines"] <= 2
                     or "".join(lines) != frozen_title.replace("\n", ""))
            or (not applied and lines)):
        raise ValueError("svg_common_header_title_mismatch")
    font = contract["font"]
    if _font(font["family"], font["weight"], font["size_px"]) != font:
        raise ValueError("svg_common_header_font_identity_invalid")
    widths, boxes = _line_evidence(lines, font, contract["title_box"], contract["baseline_offset_px"],
                                   contract["line_height_px"], contract["alignment"])
    if page_binding.get("line_widths") != widths or page_binding.get("glyph_boxes") != boxes:
        raise ValueError("svg_common_header_measurement_mismatch")


def render_common_header(contract: dict | None, page_binding: dict | None, *, index: int, count: int):
    """Return a system-owned editable SVG group, or None for legacy/explicit cover."""
    if isinstance(contract, dict) and contract.get("version") == "svg-common-header/v2":
        from services.svg_common_header_v2 import render_common_header_v2
        return render_common_header_v2(contract, page_binding, index=index, count=count)
    validate_common_header_binding(contract, page_binding, index=index, count=count)
    if contract is None or not page_binding["applied"]:
        return None
    group = ET.Element(f"{{{SVG_NS}}}g", {"id": GROUP_ID})
    ET.SubElement(group, f"{{{SVG_NS}}}rect", {"id": GROUP_ID + "-background", "x": "0", "y": "0",
        "width": str(contract["canvas"]["width"]), "height": str(contract["content_top_px"]),
        "fill": contract["background"]})
    title_group = ET.SubElement(group, f"{{{SVG_NS}}}g", {"id": TITLE_ID})
    font, box = contract["font"], contract["title_box"]
    for line_index, text in enumerate(page_binding["lines"]):
        x = box["x"] + {"left": 0, "center": box["width"] / 2, "right": box["width"]}[contract["alignment"]]
        node = ET.SubElement(title_group, f"{{{SVG_NS}}}text", {"id": TITLE_ID + f"-line-{line_index + 1}",
            "x": str(x), "y": str(box["y"] + contract["baseline_offset_px"] + line_index * contract["line_height_px"]),
            "fill": contract["color"], "font-family": font["family"], "font-size": str(font["size_px"]),
            "font-weight": str(font["weight"]), "text-anchor": {"left": "start", "center": "middle", "right": "end"}[contract["alignment"]]})
        node.text = text
    for name in ("eyebrow", "subline"):
        part = contract[name]
        if part["enabled"] and page_binding.get(name):
            part_font, part_box = part["font"], part["box"]
            node = ET.SubElement(group, f"{{{SVG_NS}}}text", {"id": GROUP_ID + "-" + name,
                "x": str(part_box["x"]), "y": str(part_box["y"] + part_font["size_px"]),
                "fill": part["color"], "font-family": part_font["family"],
                "font-size": str(part_font["size_px"]), "font-weight": str(part_font["weight"])})
            node.text = page_binding[name]
    divider = contract["divider"]
    if divider["enabled"]:
        ET.SubElement(group, f"{{{SVG_NS}}}line", {"id": GROUP_ID + "-divider", "x1": str(divider["x1"]),
            "x2": str(divider["x2"]), "y1": str(divider["y"]), "y2": str(divider["y"]),
            "stroke": divider["color"], "stroke-width": str(divider["thickness_px"])})
    return group
