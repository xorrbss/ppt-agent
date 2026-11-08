"""Common-coordinate proof of existing Cartesian chart data, independent of labels."""
from copy import deepcopy
from dataclasses import dataclass, replace
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg.chart_content_cartesian import prove_cartesian
from services.generation_core.drivers.svg.native_chart import CHART_ELEMENTS, _model_chart
from services.generation_core.drivers.svg.native_geometry import Shape, collect_primitives


COLORS = ["#2563EB", "#F97316", "#16A34A", "#9333EA"]


def _fixture(kind, values, *, grouping=None, markers=False, appearance=False, normalized=False,
             baseline=350, scale=4, legend=True):
    categories = [chr(65 + index) for index in range(len(values[0]))]
    raw = {"chartType": kind, "categories": categories,
           "series": [{"name": f"Series {s + 1}", "values": list(row),
                       "pointColors": [COLORS[s % len(COLORS)]] * len(categories)}
                      for s, row in enumerate(values)]}
    if grouping is not None:
        raw["grouping"] = grouping
    if appearance or grouping is not None:
        raw.update(categoryOrder="topToBottom" if kind == "bar" else "leftToRight",
                   valueLabels={"shown": False, "position": None}, markers=markers)
    elif markers:
        raw["markers"] = True
    group = ET.Element("g")

    def text(value, x, y, anchor="middle"):
        node = ET.SubElement(group, "text", {"x": str(x), "y": str(y), "font-size": "16",
                                               "fill": "#222222", "text-anchor": anchor})
        node.text = value

    centers = [200 + index * 200 for index in range(len(categories))]
    for category, center in zip(categories, centers, strict=False):
        text(category, 100 if kind == "bar" else center, center + 6 if kind == "bar" else 650)
    maximum = max(abs(value) for row in values for value in row) or 1
    stacked = grouping in {"stacked", "percentStacked"}
    for s, row in enumerate(values):
        color = COLORS[s % len(COLORS)]
        points = []
        for c, (value, center) in enumerate(zip(row, centers, strict=False)):
            effective = value / maximum if normalized else value
            endpoint = baseline - effective * scale
            points.append((center, endpoint))
            if kind in {"bar", "column"}:
                if value == 0:
                    continue
                if stacked:
                    prior = sum(values[t][c] for t in range(s))
                    if grouping == "percentStacked":
                        total = sum(series[c] for series in values)
                        start, extent = prior / total * 180, value / total * 180
                    else:
                        start, extent = prior * scale, value * scale
                    near = baseline + start if kind == "bar" else baseline - start
                    lo, hi = (near, near + extent) if kind == "bar" else (near - extent, near)
                    position = center
                else:
                    position = center + (s - (len(values) - 1) / 2) * 24
                    extent = effective * scale
                    lo, hi = sorted((baseline, baseline + extent if kind == "bar" else baseline - extent))
                attrs = {"x": str(lo), "y": str(position - 9), "width": str(hi - lo), "height": "18"} if kind == "bar" else {
                    "x": str(position - 9), "y": str(lo), "width": "18", "height": str(hi - lo)}
                ET.SubElement(group, "rect", {**attrs, "fill": color})
        if kind in {"line", "area"}:
            if kind == "area":
                points += [(centers[-1], baseline), (centers[0], baseline)]
            ET.SubElement(group, "polygon" if kind == "area" else "polyline", {
                "points": " ".join(f"{x},{y}" for x, y in points),
                "fill": color if kind == "area" else "none", "stroke": color, "stroke-width": "2"})
            if markers:
                for center, value in zip(centers, row, strict=False):
                    y = baseline - (value / maximum if normalized else value) * scale
                    ET.SubElement(group, "circle", {"cx": str(center), "cy": str(y), "r": "4", "fill": color})
        if legend:
            y = 70 + s * 30
            ET.SubElement(group, "rect", {"x": "900", "y": str(y - 5), "width": "10", "height": "10", "fill": color})
            text(f"Series {s + 1}", 925, y + 6, "start")
    return raw, group


def _proof(raw, group, *, canvas_texts=()):
    chart = _model_chart(raw)
    prims = collect_primitives(group, (0, 0), {}, CHART_ELEMENTS)
    return prove_cartesian(raw, chart, prims, canvas_texts=canvas_texts)


@dataclass(frozen=True)
class _TopologyShape(Shape):
    subpaths: tuple = ()
    closedSubpaths: tuple = ()


def _topology_proof(raw, group, paths, closed):
    """Exercise the parsed scene interface, without reimplementing its parser."""
    prims = collect_primitives(group, (0, 0), {}, CHART_ELEMENTS)
    index = next(i for i, shape in enumerate(prims.shapes) if shape.tag in {"polyline", "polygon"})
    shape = prims.shapes[index]
    replacement = _TopologyShape(**{**vars(shape), "tag": "path"},
                                 subpaths=tuple(tuple(path) for path in paths), closedSubpaths=tuple(closed))
    prims = replace(prims, shapes=tuple(replacement if i == index else item
                                       for i, item in enumerate(prims.shapes)))
    return prove_cartesian(raw, _model_chart(raw), prims)


@pytest.mark.parametrize("kind", ["bar", "column"])
@pytest.mark.parametrize("values", [
    [[10, 20, 30]], [[-10, 0, 20]], [[0]], [[10]],
    [[10, -20, 0], [20, -10, 30]], [[0, 10], [20, 0]],
])
@pytest.mark.parametrize("appearance", [False, True])
def test_clustered_or_implicit_bars_preserve_existing_signed_zero_and_singleton_contract(kind, values, appearance):
    raw, group = _fixture(kind, values, appearance=appearance)
    before = deepcopy(raw)
    proof = _proof(raw, group)
    assert proof["passed"], proof["reason"]
    assert len(proof["points"]) == len(values) * len(values[0])
    assert raw == before
    for point in proof["points"]:
        assert point["value"] == values[point["seriesIndex"]][point["categoryIndex"]]
        if point["value"] == 0:
            assert point["shapeIndex"] is None


@pytest.mark.parametrize("kind", ["bar", "column"])
def test_single_category_multiple_series_uses_one_shared_scale(kind):
    raw, group = _fixture(kind, [[10], [20]])
    assert _proof(raw, group)["passed"]
    bars = [node for node in group if node.tag == "rect" and node.get("width") != "10"]
    bad = bars[-1]
    if kind == "bar":
        bad.set("width", str(float(bad.get("width")) / 2))
    else:
        bad.set("height", str(float(bad.get("height")) / 2))
        bad.set("y", str(350 - float(bad.get("height"))))
    assert not _proof(raw, group)["passed"]


def test_absent_zero_bars_keep_proven_cluster_slots_for_value_label_association():
    raw, group = _fixture("column", [[0, 10], [20, 0]])
    proof = _proof(raw, group)
    assert proof["passed"]
    zeros = [point for point in proof["points"] if point["value"] == 0]
    assert [point["x"] for point in zeros] == [188, 412]
    assert all(point["absentMark"] and point["categoryCoordinateInferred"] for point in zeros)


@pytest.mark.parametrize("kind", ["bar", "column"])
def test_series_cannot_use_independent_bar_scale_even_when_each_series_is_proportional(kind):
    raw, group = _fixture(kind, [[10, 20], [20, 40]])
    for node in group:
        if node.tag != "rect" or node.get("fill") != COLORS[1] or node.get("width") == "10":
            continue
        key = "width" if kind == "bar" else "height"
        node.set(key, str(float(node.get(key)) / 2))
        if kind == "column":
            node.set("y", str(350 - float(node.get("height"))))
    assert not _proof(raw, group)["passed"]


@pytest.mark.parametrize("attack", ["wrong_sign", "shifted_origin", "extra_bar", "category_reassignment", "series_reassignment"])
def test_bar_data_association_and_zero_baseline_attacks_fail(attack):
    raw, group = _fixture("column", [[10, -20], [20, -10]])
    bars = [node for node in group if node.tag == "rect" and node.get("width") != "10"]
    if attack == "wrong_sign":
        bars[1].set("y", str(350 - float(bars[1].get("height"))))
    elif attack == "shifted_origin":
        bars[0].set("y", str(float(bars[0].get("y")) - 20))
    elif attack == "extra_bar":
        group.append(deepcopy(bars[0]))
    elif attack == "category_reassignment":
        bars[0].set("x", str(float(bars[0].get("x")) + 200))
        bars[1].set("x", str(float(bars[1].get("x")) - 200))
    else:
        for bar in bars:
            bar.set("fill", COLORS[1] if bar.get("fill") == COLORS[0] else COLORS[0])
    assert not _proof(raw, group)["passed"]


@pytest.mark.parametrize("kind", ["line", "area"])
@pytest.mark.parametrize("values", [
    [[10, 20, 15]], [[-10, 0, 10]], [[0, 10]], [[10, 0]], [[10, 10]], [[0, 0]], [[10]], [[0]],
    [[10, -20, 30], [20, -10, 0]], [[10, 20], [20, 30]],
])
@pytest.mark.parametrize("markers", [False, True])
def test_line_and_area_signed_constant_zero_singleton_and_multi_series_contract(kind, values, markers):
    raw, group = _fixture(kind, values, markers=markers)
    proof = _proof(raw, group)
    assert proof["passed"], proof["reason"]
    assert len(proof["points"]) == len(values) * len(values[0])
    assert all(point["shapeIndex"] is not None for point in proof["points"])


@pytest.mark.parametrize("kind", ["line", "area"])
@pytest.mark.parametrize("attack", ["inverted", "middle", "series_scale", "marker", "extra_marker", "category_order"])
def test_curve_association_common_affine_scale_and_marker_attacks_fail(kind, attack):
    raw, group = _fixture(kind, [[10, 20, 15], [20, 30, 25]], markers=True)
    curves = [node for node in group if node.tag in {"polygon", "polyline"}]
    if attack in {"inverted", "middle", "series_scale", "category_order"}:
        target = curves[1] if attack == "series_scale" else curves[0]
        points = [tuple(map(float, pair.split(","))) for pair in target.get("points").split()]
        if attack == "inverted":
            points[:3] = [(x, 700 - y) for x, y in points[:3]]
        elif attack == "middle":
            points[1] = (points[1][0], points[1][1] - 20)
        elif attack == "series_scale":
            points[:3] = [(x, 350 - (350 - y) / 2) for x, y in points[:3]]
        else:
            points[0], points[1] = points[1], points[0]
        target.set("points", " ".join(f"{x},{y}" for x, y in points))
    elif attack == "marker":
        next(node for node in group if node.tag == "circle").set("cx", "250")
    else:
        group.append(ET.Element("circle", {"cx": "300", "cy": "300", "r": "4", "fill": COLORS[0]}))
    assert not _proof(raw, group)["passed"]


def test_area_zero_baseline_cannot_move_without_changing_declared_values():
    raw, group = _fixture("area", [[10, 20]])
    polygon = next(node for node in group if node.tag == "polygon")
    points = polygon.get("points").split()
    points[-2:] = ["400,450", "200,450"]
    polygon.set("points", " ".join(points))
    assert not _proof(raw, group)["passed"]


@pytest.mark.parametrize("kind", ["bar", "column"])
@pytest.mark.parametrize("grouping", ["stacked", "percentStacked"])
def test_stacks_preserve_all_series_and_common_zero_baseline(kind, grouping):
    raw, group = _fixture(kind, [[10, 20], [20, 10]], grouping=grouping)
    proof = _proof(raw, group)
    assert proof["passed"], proof["reason"]
    assert len(proof["points"]) == 4
    assert all(point["zeroBaseline"] == 350 for point in proof["points"])


@pytest.mark.parametrize("grouping", ["stacked", "percentStacked"])
@pytest.mark.parametrize("attack", ["gap", "series_swap", "one_stack_scale", "extra_segment"])
def test_stack_chain_series_proportion_and_shared_scale_attacks_fail(grouping, attack):
    raw, group = _fixture("column", [[10, 20], [20, 10]], grouping=grouping)
    bars = [node for node in group if node.tag == "rect" and node.get("width") != "10"]
    if attack == "gap":
        bars[2].set("y", str(float(bars[2].get("y")) - 10))
    elif attack == "series_swap":
        for bar in bars:
            bar.set("fill", COLORS[1] if bar.get("fill") == COLORS[0] else COLORS[0])
    elif attack == "one_stack_scale":
        for bar in (bars[1], bars[3]):
            bar.set("height", str(float(bar.get("height")) / 2))
            bar.set("y", str(350 - (350 - float(bar.get("y"))) / 2))
    else:
        group.append(deepcopy(bars[0]))
    assert not _proof(raw, group)["passed"]


@pytest.mark.parametrize("grouping", ["stacked", "percentStacked"])
def test_positive_stack_totals_may_exceed_float_range_without_overflowing_proof(grouping):
    raw, group = _fixture("column", [[1, 1], [1, 1]], grouping=grouping, scale=40)
    for series in raw["series"]:
        series["values"] = [1e308, 1e308]
    assert _proof(raw, group)["passed"]


@pytest.mark.parametrize("kind", ["bar", "column", "line", "area"])
def test_finite_large_and_subnormal_values_use_normalized_geometry(kind):
    for values in ([[1e308, -1e308]], [[1e-308, -1e-308]]):
        raw, group = _fixture(kind, values, normalized=True, scale=100)
        assert _proof(raw, group)["passed"]


def test_original_shape_indices_survive_bar_sorting_and_legend_furniture():
    raw, group = _fixture("column", [[10, 20], [20, 10]])
    group[:] = list(reversed(list(group)))
    prims = collect_primitives(group, (0, 0), {}, CHART_ELEMENTS)
    proof = prove_cartesian(raw, _model_chart(raw), prims)
    assert proof["passed"]
    for point in proof["points"]:
        assert prims.shapes[point["shapeIndex"]].fill == COLORS[point["seriesIndex"]]


@pytest.mark.parametrize("kind", ["bar", "column"])
def test_uncolored_cluster_contract_binds_stable_spatial_series_order(kind):
    raw, group = _fixture(kind, [[10, 20], [20, 10]], legend=False)
    for series in raw["series"]:
        series.pop("pointColors")
    assert _proof(raw, group)["passed"]
    bars = [node for node in group if node.tag == "rect"]
    key = "y" if kind == "bar" else "x"
    for first, second in ((bars[0], bars[2]), (bars[1], bars[3])):
        a, b = first.get(key), second.get(key)
        first.set(key, b)
        second.set(key, a)
    assert not _proof(raw, group)["passed"]


def test_area_may_draw_a_separate_exact_top_boundary():
    raw, group = _fixture("area", [[10, -10, 20]])
    polygon = next(node for node in group if node.tag == "polygon")
    ET.SubElement(group, "polyline", {"points": " ".join(polygon.get("points").split()[:3]),
                                     "fill": "none", "stroke": "#111111"})
    assert _proof(raw, group)["passed"]


@pytest.mark.parametrize("kind", ["bar", "column", "area"])
def test_visible_outline_data_shapes_preserve_the_same_geometry(kind):
    raw, group = _fixture(kind, [[10, -10, 20]])
    for node in group:
        if node.tag in {"rect", "polygon"} and node.get("width") != "10":
            node.set("stroke", node.get("fill"))
            node.set("fill", "none")
    assert _proof(raw, group)["passed"]


def test_area_accepts_redundant_collinear_top_and_baseline_vertices():
    raw, group = _fixture("area", [[10, 20, 30]])
    polygon = next(node for node in group if node.tag == "polygon")
    polygon.set("points", "200,310 300,290 400,270 500,250 600,230 600,350 400,350 200,350")
    assert _proof(raw, group)["passed"]


def test_linear_curve_can_represent_a_collinear_category_without_a_redundant_vertex():
    raw, group = _fixture("line", [[10, 20, 30]])
    curve = next(node for node in group if node.tag == "polyline")
    curve.set("points", "200,310 600,230")
    assert _proof(raw, group)["passed"]


def test_uniform_actual_palette_and_visible_legend_override_stale_optional_color_metadata():
    raw, group = _fixture("column", [[10, 20], [20, 10]])
    raw["series"][0]["pointColors"] = ["#111111", "#111111"]
    assert _proof(raw, group)["passed"]


def test_swapped_visible_legend_cannot_borrow_declared_point_color_identity():
    raw, group = _fixture("column", [[10, 20], [20, 10]])
    for node in group:
        if node.tag == "rect" and node.get("width") == "10":
            node.set("fill", COLORS[1] if node.get("fill") == COLORS[0] else COLORS[0])
    assert not _proof(raw, group)["passed"]


def test_varying_point_palettes_keep_per_datum_colors_and_stable_legend_order():
    raw, group = _fixture("column", [[10, 20], [20, 10]])
    raw["series"][0]["pointColors"] = [COLORS[0], COLORS[1]]
    raw["series"][1]["pointColors"] = [COLORS[1], COLORS[0]]
    data = [node for node in group if node.tag == "rect" and node.get("width") != "10"]
    data[1].set("fill", COLORS[1])
    data[3].set("fill", COLORS[0])
    assert _proof(raw, group)["passed"]
    labels = [node for node in group if node.tag == "text" and node.text.startswith("Series")]
    labels[0].text, labels[1].text = labels[1].text, labels[0].text
    assert not _proof(raw, group)["passed"]


@pytest.mark.parametrize("markers", [False, True])
def test_direct_series_end_labels_bind_curves_without_a_legend(markers):
    raw, group = _fixture("line", [[10, 20, 15], [20, 30, 25]], legend=False, markers=markers)
    labels = []
    for s, y in enumerate((290, 250)):
        text = ET.SubElement(group, "text", {"x": "610", "y": str(y + 6), "font-size": "16",
                                            "fill": "#222222", "text-anchor": "start"})
        text.text = f"Series {s + 1}"
        labels.append(text)
    proof = _proof(raw, group)
    assert proof["passed"]
    assert proof["seriesBindings"] == ["direct-end-label", "direct-end-label"]
    assert all(len(indices) == 1 for indices in proof["seriesTextIndices"])
    labels[0].text, labels[1].text = labels[1].text, labels[0].text
    assert not _proof(raw, group)["passed"]


@pytest.mark.parametrize("kind", ["bar", "column", "line", "area"])
def test_series_legends_may_use_circle_swatches(kind):
    raw, group = _fixture(kind, [[10, 20], [20, 10]], markers=kind in {"line", "area"})
    for node in list(group):
        if node.tag == "rect" and node.get("width") == "10":
            circle = ET.Element("circle", {"cx": "905", "cy": str(float(node.get("y")) + 5),
                                           "r": "5", "fill": node.get("fill")})
            index = list(group).index(node)
            group.remove(node)
            group.insert(index, circle)
    assert _proof(raw, group)["passed"]


def test_floating_series_names_do_not_claim_visible_curve_identity():
    raw, group = _fixture("line", [[10, 20, 15], [20, 30, 25]], legend=False)
    for s in range(2):
        text = ET.SubElement(group, "text", {"x": "800", "y": str(70 + s * 30), "font-size": "16",
                                            "fill": "#222222"})
        text.text = f"Series {s + 1}"
    proof = _proof(raw, group)
    assert proof["passed"]  # Only numeric geometry; common evidence must bind names.
    assert proof["seriesBindings"] == ["common-affine-pattern", "common-affine-pattern"]
    assert proof["seriesTextIndices"] == [[], []]


@pytest.mark.parametrize("kind", ["bar", "column", "line", "area"])
def test_actual_colored_series_names_can_be_legends_without_swatches(kind):
    raw, group = _fixture(kind, [[10, 20], [20, 10]])
    for node in list(group):
        if node.tag == "rect" and node.get("width") == "10":
            group.remove(node)
        elif node.tag == "text" and node.text.startswith("Series"):
            node.set("fill", COLORS[int(node.text[-1]) - 1])
    proof = _proof(raw, group)
    assert proof["passed"]
    assert proof["seriesBindings"] == ["visible-legend", "visible-legend"]
    assert all(len(indices) == 1 for indices in proof["seriesTextIndices"])
    names = [node for node in group if node.tag == "text" and node.text.startswith("Series")]
    names[0].text, names[1].text = names[1].text, names[0].text
    assert not _proof(raw, group)["passed"]


@pytest.mark.parametrize("paths,passed", [
    ([[(200, 310), (400, 270), (600, 230)]], True),
    ([[(200, 310), (400, 270)], [(400, 270), (600, 230)]], True),
    ([[(200, 310), (300, 290)], [(350, 280), (400, 270), (600, 230)]], True),
    ([[(200, 310)], [(400, 270)], [(600, 230)]], False),
    ([[(200, 310), (300, 290)], [(500, 250), (600, 230)]], False),
    ([[(200, 310), (400, 260)], [(400, 260), (600, 230)]], False),
])
def test_path_lines_sample_only_actual_subpath_edges(paths, passed):
    raw, group = _fixture("line", [[10, 20, 30]])
    assert _topology_proof(raw, group, paths, [False] * len(paths))["passed"] is passed


def test_line_closing_edge_cannot_be_discarded_when_it_crosses_another_category_value():
    raw, group = _fixture("line", [[10, 30, 10]])
    proof = _topology_proof(raw, group, [[(200, 310), (400, 230), (600, 310)]], [True])
    assert not proof["passed"]


@pytest.mark.parametrize("closed", [False, True])
def test_signed_area_can_use_independently_filled_contours_at_real_zero_crossings(closed):
    raw, group = _fixture("area", [[10, -10, 10]])
    paths = [[(200, 310), (300, 350), (200, 350)],
             [(300, 350), (400, 390), (500, 350)],
             [(500, 350), (600, 310), (600, 350)]]
    proof = _topology_proof(raw, group, paths, [closed] * 3)
    assert proof["passed"]
    assert proof["baseline"] == 350
    assert [point["y"] for point in proof["points"]] == [310, 390, 310]


@pytest.mark.parametrize("paths", [
    [[(200, 310)], [(400, 270)], [(600, 230)], [(600, 350)], [(200, 350)]],
    [[(200, 310)], [(400, 270)], [(600, 230)], [(600, 350), (200, 350)]],
])
def test_area_cannot_close_across_move_only_subpaths_to_invent_its_top_boundary(paths):
    raw, group = _fixture("area", [[10, 20, 30]])
    assert not _topology_proof(raw, group, paths, [False] * len(paths))["passed"]


def test_area_cannot_replace_continuous_nonzero_fill_with_separate_category_bars():
    raw, group = _fixture("area", [[10, 10, 10]])
    paths = [[(left, 310), (right, 310), (right, 350), (left, 350)]
             for left, right in [(200, 210), (390, 410), (590, 600)]]
    assert not _topology_proof(raw, group, paths, [True] * 3)["passed"]


def test_area_zero_interval_may_have_no_filled_contour():
    raw, group = _fixture("area", [[10, 0, 0, 10]])
    paths = [[(200, 310), (400, 350), (200, 350)],
             [(600, 350), (800, 310), (800, 350)]]
    proof = _topology_proof(raw, group, paths, [True, True])
    assert proof["passed"]
    assert [point["y"] for point in proof["points"]] == [310, 350, 350, 310]


def test_singleton_area_cannot_borrow_a_redundant_interior_vertex_as_its_calibrated_endpoint():
    raw, group = _fixture("area", [[10]])
    next(node for node in group if node.tag == "polygon").set("points", "200,270 200,310 200,350")
    for value, y in [(0, 350), (20, 270)]:
        ET.SubElement(group, "line", {"x1": "100", "x2": "300", "y1": str(y), "y2": str(y),
                                     "stroke": "#CCCCCC"})
        label = ET.SubElement(group, "text", {"x": "80", "y": str(y + 6), "font-size": "16",
                                              "fill": "#222222", "text-anchor": "end"})
        label.text = str(value)
    assert not _proof(raw, group)["passed"]


@pytest.mark.parametrize("kind", ["line", "area"])
def test_move_only_subpath_does_not_change_an_existing_actual_curve(kind):
    raw, group = _fixture(kind, [[10, 20, 30]])
    top = [(200, 310), (400, 270), (600, 230)]
    points = top + ([(600, 350), (200, 350)] if kind == "area" else [])
    assert _topology_proof(raw, group, [points, [(400, 900)]], [kind == "area", False])["passed"]


@pytest.mark.parametrize("markers", [False, True])
def test_numerically_identical_series_may_share_one_visibly_identical_trace(markers):
    raw, group = _fixture("line", [[10, 20, 15], [10, 20, 15]], markers=markers, legend=False)
    raw["series"][1]["pointColors"] = list(raw["series"][0]["pointColors"])
    for node in list(group):
        if node.get("stroke") == COLORS[1] or node.get("fill") == COLORS[1]:
            group.remove(node)
    proof = _proof(raw, group)
    assert proof["passed"]
    assert len(proof["points"]) == 6
    assert len({point["shapeIndex"] for point in proof["points"]}) == 1


def _fixed_fixture():
    raw, group = _fixture("column", [[10, 20], [20, 40]], grouping="clustered", baseline=550)
    raw.update(axisContract="native-chart-fixed-value-axis/v1", geometryContract="native-chart-coherent-grid/v2",
               valueAxis={"minimum": 0, "maximum": 100, "majorUnit": 50, "title": "kg"})
    siblings = ET.Element("g")
    for value in (0, 50, 100):
        y = 550 - value * 4
        ET.SubElement(group, "line", {"x1": "100", "x2": "500", "y1": str(y), "y2": str(y),
                                     "stroke": "#CCCCCC"})
        text = ET.SubElement(siblings, "text", {"x": "80", "y": str(y + 6), "font-size": "16",
                                                "fill": "#222222", "text-anchor": "end"})
        text.text = str(value)
    title = ET.SubElement(siblings, "text", {"x": "80", "y": "130", "font-size": "16",
                                            "fill": "#222222", "text-anchor": "end"})
    title.text = "kg"
    return raw, group, siblings


def test_existing_fixed_axis_complete_grid_ticks_and_sibling_title_contract():
    raw, group, siblings = _fixed_fixture()
    canvas_texts = collect_primitives(siblings, (0, 0), {}, CHART_ELEMENTS).texts
    proof = _proof(raw, group, canvas_texts=canvas_texts)
    assert proof["passed"], proof["reason"]
    assert proof["calibrated"]
    assert proof["baseline"] == 550
    assert proof["canvasTickTextIndices"] == [0, 1, 2]
    assert proof["canvasAxisTitleTextIndices"] == [3]
    assert proof["canvasTextIndices"] == [0, 1, 2, 3]
    assert proof["axisTitleTextIndices"] == []
    assert proof["tickSourceBindings"] == [
        {"value": 100, "position": 150, "scope": "canvas", "textIndex": 2},
        {"value": 50, "position": 350, "scope": "canvas", "textIndex": 1},
        {"value": 0, "position": 550, "scope": "canvas", "textIndex": 0}]


@pytest.mark.parametrize("gap", [False, True])
def test_fixed_grid_calibration_requires_the_actual_subpaths_to_cover_the_span(gap):
    raw, group, siblings = _fixed_fixture()
    prims = collect_primitives(group, (0, 0), {}, CHART_ELEMENTS)
    index = next(i for i, shape in enumerate(prims.shapes) if shape.tag == "line")
    shape = prims.shapes[index]
    y = shape.box.y0
    paths = (((100, y), (110 if gap else 300, y)), ((490 if gap else 300, y), (500, y)))
    path = _TopologyShape(**{**vars(shape), "tag": "path"}, subpaths=paths,
                          closedSubpaths=(False, False))
    prims = replace(prims, shapes=tuple(path if i == index else item for i, item in enumerate(prims.shapes)))
    canvas_texts = collect_primitives(siblings, (0, 0), {}, CHART_ELEMENTS).texts
    assert prove_cartesian(raw, _model_chart(raw), prims, canvas_texts=canvas_texts)["passed"] is not gap


def test_fixed_axis_title_inside_group_reports_its_local_text_identity():
    raw, group, siblings = _fixed_fixture()
    title = next(node for node in siblings if node.text == "kg")
    siblings.remove(title)
    group.append(title)
    canvas_texts = collect_primitives(siblings, (0, 0), {}, CHART_ELEMENTS).texts
    proof = _proof(raw, group, canvas_texts=canvas_texts)
    assert proof["passed"]
    assert len(proof["axisTitleTextIndices"]) == 1
    assert proof["canvasAxisTitleTextIndices"] == []
    assert proof["canvasTextIndices"] == [0, 1, 2]


def test_tick_source_binding_keeps_local_scientific_text_and_canvas_indices_distinct():
    raw, group, siblings = _fixed_fixture()
    local = next(node for node in siblings if node.text == "50")
    siblings.remove(local)
    local.text = "5e1"
    group.append(local)
    canvas_texts = collect_primitives(siblings, (0, 0), {}, CHART_ELEMENTS).texts
    proof = _proof(raw, group, canvas_texts=canvas_texts)
    assert proof["passed"]
    assert proof["tickSourceBindings"] == [
        {"value": 100, "position": 150, "scope": "canvas", "textIndex": 1},
        {"value": 50, "position": 350, "scope": "local", "textIndex": 4},
        {"value": 0, "position": 550, "scope": "canvas", "textIndex": 0}]


@pytest.mark.parametrize("style", ["outline", "varying_points"])
def test_fixed_axis_data_proof_does_not_require_native_fill_or_uniform_palette(style):
    raw, group, siblings = _fixed_fixture()
    data = [node for node in group if node.tag == "rect" and node.get("width") != "10"]
    if style == "outline":
        for node in data:
            node.set("stroke", node.get("fill"))
            node.set("fill", "none")
    else:
        raw["series"][0]["pointColors"] = [COLORS[0], COLORS[1]]
        raw["series"][1]["pointColors"] = [COLORS[1], COLORS[0]]
        data[1].set("fill", COLORS[1])
        data[3].set("fill", COLORS[0])
    canvas_texts = collect_primitives(siblings, (0, 0), {}, CHART_ELEMENTS).texts
    assert _proof(raw, group, canvas_texts=canvas_texts)["passed"]


@pytest.mark.parametrize(("unit", "maximum"), [(1e-308, 1e-307), (1e299, 1e300), (1e307, 1e308)])
def test_fixed_axes_keep_finite_scientific_tick_values_at_extreme_scales(unit, maximum):
    raw, group, siblings = _fixed_fixture()
    raw["series"][0]["values"] = [unit, unit * 2]
    raw["series"][1]["values"] = [unit * 2, unit * 4]
    raw["valueAxis"].update(maximum=maximum, majorUnit=maximum / 2)
    next(node for node in siblings if node.text == "50").text = str(maximum / 2)
    next(node for node in siblings if node.text == "100").text = str(maximum)
    canvas_texts = collect_primitives(siblings, (0, 0), {}, CHART_ELEMENTS).texts
    proof = _proof(raw, group, canvas_texts=canvas_texts)
    assert proof["passed"]
    assert proof["tickSourceBindings"] == [
        {"value": maximum, "position": 150, "scope": "canvas", "textIndex": 2},
        {"value": maximum / 2, "position": 350, "scope": "canvas", "textIndex": 1},
        {"value": 0, "position": 550, "scope": "canvas", "textIndex": 0}]
    if maximum < 1:
        next(node for node in siblings if node.text == str(maximum / 2)).text = "0"
        canvas_texts = collect_primitives(siblings, (0, 0), {}, CHART_ELEMENTS).texts
        assert not _proof(raw, group, canvas_texts=canvas_texts)["passed"]


@pytest.mark.parametrize("attack", ["grid", "tick", "title", "bar"])
def test_fixed_axis_calibration_attacks_fail(attack):
    raw, group, siblings = _fixed_fixture()
    if attack == "grid":
        group.remove(next(node for node in group if node.tag == "line"))
    elif attack == "tick":
        next(node for node in siblings if node.text == "50").text = "55"
    elif attack == "title":
        siblings.remove(next(node for node in siblings if node.text == "kg"))
    else:
        next(node for node in group if node.tag == "rect" and node.get("width") != "10").set("height", "20")
    canvas_texts = collect_primitives(siblings, (0, 0), {}, CHART_ELEMENTS).texts
    assert not _proof(raw, group, canvas_texts=canvas_texts)["passed"]


def test_legendless_bars_with_many_possible_series_layouts_are_refused_quickly():
    """Fewer drawn bars than series, without a legend, must not search every layout."""
    import time

    values = [[10 + s + c for c in range(7)] for s in range(6)]
    raw, group = _fixture("column", values, legend=False)
    bars = [node for node in group if node.tag == "rect"]
    for node in bars[3 * 7:]:  # keep three of six series drawn in every category
        group.remove(node)
    started = time.monotonic()
    proof = _proof(raw, group)
    assert time.monotonic() - started < 5
    assert not proof["passed"]
    assert proof.get("reason") == "chart_content_bar_series_association_ambiguous", proof
