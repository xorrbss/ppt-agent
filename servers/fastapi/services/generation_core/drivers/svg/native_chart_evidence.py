"""Pinned chart drawing evidence: legacy v4 tokens and v5 geometric proof."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
import math
import re
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.native_chart import CHART_ELEMENTS, _drawn_series_name, _model_chart
from services.generation_core.drivers.svg.native_declarations import bound_declarations
from services.generation_core.drivers.svg.native_geometry import (
    NativeRejection, collect_primitives, find_group, inherit, normalize_text,
)

_VALUE = re.compile(r"([-+−]?)([₩$€£¥]?)\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*([%％A-Za-z가-힣/²³]*)")


def _overlaps(a, b) -> bool:
    return min(a.x1, b.x1) > max(a.x0, b.x0) and min(a.y1, b.y1) > max(a.y0, b.y0)


def _v5_path_subpaths(source):
    """Preserve admitted M/L/H/V/Z topology instead of inventing M-to-M ink."""
    from services.generation_core.drivers.svg.native_geometry import _NUMBER
    if not re.fullmatch(r"[MLHVZmlhvz0-9eE+.,\s-]+", source):
        raise NativeRejection("native_chart_content_path_invalid")
    paths, closed, current, position, start = [], [], [], (0.0, 0.0), (0.0, 0.0)
    if not re.match(r"\s*[Mm]", source):
        raise NativeRejection("native_chart_content_path_invalid")
    def finish(is_closed=False):
        nonlocal current
        if current:
            if is_closed and current[-1] != current[0]:
                current.append(current[0])
            paths.append(tuple(current))
            closed.append(is_closed)
            current = []
    for command, raw in re.findall(r"([MLHVZmlhvz])([^MLHVZmlhvz]*)", source):
        values = [float(value) for value in _NUMBER.findall(raw)]
        name, relative = command.upper(), command.islower()
        if name == "Z":
            if values or not current:
                raise NativeRejection("native_chart_content_path_invalid")
            position = start
            finish(True)
            continue
        arity = 1 if name in {"H", "V"} else 2
        if not values or len(values) % arity:
            raise NativeRejection("native_chart_content_path_invalid")
        for index in range(0, len(values), arity):
            x, y = position
            if name == "H":
                point = (x + values[index] if relative else values[index], y)
            elif name == "V":
                point = (x, y + values[index] if relative else values[index])
            else:
                point = (x + values[index] if relative else values[index], y + values[index + 1] if relative else values[index + 1])
            if name == "M" and index == 0:
                finish()
                current, start = [point], point
            else:
                if not current:
                    current = [position]
                    start = position
                current.append(point)
            position = point
    finish()
    if not paths or not all(math.isfinite(value) for path in paths for point in path for value in point):
        raise NativeRejection("native_chart_content_path_invalid")
    return tuple(paths), tuple(closed)


def _v5_scene(root, measurement=None, svg=None):
    """Reuse canonical primitives, supplementing text with bound renderer boxes.

    The old native reader intentionally retains its historical tspan layout.
    This new reader only changes v5's evidence and handles the admitted tspan,
    letter-spacing and positive uniform-transform spelling without changing
    any renderer or earlier native-object output.
    """
    from dataclasses import dataclass
    from services.generation_core.drivers.svg.native_geometry import (
        Box, DrawnText, DrawnTextRun, GroupPrimitives, Shape, _compose_transform,
        _transform_of, paint, shape_of, transform_shape,
    )
    from services.generation_core.drivers.svg.fallback import _text_em
    from services.svg_contract import source_digest
    from services.svg_text_overlap import _ink_rect

    @dataclass(frozen=True)
    class ChartText(DrawnText):
        actual_box: object = None
        fragments: tuple = ()
        paint_fragments: tuple = ()
        def box(self):
            return self.actual_box if self.actual_box is not None else super().box()

    @dataclass(frozen=True)
    class ChartShape(Shape):
        subpaths: tuple = ()
        closedSubpaths: tuple = ()

    measured = {}
    if (measurement and measurement.get("measurement_complete") is True
            and measurement.get("source_digest") == source_digest(svg)
            and measurement.get("wrapper_digest")
            and isinstance(measurement.get("browser_metrics"), dict)
            and measurement["browser_metrics"].get("source_digest") == measurement["wrapper_digest"]):
        # Unbound measurements cannot move the source geometry or glyphs.
        # The separate post-render paint consumer rejects their missing chain.
        measured = {item.get("qid"): item for item in measurement["browser_metrics"].get("elements", [])}
    order = {node: index for index, node in enumerate(root.iter())}
    records = []

    def text_record(node, position, attrs):
        fragments = []
        cursor = [float(node.get("x", 0)), float(node.get("y", 0))]
        chunks, chunk = {}, 0
        def emit(content, style, transform):
            if not content:
                return
            nonlocal chunk
            size = float(style.get("font-size", 16))
            spacing = float(style.get("letter-spacing", 0))
            advance = _text_em(content) * size + max(0, len(content) - 1) * spacing
            entry = {"content": content, "x": cursor[0], "y": cursor[1], "size": size,
                     "fill": paint(style.get("fill", "black")), "alpha": float(style.get("fill-opacity", 1)) * style.get("_opacity", 1),
                     "family": style.get("font-family", ""), "weight": style.get("font-weight", "normal"),
                     "position": transform, "advance": advance, "chunk": chunk,
                     "anchor": style.get("text-anchor", "start")}
            fragments.append(entry)
            chunks[chunk] = chunks.get(chunk, 0) + advance
            cursor[0] += advance
        emit(node.text, attrs, position)
        for child in node:
            if child.tag.rsplit("}", 1)[-1] != "tspan" or list(child):
                raise NativeRejection("native_chart_text_nesting_invalid")
            if "x" in child.attrib or "y" in child.attrib or "dy" in child.attrib:
                chunk += 1
                if "x" in child.attrib:
                    cursor[0] = float(child.get("x"))
                if "y" in child.attrib:
                    cursor[1] = float(child.get("y"))
                if "dy" in child.attrib:
                    cursor[1] += float(child.get("dy"))
            child_attrs = inherit(attrs, child)
            if child.get("letter-spacing") is not None:
                child_attrs["letter-spacing"] = child.get("letter-spacing")
            emit(child.text, child_attrs, _compose_transform(position, _transform_of(child)))
            emit(child.tail, attrs, position)
        if not fragments:
            return None
        boxes, runs, glyphs, painted = [], [], [], []
        for fragment in fragments:
            dx, dy, scale = fragment["position"]
            shift = {"middle": chunks[fragment["chunk"]] / 2, "end": chunks[fragment["chunk"]]}.get(fragment["anchor"], 0)
            left = dx + scale * (fragment["x"] - shift)
            baseline = dy + scale * fragment["y"]
            boxes.append(Box(left, baseline - fragment["size"] * scale,
                             left + fragment["advance"] * scale, baseline + fragment["size"] * scale * .25))
            cursor_x = left
            for character in fragment["content"]:
                advance = _text_em(character) * fragment["size"] * scale
                if not character.isspace() and advance > 0:
                    glyph = Box(cursor_x, baseline - fragment["size"] * scale,
                                cursor_x + advance, baseline + fragment["size"] * scale * .25)
                    glyphs.append(glyph)
                    painted.append((glyph, fragment["fill"], fragment["alpha"]))
                cursor_x += advance + float(attrs.get("letter-spacing", 0)) * scale
            runs.append(DrawnTextRun(fragment["content"], fragment["size"] * scale,
                                    fragment["family"], fragment["fill"], fragment["weight"]))
        box = boxes[0]
        for item in boxes[1:]:
            box = box.union(item)
        item = measured.get(f"svg-{order[node]}")
        glyph_boxes = []
        if item is not None:
            if item.get("tag") != "text":
                raise NativeRejection("native_chart_measurement_binding_invalid")
            descendants = {f"svg-{order[child]}" for child in node.iter()}
            members = [member for qid, member in measured.items() if qid in descendants and member.get("has_owned_text")]
            complete_glyphs = True
            for member in members:
                if (member.get("css_visible") is False or member.get("clipped") or member.get("glyph_clipped")
                        or member.get("effective_opacity", 1) <= 0
                        or member.get("occluded_fraction", 0) > 0):
                    raise NativeRejection("native_chart_content_hidden")
                from services.generation_core.drivers.svg.source_paint_occlusion import _complete_glyphs
                clusters = [cluster for fragment in member.get("text_ink_fragments", [])
                            for cluster in fragment.get("glyphInkClusters", [])
                            if cluster.get("basis") == "isolated-matching-advance" and _ink_rect(cluster) is not None]
                if not _complete_glyphs(member, clusters):
                    complete_glyphs = False
                for fragment in member.get("text_ink_fragments", []):
                    rect = _ink_rect(fragment)
                    clusters = fragment.get("glyphInkClusters") or []
                    for cluster in clusters:
                        ink = _ink_rect(cluster)
                        if ink is not None:
                            glyph_boxes.append(Box(ink["x"], ink["y"], ink["x"] + ink["width"], ink["y"] + ink["height"]))
                    if rect is not None and not clusters:
                        glyph_boxes.append(Box(rect["x"], rect["y"], rect["x"] + rect["width"], rect["y"] + rect["height"]))
            if not complete_glyphs:
                glyph_boxes = []
            if glyph_boxes:
                box = glyph_boxes[0]
                for item_box in glyph_boxes[1:]:
                    box = box.union(item_box)
        semantic_lines = []
        for fragment in fragments:
            if semantic_lines and fragment["y"] == semantic_lines[-1][0]:
                semantic_lines[-1][1] += fragment["content"]
            else:
                semantic_lines.append([fragment["y"], fragment["content"]])
        content = normalize_text(" ".join(content for _, content in semantic_lines))
        if not content:
            return None
        first = fragments[0]
        dx, dy, scale = first["position"]
        return ChartText((content,), ((dx + scale * first["x"], dy + scale * first["y"]),),
            first["size"] * scale, first["family"], first["fill"], first["weight"], first["anchor"],
            all(run.fill is not None and run.size > 0 and fragment["alpha"] > 0
                for run, fragment in zip(runs, fragments, strict=True)), (tuple(runs),), box,
            tuple(glyph_boxes or glyphs or boxes), tuple(painted))

    def visit(node, parent_position, inherited, ancestors):
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"style", "defs", "clipPath", "mask", "filter"} or any(
                key in node.attrib for key in ("style", "class", "clip-path", "mask", "filter",
                    "writing-mode", "textLength", "lengthAdjust", "rotate", "baseline-shift",
                    "dominant-baseline", "alignment-baseline", "direction", "unicode-bidi")):
            raise NativeRejection("native_chart_content_svg_contract_invalid")
        if node.get("display") not in (None, "inline") or node.get("visibility") not in (None, "visible"):
            raise NativeRejection("native_chart_content_hidden")
        position = _compose_transform(parent_position, _transform_of(node))
        attrs = inherit(inherited, node)
        if node.get("letter-spacing") is not None:
            attrs["letter-spacing"] = node.get("letter-spacing")
        if tag == "svg" and node is not root:
            raise NativeRejection("native_chart_content_svg_contract_invalid")
        if tag in {"svg", "g"}:
            for child in node:
                visit(child, position, attrs, (*ancestors, node))
            return
        if tag == "text":
            text = text_record(node, position, attrs)
            if text is not None:
                records.append({"node": node, "ancestors": ancestors, "text": text, "order": order[node],
                                "alpha": attrs.get("_opacity", 1) * float(attrs.get("fill-opacity", 1))})
            return
        if tag not in CHART_ELEMENTS | {"image"}:
            raise NativeRejection("native_chart_content_svg_contract_invalid")
        if tag == "path" and not re.fullmatch(r"[MLHVZmlhvz0-9eE+.,\s-]+", node.get("d", "")):
            raise NativeRejection("native_chart_content_path_invalid")
        for key in ("width", "height", "r", "rx", "ry"):
            if key in node.attrib and float(node.get(key)) < 0:
                raise NativeRejection("native_chart_content_geometry_invalid")
        shape = transform_shape(shape_of(node, tag, 0, 0, attrs), position)
        if tag == "path":
            paths, closed_paths = _v5_path_subpaths(node.get("d", ""))
            dx, dy, scale = position
            paths = tuple(tuple((dx + scale * x, dy + scale * y) for x, y in path) for path in paths)
            drawn = [path for path in paths if len(path) >= 2 and any(point != path[0] for point in path[1:])]
            vertices = tuple(point for path in drawn for point in path)
            signed_areas, centers = [], []
            for path in paths:
                edges = list(zip(path, (*path[1:], path[0]), strict=True))
                cross = math.fsum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
                signed_areas.append(cross / 2)
                centers.append((math.fsum((a[0] + b[0]) * (a[0] * b[1] - b[0] * a[1]) for a, b in edges) / (3 * cross),
                                math.fsum((a[1] + b[1]) * (a[0] * b[1] - b[0] * a[1]) for a, b in edges) / (3 * cross)) if cross else None)
            signed_area = math.fsum(signed_areas)
            center = ((math.fsum(area * point[0] for area, point in zip(signed_areas, centers, strict=True) if point) / signed_area,
                       math.fsum(area * point[1] for area, point in zip(signed_areas, centers, strict=True) if point) / signed_area)
                      if signed_area else None)
            box = (Box(min(x for x, _ in vertices), min(y for _, y in vertices), max(x for x, _ in vertices), max(y for _, y in vertices))
                   if vertices else Box(0, 0, 0, 0))
            shape = ChartShape(**{**vars(shape), "box": box, "vertices": vertices, "point_count": len(vertices),
                                   "area": abs(signed_area), "centroid": center,
                                   "fill": shape.fill if signed_area else None,
                                   "stroke": shape.stroke if drawn else None}, subpaths=paths, closedSubpaths=closed_paths)
        records.append({"node": node, "ancestors": ancestors, "shape": shape, "order": order[node], "attrs": attrs})
    visit(root, (0, 0, 1), {}, ())
    return records, GroupPrimitives


def _v5_contains(shape, x, y, node=None):
    from services.generation_core.drivers.svg.native_chart_visual import _inside_polygon
    box = shape.box
    if shape.tag in {"polygon", "path", "polyline"}:
        if getattr(shape, "subpaths", ()):
            winding = 0
            for path in shape.subpaths:
                for a, b in zip(path, (*path[1:], path[0]), strict=True):
                    cross = (b[0] - a[0]) * (y - a[1]) - (x - a[0]) * (b[1] - a[1])
                    winding += int(a[1] <= y < b[1] and cross > 0) - int(b[1] <= y < a[1] and cross < 0)
            return bool(shape.fill and winding)
        return bool(shape.fill and len(shape.vertices) >= 3 and _inside_polygon(x, y, shape.vertices))
    if shape.tag in {"circle", "ellipse"}:
        rx, ry = box.width / 2, box.height / 2
        if rx <= 0 or ry <= 0:
            return False
        return ((x - (box.x0 + box.x1) / 2) / rx) ** 2 + ((y - (box.y0 + box.y1) / 2) / ry) ** 2 <= 1
    if shape.tag in {"rect", "image"}:
        if not box.contains(x, y, 0):
            return False
        if shape.tag == "image":
            return True
        scale_x = box.width / float(node.get("width", box.width)) if node is not None and float(node.get("width", box.width)) else 1
        scale_y = box.height / float(node.get("height", box.height)) if node is not None and float(node.get("height", box.height)) else 1
        rx = min(float(node.get("rx", node.get("ry", 0))) * scale_x if node is not None else 0, box.width / 2)
        ry = min(float(node.get("ry", node.get("rx", 0))) * scale_y if node is not None else 0, box.height / 2)
        if rx <= 0 or ry <= 0 or box.x0 + rx <= x <= box.x1 - rx or box.y0 + ry <= y <= box.y1 - ry:
            return True
        cx = box.x0 + rx if x < box.x0 + rx else box.x1 - rx
        cy = box.y0 + ry if y < box.y0 + ry else box.y1 - ry
        return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1
    return False


def _v5_paint_at(record, x, y):
    """Canonical solid fill/stroke at a point, with inherited paint alpha."""
    shape, attrs = record["shape"], record.get("attrs", {})
    if shape.tag == "image" and shape.box.contains(x, y, 0):
        if "_raster" not in record:
            import base64
            import io
            from PIL import Image
            href = record["node"].get("href", record["node"].get("{http://www.w3.org/1999/xlink}href", ""))
            if not re.fullmatch(r"data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]+", href):
                raise NativeRejection("native_chart_raster_paint_invalid")
            try:
                with Image.open(io.BytesIO(base64.b64decode(href.split(",", 1)[1]))) as source:
                    record["_raster"] = source.convert("RGBA")
            except (OSError, ValueError) as error:
                raise NativeRejection("native_chart_raster_paint_invalid") from error
        raster = record["_raster"]
        box = shape.box
        source_width, source_height = raster.size
        if box.width <= 0 or box.height <= 0:
            return None, 0
        aspect = record["node"].get("preserveAspectRatio", "xMidYMid meet").split()
        if aspect and aspect[0] == "none":
            pixel_x, pixel_y = (x - box.x0) / box.width * source_width, (y - box.y0) / box.height * source_height
        else:
            scale = (max if "slice" in aspect else min)(box.width / source_width, box.height / source_height)
            alignment = aspect[0] if aspect else "xMidYMid"
            offset_x = (box.width - source_width * scale) * (0 if "xMin" in alignment else 1 if "xMax" in alignment else .5)
            offset_y = (box.height - source_height * scale) * (0 if "YMin" in alignment else 1 if "YMax" in alignment else .5)
            pixel_x, pixel_y = (x - box.x0 - offset_x) / scale, (y - box.y0 - offset_y) / scale
        if not 0 <= pixel_x < source_width or not 0 <= pixel_y < source_height:
            return None, 0
        red, green, blue, alpha = raster.getpixel((int(pixel_x), int(pixel_y)))
        return f"#{red:02X}{green:02X}{blue:02X}", alpha / 255 * attrs.get("_opacity", 1)
    if shape.stroke and shape.stroke_alpha > 0 and shape.stroke_width > 0:
        box = shape.box
        radius = shape.stroke_width / 2
        if shape.tag in {"circle", "ellipse"}:
            rx, ry = box.width / 2, box.height / 2
            if rx > 0 and ry > 0:
                distance = abs(math.hypot((x - (box.x0 + box.x1) / 2) / rx,
                                          (y - (box.y0 + box.y1) / 2) / ry) - 1) * min(rx, ry)
                if distance <= radius:
                    return shape.stroke, shape.stroke_alpha
        else:
            vertices = shape.vertices
            if shape.tag == "rect":
                vertices = ((box.x0, box.y0), (box.x1, box.y0), (box.x1, box.y1), (box.x0, box.y1), (box.x0, box.y0))
            if shape.tag in {"path", "polygon"} and vertices:
                vertices = (*vertices, vertices[0])
            segments = ([(a, b) for path in shape.subpaths for a, b in zip(path, path[1:], strict=False)]
                        if getattr(shape, "subpaths", ()) else list(zip(vertices, vertices[1:], strict=False)))
            for a, b in segments:
                dx, dy = b[0] - a[0], b[1] - a[1]
                denominator = dx * dx + dy * dy
                position = ((x - a[0]) * dx + (y - a[1]) * dy) / denominator if denominator else 0
                if not 0 <= position <= 1 and attrs.get("stroke-linecap", "butt") != "round":
                    continue
                position = max(0, min(1, position))
                if math.hypot(x - a[0] - position * dx, y - a[1] - position * dy) <= radius:
                    return shape.stroke, shape.stroke_alpha
    if shape.fill and _v5_contains(shape, x, y, record["node"]):
        return shape.fill, shape.fill_alpha
    return None, 0


def _v5_visibility(root, group, records, prims, geometry, *, chart_type=None):
    """Check actual paint ownership, preserving supported alpha and strokes.

    Readability thresholds remain owned by the existing renderer. This only
    rejects hidden/missing chart ink and later solid overpaint; it introduces
    no font-size floor or contrast threshold.
    """
    from services.generation_core.drivers.svg.native_paint import _mix
    canvas = root.get("viewBox", "").split()
    if len(canvas) != 4:
        return "native_chart_canvas_invalid"
    x0, y0, width, height = map(float, canvas)
    if not all(math.isfinite(value) for value in (x0, y0, width, height)) or width <= 0 or height <= 0:
        return "native_chart_canvas_invalid"
    local = [record for record in records if group in record["ancestors"]]
    shapes = [record for record in local if "shape" in record]
    data_nodes = {shapes[index]["node"] for index in geometry.get("usedShapeIndices", [])}

    def visible_bound_marker(index):
        # A same-color trace can hide the marker's center without hiding its
        # off-trace ink. This is source visibility only; capture still proves
        # the entire datum anchor and each independently required incident edge.
        if chart_type not in {"line", "area"}:
            return False
        marker = shapes[index]
        shape = marker["shape"]
        if shape.tag not in {"circle", "ellipse"}:
            return False
        points = [point for point in geometry.get("points", [])
                  if point.get("markerShapeIndex") == index]
        if not points:
            return False
        box = shape.box
        cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        rx, ry = box.width / 2, box.height / 2
        if not all(math.isfinite(value) for value in (cx, cy, rx, ry)) or min(rx, ry) <= 0:
            return False
        traces = []
        for point in points:
            trace_index = point.get("shapeIndex")
            if (type(trace_index) is not int or not 0 <= trace_index < len(shapes)
                    or trace_index == index or abs(point.get("x", math.inf) - cx) > 1e-7
                    or abs(point.get("y", math.inf) - cy) > 1e-7):
                return False
            trace = shapes[trace_index]
            if (trace["order"] >= marker["order"]
                    or trace["shape"].tag not in {"line", "polyline", "path", "polygon"}):
                return False
            color, alpha = _v5_paint_at(trace, cx, cy)
            if color is None or alpha <= 0:
                return False
            traces.append(trace)

        def composite(x, y, *, omit_marker):
            color = (255, 255, 255)
            for layer in records:
                if "shape" not in layer or omit_marker and layer["node"] is marker["node"]:
                    continue
                paint, alpha = _v5_paint_at(layer, x, y)
                if paint is not None and alpha > 0:
                    color = _mix(color, paint, alpha)
            return color

        # Probe a small patch, not an isolated center/bbox/perimeter coincidence.
        # All samples must be actual marker ink, outside every bound trace, and
        # distinguishable in the final original-paint layer composition.
        margin = min(rx, ry) / 64
        for radius in (.5, .75, .95):
            for step in range(16):
                angle = step * math.pi / 8
                x, y = cx + radius * rx * math.cos(angle), cy + radius * ry * math.sin(angle)
                patch = [(x, y), *((x + dx, y + dy) for dx in (-margin, margin) for dy in (-margin, margin))]
                visible = True
                for px, py in patch:
                    paint, alpha = _v5_paint_at(marker, px, py)
                    if (not x0 < px < x0 + width or not y0 < py < y0 + height
                            or paint is None or alpha <= 0
                            or any((lambda ink: ink[0] is not None and ink[1] > 0)(
                                _v5_paint_at(trace, px, py)) for trace in traces)
                            or composite(px, py, omit_marker=False) == composite(px, py, omit_marker=True)):
                        visible = False
                        break
                if visible:
                    return True
        return False

    for record in local:
        text = record.get("text")
        if text is None:
            continue
        if (text.fill is None or record.get("alpha", 1) <= 0 or not text.uniform
                or any(run.fill is None or run.size <= 0 for runs in text.run_lines for run in runs)):
            return "native_chart_content_hidden"
        box = text.box()
        if box.x1 <= x0 or box.y1 <= y0 or box.x0 >= x0 + width or box.y0 >= y0 + height:
            return "native_chart_content_off_canvas"
        for fragment, fragment_color, fragment_alpha in text.paint_fragments:
            behind = (255, 255, 255)
            for other in records:
                shape = other.get("shape")
                if shape is None or other["order"] >= record["order"]:
                    continue
                px, py = (fragment.x0 + fragment.x1) / 2, (fragment.y0 + fragment.y1) / 2
                paint_color, paint_alpha = _v5_paint_at(other, px, py)
                if paint_color:
                    behind = _mix(behind, paint_color, paint_alpha)
            if fragment_color is None or _mix(behind, fragment_color, fragment_alpha) == behind:
                return "native_chart_content_hidden"
        for fragment in text.fragments:
            for other in records:
                shape = other.get("shape")
                if shape is None or other["order"] <= record["order"]:
                    continue
                corners = [(fragment.x0, fragment.y0), (fragment.x1, fragment.y0),
                           (fragment.x0, fragment.y1), (fragment.x1, fragment.y1)]
                if all((lambda ink: ink[0] is not None and ink[0] != text.fill and ink[1] >= 1)(
                        _v5_paint_at(other, x, y)) for x, y in corners):
                    return "native_chart_content_occluded"
    for index in set(geometry.get("usedShapeIndices", [point.get("shapeIndex") for point in geometry.get("points", [])])) | set(geometry.get("furnitureShapeIndices", [])):
        if index is None:
            continue
        record = shapes[index]
        shape = record["shape"]
        if not ((shape.fill and shape.fill_alpha > 0) or (shape.stroke and shape.stroke_alpha > 0 and shape.stroke_width > 0)):
            return "native_chart_data_not_drawn"
        box = shape.box
        if box.x1 < x0 or box.y1 < y0 or box.x0 > x0 + width or box.y0 > y0 + height:
            return "native_chart_content_off_canvas"
        px, py = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        behind = (255, 255, 255)
        for previous in records:
            backdrop = previous.get("shape")
            if backdrop is not None and previous["order"] < record["order"]:
                color, alpha = _v5_paint_at(previous, px, py)
                if color:
                    behind = _mix(behind, color, alpha)
        if all(_mix(behind, color, alpha) == behind for color, alpha in (
                (shape.fill, shape.fill_alpha), (shape.stroke, shape.stroke_alpha)) if color is not None):
            if not visible_bound_marker(index):
                return "native_chart_data_not_drawn"
        # A later full covering paint cannot borrow the hidden original shape.
        for other in records:
            covering = other.get("shape")
            if covering is None or other["order"] <= record["order"]:
                continue
            if chart_type in {"line", "area"} and record["node"] in data_nodes and other["node"] in data_nodes:
                # The capture probe composes the mathematically proved native
                # layer order and retains its owned-covered denominator.
                continue
            if all((lambda ink: ink[0] is not None and ink[0] != (shape.fill or shape.stroke) and ink[1] >= 1)(
                    _v5_paint_at(other, x, y)) for x, y in (
                    (box.x0, box.y0), (box.x1, box.y0), (box.x0, box.y1), (box.x1, box.y1))):
                return "native_chart_data_occluded"
    return None


def _v5_content_geometry(root, group, raw, chart, prims, *, measurement=None, svg=None, native_group_ids=(), check_visibility=True):
    from services.generation_core.drivers.svg.chart_content_cartesian import prove_cartesian
    from services.generation_core.drivers.svg.chart_content_polar import prove_polar
    records, primitive_type = _v5_scene(root, measurement, svg)
    local = [record for record in records if group in record["ancestors"]]
    prims = primitive_type(tuple(record["shape"] for record in local if "shape" in record),
                           tuple(record["text"] for record in local if "text" in record), prims.tags)
    canvas_texts = tuple(record["text"] for record in records if "text" in record
        and group not in record["ancestors"]
        and not any(ancestor.get("id") in native_group_ids for ancestor in record["ancestors"]))
    geometry = (prove_polar(raw, chart, prims) if chart["chartType"] in {"pie", "donut"}
                else prove_cartesian(raw, chart, prims, canvas_texts=canvas_texts))
    if not geometry.get("passed"):
        return geometry
    if len(chart["series"]) > 1 and any(not indices for indices in geometry.get("seriesTextIndices", [])):
        return {"passed": False, "reason": "native_chart_content_series_binding_unproven"}
    if len(chart["series"]) > 1 and len(geometry.get("seriesTextIndices", [])) != len(chart["series"]):
        return {"passed": False, "reason": "native_chart_content_series_binding_unproven"}
    used = set(geometry.get("usedShapeIndices", [])) | set(geometry.get("furnitureShapeIndices", []))
    for point in geometry.get("points", []):
        used.update(index for index in (point.get("shapeIndex"), point.get("markerShapeIndex")) if index is not None)
    plot_shapes = [prims.shapes[index] for index in used if 0 <= index < len(prims.shapes)]
    for index, shape in enumerate(prims.shapes):
        if index in used or not ((shape.fill and shape.fill_alpha > 0) or (shape.stroke and shape.stroke_alpha > 0)):
            continue
        legend = (shape.tag in {"rect", "circle", "ellipse"} and any(
            0 < shape.box.width <= text.size * 2 and 0 < shape.box.height <= text.size * 2
            and 0 <= text.box().x0 - shape.box.x1 <= text.size * 2
            and abs((shape.box.y0 + shape.box.y1 - text.box().y0 - text.box().y1) / 2) <= text.size
            and normalize_text(text.text) in {normalize_text(series["name"]) for series in chart["series"]}
                | {normalize_text(category) for category in chart["categories"]}
            for text in prims.texts))
        backdrop = (shape.tag == "rect" and plot_shapes and index < min(used)
                    and all(shape.box.contains(other.box.x0, other.box.y0, 0)
                            and shape.box.contains(other.box.x1, other.box.y1, 0) for other in plot_shapes))
        if not legend and not backdrop:
            return {"passed": False, "reason": "native_chart_content_extra_data_shape"}
    visibility = _v5_visibility(root, group, records, prims, geometry,
                                chart_type=chart["chartType"]) if check_visibility else None
    if visibility:
        return {"passed": False, "reason": visibility}
    # Bind every visible value occurrence to a datum witness, independently of
    # native label-placement/editability. Calibrated axis ticks are furniture.
    title_labels = {normalize_text(label) for label in chart["categories"]}
    title_labels.update(normalize_text(series["name"]) for series in chart["series"])
    title_labels.update(normalize_text(re.split(r"\s*\(", series["name"], maxsplit=1)[0]) for series in chart["series"])
    title_labels.add(normalize_text(chart.get("title", "")))
    title_labels.update(normalize_text(raw.get(key, {}).get("title", "")) for key in ("categoryAxis", "valueAxis"))
    ticks = set(geometry.get("tickTextIndices", []))
    values = [(index, text, _VALUE.fullmatch(normalize_text(text.text))) for index, text in enumerate(prims.texts)
              if index not in ticks and normalize_text(text.text) not in title_labels]
    numbers = [(index, text, match) for index, text, match in values if match is not None]
    require = raw.get("valueLabels", {}).get("shown") is True or bool(numbers)
    remaining = list(numbers)
    bindings = []
    label_owners = {}
    segment_owned_labels = set()
    if (chart["chartType"] in {"bar", "column"}
            and chart.get("grouping") in {"stacked", "percentStacked"} and require):
        # A number never chooses its own segment. Interior placement is also
        # recorded separately for the optional same-color seam alternative.
        outside_end = raw.get("valueLabels", {}).get("position") == "outsideEnd"
        if outside_end:
            horizontal = chart["chartType"] == "bar"
            cross_key, value_key = ("y", "x") if horizontal else ("x", "y")
            clusters = {category: [] for category in range(len(chart["categories"]))}
            for item in numbers:
                box = item[1].box()
                cross = (box.y0 + box.y1) / 2 if horizontal else (box.x0 + box.x1) / 2
                distances = [(abs(cross - point[cross_key]), point["categoryIndex"]) for point in geometry["points"]]
                nearest = min(distance for distance, _ in distances)
                categories = {category for distance, category in distances if abs(distance - nearest) <= 1e-7}
                if len(categories) != 1:
                    return {"passed": False, "reason": "native_chart_content_value_binding_unproven"}
                clusters[categories.pop()].append(item)
            for category, labels in clusters.items():
                points = sorted((point for point in geometry["points"] if point["categoryIndex"] == category),
                                key=lambda point: point[value_key])
                labels.sort(key=lambda item: (item[1].box().x0 + item[1].box().x1) / 2 if horizontal
                            else (item[1].box().y0 + item[1].box().y1) / 2)
                if len(labels) != len(points):
                    return {"passed": False, "reason": "native_chart_content_value_binding_unproven"}
                positions = [(item[1].box().x0 + item[1].box().x1) / 2 if horizontal
                             else (item[1].box().y0 + item[1].box().y1) / 2 for item in labels]
                if any(abs(a - b) <= 1e-7 for a, b in zip(positions, positions[1:], strict=False)):
                    return {"passed": False, "reason": "native_chart_content_value_binding_unproven"}
                for point, item in zip(points, labels, strict=True):
                    label_owners[item[0]] = (point["seriesIndex"], category)
        for index, text, _match in numbers:
            if outside_end:
                continue
            box = text.box()
            x, y = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
            inside = [point for point in geometry["points"] if point.get("shapeIndex") is not None
                and prims.shapes[point["shapeIndex"]].box.x0 < x < prims.shapes[point["shapeIndex"]].box.x1
                and prims.shapes[point["shapeIndex"]].box.y0 < y < prims.shapes[point["shapeIndex"]].box.y1
                and _v5_contains(prims.shapes[point["shapeIndex"]], x, y)]
            if len(inside) == 1:
                point = inside[0]
                segment_owned_labels.add(index)
            elif len(inside) > 1 or raw.get("valueLabels", {}).get("position") == "center":
                return {"passed": False, "reason": "native_chart_content_value_binding_unproven"}
            else:
                # Undeclared placement still requires an unambiguous anchor.
                distances = [(abs(x - point["x"]) + abs(y - point["y"]), point)
                    for point in geometry["points"] if point.get("x") is not None and point.get("y") is not None]
                nearest = min((distance for distance, _ in distances), default=None)
                candidates = [point for distance, point in distances if abs(distance - nearest) <= 1e-7]
                if len(candidates) != 1:
                    return {"passed": False, "reason": "native_chart_content_value_binding_unproven"}
                point = candidates[0]
            label_owners[index] = (point["seriesIndex"], point["categoryIndex"])
    if chart["chartType"] in {"line", "area"} and len(chart["series"]) > 1 and require:
        # Establish the visible association before inspecting a label's number.
        # A broad filled area is not evidence of ownership of every label in it.
        clusters = {index: [] for index in range(len(chart["categories"]))}
        for item in numbers:
            box = item[1].box()
            center = (box.x0 + box.x1) / 2
            distances = [(abs(center - point["x"]), point["categoryIndex"])
                         for point in geometry["points"] if point.get("x") is not None]
            if not distances:
                return {"passed": False, "reason": "native_chart_content_value_binding_unproven"}
            nearest = min(distance for distance, _ in distances)
            categories = {category for distance, category in distances if abs(distance - nearest) <= 1e-7}
            if len(categories) != 1:
                return {"passed": False, "reason": "native_chart_content_value_binding_unproven"}
            clusters[categories.pop()].append(item)
        colors = geometry.get("seriesColors", [])
        for category_index, labels in clusters.items():
            points = [point for point in geometry["points"] if point["categoryIndex"] == category_index]
            if len(labels) != len(points) or any(point.get("y") is None for point in points):
                return {"passed": False, "reason": "native_chart_content_value_binding_unproven"}
            unassigned = list(points)
            neutral = []
            for item in labels:
                matching = [point for point in points if point["seriesIndex"] < len(colors)
                            and item[1].uniform and colors[point["seriesIndex"]] == item[1].fill
                            and all(color == item[1].fill for _, color, _ in item[1].paint_fragments)]
                if len(matching) == 1:
                    point = matching[0]
                    if point not in unassigned:
                        return {"passed": False, "reason": "native_chart_content_value_binding_unproven"}
                    label_owners[item[0]] = (point["seriesIndex"], category_index)
                    unassigned.remove(point)
                else:
                    neutral.append(item)
            unassigned.sort(key=lambda point: point["y"])
            neutral.sort(key=lambda item: (item[1].box().y0 + item[1].box().y1) / 2)
            for position, (point, item) in enumerate(zip(unassigned, neutral, strict=True)):
                if position and abs(point["y"] - unassigned[position - 1]["y"]) <= 1e-7:
                    previous = neutral[position - 1]
                    if (point["value"] != unassigned[position - 1]["value"]
                            or normalize_text(item[1].text) != normalize_text(previous[1].text)):
                        return {"passed": False, "reason": "native_chart_content_value_binding_unproven"}
                label_owners[item[0]] = (point["seriesIndex"], category_index)
    for point in geometry.get("points", []):
        candidates = []
        for index, text, match in remaining:
            if label_owners and label_owners.get(index) != (point["seriesIndex"], point["categoryIndex"]):
                continue
            number = Decimal(("-" if match[1] in ("-", "−") else "") + match[3].replace(",", "") + (match[4] or ""))
            if number != Decimal(str(point["value"])):
                continue
            box = text.box()
            x, y = point.get("x"), point.get("y")
            shape_index = point.get("shapeIndex")
            shape = prims.shapes[shape_index] if shape_index is not None else None
            cross = (box.y0 + box.y1) / 2 if chart["chartType"] == "bar" else (box.x0 + box.x1) / 2
            datum_cross = y if chart["chartType"] == "bar" else x
            others = [other.get("y") if chart["chartType"] == "bar" else other.get("x")
                      for other in geometry.get("points", []) if other["categoryIndex"] != point["categoryIndex"]]
            if (chart["chartType"] not in {"pie", "donut"} and datum_cross is not None
                    and any(other is not None and abs(cross - other) + .25 < abs(cross - datum_cross) for other in others)):
                continue
            if chart["chartType"] in {"pie", "donut"} and shape is not None:
                cx, cy = geometry["center"]
                rx, ry = geometry["radii"]
                angle = math.atan2(((box.y0 + box.y1) / 2 - cy) / ry,
                                   ((box.x0 + box.x1) / 2 - cx) / rx) % (2 * math.pi)
                category = chart["categories"][point["categoryIndex"]]
                nearby = [label for label in prims.texts if normalize_text(label.text) == normalize_text(category)
                    and abs((box.y0 + box.y1 - label.box().y0 - label.box().y1) / 2) <= text.size
                    and min(abs(box.x0 - label.box().x1), abs(label.box().x0 - box.x1)) <= text.size * 3]
                if (angle - point.get("startAngle", 0)) % (2 * math.pi) <= point.get("sweepAngle", 0) + 1e-6 or nearby:
                    candidates.append((index, text, match))
            elif (shape is not None and chart["chartType"] not in {"line", "area"}
                    and _v5_contains(shape, (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)):
                candidates.append((index, text, match))
            elif x is not None and y is not None:
                near_x = abs((box.x0 + box.x1) / 2 - x) <= max(text.size * 1.5, box.width / 2)
                near_y = abs((box.y0 + box.y1) / 2 - y) <= max(text.size * 2, box.height / 2)
                if (chart["chartType"] == "bar" and near_y and abs(box.x0 - x) <= text.size * 3
                        or chart["chartType"] != "bar" and near_x and abs(box.y1 - y) <= text.size * 3):
                    candidates.append((index, text, match))
            elif point["value"] == 0:
                category = chart["categories"][point["categoryIndex"]]
                category_labels = [label for label in prims.texts if normalize_text(label.text) == normalize_text(category)]
                if (x is not None and abs((box.x0 + box.x1) / 2 - x) <= text.size
                        or x is None and any(abs((box.x0 + box.x1 - label.box().x0 - label.box().x1) / 2) <= text.size for label in category_labels)):
                    candidates.append((index, text, match))
        if require and not candidates:
            return {"passed": False, "reason": "native_chart_content_value_binding_unproven"}
        if candidates:
            chosen = min(candidates, key=lambda item: abs((item[1].box().x0 + item[1].box().x1) / 2 - (point.get("x") or 0))
                         + abs((item[1].box().y0 + item[1].box().y1) / 2 - (point.get("y") or 0)))
            remaining.remove(chosen)
            bindings.append({"seriesIndex": point["seriesIndex"], "categoryIndex": point["categoryIndex"],
                             "unit": chosen[2][2] + chosen[2][5], "textIndex": chosen[0],
                             **({"segmentOwnershipProven": True} if chosen[0] in segment_owned_labels else {})})
    if remaining or any(match is None for _, _, match in values):
        return {"passed": False, "reason": "native_chart_content_text_unaccounted"}
    return {**geometry, "valueLabelsProven": True, "numericLabelsRequired": require,
            "valueLabelBindings": bindings}


def _visible_group(root: ET.Element, group: ET.Element) -> bool:
    parents = {child: parent for parent in root.iter() for child in parent}
    ancestors, node = [], parents.get(group)
    while node is not None:
        if node.tag.rsplit("}", 1)[-1] not in {"svg", "g"}:
            return False
        ancestors.append(node)
        node = parents.get(node)
    # Evidence must not borrow hidden, clipped or CSS-controlled text. Browser
    # completeness/visibility checks still run independently on the whole page.
    for node in [*ancestors, *group.iter()]:
        if (any(node.get(key) not in (None, "none") for key in ("clip-path", "mask", "filter"))
                or any(key in node.attrib for key in ("style", "class"))
                or node.get("display") not in (None, "inline")
                or node.get("visibility") not in (None, "visible")):
            return False
        for key in ("opacity", "fill-opacity"):
            try:
                alpha = float(node.get(key, "1"))
            except ValueError:
                return False
            if not math.isfinite(alpha) or alpha <= 0:
                return False
    return True


def chart_drawing_evidence(payload: dict, svg: str, *, revision: str, measurement: dict | None = None,
                           render_capture=None, post_render: bool = False) -> dict[str, dict]:
    """Read only current bound chart groups; no drawing is inferred from JSON."""
    record, current = bound_declarations(payload, svg)
    if not current or record is None:
        return {}
    root = ET.fromstring(svg)
    evidence = {}
    for declaration in record.get("objects") or []:
        if not isinstance(declaration, dict) or declaration.get("kind") != "chart":
            continue
        group_id = declaration.get("id")
        try:
            chart = _model_chart(declaration.get("raw"))
            found = find_group(root, group_id)
            if found is None or not _visible_group(root, found[0]):
                continue
            from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
            if revision == REQUIREMENTS_VERSION_V5:
                records, primitive_type = _v5_scene(root, measurement, svg)
                local = [entry for entry in records if found[0] in entry["ancestors"]]
                prims = primitive_type(tuple(entry["shape"] for entry in local if "shape" in entry),
                                       tuple(entry["text"] for entry in local if "text" in entry),
                                       frozenset(node.tag.rsplit("}", 1)[-1] for node in found[0].iter()))
            else:
                prims = collect_primitives(*found, CHART_ELEMENTS)
            texts = [normalize_text(" ".join(text.lines)) for text in prims.texts
                     if text.fill is not None and text.size > 0 and all(run.fill is not None and run.size > 0
                         for runs in text.run_lines for run in runs)]
            available = Counter(texts)
            missing = []
            for label in chart["categories"]:
                if available[normalize_text(label)] > 0:
                    available[normalize_text(label)] -= 1
                else:
                    missing.append(label)
            title = chart.get("title")
            if title and normalize_text(title) not in texts:
                missing.append(title)
            for series in chart["series"]:
                try:
                    _drawn_series_name(series["name"], set(texts), revision=revision,
                        declared_title=(title if title and len(chart["series"]) == 1
                                        and texts.count(normalize_text(title)) == 1 else None))
                except NativeRejection:
                    missing.append(series["name"])
            labels = {normalize_text(label) for label in chart["categories"]}
            labels.update(normalize_text(series["name"]) for series in chart["series"])
            if title:
                labels.add(normalize_text(title))
            proof = {"chart": chart, "missingLabels": missing,
                     "valueTexts": [text for text in texts if text not in labels]}
            if revision == REQUIREMENTS_VERSION_V5:
                proof["contentGeometry"] = _v5_content_geometry(root, found[0], declaration["raw"], chart, prims,
                    measurement=measurement, svg=svg,
                    native_group_ids={item.get("id") for item in record.get("objects", []) if isinstance(item, dict)})
            evidence[group_id] = proof
        except (NativeRejection, TypeError, ValueError, AttributeError, KeyError, IndexError, ZeroDivisionError):
            # An internal slip in reading one chart rejects that chart; it never
            # aborts publication of the page.
            continue
    from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
    if revision == REQUIREMENTS_VERSION_V5:
        rendered = post_render or render_capture is not None or isinstance(measurement, dict) and any(key in measurement for key in (
            "measurement_complete", "browser_metrics", "source_digest", "wrapper_digest", "png_digest", "metrics_digest"))
        if rendered:
            from services.generation_core.drivers.svg.chart_content_paint import validate_chart_content_paint
            manifest = chart_witness_render_kwargs(payload, svg, revision=revision).get("chart_witness_manifest")
            paint = validate_chart_content_paint(manifest, measurement, capture=render_capture)
            witnessed = {chart.get("id") for chart in (manifest or {}).get("charts", [])}
        else:
            paint = {"passed": False, "status": "pending", "reason": "native_chart_capture_not_measured"}
            witnessed = None
        for group_id, proof in evidence.items():
            # A chart the manifest could not cover gets its own failure instead of
            # sharing (or spoiling) the page's paint result.
            proof["paintEvidence"] = (dict(paint) if witnessed is None or group_id in witnessed else
                                      {"passed": False, "reason": "native_chart_witness_unavailable"})
    return evidence


def chart_value_differences(evidence: dict, expected: dict, *, strict_units: bool = True) -> dict:
    """Require every value occurrence, with compatible units, inside this group."""
    from services.generation_core.drivers.svg.native_requirements import _unit_key

    units = {_unit_key(label["unit"]) for label in expected.get("requiredUnitLabels", [])}
    # With heterogeneous units, visible text alone cannot establish which
    # series owns a value. Keep the strict native projection requirement.
    ambiguous_units = strict_units and len(units) > 1
    drawn: Counter = Counter()
    incompatible = []
    for text in evidence["valueTexts"]:
        match = _VALUE.fullmatch(text)
        if not match:
            continue
        unit = _unit_key(match[2] + match[5])
        if strict_units and unit and unit not in units:
            incompatible.append(text)
            continue
        value = ("-" if match[1] in ("-", "−") else "") + match[3].replace(",", "") + (match[4] or "")
        drawn[Decimal(value)] += 1
    missing = []
    for series in expected.get("series", [expected]):
        for value in series.get("values", []):
            try:
                number = Decimal(str(value).replace(",", ""))
            except InvalidOperation:
                missing.append(str(value))
                continue
            if ambiguous_units or not number.is_finite() or drawn[number] == 0:
                missing.append(str(value))
            else:
                drawn[number] -= 1
    return {"missingValues": missing, "unexpectedValues": [*incompatible,
            *(str(value) for value, count in drawn.items() for _ in range(count))]}


def chart_projection_diagnostics(summary: dict | None, evidence: dict[str, dict]) -> dict | None:
    """Attach actionable witnesses without changing legacy receipt summaries."""
    if summary is None:
        return None
    dropped = []
    for item in summary.get("dropped", []):
        proof = evidence.get(item.get("id"))
        if item.get("kind") == "chart" and proof is not None:
            item = {**item, "missingLabels": proof["missingLabels"],
                    **chart_value_differences(proof, proof["chart"], strict_units=False)}
            if "contentGeometry" in proof:
                item["contentGeometry"] = proof["contentGeometry"]
        dropped.append(item)
    return {**summary, "dropped": dropped}


def chart_witness_render_kwargs(payload: dict, svg: str, *, revision: str) -> dict:
    """Keep the optional renderer argument entirely absent on legacy paths."""
    try:
        manifest = build_chart_witness_manifest(payload, svg, revision=revision)
    except (NativeRejection, ET.ParseError, TypeError, ValueError, KeyError, AttributeError, IndexError,
            ZeroDivisionError):
        manifest = None
    return {"chart_witness_manifest": manifest} if manifest is not None else {}


def _bar_cross_segment(box, position, horizontal_bar):
    """The segment across a bar at ``position`` (x for horizontal bars, y for columns)."""
    if horizontal_bar:
        return [[position, box.y0], [position, box.y1]]
    return [[box.x0, position], [box.x1, position]]


def build_chart_witness_manifest(payload: dict, svg: str, *, revision: str) -> dict | None:
    """Server-derived chart semantics for the existing same-capture paint probe.

    No caller-supplied overlap permission is consumed. Invalid static geometry
    stays a quality failure; it cannot acquire authority from a paint receipt.
    This builder is absent for every earlier requirement revision.
    """
    from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
    from utils.hashing import canonical_json_digest, sha256_prefixed
    from services.svg_contract import source_digest
    if revision != REQUIREMENTS_VERSION_V5:
        return None
    declaration_record, current = bound_declarations(payload, svg)
    if not current or not declaration_record:
        return None
    declarations = [item for item in declaration_record.get("objects", [])
                    if isinstance(item, dict) and item.get("kind") == "chart"]
    if not declarations:
        return None
    root = ET.fromstring(svg)
    nodes = list(root.iter())
    indexes = {node: index for index, node in enumerate(nodes)}
    parents = {child: parent for parent in nodes for child in parent}
    def qid(node):
        return f"svg-{indexes[node]}"
    tree = [{"qid": qid(node), "parentQid": qid(parents[node]) if node in parents else None,
             "tag": node.tag, "attributes": dict(node.attrib), "text": node.text or "", "tail": node.tail or ""}
            for node in nodes]
    scene, primitive_type = _v5_scene(root)
    referenced = set()
    charts = []
    native_ids = {item.get("id") for item in declaration_record.get("objects", []) if isinstance(item, dict)}

    def owned_text(node):
        return "".join(value for value in [node.text, *(child.tail for child in node)] if value and value.strip())

    for declaration in declarations:
        found = find_group(root, declaration.get("id"))
        if found is None:
            # One unbound or unprovable chart leaves only itself without a witness;
            # the other charts on the page keep their paint proof.
            continue
        group = found[0]
        chart = _model_chart(declaration["raw"])
        local = [record for record in scene if group in record["ancestors"]]
        shapes = [record for record in local if "shape" in record]
        texts = [record for record in local if "text" in record]
        canvas_texts = [record for record in scene if "text" in record and group not in record["ancestors"]
                        and not any(ancestor.get("id") in native_ids for ancestor in record["ancestors"])]
        prims = primitive_type(tuple(record["shape"] for record in shapes), tuple(record["text"] for record in texts),
                               frozenset(node.tag.rsplit("}", 1)[-1] for node in group.iter()))
        geometry = _v5_content_geometry(root, group, declaration["raw"], chart, prims, svg=svg,
                                       native_group_ids=native_ids, check_visibility=False)
        if geometry.get("passed") is not True:
            continue
        chart_id = declaration["id"]
        datums, glyphs, relationships = [], [], []
        primitive_qids = set()
        glyph_targets = []
        datum_ids = {(point["seriesIndex"], point["categoryIndex"]):
                     f"{chart_id}/s{point['seriesIndex']}/c{point['categoryIndex']}" for point in geometry["points"]}

        def boundary_support(point, shape, target_qid, *, chart=chart, geometry=geometry, datum_ids=datum_ids):
            """Actual numeric-determining boundaries, never a fabricated ink box."""
            if shape is None or point.get("geometryAnchorOnly"):
                return []
            datum_id = datum_ids[point["seriesIndex"], point["categoryIndex"]]
            def make_obligation(suffix, segment_list=None, ellipse=None):
                return {"id": f"{datum_id}/{suffix}", "kind": suffix, "primitiveQid": target_qid,
                        "regionRule": "native-alpha-boundary-neighborhood/v1", "radiusPhysicalPixels": 1,
                        "segments": segment_list or [], **({"ellipse": ellipse} if ellipse else {}), "alternatives": []}
            if chart["chartType"] in {"bar", "column"}:
                horizontal_bar = chart["chartType"] == "bar"
                endpoint = point["x"] if horizontal_bar else point["y"]
                result = [make_obligation("value-end", [_bar_cross_segment(shape.box, endpoint, horizontal_bar)])]
                if point.get("baseline") is not None:
                    result.append(make_obligation("value-base",
                        [_bar_cross_segment(shape.box, point["baseline"], horizontal_bar)]))
                return result
            paths = getattr(shape, "subpaths", ()) or (shape.vertices,)
            closed = getattr(shape, "closedSubpaths", ())
            edges = [(a, b) for index, path in enumerate(paths) if len(path) >= 2
                     for a, b in zip(path, (*path[1:], path[0]) if (shape.fill or shape.tag == "polygon"
                         or index < len(closed) and closed[index]) else path[1:], strict=False) if a != b]
            if chart["chartType"] in {"line", "area"}:
                # The family proof already verifies every actual top edge and
                # its full domain. Canonical category-local pieces remove only
                # collinear spelling differences, retaining the actual qid ink.
                row = sorted((other for other in geometry["points"] if other["seriesIndex"] == point["seriesIndex"]),
                             key=lambda other: other["categoryIndex"])
                category_index = point["categoryIndex"]
                result = [make_obligation("value-anchor", [[ [point["x"], point["y"]], [point["x"], point["y"]] ]])]
                for left, right in ((category_index - 1, category_index), (category_index, category_index + 1)):
                    if left < 0 or right >= len(row):
                        continue
                    a, b = row[left], row[right]
                    midpoint = [(a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2]
                    segment = [midpoint, [b["x"], b["y"]]] if right == category_index else [[a["x"], a["y"]], midpoint]
                    result.append(make_obligation(f"incident-edge-{len(result)}", [segment]))
                return result
            cx, cy = geometry["center"]
            rx, ry = geometry["radii"]
            if shape.tag in {"circle", "ellipse"}:
                return [make_obligation("polar-outer-arc", ellipse={"center": [cx, cy], "radii": [rx, ry],
                    "startAngle": 0, "sweepAngle": 2 * math.pi})]
            outer = [[list(a), list(b)] for a, b in edges
                     if all(abs(math.hypot((x - cx) / rx, (y - cy) / ry) - 1) <= max(1e-4, .05 / min(rx, ry)) for x, y in (a, b))]
            return [make_obligation("polar-outer-arc", outer)] if outer else []

        def add_glyph(text_index, role, owners, *, texts=texts):
            if text_index is None or not 0 <= text_index < len(texts):
                return []
            return add_record_glyph(texts[text_index], role, owners)

        def add_record_glyph(record, role, owners, *, glyph_targets=glyph_targets, glyphs=glyphs):
            text_node = record["node"]
            targets = []
            for node in text_node.iter():
                content = owned_text(node)
                if not content:
                    continue
                length = len(content.encode("utf-16-le")) // 2
                target = {"qid": qid(node), "start": 0, "length": length}
                item = {"qid": target["qid"], "ownedTextDigest": sha256_prefixed(content.encode("utf-8")),
                        "ranges": [{"start": 0, "length": length}], "role": role, "datumIds": list(owners)}
                if item not in glyphs:
                    glyphs.append(item)
                if target not in glyph_targets:
                    glyph_targets.append(target)
                targets.append(target)
                referenced.add(node)
            return targets

        for point in geometry["points"]:
            datum_id = datum_ids[point["seriesIndex"], point["categoryIndex"]]
            shape_indices = [index for index in (point.get("shapeIndex"), point.get("markerShapeIndex")) if index is not None]
            sources = [qid(shapes[index]["node"]) for index in dict.fromkeys(shape_indices)]
            primitive_qids.update(sources)
            referenced.update(shapes[index]["node"] for index in shape_indices)
            role = ({"bar": "bar-profile", "column": "bar-profile", "line": "line-trace",
                     "area": "area-boundary", "pie": "polar-sector", "donut": "polar-ring-sector"}[chart["chartType"]]
                    if sources else "zero" if point["value"] == 0 else "degenerate")
            if chart.get("grouping") in {"stacked", "percentStacked"}:
                role = "stack-segment"
            support = {"anchor": {key: point[key] for key in ("x", "y") if point.get(key) is not None}}
            if chart["chartType"] in {"line", "area"} and point.get("x") is not None:
                positions = sorted({other["x"] for other in geometry["points"] if other.get("x") is not None})
                position = positions.index(point["x"])
                support["categoryStrip"] = {
                    "minimumX": (positions[position - 1] + positions[position]) / 2 if position else None,
                    "maximumX": (positions[position] + positions[position + 1]) / 2 if position + 1 < len(positions) else None}
            if point.get("baseline") is not None:
                support["baseline"] = point["baseline"]
            support["visibilityObligations"] = boundary_support(point,
                prims.shapes[point["shapeIndex"]] if point.get("shapeIndex") is not None else None,
                qid(shapes[point["shapeIndex"]]["node"]) if point.get("shapeIndex") is not None else None)
            support["contributionRegions"] = []
            if chart["chartType"] in {"bar", "column"} and point.get("shapeIndex") is not None and point.get("baseline") is not None:
                shape = prims.shapes[point["shapeIndex"]]
                if chart["chartType"] == "bar":
                    interior = [[[point["baseline"], point["y"]], [point["x"], point["y"]]]]
                    sides = [[[point["baseline"], edge], [point["x"], edge]] for edge in (shape.box.y0, shape.box.y1)]
                else:
                    interior = [[[point["x"], point["baseline"]], [point["x"], point["y"]]]]
                    sides = [[[edge, point["baseline"]], [edge, point["y"]]] for edge in (shape.box.x0, shape.box.x1)]
                if not shape.fill or shape.fill_alpha <= 0:
                    interior = sides
                elif shape.stroke and shape.stroke_alpha > 0:
                    interior += sides
                for name, segments in (("interior-profile", interior), ("incident-sides", sides)):
                    support["contributionRegions"].append({"id": f"{datum_id}/{name}", "kind": name,
                        "primitiveQid": qid(shapes[point["shapeIndex"]]["node"]), "segments": segments,
                        "regionRule": "native-alpha-boundary-neighborhood/v1", "radiusPhysicalPixels": 1})
            datums.append({"id": datum_id, "seriesIndex": point["seriesIndex"], "categoryIndex": point["categoryIndex"],
                           "value": str(point["value"]), "primitiveQids": sources, "geometryRole": role,
                           "geometrySupport": support})
            if point.get("markerShapeIndex") is not None:
                relationships.append({"kind": "marker-on-datum", "datumId": datum_id,
                                      "qid": qid(shapes[point["markerShapeIndex"]]["node"])})
        for category_index, text_index in enumerate(geometry.get("categoryTextIndices", [])):
            owners = [identifier for (series_index, index), identifier in datum_ids.items() if index == category_index]
            add_glyph(text_index, "category", owners)
        for binding in geometry.get("valueLabelBindings", []):
            datum_id = datum_ids[binding["seriesIndex"], binding["categoryIndex"]]
            for target in add_glyph(binding["textIndex"], "value", [datum_id]):
                relationships.append({"kind": "value-on-datum", "datumId": datum_id, **target})
        series_indices = geometry.get("seriesTextIndices", [])
        for series_index, series in enumerate(chart["series"]):
            actual_indices = series_indices[series_index] if series_index < len(series_indices) else []
            if isinstance(actual_indices, int):
                actual_indices = [actual_indices]
            if not actual_indices and len(chart["series"]) == 1:
                actual_indices = [index for index, record in enumerate(texts)
                                  if normalize_text(record["text"].text) == normalize_text(series["name"])]
                if len(chart["series"]) == 1 and not actual_indices:
                    actual_indices = [index for index, record in enumerate(texts)
                                      if normalize_text(record["text"].text) == normalize_text(chart.get("title", ""))]
            for text_index in actual_indices:
                add_glyph(text_index, "series", [identifier for (index, _), identifier in datum_ids.items() if index == series_index])
        for text_index, record in enumerate(texts):
            if normalize_text(record["text"].text) == normalize_text(chart.get("title", "")):
                add_glyph(text_index, "title-unit", list(datum_ids.values()))
        for text_index in geometry.get("tickTextIndices", []):
            add_glyph(text_index, "axis", list(datum_ids.values()))
        for text_index in geometry.get("axisTitleTextIndices", []):
            add_glyph(text_index, "title-unit", list(datum_ids.values()))
        for text_index in geometry.get("canvasTextIndices", []):
            if 0 <= text_index < len(canvas_texts):
                role = "title-unit" if text_index in geometry.get("canvasAxisTitleTextIndices", []) else "axis"
                add_record_glyph(canvas_texts[text_index], role, list(datum_ids.values()))
        for index in geometry.get("furnitureShapeIndices", []):
            node = shapes[index]["node"]
            primitive_qids.add(qid(node))
            referenced.add(node)
            if chart["chartType"] == "donut" and node.tag.rsplit("}", 1)[-1] in {"circle", "ellipse"}:
                relationships.append({"kind": "proved-donut-hole", "qid": qid(node)})
            elif geometry.get("calibrated") and node.tag.rsplit("}", 1)[-1] == "line":
                relationships.append({"kind": "calibrated-axis", "qid": qid(node)})
        from services.generation_core.drivers.svg.native_requirements import _series_name_unit, _unit_key
        from decimal import Decimal
        data_qids = {value for datum in datums for value in datum["primitiveQids"]}
        points_by_id = {datum_ids[point["seriesIndex"], point["categoryIndex"]]: point for point in geometry["points"]}
        def primitive_target(datum_id, source_qid, *, chart_id=chart_id):
            return f"{chart_id}/primitive/{source_qid}/{datum_id}"
        def glyph_target(glyph, span, *, chart_id=chart_id):
            return f"{chart_id}/glyph/{glyph['qid']}/{span['start']}/{span['length']}"
        def unit_key(datum, *, chart=chart, declaration=declaration, geometry=geometry):
            name, title = chart["series"][datum["seriesIndex"]]["name"], chart.get("title", "")
            units = {_unit_key(binding["unit"]) for binding in geometry.get("valueLabelBindings", [])
                     if binding["seriesIndex"] == datum["seriesIndex"] and binding["categoryIndex"] == datum["categoryIndex"]
                     and binding.get("unit")}
            paired = [match[1] for match in re.finditer(r"(?<!\w)" + re.escape(name) + r"\s*\(([^()]*)\)", title, re.I)
                      if not any(other["name"] != name and other["name"].casefold().endswith(name.casefold())
                          and title[max(0, match.start() - len(other["name"]) + len(name)):match.start() + len(name)].casefold() == other["name"].casefold()
                          for other in chart["series"])]
            declared = [_series_name_unit(name), *paired,
                        _series_name_unit(title) if title.count("(") <= 1 else None,
                        _series_name_unit(f"({declaration['raw'].get('valueAxis', {}).get('title', '')})")]
            units.update(_unit_key(unit) for unit in declared if unit)
            return next(iter(units)) if len(units) == 1 else "" if not units else None
        axis_key = canonical_json_digest({"chartId": chart_id, "chartType": chart["chartType"],
            "grouping": chart.get("grouping", "clustered"), "valueScale": geometry.get("valueScale"),
            "baseline": geometry.get("baseline"), "axisContract": chart.get("axisContract"),
            "valueAxis": declaration["raw"].get("valueAxis")})
        for datum in datums:
            support = datum["geometrySupport"]
            support["axisKey"] = axis_key
            support["unitKey"] = unit_key(datum)
            support["geometryDigest"] = canonical_json_digest(support)
        for datum in datums:
            point = points_by_id[datum["id"]]
            marker_index, source_index = point.get("markerShapeIndex"), point.get("shapeIndex")
            if marker_index is None or source_index is None:
                continue
            marker_qid, source_qid = qid(shapes[marker_index]["node"]), qid(shapes[source_index]["node"])
            if int(marker_qid[4:]) <= int(source_qid[4:]):
                continue
            for obligation in datum["geometrySupport"]["visibilityObligations"]:
                if obligation["primitiveQid"] == source_qid:
                    obligation["alternatives"].append({"kind": "marker-on-datum",
                        "markerTargetId": primitive_target(datum["id"], marker_qid), "markerQid": marker_qid,
                        "geometrySupportDigest": datum["geometrySupport"]["geometryDigest"],
                        "requiresEntireBoundaryCoverage": True, "requiresOriginalContribution": True})

        # Equivalence is semantic and local: a shared point cannot lend a
        # different-valued incident edge its paint. The class must terminate
        # in actual joint/original contribution, never mutual borrowing.
        classes = {}
        for datum in datums:
            for obligation in datum["geometrySupport"]["visibilityObligations"]:
                if unit_key(datum) is None:
                    continue
                key = canonical_json_digest({"categoryIndex": datum["categoryIndex"], "value": datum["value"],
                    "unitKey": unit_key(datum), "axisKey": axis_key,
                    "kind": "incident-edge" if obligation["kind"].startswith("incident-edge") else obligation["kind"],
                    "segments": sorted(sorted(segment) for segment in obligation["segments"]),
                    "ellipse": obligation.get("ellipse")})
                classes.setdefault(key, []).append((datum, obligation))
        equivalence_classes = []
        for digest, members in classes.items():
            if len({datum["id"] for datum, _ in members}) < 2:
                continue
            class_id = f"{chart_id}/equivalence/{digest[7:]}"
            member_records = [{"datumId": datum["id"], "targetId": primitive_target(datum["id"], obligation["primitiveQid"]),
                "supportId": obligation["id"], "geometrySupportDigest": datum["geometrySupport"]["geometryDigest"]}
                for datum, obligation in members]
            equivalence_classes.append({"id": class_id, "axisKey": axis_key, "unitKey": unit_key(members[0][0]),
                "categoryIndex": members[0][0]["categoryIndex"], "value": members[0][0]["value"],
                "members": member_records, "requiresJointOriginalContribution": True})
            for _, obligation in members:
                obligation["alternatives"].append({"kind": "equivalent-native-support", "classId": class_id})

        for datum in datums:
            point = points_by_id[datum["id"]]
            for obligation in datum["geometrySupport"]["visibilityObligations"]:
                if chart.get("grouping") in {"stacked", "percentStacked"}:
                    for neighbor in datums:
                        if (unit_key(datum) is None or unit_key(neighbor) != unit_key(datum)
                                or neighbor["categoryIndex"] != datum["categoryIndex"]
                                or abs(neighbor["seriesIndex"] - datum["seriesIndex"]) != 1):
                            continue
                        for shared in neighbor["geometrySupport"]["visibilityObligations"]:
                            if obligation["segments"] != shared["segments"]:
                                continue
                            amount = sum(Decimal(str(series["values"][datum["categoryIndex"]]))
                                         for series in chart["series"][:max(datum["seriesIndex"], neighbor["seriesIndex"])] )
                            if chart["grouping"] == "percentStacked":
                                amount /= sum(Decimal(str(series["values"][datum["categoryIndex"]])) for series in chart["series"])
                            obligation["alternatives"].append({"kind": "shared-stack-interface",
                                "neighborDatumId": neighbor["id"], "neighborTargetId": primitive_target(neighbor["id"], shared["primitiveQid"]),
                                "neighborSupportId": shared["id"], "geometrySupportDigest": neighbor["geometrySupport"]["geometryDigest"],
                                "cumulativeValue": str(amount), "grouping": chart["grouping"], "axisKey": axis_key,
                                "datumInteriorSupportId": f"{datum['id']}/interior-profile",
                                "neighborInteriorSupportId": f"{neighbor['id']}/interior-profile",
                                "requiresBothInteriorContribution": True, "requiresBoundaryContrast": True})
                            owned = {(binding["seriesIndex"], binding["categoryIndex"])
                                     for binding in geometry.get("valueLabelBindings", [])
                                     if binding.get("segmentOwnershipProven") is True}
                            def value_glyph_ids(owner, *, glyphs=glyphs):
                                return [glyph_target(glyph, span) for glyph in glyphs
                                        if glyph["role"] == "value" and glyph["datumIds"] == [owner["id"]]
                                        for span in glyph["ranges"]]
                            # Same-color paint alone never supplies a series name.
                            # The existing family proof also binds visible named
                            # series order to the actual cumulative stack order.
                            ordered_names = (geometry.get("visibleLegendOrder") == list(range(len(chart["series"])))
                                and all(geometry.get("seriesTextIndices", [])))
                            if (ordered_names and (datum["seriesIndex"], datum["categoryIndex"]) in owned
                                    and (neighbor["seriesIndex"], neighbor["categoryIndex"]) in owned
                                    and value_glyph_ids(datum) and value_glyph_ids(neighbor)):
                                category_datums = sorted((item for item in datums
                                    if item["categoryIndex"] == datum["categoryIndex"]), key=lambda item: item["seriesIndex"])
                                def endpoint_reference(owner, kind):
                                    endpoint = next(item for item in owner["geometrySupport"]["visibilityObligations"]
                                                    if item["kind"] == kind)
                                    return {"datumId": owner["id"], "targetId": primitive_target(owner["id"], endpoint["primitiveQid"]),
                                        "supportId": endpoint["id"], "geometrySupportDigest": owner["geometrySupport"]["geometryDigest"]}
                                obligation["alternatives"].append({"kind": "bound-stack-value-glyphs",
                                    "neighborDatumId": neighbor["id"], "neighborTargetId": primitive_target(neighbor["id"], shared["primitiveQid"]),
                                    "neighborSupportId": shared["id"], "geometrySupportDigest": neighbor["geometrySupport"]["geometryDigest"],
                                    "cumulativeValue": str(amount), "grouping": chart["grouping"], "axisKey": axis_key,
                                    "unitKey": unit_key(datum), "datumValueGlyphTargetIds": value_glyph_ids(datum),
                                    "neighborValueGlyphTargetIds": value_glyph_ids(neighbor),
                                    "datumInteriorSupportId": f"{datum['id']}/interior-profile",
                                    "neighborInteriorSupportId": f"{neighbor['id']}/interior-profile",
                                    "stackBaseline": endpoint_reference(category_datums[0], "value-base"),
                                    "stackOuterTotal": endpoint_reference(category_datums[-1], "value-end")})
                if unit_key(datum) is not None and geometry.get("calibrated") and chart["chartType"] in {"bar", "column"}:
                    coordinate = obligation["segments"][0][0][0 if chart["chartType"] == "bar" else 1]
                    for grid_index in geometry.get("furnitureShapeIndices", []):
                        grid = prims.shapes[grid_index]
                        grid_coordinate = grid.box.x0 if chart["chartType"] == "bar" else grid.box.y0
                        is_line = grid.tag in {"line", "path", "polyline"} and grid.stroke
                        flat = grid.box.width == 0 if chart["chartType"] == "bar" else grid.box.height == 0
                        if not is_line or not flat or abs(grid_coordinate - coordinate) > 1e-9:
                            continue
                        expected_value = Decimal(str(point["value"])) if obligation["kind"] == "value-end" else Decimal(0)
                        if chart.get("grouping") in {"stacked", "percentStacked"}:
                            stop = datum["seriesIndex"] + (obligation["kind"] == "value-end")
                            expected_value = sum((Decimal(str(series["values"][datum["categoryIndex"]]))
                                                  for series in chart["series"][:stop]), Decimal(0))
                            if chart["grouping"] == "percentStacked":
                                total = sum(Decimal(str(series["values"][datum["categoryIndex"]])) for series in chart["series"])
                                tick_values = [binding["value"] for binding in geometry.get("tickSourceBindings", [])]
                                expected_value = expected_value / total * (100 if any(value is not None and abs(value) > 1 for value in tick_values) else 1)
                        ticks = []
                        for binding in geometry.get("tickSourceBindings", []):
                            if (Decimal(str(binding["value"])) != expected_value
                                    or abs(binding["position"] - grid_coordinate) > 1e-9):
                                continue
                            source = canvas_texts if binding["scope"] == "canvas" else texts
                            source_node = source[binding["textIndex"]]["node"]
                            owned_qids = {qid(node) for node in source_node.iter()}
                            ticks.extend(glyph_target(glyph, span) for glyph in glyphs
                                         if glyph["role"] == "axis" and glyph["qid"] in owned_qids for span in glyph["ranges"])
                        if ticks:
                            grid_qid = qid(shapes[grid_index]["node"])
                            obligation["alternatives"].append({"kind": "calibrated-value-line", "gridQid": grid_qid,
                                "gridTargetId": f"{chart_id}/primitive/{grid_qid}/furniture", "tickTargetIds": ticks,
                                "titleTargetIds": [glyph_target(glyph, span) for glyph in glyphs if glyph["role"] == "title-unit" for span in glyph["ranges"]],
                                "value": str(expected_value), "unitKey": unit_key(datum), "axisKey": axis_key,
                                "datumInteriorSupportId": f"{datum['id']}/interior-profile",
                                "datumSideSupportId": f"{datum['id']}/incident-sides",
                                "requiresDatumInteriorContribution": True, "requiresIncidentSideContribution": True})

        def local_overlap(datum, target_qid, occluder_qid):
            return {"datumId": datum["id"], "targetQid": target_qid, "occluderQid": occluder_qid,
                "geometrySupportDigest": datum["geometrySupport"]["geometryDigest"],
                "regionRule": "native-alpha-intersection-in-datum-support/v1",
                "geometrySupport": {key: value for key, value in datum["geometrySupport"].items()
                                    if key in {"anchor", "categoryStrip", "baseline"}}}
        if chart["chartType"] in {"line", "area"} and len(chart["series"]) > 1:
            relationships.append({"kind": "native-series-layer-order", "primitiveQids": sorted(data_qids, key=lambda value: int(value[4:])),
                "localOverlaps": [local_overlap(datum, target_qid, other) for datum in datums
                    for target_qid in datum["primitiveQids"] for other in data_qids
                    if int(other[4:]) > int(target_qid[4:])]})
        for relation in relationships:
            if relation["kind"] in {"calibrated-axis", "proved-donut-hole"}:
                relation["localOverlaps"] = [local_overlap(datum, target_qid, relation["qid"]) for datum in datums
                    for target_qid in datum["primitiveQids"] if int(relation["qid"][4:]) > int(target_qid[4:])]
        if chart.get("grouping") in {"stacked", "percentStacked"}:
            relationships.append({"kind": "proved-stack-adjacency", "localOverlaps": [local_overlap(datum, target_qid, other_qid)
                for datum in datums for neighbor in datums if neighbor["categoryIndex"] == datum["categoryIndex"]
                and abs(neighbor["seriesIndex"] - datum["seriesIndex"]) == 1
                for target_qid in datum["primitiveQids"] for other_qid in neighbor["primitiveQids"]
                if int(other_qid[4:]) > int(target_qid[4:])]})
        if chart["chartType"] in {"pie", "donut"}:
            from services.generation_core.drivers.svg.chart_content_polar import prove_polar_fill_adjacencies
            separators = []
            by_category = {datum["categoryIndex"]: datum for datum in datums}
            for topology in prove_polar_fill_adjacencies(prims, geometry):
                first, second = [by_category[index] for index in topology["categoryIndices"]]
                if unit_key(first) is None or unit_key(first) != unit_key(second):
                    continue
                for datum, neighbor in ((first, second), (second, first)):
                    for target_qid in datum["primitiveQids"]:
                        for occluder_qid in neighbor["primitiveQids"]:
                            if int(occluder_qid[4:]) <= int(target_qid[4:]):
                                continue
                            separators.append({**local_overlap(datum, target_qid, occluder_qid),
                                "occluderPaint": "all", "neighborDatumId": neighbor["id"],
                                "neighborGeometrySupportDigest": neighbor["geometrySupport"]["geometryDigest"],
                                "axisKey": axis_key, "unitKey": unit_key(datum),
                                "polarTopology": topology})
            if separators:
                relationships.append({"kind": "proved-polar-adjacency", "localOverlaps": separators})
        referenced.add(group)
        charts.append({"id": chart_id, "groupQid": qid(group), "chartType": chart["chartType"],
            "geometryEvidenceDigest": canonical_json_digest({"chart": chart, "geometry": geometry}),
            "datums": datums, "glyphs": glyphs, "compositionGroup": {
                "id": f"{chart_id}/composition", "primitiveQids": sorted(primitive_qids, key=lambda value: int(value[4:])),
                "glyphTargets": glyph_targets, "relationships": relationships,
                "equivalenceClasses": equivalence_classes}})
    if not charts:
        return None
    manifest = {"schema": "svg-native-chart-witness/v1", "requirementsRevision": revision,
        "canonicalSvgDigest": source_digest(svg),
        "treeDerivativeDigest": canonical_json_digest({"schema": "svg-chart-node-tree/v1", "nodes": tree}),
        "charts": charts,
        "nodeBindings": [{"qid": qid(node), "parentQid": qid(parents[node]) if node in parents else None,
                          "tag": node.tag.rsplit("}", 1)[-1], "attributesDigest": canonical_json_digest(dict(node.attrib)),
                          **({"ownedTextDigest": sha256_prefixed(owned_text(node).encode("utf-8"))} if owned_text(node) else {})}
                         for node in nodes if node in referenced]}
    manifest["manifestDigest"] = canonical_json_digest(manifest)
    return manifest
