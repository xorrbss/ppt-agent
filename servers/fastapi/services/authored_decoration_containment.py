"""Bounded placement of explicit bands, rings, corner outlines and panel sheens."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import math
import re

from services.authored_composition_policy import _AuthoredStartTagCollector, _attribute_map
from services.authored_decoration_panel_sheen import (
    panel_sheen_family, panel_sheen_geometry, without_approved_panel_sheen_clip,
)

_MARKER = "authored-decoration-containment-v3"
_QID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_FORBIDDEN = re.compile(r"chart|table|graph|plot|sparkline|axis|legend|connector|arrow|logo|icon|image|source", re.I)


@dataclass(frozen=True)
class DecorationContainmentPlan:
    candidate: str
    targets: tuple[str, ...]
    clips: Mapping[str, tuple[float, float, float, float]]
    translations: Mapping[str, tuple[float, float]]
    scales: Mapping[str, float]
    geometry_styles: Mapping[str, Mapping[str, float | tuple[float, float]]] = field(default_factory=dict)


class _Document(_AuthoredStartTagCollector):
    def __init__(self):
        super().__init__()
        self.parents: list[int | None] = []
        self.valid = True

    def handle_starttag(self, tag, attrs):
        self.parents.append(self._stack[-1][1] if self._stack else None)
        names = [name.lower() for name, _ in attrs]
        self.valid &= len(names) == len(set(names)) and not any(name.startswith("on") for name in names)
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.valid &= bool(self._stack and self._stack[-1][0] == tag.lower())
        super().handle_endtag(tag)

    def handle_startendtag(self, tag, attrs):
        self.parents.append(self._stack[-1][1] if self._stack else None)
        names = [name.lower() for name, _ in attrs]
        self.valid &= len(names) == len(set(names)) and not any(name.startswith("on") for name in names)
        super().handle_startendtag(tag, attrs)


def _number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def _px(value):
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)px", str(value))
    number = float(match[1]) if match else None
    return number if number is not None and math.isfinite(number) else None


def _box(value):
    if not isinstance(value, Mapping):
        return None
    values = tuple(_number(value.get(key)) for key in ("x", "y", "width", "height"))
    return values if all(v is not None for v in values) and min(values[2:]) > 0 else None


def _quad(value):
    if not isinstance(value, list) or len(value) != 4 or not all(isinstance(p, Mapping) for p in value):
        return None
    points = tuple((_number(p.get("x")), _number(p.get("y"))) for p in value)
    return points if all(v is not None for point in points for v in point) else None


def _inside(points, clip):
    return all(clip[0] <= x <= clip[2] and clip[1] <= y <= clip[3] for x, y in points)


def _intersects(points, rect):
    """Separating-axis test, preserving the thin rotated band's actual quad."""
    x, y, width, height = rect
    other = ((x, y), (x + width, y), (x + width, y + height), (x, y + height))
    axes = [(1, 0), (0, 1)] + [
        (points[(i + 1) % 4][1] - p[1], p[0] - points[(i + 1) % 4][0])
        for i, p in enumerate(points)
    ]
    for ax, ay in axes:
        left = [px * ax + py * ay for px, py in points]
        right = [px * ax + py * ay for px, py in other]
        if max(left) <= min(right) or max(right) <= min(left):
            return False
    return True


def _static_document(html):
    """Animation and generated paint have no frozen per-node geometry proof."""
    css = "\n".join(re.findall(r"<style\b[^>]*>(.*?)</style\s*>", html, re.I | re.S))
    inline = [m[1] for m in re.findall(r"\bstyle\s*=\s*(['\"])(.*?)\1", html, re.I | re.S)]
    if any(re.search(r"(?:^|;)\s*transform\s*:", value, re.I) for value in inline):
        return False
    css += "\n" + "\n".join(inline)
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = without_approved_panel_sheen_clip(css)
    if re.search(r"@|::|:{1,2}(?:before|after|first-letter|first-line)\b|(?:^|[;{])\s*(?:animation[\w-]*|transition[\w-]*|text-shadow|border-image[\w-]*|outline[\w-]*|mask[\w-]*|clip[\w-]*|filter|backdrop-filter|perspective|contain|content-visibility|zoom|rotate|scale|translate)\s*:", css, re.I):
        return False
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"(?:^|;)\s*transform\s*:", body, re.I) and re.fullmatch(r"\s*\.(?:angle-band(?:-[a-z0-9]+)?|decorative-band|ornament-band|field-glow|corner-block)\s*", selector) is None:
            return False
        if re.search(r"\b(?:html|body)\b|:root|\*|\[", selector, re.I) and re.search(r"(?:^|;)\s*border[\w-]*\s*:", body, re.I):
            return False  # Unmeasured document-root rounded/border clipping.
    return True


def _radii(css, width, height):
    radii = []
    for corner in ("TopLeft", "TopRight", "BottomRight", "BottomLeft"):
        tokens = str(css.get("border" + corner + "Radius", "")).split()
        if len(tokens) == 1:
            tokens *= 2
        if len(tokens) != 2:
            return None
        values = [_px(token) for token in tokens]
        if any(value is None or value < 0 for value in values):
            return None
        radii.append(values)
    ratios = [1.0]
    for extent, total in ((width, radii[0][0] + radii[1][0]), (width, radii[3][0] + radii[2][0]), (height, radii[0][1] + radii[3][1]), (height, radii[1][1] + radii[2][1])):
        if total:
            ratios.append(extent / total)
    factor = min(ratios)
    return tuple((rx * factor, ry * factor) for rx, ry in radii)


def _plain_effects(css, *, target=False, panel_sheen=False):
    expected = {key: "none" for key in ("filter", "backdropFilter", "clipPath", "maskImage", "borderImageSource", "perspective", "translate", "rotate", "scale", "contain")}
    expected.update(mixBlendMode="normal", opacity="1", contentVisibility="visible")
    if panel_sheen:
        expected.pop("clipPath")  # Exact polygon already proved by the family.
    if css.get("zoom") not in ("normal", "1") or any(css.get(key) != value for key, value in expected.items()) or _px(css.get("overflowClipMargin")) != 0:
        return False
    # An ancestor's own shadow does not enlarge its child's painted footprint.
    outline_width = _px(css.get("outlineWidth"))
    return not target or (css.get("boxShadow") == css.get("textShadow") == "none" and css.get("outlineStyle") == "none" and outline_width is not None and outline_width >= 0)


def _clip_rect(css, box, *, direct_parent):
    radii = _radii(css, box[2], box[3])
    borders = [_px(css.get("border" + side + "Width")) for side in ("Left", "Top", "Right", "Bottom")]
    pads = [_px(css.get("padding" + side)) for side in ("Left", "Top", "Right", "Bottom")]
    if radii is None or any(value is None or value < 0 for value in [*borders, *pads]):
        return None
    # This inscribed rectangle is inside each normalized corner ellipse. Add
    # border widths and one CSS pixel, keeping the complete quad off the clip.
    corner_insets = (max(radii[0][0], radii[3][0]), max(radii[0][1], radii[1][1]), max(radii[1][0], radii[2][0]), max(radii[2][1], radii[3][1]))
    insets = [border + max(radius * (1 - 1 / math.sqrt(2)) + 1, pad if direct_parent else 0) for border, radius, pad in zip(borders, corner_insets, pads, strict=True)]
    return (box[0] + insets[0], box[1] + insets[1], box[0] + box[2] - insets[2], box[1] + box[3] - insets[3])


def _family(qid, node, css):
    if not isinstance(css, Mapping):
        return None
    classes = node.get("class", "").split()
    if panel_sheen_family(qid, node, css):
        return "panel-sheen"
    if re.search(r"(?:^|[._:-])decor(?:ation)?(?:[._:-]|$)", qid, re.I) and any(re.fullmatch(r"angle-band(?:-[a-z0-9]+)?|decorative-band|ornament-band", value) for value in classes):
        return "band"
    if node.get("data-semantic-role") != "decoration":
        return None
    family = "ring" if "field-glow" in classes and re.search(r"(?:^|[._:-])glow$", qid, re.I) else "corner" if "corner-block" in classes and re.search(r"(?:^|[._:-])corner$", qid, re.I) else None
    width, height = _px(css.get("width")), _px(css.get("height"))
    if not family or width is None or height != width or width < 32 or css.get("boxSizing") != "border-box" or css.get("backgroundColor") != "rgba(0, 0, 0, 0)":
        return None
    if family == "ring" and (css.get("transform") != "none" or any(_px(css.get("padding" + side)) != 0 for side in ("Top", "Right", "Bottom", "Left")) or any(css.get("min" + axis) != "0px" or css.get("max" + axis) != "none" for axis in ("Width", "Height")) or [_px(token) for token in str(css.get("transformOrigin")).split()] != [width / 2, height / 2]):
        return None
    borders = [_px(css.get("border" + side + "Width")) for side in ("Top", "Right", "Bottom", "Left")]
    radii = _radii(css, width, height)
    if radii is None or len(set(borders)) != 1 or borders[0] is None or not 0 < borders[0] < width / 2 or any(css.get("border" + side + "Style") != "solid" for side in ("Top", "Right", "Bottom", "Left")):
        return None
    return family if (all(abs(rx - width / 2) < .01 and abs(ry - height / 2) < .01 for rx, ry in radii) if family == "ring" else all(rx == ry and rx <= width / 4 for rx, ry in radii)) else None


def _ring_geometry(css, scale, delta):
    """Keep identity mapping for the existing exact rounded-surface sensor."""
    lengths = {key: _px(css.get(key)) for key in ("left", "top", "right", "bottom", "width", "height", "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth", "borderTopLeftRadius", "borderTopRightRadius", "borderBottomRightRadius", "borderBottomLeftRadius")}
    if any(value is None for value in lengths.values()):
        return None
    scaled = {key: value * scale for key, value in lengths.items() if key not in {"left", "top", "right", "bottom"}}
    if any(abs(value - round(value)) > 1e-9 for key, value in scaled.items() if key in {"width", "height"} or key.endswith("Width")):
        return None  # Avoid CSS border/layout pixel snapping changing proportions.
    # Materialize on Chromium's layout grid, then bind the plan to those exact
    # coordinates instead of relaxing the fresh-quad comparison tolerance.
    shift_x = round((lengths["left"] + delta[0] + (lengths["width"] - scaled["width"]) / 2) * 64) / 64 - lengths["left"]
    shift_y = round((lengths["top"] + delta[1] + (lengths["height"] - scaled["height"]) / 2) * 64) / 64 - lengths["top"]
    scaled.update(left=lengths["left"] + shift_x, top=lengths["top"] + shift_y,
                  right=lengths["right"] - shift_x + lengths["width"] - scaled["width"],
                  bottom=lengths["bottom"] - shift_y + lengths["height"] - scaled["height"])
    scaled["transformOrigin"] = (scaled["width"] / 2, scaled["height"] / 2)
    return scaled


def _placement(points, clip, obstacles):
    min_x, max_x = min(p[0] for p in points), max(p[0] for p in points)
    min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
    xlo, xhi, ylo, yhi = clip[0] + .1 - min_x, clip[2] - .1 - max_x, clip[1] + .1 - min_y, clip[3] - .1 - max_y
    if xlo > xhi or ylo > yhi:
        return None
    xs, ys = {max(xlo, min(0, xhi)), xlo, xhi}, {max(ylo, min(0, yhi)), ylo, yhi}
    for x, y, width, height in obstacles:
        xs.update((max(xlo, min(x - max_x - 1, xhi)), max(xlo, min(x + width - min_x + 1, xhi))))
        ys.update((max(ylo, min(y - max_y - 1, yhi)), max(ylo, min(y + height - min_y + 1, yhi))))
    choices = sorted(((dx, dy) for dx in xs for dy in ys), key=lambda p: (p[0] ** 2 + p[1] ** 2, p))
    return next(((dx, dy) for dx, dy in choices if not any(_intersects(tuple((x + dx, y + dy) for x, y in points), obstacle) for obstacle in obstacles)), None)


def _bound_metrics(html, metrics):
    from utils.slide_capture import DOM_METRICS_VERSION

    if not isinstance(metrics, Mapping) or metrics.get("dom_metrics_version") != DOM_METRICS_VERSION:
        return None
    if metrics.get("source_digest") != "sha256:" + sha256(html.encode()).hexdigest() or metrics.get("fonts_ready") is not True:
        return None
    if re.fullmatch(r"sha256:[0-9a-f]{64}", str(metrics.get("render_digest", ""))) is None:
        return None
    if metrics.get("width") != 1280 or metrics.get("height") != 720:
        return None
    elements = metrics.get("elements")
    if not isinstance(elements, list) or any(not isinstance(item, Mapping) for item in elements):
        return None
    qids = [item.get("qid") for item in elements]
    if any(not isinstance(qid, str) or _QID.fullmatch(qid) is None for qid in qids) or len(set(qids)) != len(qids):
        return None
    return {item["qid"]: item for item in elements}


def plan_decoration_containment(html, browser_metrics, *, profile_id="ordinary", reference_mode="style_reference"):
    """Translation first; only explicit empty rings/corners may uniformly shrink."""
    if profile_id != "ordinary" or reference_mode == "exact_reconstruction" or "authored-decoration-containment-v" in html:
        return None
    if re.search(r"data-source-|<(?:script|link|iframe|object|embed)\b", html, re.I):
        return None
    metrics = _bound_metrics(html, browser_metrics)
    if metrics is None or not _static_document(html):
        return None
    parser = _Document()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, TypeError):
        return None
    if not parser.valid or parser._stack:
        return None
    attrs = [_attribute_map(row[1]) for row in parser.tags]
    if any(tag[0] in {"html", "body"} and not row.get("data-qid") and any(key in row for key in ("class", "style", "id")) for tag, row in zip(parser.tags, attrs, strict=True)):
        return None
    declared = [row.get("data-qid") for row in attrs if row.get("data-qid")]
    if len(set(declared)) != len(declared) or set(declared) != set(metrics):
        return None
    indices = {row.get("data-qid"): i for i, row in enumerate(attrs) if row.get("data-qid")}
    if any(item.get("source_block_id") not in (None, "") for item in metrics.values()):
        return None
    obstacles = [_box(fragment) for item in metrics.values() if item.get("has_owned_text") is True for fragment in item.get("text_fragments", ())]
    obstacles.extend(_box(item.get("bbox")) for qid, item in metrics.items() if item.get("has_owned_paint") is True and item.get("has_owned_text") is False and indices[qid] not in parser.parents and not _family(qid, attrs[indices[qid]], item.get("computed_style", {})))
    if any(rect is None for rect in obstacles) or len(obstacles) > 32:
        return None
    rules, clips, translations, scales, geometry_styles = [], {}, {}, {}, {}
    for qid, item in metrics.items():
        index = indices[qid]
        node = attrs[index]
        style, measured = item.get("computed_style", {}), item.get("measurement", {})
        if not isinstance(style, Mapping) or not isinstance(measured, Mapping):
            continue
        family = _family(qid, node, style)
        if family is None:
            continue
        points, bounds = _quad(item.get("quad")), _box(item.get("bbox"))
        if (parser.tags[index][0] != "div" or index in parser.parents or parser.owns_text[index]
            or node.get("data-semantic-role") not in {"supporting", "decoration"}
            or item.get("semantic_role") != node.get("data-semantic-role")
            or _FORBIDDEN.search(" ".join(str(v) for v in node.values()))
            or any(k.startswith("aria-") or k in {"role", "alt", "title"} for k in node)
            or item.get("has_owned_text") is not False or item.get("has_owned_paint") is not True
            or any(item.get(k) != "" for k in ("owned_text", "rendered_text")) or item.get("text_fragments") != []
            or style.get("position") != "absolute" or style.get("display") != "block"
            or (family != "panel-sheen" and style.get("backgroundImage") != "none") or style.get("opacity") != "1"
            or not _plain_effects(style, target=True, panel_sheen=family == "panel-sheen")
            or item.get("css_visible") is not True or _number(item.get("effective_opacity")) != 1
            or any(measured.get(k) is not True for k in ("geometry", "transform", "clipping", "stacking"))
            or points is None or bounds is None):
            continue
        parent_index = parser.parents[index]
        parent_qid = attrs[parent_index].get("data-qid") if parent_index is not None else None
        proof = item.get("containing_block_proof", {})
        parent = metrics.get(parent_qid)
        if not isinstance(proof, Mapping) or parent is None or item.get("parent_qid") != parent_qid or item.get("offset_parent_qid") != parent_qid:
            continue
        if proof.get("source") != "chrome" or proof.get("telemetryComplete") is not True or proof.get("exactBoundOffsetParent") is not True or proof.get("offsetParentQid") != parent_qid or _box(proof.get("offsetParentBoundsPx")) != _box(parent.get("bbox")):
            continue
        clip = [1.0, 1.0, 1279.0, 719.0]
        cursor, valid = parent_index, True
        while cursor is not None:
            ancestor_qid = attrs[cursor].get("data-qid")
            tag = parser.tags[cursor][0]
            if ancestor_qid is None and tag in {"html", "body"}:
                cursor = parser.parents[cursor]
                continue
            ancestor = metrics.get(ancestor_qid, {})
            css, box = ancestor.get("computed_style", {}), _box(ancestor.get("bbox"))
            if not isinstance(css, Mapping) or box is None or ancestor.get("measurement", {}).get("geometry") is not True or css.get("transform") != "none" or not _plain_effects(css):
                valid = False
                break
            if cursor == parent_index or css.get("overflowX") != "visible" or css.get("overflowY") != "visible":
                inner = _clip_rect(css, box, direct_parent=cursor == parent_index)
                if inner is None:
                    valid = False
                    break
                clip = [max(clip[0], inner[0]), max(clip[1], inner[1]), min(clip[2], inner[2]), min(clip[3], inner[3])]
            cursor = parser.parents[cursor]
        if not valid or _inside(points, clip):
            continue
        target_obstacles = obstacles
        if family == "panel-sheen":
            # The message's text-owner box and border can cover the sheen even
            # where no glyph exists: avoid complete boxes, not only glyphs.
            target_obstacles = [*obstacles, *(_box(value.get("bbox")) for value in metrics.values() if value.get("has_owned_text") is True)]
            if any(value is None for value in target_obstacles) or len(target_obstacles) > 64:
                continue
        matrix = re.fullmatch(r"matrix\(([^)]+)\)", str(style.get("transform")))
        if matrix is None and style.get("transform") != "none":
            continue
        try:
            values = [float(value) for value in matrix[1].split(",")] if matrix else [1, 0, 0, 1, 0, 0]
        except ValueError:
            continue
        if len(values) != 6 or not all(math.isfinite(v) for v in values) or abs(values[0] ** 2 + values[1] ** 2 - 1) > 1e-5 or abs(values[2] + values[1]) > 1e-5 or abs(values[3] - values[0]) > 1e-5:
            continue
        if len(clips) >= 4:
            return DecorationContainmentPlan(html, (qid,), {}, {}, {})
        scale, delta = 1.0, _placement(points, clip, target_obstacles)
        if family == "ring" and _ring_geometry(style, scale, (0, 0)) is None:
            continue
        if delta is None and family != "band":
            width, height = _px(style.get("width")), _px(style.get("height"))
            origin = [_px(token) for token in str(style.get("transformOrigin")).split()]
            if width is None or height is None or origin != [width / 2, height / 2]:
                return DecorationContainmentPlan(html, (qid,), {}, {}, {})
            cx, cy = (sum(p[axis] for p in points) / 4 for axis in (0, 1))
            for step in (range(63, 31, -1) if family == "panel-sheen" else range(19, 9, -1)):
                scale = step / (64 if family == "panel-sheen" else 20)
                if min(width, height) * scale < 16:
                    break
                if family == "ring" and _ring_geometry(style, scale, (0, 0)) is None:
                    continue
                if family == "panel-sheen" and panel_sheen_geometry(style, scale, (0, 0)) is None:
                    continue
                scaled = tuple((cx + (x - cx) * scale, cy + (y - cy) * scale) for x, y in points)
                delta = _placement(scaled, clip, target_obstacles)
                if delta is not None:
                    break
        if delta is None:
            return DecorationContainmentPlan(html, (qid,), {}, {}, {})
        dx, dy = delta
        if family in {"ring", "panel-sheen"}:
            expected = panel_sheen_geometry(style, scale, delta) if family == "panel-sheen" else _ring_geometry(style, scale, delta)
            if expected is None:
                return DecorationContainmentPlan(html, (qid,), {}, {}, {})
            dx = expected["left"] - _px(style["left"]) - (_px(style["width"]) - expected["width"]) / 2
            dy = expected["top"] - _px(style["top"]) - (_px(style["height"]) - expected["height"]) / 2
            delta = (dx, dy)
            cx, cy = (sum(p[axis] for p in points) / 4 for axis in (0, 1))
            materialized = tuple((cx + (x - cx) * scale + dx, cy + (y - cy) * scale + dy) for x, y in points)
            if not _inside(materialized, clip) or any(_intersects(materialized, obstacle) for obstacle in target_obstacles):
                return DecorationContainmentPlan(html, (qid,), {}, {}, {})
            geometry_styles[qid] = expected
            declarations = []
            for key, value in expected.items():
                property_name = re.sub(r"[A-Z]", lambda match: "-" + match[0].lower(), key)
                css_value = "auto" if key in {"right", "bottom"} else "50% 50%" if key == "transformOrigin" else f"{value:.9f}px"
                declarations.append(f"{property_name}:{css_value}!important")
            rules.append(f'[data-qid="{qid}"]{{{";".join(declarations)}}}')
        else:
            values[:4] = [v * scale for v in values[:4]]
            values[4] += dx
            values[5] += dy
            rules.append(f'[data-qid="{qid}"]{{transform:matrix({",".join(f"{v:.9f}" for v in values)})!important}}')
        clips[qid], translations[qid], scales[qid] = tuple(clip), delta, scale
    if not rules or re.search(r"</head\s*>", html, re.I) is None:
        return None
    insertion = f'<style>/* {_MARKER} */{"".join(rules)}</style>'
    candidate = re.sub(r"</head\s*>", lambda m: insertion + m[0], html, count=1, flags=re.I)
    return DecorationContainmentPlan(candidate, tuple(clips), clips, translations, scales, geometry_styles)


def validate_decoration_containment(plan, original_html, original_metrics, candidate_html, candidate_metrics):
    """A fresh candidate never inherits success from its pre-repair telemetry."""
    before, after = _bound_metrics(original_html, original_metrics), _bound_metrics(candidate_html, candidate_metrics)
    if candidate_html != plan.candidate or before is None or after is None or list(before) != list(after):
        return False
    for qid, item in after.items():
        original = before[qid]
        if any(item.get(k) != original.get(k) for k in ("tag", "parent_qid", "offset_parent_qid", "semantic_role", "source_block_id", "owned_text", "rendered_text", "has_owned_text")):
            return False
        if item.get("measurement", {}).get("occlusion") is not True:
            return False
        fraction = _number(item.get("occluded_fraction"))
        if fraction is None:
            return False
        if qid not in plan.targets:
            if any(item.get(k) != original.get(k) for k in ("bbox", "quad", "computed_style", "text_fragments")):
                return False
            for key in ("occluded_fraction", "continuous_occluded_fraction", "occlusion_unknown_fraction"):
                prior, current = _number(original.get(key)), _number(item.get(key))
                if prior is None or current is None or not 0 <= prior <= 1 or not 0 <= current <= prior + 1e-9:
                    return False
            old_occluders, new_occluders = original.get("occlusion_occluder_qids"), item.get("occlusion_occluder_qids")
            if (not isinstance(old_occluders, list) or not isinstance(new_occluders, list)
                or any(not isinstance(qid, str) for qid in [*old_occluders, *new_occluders])
                or not set(new_occluders).issubset(old_occluders)):
                return False
        else:
            points = _quad(item.get("quad"))
            if points is None or not _inside(points, plan.clips[qid]) or fraction != 0 or _number(item.get("occlusion_unknown_fraction")) != 0:
                return False
            dx, dy = plan.translations[qid]
            prior_points, scale = _quad(original.get("quad")), plan.scales[qid]
            if prior_points is None or not isinstance(item.get("computed_style"), Mapping) or not isinstance(original.get("computed_style"), Mapping):
                return False
            cx, cy = (sum(p[axis] for p in prior_points) / 4 for axis in (0, 1))
            if any(abs(a - b) > .01 for point, prior in zip(points, prior_points, strict=True) for a, b in zip(point, (cx + (prior[0] - cx) * scale + dx, cy + (prior[1] - cy) * scale + dy), strict=True)):
                return False
            expected_style = plan.geometry_styles.get(qid, {})
            allowed = set(expected_style) if expected_style else {"transform"}
            if {k: v for k, v in item["computed_style"].items() if k not in allowed} != {k: v for k, v in original["computed_style"].items() if k not in allowed}:
                return False
            for key, expected in expected_style.items():
                current = tuple(_px(token) for token in str(item["computed_style"].get(key)).split()) if key == "transformOrigin" else (_px(item["computed_style"].get(key)),)
                expected_values = expected if isinstance(expected, tuple) else (expected,)
                if len(current) != len(expected_values) or any(value is None or abs(value - desired) > .01 for value, desired in zip(current, expected_values, strict=True)):
                    return False
            if any(item.get("measurement", {}).get(key) is not True for key in ("geometry", "transform", "clipping")):
                return False
    return True
