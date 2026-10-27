"""Read chart text roles from the exact SVG being replaced by a native chart."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
from statistics import median
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.native_chart import CHART_ELEMENTS
from services.generation_core.drivers.svg.native_chart_visual import _bar_matrix, _category_texts, _value_texts
from services.generation_core.drivers.svg.native_geometry import (
    Box, NativeRejection, collect_primitives, find_group, is_bold, normalize_text, paint, parse_label_number,
)


def _relative_layout(box, anchor) -> dict | None:
    values = ((box.x0 - anchor.x) / anchor.width, (box.y0 - anchor.y) / anchor.height,
              box.width / anchor.width, box.height / anchor.height)
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in values):
        return None
    x, y, width, height = values
    if width <= 0 or height <= 0 or x + width > 1 + 1e-6 or y + height > 1 + 1e-6:
        return None
    return dict(zip(("x", "y", "w", "h"), values, strict=True))


def _source_grid_box(lines, plots, baseline, horizontal):
    """Require a baseline and equally spaced, coextensive source grid lines."""
    bounds = plots[0].box
    for shape in plots[1:]:
        bounds = bounds.union(shape.box)
    parallel = [line.box for line in lines if (
        line.box.width <= 1 and line.box.height >= bounds.height * .8 if horizontal else
        line.box.height <= 1 and line.box.width >= bounds.width * .8)]
    baselines = [box for box in parallel if abs((box.x0 if horizontal else box.y0) - baseline) <= 1]
    if len(baselines) != 1:
        return None
    base = baselines[0]
    matching = [box for box in parallel if (
        abs(box.y0 - base.y0) <= 1 and abs(box.y1 - base.y1) <= 1 if horizontal else
        abs(box.x0 - base.x0) <= 1 and abs(box.x1 - base.x1) <= 1)]
    distances = sorted({box.x0 - baseline if horizontal else baseline - box.y0 for box in matching})
    if len(distances) < 3 or abs(distances[0]) > 1:
        return None
    step = median(right - left for left, right in zip(distances, distances[1:], strict=False))
    if step <= 0 or any(abs(right - left - step) > max(1, step * .01)
                        for left, right in zip(distances, distances[1:], strict=False)):
        return None
    if horizontal:
        box = Box(baseline, base.y0, baseline + distances[-1], base.y1)
    else:
        box = Box(base.x0, baseline - distances[-1], base.x1, baseline)
    return box, len(distances) - 1


def _proportional_bar_plot(projected, item, plots, categories, axes, grid_lines=(), source_lines=()):
    """Only infer a zero baseline from a single series with one drawn scale."""
    if projected["type"] not in {"bar", "column"} or len(item.payload["series"]) != 1:
        return
    values = item.payload["series"][0]["values"]
    if (len(plots) != len(values) or len(categories) < 2 or len(set(values)) < 2
            or any(value <= 0 for value in values)):
        return
    horizontal = projected["type"] == "bar"
    baseline = [shape.box.x0 if horizontal else shape.box.y1 for shape in plots]
    lengths = [shape.box.width if horizontal else shape.box.height for shape in plots]
    scale = max(lengths) / max(values)
    tolerance = max(1.0, max(lengths) * .01)
    if max(baseline) - min(baseline) > 1 or any(abs(length - scale * value) > tolerance
                                              for length, value in zip(lengths, values, strict=True)):
        return
    centers = sorted((text.box().y0 + text.box().y1) / 2 if horizontal else text.points[0][0]
                     for text in categories)
    spacing = median(right - left for left, right in zip(centers, centers[1:], strict=False))
    if spacing <= 0 or any(abs(right - left - spacing) > 1 for left, right in zip(centers, centers[1:], strict=False)):
        return
    thickness = median(shape.box.height if horizontal else shape.box.width for shape in plots)
    if thickness <= 0 or thickness > spacing:
        return
    if horizontal:
        box = Box(baseline[0], centers[0] - spacing / 2, baseline[0] + max(lengths), centers[-1] + spacing / 2)
    else:
        box = Box(centers[0] - spacing / 2, baseline[0] - max(lengths), centers[-1] + spacing / 2, baseline[0])
    maximum, major_unit = max(values), None
    if grid_lines:
        grid = _source_grid_box(grid_lines, plots, baseline[0], horizontal)
        if grid is None:
            return
        box, intervals = grid
        scale = median(length / value for length, value in zip(lengths, values, strict=True))
        if any(abs(length - scale * value) > tolerance for length, value in zip(lengths, values, strict=True)):
            return
        extent = box.width if horizontal else box.height
        maximum = extent / scale
        if not math.isfinite(maximum) or maximum < max(values):
            return
        major_unit = maximum / intervals
        spacing = (box.height if horizontal else box.width) / len(categories)
    elif source_lines:
        # A drawn baseline resolves small category-cell estimates beyond the
        # frozen group bounds. Its endpoints must agree with those cells and
        # cover every bar; arbitrary bounding-box clipping is not evidence.
        bounds = plots[0].box
        for shape in plots[1:]:
            bounds = bounds.union(shape.box)
        edge_tolerance = max(1, spacing * .03)
        candidates = []
        for line in source_lines:
            drawn = line.box
            if horizontal:
                valid = (drawn.width <= 1 and abs(drawn.x0 - baseline[0]) <= 1
                         and drawn.y0 <= bounds.y0 and drawn.y1 >= bounds.y1
                         and abs(drawn.y0 - box.y0) <= edge_tolerance
                         and abs(drawn.y1 - box.y1) <= edge_tolerance)
                candidate = Box(box.x0, drawn.y0, box.x1, drawn.y1)
            else:
                valid = (drawn.height <= 1 and abs(drawn.y0 - baseline[0]) <= 1
                         and drawn.x0 <= bounds.x0 and drawn.x1 >= bounds.x1
                         and abs(drawn.x0 - box.x0) <= edge_tolerance
                         and abs(drawn.x1 - box.x1) <= edge_tolerance)
                candidate = Box(drawn.x0, box.y0, drawn.x1, box.y1)
            if valid:
                candidates.append(candidate)
        if len(candidates) == 1:
            box = candidates[0]
            spacing = (box.height if horizontal else box.width) / len(categories)
    layout = _relative_layout(box, item.anchor)
    if layout is not None:
        projected["plot_layout"] = layout
        axes["value"].update(minimum=0, maximum=maximum)
        if major_unit is not None:
            axes["value"]["major_unit"] = major_unit
        projected["gap_width"] = max(0, min(500, round((spacing / thickness - 1) * 100)))


def _proven_grid_underlay(root, item, plots, grid_lines, proven_lines):
    """Copy only uniquely proven chart-local lines that were painted before bars."""
    selected = [line for line in proven_lines if line in grid_lines]
    if not selected:
        return None
    found = find_group(root, item.id)
    if found is None:
        raise NativeRejection("native_chart_grid_underlay_unproven")
    group, offset, inherited = found
    prims = collect_primitives(group, offset, inherited, CHART_ELEMENTS)
    nodes = []

    def leaves(node):
        for child in node:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "g":
                leaves(child)
            elif tag != "text":
                nodes.append(child)

    leaves(group)
    if len(nodes) != len(prims.shapes):
        raise NativeRejection("native_chart_grid_underlay_unproven")
    selected_nodes, selected_positions = set(), []
    for line in selected:
        matches = [i for i, shape in enumerate(prims.shapes) if shape == line]
        if len(matches) != 1 or nodes[matches[0]].tag.rsplit("}", 1)[-1] != "line":
            raise NativeRejection("native_chart_grid_underlay_unproven")
        selected_positions.append(matches[0])
        selected_nodes.add(nodes[matches[0]])
    bars = [i for i, shape in enumerate(prims.shapes) if shape in plots]
    if (len(selected_nodes) != len(selected) or len(bars) != len(plots)
            or not bars or max(selected_positions) >= min(bars)):
        raise NativeRejection("native_chart_grid_underlay_unproven")

    def retained(node):
        if node in selected_nodes:
            result = deepcopy(node)
        elif node.tag.rsplit("}", 1)[-1] == "g":
            result = ET.Element(node.tag, dict(node.attrib))
            result.extend(copy for child in node if (copy := retained(child)) is not None)
            if not len(result):
                return None
        else:
            return None
        for key in list(result.attrib):
            if key == "id" or key.startswith(("data-native-", "data-pptx-")):
                del result.attrib[key]
        return result

    return retained(group)


def _retained_axis_plot(projected, item, root, plots, categories, axes, grid_lines=(),
                        source_underlays=None):
    """Bind retained ticks to proven sibling or chart-local grid lines."""
    if projected["type"] not in {"bar", "column"} or len(item.payload["series"]) != 1:
        return None
    values = item.payload["series"][0]["values"]
    if (len(plots) != len(values) or len(categories) < 2 or len(set(values)) < 2
            or any(value <= 0 for value in values) or any(not text.uniform for text in categories)):
        return None  # Preserve the established mixed/negative and ambiguous cases.
    horizontal = projected["type"] == "bar"
    bases = [shape.box.x0 if horizontal else shape.box.y1 for shape in plots]
    lengths = [shape.box.width if horizontal else shape.box.height for shape in plots]
    scale = median(length / value for length, value in zip(lengths, values, strict=True))
    tolerance = max(1, max(lengths) * .01)
    if (max(bases) - min(bases) > 1 or any(abs(length - scale * value) > tolerance
            for length, value in zip(lengths, values, strict=True))):
        return None
    centers = [(shape.box.y0 + shape.box.y1) / 2 if horizontal else
               (shape.box.x0 + shape.box.x1) / 2 for shape in plots]
    label_centers = [(text.box().y0 + text.box().y1) / 2 if horizontal else text.points[0][0]
                     for text in categories]
    if any(abs(left - right) > 1 for left, right in zip(centers, label_centers, strict=True)):
        return None
    ordered = sorted(centers)
    spacing = median(right - left for left, right in zip(ordered, ordered[1:], strict=False))
    thickness = median(shape.box.height if horizontal else shape.box.width for shape in plots)
    if (spacing <= 0 or not 0 < thickness <= spacing or any(abs(right - left - spacing) > 1
            for left, right in zip(ordered, ordered[1:], strict=False))):
        return None
    # Exclude all chart/table drawings, and unsupported decorative primitives,
    # while retaining sibling group transforms and inherited line/text paint.
    siblings = ET.fromstring(ET.tostring(root))
    def prune(node):
        for child in list(node):
            if (child.get("id") == item.id or child.get("id", "").startswith("native-")
                    or child.tag.rsplit("}", 1)[-1] not in {"g", "line", "text"}):
                node.remove(child)
            elif child.tag.rsplit("}", 1)[-1] == "g":
                prune(child)
    prune(siblings)
    try:
        furniture = collect_primitives(siblings, (0, 0), {}, CHART_ELEMENTS)
        canvas_width, canvas_height = (float(root.get(key, "0")) for key in ("width", "height"))
    except (NativeRejection, ValueError):
        return None
    low, high = ordered[0] - spacing / 2, ordered[-1] + spacing / 2
    candidates, proven_lines = [], []
    # Numeric ticks can be siblings while their explicit grid lines belong to
    # the native chart group. Group membership does not change their scale;
    # retain all existing numeric, alignment, uniqueness and bounds checks.
    for line in (*furniture.shapes, *grid_lines):
        box = line.box
        start, end = (box.y0, box.y1) if horizontal else (box.x0, box.x1)
        position = box.x0 if horizontal else box.y0
        if (not line.stroke or line.stroke_alpha <= 0
                or (box.width if horizontal else box.height) > 1
                or not low - spacing * .25 <= start <= low + 1
                or not high - 1 <= end <= high + spacing * .25):
            continue
        value = (position - bases[0] if horizontal else bases[0] - position) / scale
        ticks = []
        for text in furniture.texts:
            number = parse_label_number(text.text)
            if number is None or not text.uniform or abs(number - value) * scale > 1:
                continue
            text_box = text.box()
            nearby = (0 <= text_box.y0 - end <= text.size * 3 and
                      abs(text.points[0][0] - position) <= text.size * .75 if horizontal else
                      0 <= start - text_box.x1 <= text.size * 3 and
                      abs(text.points[0][1] - position) <= text.size * .75)
            if nearby:
                ticks.append(number)
        if len(ticks) == 1 and ticks[0] >= 0:
            candidates.append((line.box, ticks[0]))
            proven_lines.append(line)
    if (len(candidates) < 3 or len({value for _, value in candidates}) != len(candidates)
            or sum(value == 0 for _, value in candidates) != 1):
        return None
    reference = candidates[0][0]
    if any(abs((box.y0 if horizontal else box.x0) - (reference.y0 if horizontal else reference.x0)) > 1
           or abs((box.y1 if horizontal else box.x1) - (reference.y1 if horizontal else reference.x1)) > 1
           for box, _ in candidates):
        return None
    maximum = max(value for _, value in candidates)
    if maximum < max(values):
        return None
    plot = (Box(bases[0], low, bases[0] + scale * maximum, high) if horizontal else
            Box(low, bases[0] - scale * maximum, high, bases[0]))
    anchor = item.anchor
    frame = Box(anchor.x, anchor.y, anchor.x + anchor.width, anchor.y + anchor.height).union(plot)
    if not (0 <= frame.x0 < frame.x1 <= canvas_width and 0 <= frame.y0 < frame.y1 <= canvas_height):
        return None
    source = _editable_source_legend(categories, (), anchor)
    if source is None:
        return None
    underlay = _proven_grid_underlay(root, item, plots, grid_lines, proven_lines)
    if underlay is not None and source_underlays is not None:
        source_underlays.append(underlay)
    # Rebase every pre-existing layout when the proven category cells extend
    # beyond the frozen primitive-only frame; do not mutate the saved sidecar.
    title = projected.get("title")
    for layout in (title.get("layout") if isinstance(title, dict) else None,
                   projected.get("style", {}).get("legend_layout")):
        if layout is not None:
            layout["x"] = (anchor.x + layout["x"] * anchor.width - frame.x0) / frame.width
            layout["y"] = (anchor.y + layout["y"] * anchor.height - frame.y0) / frame.height
            layout["w"] *= anchor.width / frame.width
            layout["h"] *= anchor.height / frame.height
    projected.update(x=frame.x0, y=frame.y0, width=frame.width, height=frame.height,
                     plot_layout=dict(x=(plot.x0 - frame.x0) / frame.width,
                                      y=(plot.y0 - frame.y0) / frame.height,
                                      w=plot.width / frame.width, h=plot.height / frame.height),
                     gap_width=max(0, min(500, round((spacing / thickness - 1) * 100))))
    axes["value"].update(minimum=0, maximum=maximum, visible=False, label_position="none", major_gridlines=False)
    axes["category"].update(visible=False, label_position="none", major_gridlines=False)
    return source


def _editable_source_legend(texts, markers, anchor):
    """Use ordinary native text/shapes when Office replaces a series legend label."""
    if any(not text.uniform or _relative_layout(text.box(), anchor) is None for text in texts):
        return None
    if any(_relative_layout(marker.box, anchor) is None for marker in markers):
        return None
    group = ET.Element("{http://www.w3.org/2000/svg}g")
    for marker in markers:
        box = marker.box
        if marker.tag == "circle":
            if abs(box.width - box.height) > .001:
                return None
            geometry = {"cx": str((box.x0 + box.x1) / 2), "cy": str((box.y0 + box.y1) / 2),
                        "r": str(box.width / 2)}
        else:
            geometry = {"x": str(box.x0), "y": str(box.y0), "width": str(box.width), "height": str(box.height)}
        ET.SubElement(group, "{http://www.w3.org/2000/svg}" + marker.tag, {
            **geometry, "fill": marker.fill or "none", "fill-opacity": str(marker.fill_alpha),
            "stroke": marker.stroke or "none", "stroke-opacity": str(marker.stroke_alpha),
            "stroke-width": str(marker.stroke_width),
        })
    for text in texts:
        node = ET.SubElement(group, "{http://www.w3.org/2000/svg}text", {
            "font-family": text.family, "font-size": str(text.size), "font-weight": text.weight,
            "fill": text.fill or "#000000", "text-anchor": text.anchor,
        })
        for line, (x, y) in zip(text.lines, text.points, strict=True):
            ET.SubElement(node, "{http://www.w3.org/2000/svg}tspan", {"x": str(x), "y": str(y)}).text = line
    return group


def _reserve_automatic_source_furniture(projected, item, prims, legend, markers, title_text):
    """Separate proven top furniture from an otherwise automatic chart frame."""
    if len(legend) != 1 or len(markers) != 1:
        return None
    title = normalize_text(item.payload.get("title") or "")
    title_texts = [text for text in prims.texts if title and normalize_text(text.text) == title]
    if title and (len(title_texts) != 1 or title_text is None):
        return None
    furniture = [*title_texts, *legend]
    source = _editable_source_legend(furniture, markers, item.anchor)
    if source is None:
        return None
    furniture_ids = {id(text) for text in furniture}
    marker_ids = {id(marker) for marker in markers}
    anchor = item.anchor
    remaining = [text.box() for text in prims.texts if id(text) not in furniture_ids]
    for shape in prims.shapes:
        if id(shape) in marker_ids:
            continue
        box = shape.box
        if (shape.tag == "rect" and shape.fill and shape.fill_alpha >= .99
                and box.x0 <= anchor.x and box.y0 <= anchor.y
                and box.x1 >= anchor.x + anchor.width and box.y1 >= anchor.y + anchor.height):
            continue  # A background covering the entire frozen chart frame.
        remaining.append(box)
    if not remaining:
        return None
    top = max([text.box().y1 for text in furniture] + [marker.box.y1 for marker in markers])
    bottom = anchor.y + anchor.height
    if not anchor.y < top < bottom:
        return None
    # Check every surviving primitive, including numeric axes, value labels and
    # grid lines. No plot extent, value range or missing whitespace is inferred.
    if any(box.x0 < anchor.x or box.x1 > anchor.x + anchor.width
           or box.y0 <= top or box.y1 > bottom for box in remaining):
        return None
    projected.update(y=top, height=bottom - top)
    projected.pop("title", None)
    projected.pop("title_reserve_space", None)
    style = projected.get("style", {})
    style["show_legend"] = False
    style.pop("legend_layout", None)
    style.pop("legend_overlay", None)
    return source


def _automatic_plot_furniture(projected, source_legend, *, item=None, prims=None,
                              legend=(), markers=(), title_text=None):
    """Keep source series legends and Office's automatic title space reservation."""
    if "plot_layout" in projected:
        return source_legend
    if source_legend is not None and item is not None:
        source = _reserve_automatic_source_furniture(projected, item, prims, legend, markers, title_text)
        if source is not None:
            return source
    title = projected.get("title")
    if isinstance(title, dict):
        title.pop("layout", None)
        projected["title_reserve_space"] = True
    style = projected.get("style", {})
    style.pop("legend_layout", None)
    style.pop("legend_overlay", None)
    if source_legend is not None:
        style["show_legend"] = False
    return source_legend


def _reserve_source_category_frame(projected, item, root, categories):
    """A nearby source panel can prove bottom room absent from the frozen anchor."""
    if projected["type"] != "column" or "plot_layout" not in projected or not categories:
        return
    if any(not text.uniform for text in categories):
        return
    size = max(text.size for text in categories)
    bottom = max(text.box().y1 for text in categories)
    anchor = item.anchor
    edge = anchor.y + anchor.height
    if size <= 0 or not 0 <= edge - bottom <= size * .25:
        return
    try:
        page = collect_primitives(root, (0, 0), {}, CHART_ELEMENTS)
        canvas_height = float(root.get("height", "0"))
    except (NativeRejection, ValueError):
        return
    frames = []
    for shape in page.shapes:
        box = shape.box
        margins = (anchor.x - box.x0, anchor.y - box.y0,
                   box.x1 - anchor.x - anchor.width, box.y1 - edge)
        if (shape.tag == "rect" and shape.fill and shape.fill_alpha >= .99
                and all(math.isfinite(value) and 0 <= value <= size * 3 for value in margins)
                and box.y1 - bottom >= size * .5 and box.y1 <= canvas_height):
            frames.append(box)
    if len(frames) != 1:
        return
    frame = frames[0]
    for shape in page.shapes:
        box = shape.box
        if box.x0 <= frame.x0 and box.y0 <= frame.y0 and box.x1 >= frame.x1 and box.y1 >= frame.y1:
            continue  # The proven panel, its outline and surrounding backgrounds.
        if (box.y1 > edge and box.y0 < frame.y1
                and box.x1 > anchor.x and box.x0 < anchor.x + anchor.width):
            return
    category_keys = {(normalize_text(text.text), text.points) for text in categories}
    for text in page.texts:
        box = text.box()
        if ((normalize_text(text.text), text.points) not in category_keys
                and box.y1 > edge and box.y0 < frame.y1
                and box.x1 > anchor.x and box.x0 < anchor.x + anchor.width):
            return
    height = frame.y1 - anchor.y
    ratio = anchor.height / height
    title = projected.get("title")
    for layout in (projected.get("plot_layout"), title.get("layout") if isinstance(title, dict) else None,
                   projected.get("style", {}).get("legend_layout")):
        if layout is not None:
            layout["y"] *= ratio
            layout["h"] *= ratio
    projected["height"] = height


def _editable_source_categories(projected, item, categories):
    """Keep proven edge labels outside Office's chart-frame clipping region."""
    if (projected["type"] != "column" or "plot_layout" not in projected
            or len(item.payload["series"]) != 1 or not categories
            or projected.get("height") != item.anchor.height):
        return None  # Keep the existing proven panel reservation unchanged.
    expected = [normalize_text(value) for value in item.payload["categories"]]
    if (len(set(expected)) != len(expected) or len(categories) != len(expected)
            or [normalize_text(text.text) for text in categories] != expected
            or any(not text.uniform for text in categories)):
        return None
    size = max(text.size for text in categories)
    bottom = max(text.box().y1 for text in categories)
    if size <= 0 or not 0 <= item.anchor.y + item.anchor.height - bottom <= size * .25:
        return None
    source = _editable_source_legend(categories, (), item.anchor)
    if source is not None:
        projected["axes"]["category"]["label_position"] = "none"
    return source


def _apply_drawn_chart_style_core(projected: dict, item, root, *, source_underlays=None) -> ET.Element | None:
    """Preserve visible labels without trusting a frozen chart-wide text colour."""
    if item.svg_appearance is None:
        return
    found = find_group(root, item.id)
    if found is None:
        return
    group, offset, inherited = found
    try:
        prims = collect_primitives(group, offset, inherited, CHART_ELEMENTS)
        categories = _category_texts(item.payload, prims)
        if item.payload["chartType"] in {"bar", "column"}:
            plots = [shape for series in _bar_matrix(item.payload, prims, categories) for shape in series]
        else:
            plots = [shape for shape in prims.shapes if shape.tag in {"polyline", "path", "polygon"}]
    except NativeRejection:
        return  # Historical sidecars retain the established converter fallback.
    names = {normalize_text(series["name"]) for series in item.payload["series"]}
    title = normalize_text(item.payload.get("title") or "")
    category_names = {normalize_text(value) for value in item.payload["categories"]}
    semantic_names = names | category_names | {title}
    semantic = [text for text in prims.texts if normalize_text(text.text) in semantic_names]
    values = {value for series in item.payload["series"] for value in series["values"]}
    value_labels = [text for text in prims.texts if normalize_text(text.text) not in semantic_names
                    and parse_label_number(text.text) in values]
    style = projected.setdefault("style", {})
    colours = [text.fill for text in semantic if text.fill]
    if colours:
        style["text_color"] = Counter(colours).most_common(1)[0][0]
    if categories:
        style["axis_font_size"] = Counter(text.size for text in categories).most_common(1)[0][0]
    title_text = next((text for text in semantic if title and normalize_text(text.text) == title), None)
    if title_text:
        # The converter supports a rich title entry with independent colour and size.
        projected["title"] = {"text": item.payload["title"], "color": title_text.fill,
                              "font_size": title_text.size, "font_family": title_text.family,
                              "bold": is_bold(title_text.weight),
                              "align": {"middle": "ctr", "end": "r"}.get(title_text.anchor, "l")}
        layout = _relative_layout(title_text.box(), item.anchor)
        if layout is not None:
            projected["title"]["layout"] = layout
            projected["title_reserve_space"] = False
    if value_labels and "data_labels" in projected:
        labels = projected["data_labels"]
        colours = [text.fill for text in value_labels if text.fill]
        if colours:
            labels["color"] = Counter(colours).most_common(1)[0][0]
        labels["font_size"] = Counter(text.size for text in value_labels).most_common(1)[0][0]
        labels["bold"] = all(is_bold(text.weight) for text in value_labels)
    if projected["type"] in {"pie", "doughnut"}:
        return _automatic_plot_furniture(projected, None)
    legend = [text for text in prims.texts if normalize_text(text.text) in names
              and normalize_text(text.text) not in category_names | {title}]
    style["show_legend"] = bool(legend)
    source_legend = None
    source_markers = []
    if legend:
        style["legend_font_size"] = Counter(text.size for text in legend).most_common(1)[0][0]
        center_y = sum((text.box().y0 + text.box().y1) / 2 for text in legend) / len(legend)
        style["legend_position"] = "top" if center_y < item.anchor.y + item.anchor.height / 2 else "bottom"
        legend_box = legend[0].box()
        for text in legend[1:]:
            legend_box = legend_box.union(text.box())
        for text in legend:
            box = text.box()
            markers = [shape for shape in prims.shapes if shape.tag in {"rect", "circle"} and shape.fill
                       and 0 < shape.box.width <= text.size * 1.5 and 0 < shape.box.height <= text.size * 1.5
                       and 0 <= box.x0 - shape.box.x1 <= text.size * 2
                       and box.y0 <= (shape.box.y0 + shape.box.y1) / 2 <= box.y1]
            if markers:
                marker = min(markers, key=lambda shape: box.x0 - shape.box.x1)
                source_markers.append(marker)
                legend_box = legend_box.union(marker.box)
                for index, series in enumerate(item.payload["series"]):
                    if normalize_text(series["name"]) == normalize_text(text.text):
                        style["colors"][index] = marker.fill
        layout = _relative_layout(legend_box, item.anchor)
        if layout is not None:
            style["legend_layout"] = layout
            style["legend_overlay"] = True
        projected["vary_colors"] = False  # Per-point dPt colours remain; the legend names series.
        if (projected["type"] in {"bar", "column"} and len(item.payload["series"]) == 1
                and len(legend) == 1):
            source_legend = _editable_source_legend(legend, source_markers, item.anchor)
            if source_legend is not None:
                style["show_legend"] = False
    if not plots:
        return _automatic_plot_furniture(projected, source_legend, item=item, prims=prims,
                                        legend=legend, markers=source_markers, title_text=title_text)
    plot = plots[0].box
    for shape in plots[1:]:
        plot = plot.union(shape.box)
    horizontal = projected["type"] == "bar"
    lines = [shape for shape in prims.shapes if shape.tag == "line" and shape.stroke and shape.stroke_alpha > 0]
    grids = [shape for shape in lines if (
        shape.box.height >= plot.height * .8 and shape.box.width <= 1
        and plot.x0 + 1 < shape.box.x0 < plot.x1 - 1 if horizontal else
        shape.box.width >= plot.width * .8 and shape.box.height <= 1
        and plot.y0 + 1 < shape.box.y0 < plot.y1 - 1)]
    tick_labels = [text for text in prims.texts if normalize_text(text.text) not in semantic_names
                   and parse_label_number(text.text) is not None and parse_label_number(text.text) not in values]
    axes = projected.setdefault("axes", {})
    axes.setdefault("category", {}).update(visible=True, major_gridlines=False)
    # Numeric axes and grid lines are optional furniture; do not synthesize them.
    axes.setdefault("value", {}).update(visible=bool(tick_labels or grids),
                                        label_position="nextTo" if tick_labels else "none",
                                        major_gridlines=bool(grids))
    retained_categories = None
    if not tick_labels:
        retained_categories = _retained_axis_plot(projected, item, root, plots, categories, axes,
                                                 grid_lines=lines if grids else (),
                                                 source_underlays=source_underlays)
    if not tick_labels and retained_categories is None:
        _proportional_bar_plot(projected, item, plots, categories, axes,
                               grid_lines=lines if grids else (), source_lines=lines)
    _reserve_source_category_frame(projected, item, root, categories)
    source_legend = _automatic_plot_furniture(projected, source_legend, item=item, prims=prims,
                                             legend=legend, markers=source_markers, title_text=title_text)
    source_categories = retained_categories if retained_categories is not None else _editable_source_categories(projected, item, categories)
    if source_categories is not None:
        if source_legend is None:
            return source_categories
        source_legend.extend(source_categories)
    return source_legend



def _source_data_label_format(texts):
    """Infer a shared Office number format only from consistent visible labels."""
    import re

    if not texts:
        return None
    parsed = []
    for text in texts:
        literal = text.text.strip()
        match = re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.([0-9]+))?", literal)
        if match is None:
            return None
        integer = literal.lstrip('+-').split('.', 1)[0]
        if len(integer) > 1 and integer.startswith('0'):
            return None
        parsed.append((len(match.group(1) or ''), ',' in integer, abs(float(literal.replace(',', '')))))
    precisions = {row[0] for row in parsed}
    if len(precisions) != 1:
        return None
    grouped = any(row[1] for row in parsed)
    if grouped and any(value >= 1000 and not has_grouping for _, has_grouping, value in parsed):
        return None
    decimals = next(iter(precisions))
    return ('#,##0' if grouped else '0') + ('.' + '0' * decimals if decimals else '')


def _source_text_visible(root, group, value, *, unique):
    """Require painted source text nodes and painted ancestors/descendants."""
    def path_to(node, target):
        if node is target:
            return (node,)
        for child in node:
            path = path_to(child, target)
            if path:
                return (node, *path)
        return ()

    def visible(node):
        if any(key in node.attrib for key in ("style", "class", "clip-path", "mask", "filter")):
            return False
        if (node.get("display") not in (None, "inline")
                or node.get("visibility") not in (None, "visible")
                or paint(node.get("fill", "black")) is None):
            return False
        try:
            return all(math.isfinite(float(node.get(key, "1")))
                       and float(node.get(key, "1")) > 0
                       for key in ("opacity", "fill-opacity"))
        except ValueError:
            return False

    group_path = path_to(root, group)
    if not group_path:
        return False
    matches = [node for node in group.iter()
               if node.tag.rsplit("}", 1)[-1] == "text"
               and normalize_text(" ".join(node.itertext())) == normalize_text(value)]
    if not matches or (unique and len(matches) != 1):
        return False
    return all(all(visible(node) for node in
                   (*group_path, *path_to(group, match)[1:], *match.iter()))
               for match in matches)


def _fixed_axis_source_titles(projected, item, prims, root, group):
    """Retain exact source title positions when Office shifts native titles."""
    if item.payload.get("axisContract") != "native-chart-fixed-value-axis/v1":
        return None, set()
    source = []
    for value in (item.payload.get("title"), item.payload.get("valueAxis", {}).get("title")):
        if not value:
            continue
        matches = [text for text in prims.texts if normalize_text(text.text) == normalize_text(value)]
        if (len(matches) != 1 or not matches[0].uniform or matches[0].fill is None
                or not math.isfinite(matches[0].size) or matches[0].size <= 0
                or not _source_text_visible(root, group, value, unique=True)):
            raise NativeRejection("native_chart_source_title_unprojectable")
        source.append(matches[0])
    if len({id(text) for text in source}) != len(source):
        raise NativeRejection("native_chart_source_title_ambiguous")
    if not source:
        return None, set()
    overlay = _editable_source_legend(source, (), item.anchor)
    if overlay is None:
        raise NativeRejection("native_chart_source_title_unprojectable")
    projected.pop("title", None)
    projected.pop("title_reserve_space", None)
    axes = projected.get("axis_titles")
    if isinstance(axes, dict) and item.payload.get("valueAxis", {}).get("title"):
        if axes.get("value") != item.payload["valueAxis"]["title"]:
            raise NativeRejection("native_chart_source_title_conflict")
        axes.pop("value")
        if not axes:
            projected.pop("axis_titles")
    return overlay, {id(text) for text in source}


def _fixed_axis_series_label_styles(projected, item, prims, categories, value_labels):
    """Bind every source label's paint and weight to its editable series."""
    if item.payload.get("axisContract") != "native-chart-fixed-value-axis/v1":
        return
    if not isinstance(projected.get("data_labels"), dict):
        if value_labels:
            raise NativeRejection("native_chart_data_label_style_unproven")
        return
    try:
        matrix = _bar_matrix(item.payload, prims, categories)
    except NativeRejection as error:
        raise NativeRejection("native_chart_data_label_style_unproven") from error
    if len(value_labels) != sum(len(shapes) for shapes in matrix):
        raise NativeRejection("native_chart_data_label_style_unproven")
    remaining = list(value_labels)
    for projected_series, source_series, shapes in zip(
            projected["series"], item.payload["series"], matrix, strict=True):
        points = []
        for index, (value, shape) in enumerate(zip(source_series["values"], shapes, strict=True)):
            box = shape.box
            matches = [text for text in remaining
                       if parse_label_number(text.text) == value and text.points
                       and box.x0 - 1 <= text.points[0][0] <= box.x1 + 1
                       and box.y0 - 1 <= text.points[0][1] <= box.y1 + 1]
            if len(matches) != 1 or not matches[0].uniform or matches[0].fill is None:
                raise NativeRejection("native_chart_data_label_style_unproven")
            text = matches[0]
            remaining.remove(text)
            points.append({"idx": index, "color": text.fill,
                           "bold": is_bold(text.weight), "font_size": text.size,
                           "font_family": text.family.split(",")[0].strip().strip("\"'")})
        labels = {key: projected["data_labels"][key]
                  for key in ("position", "number_format", "show_value", "show_category")
                  if key in projected["data_labels"]}
        projected_series["data_labels"] = {**labels, "points": points}
    if remaining:
        raise NativeRejection("native_chart_data_label_style_unproven")


def apply_drawn_chart_style(projected, item, root: ET.Element, *, source_underlays=None):
    """Keep proven visible precision and non-data chart text as editable furniture."""
    furniture = _apply_drawn_chart_style_core(projected, item, root, source_underlays=source_underlays)
    found = find_group(root, item.id)
    if found is None:
        return furniture
    group, offset, inherited = found
    prims = collect_primitives(group, offset, inherited, CHART_ELEMENTS)
    categories = _category_texts(item.payload, prims)
    value_labels = _value_texts(item.payload, prims)
    if item.payload.get("axisContract") == "native-chart-fixed-value-axis/v1":
        if any(not _source_text_visible(root, group, text.text, unique=False)
               for text in value_labels):
            raise NativeRejection("native_chart_data_label_style_unproven")
    labels = projected.get('data_labels')
    number_format = _source_data_label_format(value_labels)
    if (item.payload.get("axisContract") == "native-chart-fixed-value-axis/v1"
            and value_labels and number_format is None):
        raise NativeRejection("native_chart_data_label_style_unproven")
    if isinstance(labels, dict) and number_format is not None:
        labels['number_format'] = number_format
    _fixed_axis_series_label_styles(projected, item, prims, categories, value_labels)

    # Point overrides belong to each native label, rather than a majority paint.
    # Unique value matching is independent of the SVG child order. Repeated
    # values use the already-proven category coordinate to identify the point.
    if isinstance(labels, dict) and len(item.payload['series']) == 1:
        amounts = item.payload['series'][0]['values']
        remaining = list(value_labels)
        points = []
        for index, amount in enumerate(amounts):
            matches = [text for text in remaining if parse_label_number(text.text) == amount]
            if not matches:
                break
            if len(matches) > 1:
                if projected['type'] not in {'bar', 'column', 'line', 'area'} or index >= len(categories):
                    break
                axis = 1 if projected['type'] == 'bar' else 0
                coordinate = categories[index].points[0][axis]
                matches.sort(key=lambda text: abs(text.points[0][axis] - coordinate))
                if abs(matches[0].points[0][axis] - coordinate) == abs(matches[1].points[0][axis] - coordinate):
                    break
            text = matches[0]
            if not text.uniform:
                break
            remaining.remove(text)
            point = {'idx': index, 'font_size': text.size, 'bold': is_bold(text.weight)}
            if text.fill:
                point['color'] = text.fill
            if text.family:
                point['font_family'] = text.family.split(',')[0].strip().strip("\"'")
            points.append(point)
        if len(points) == len(amounts) and not remaining:
            labels['points'] = points

    title_overlay, title_ids = _fixed_axis_source_titles(projected, item, prims, root, group)
    if title_overlay is not None:
        if furniture is None:
            furniture = title_overlay
        else:
            furniture.extend(title_overlay)

    used_ids = {id(text) for text in categories + value_labels} | title_ids
    used_text = {normalize_text(series['name']) for series in item.payload.get('series', ()) if isinstance(series.get('name'), str)}
    for value in (item.payload.get('title', ''), projected.get('title', '')):
        if isinstance(value, dict):
            value = value.get('text', '')
        if isinstance(value, str):
            used_text.add(normalize_text(value))
    if furniture is not None:
        used_text.update(normalize_text(''.join(node.itertext())) for node in furniture.iter() if node.tag.rsplit('}', 1)[-1] == 'text')
    extras = [text for text in prims.texts if id(text) not in used_ids and parse_label_number(text.text) is None and normalize_text(text.text) not in used_text]
    extra_furniture = _editable_source_legend(extras, (), item.anchor)
    if extra_furniture is None:
        return furniture
    if furniture is None:
        return extra_furniture
    furniture.extend(extra_furniture)
    return furniture
