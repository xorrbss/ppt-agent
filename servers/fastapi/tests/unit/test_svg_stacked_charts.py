"""Drawn stacked / percent-stacked bar and column charts through publication, export and readback."""
from __future__ import annotations

import json
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg.native_declarations import (
    bind_native_declarations, extract_native_declarations,
)
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.native_objects import NativeObject, NativeObjectsError
from services.svg_contract import finalize_svg, validate_svg
from tests.unit.test_svg_native_objects_end_to_end import _export, _frames

CATEGORIES = ["Q1", "Q2", "Q3"]
SERIES = [("Alpha", "#2457D6", [30, 50, 20]), ("Beta", "#68A4FF", [20, 25, 40]),
          ("Gamma", "#19A974", [10, 15, 30])]
FONT = 'font-family="Arial" font-size="18" fill="#202020"'
EXTENT = 300  # px of the full (percent) stack, or of the tallest stack in "stacked"


def _text(x, y, value, *, anchor="middle"):
    return f'<text x="{x}" y="{y}" {FONT} text-anchor="{anchor}">{value}</text>'


def _group(kind: str, grouping: str, *, mutate=None, labels="center") -> str:
    """A drawn stack; ``mutate(segments)`` may corrupt ``{(series, category): [x, y, w, h]}``."""
    horizontal = kind == "bar"
    totals = [sum(values[c] for _, _, values in SERIES) for c in range(len(CATEGORIES))]
    scale = EXTENT / max(totals) if grouping == "stacked" else None
    segments: dict[tuple[int, int], list[float]] = {}
    for c in range(len(CATEGORIES)):
        offset = 0.0
        for s, (_, _, values) in enumerate(SERIES):
            size = (values[c] * scale if scale else values[c] / totals[c] * EXTENT)
            slot = (190 * c + 230) if not horizontal else (185 + 100 * c)
            if horizontal:
                segments[(s, c)] = [240 + offset, slot, size, 60]
            else:
                segments[(s, c)] = [slot, 600 - offset - size, 70, size]
            offset += size
    if mutate:
        mutate(segments)
    parts = []
    for index, (name, color, _) in enumerate(SERIES):
        parts += [f'<rect x="{900 + 120 * index}" y="118" width="14" height="14" fill="{color}"/>',
                  _text(920 + 120 * index, 132, name, anchor="start")]
    for (s, _c), (x, y, w, h) in segments.items():
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{SERIES[s][1]}"/>')
    for c, name in enumerate(CATEGORIES):
        if horizontal:
            parts.append(_text(190, 185 + 100 * c + 38, name))
        else:
            parts.append(_text(265 + 190 * c, 635, name))
    if labels:
        for (s, c), (x, y, w, h) in segments.items():
            parts.append(_text(x + w / 2, y + h / 2 + 6, SERIES[s][2][c]))
    declaration = {
        "chartType": kind, "grouping": grouping, "categories": CATEGORIES,
        "series": [{"name": n, "values": v, "pointColors": [color] * len(CATEGORIES)} for n, color, v in SERIES],
        "categoryOrder": "topToBottom" if horizontal else "leftToRight",
        "valueLabels": {"shown": bool(labels), "position": labels or None}, "markers": False,
    }
    encoded = json.dumps(declaration, separators=(",", ":"))
    return f'<g id="native-chart-stack" data-native-chart=\'{encoded}\'>{"".join(parts)}</g>'


def _publish(group: str):
    raw = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
           f'<rect width="1280" height="720" fill="#FFFFFF"/>{group}</svg>')
    root = ET.fromstring(raw)
    declarations = extract_native_declarations(root)
    authored = ET.tostring(root, encoding="unicode")
    validate_svg(authored)
    content = {"svg_content": authored}
    bind_native_declarations(content, authored, declarations)
    content["svg_content"] = finalize_svg(authored, index=0, n=1)
    sidecar, receipt = page_native_objects(content, pt_per_px=0.75)
    if sidecar is not None:
        content["nativeObjects"] = sidecar
    content["svgReceipt"] = {"nativeObjects": receipt}
    return content["svg_content"], content


@pytest.mark.parametrize("kind", ["column", "bar"])
@pytest.mark.parametrize("grouping", ["stacked", "percentStacked"])
def test_drawn_stack_becomes_an_editable_stacked_chart(kind, grouping, tmp_path):
    svg, content = _publish(_group(kind, grouping))
    receipt = content["svgReceipt"]["nativeObjects"]
    assert receipt["accepted"] == 1 and receipt["dropped"] == []
    payload = content["nativeObjects"]["objects"][0]["payload"]
    assert payload["schemaVersion"] == "authored-native-chart/v3" and payload["grouping"] == grouping
    assert "overlap" not in payload
    assert [item["color"] for item in payload["series"]] == [color for _, color, _ in SERIES]

    deck, evidence = _export(tmp_path, [svg], [content])

    assert evidence["structuralReadback"]["nativeObjects"] == {"offered": 1, "native": 1, "fellBack": 0, "reasons": []}
    (frame,) = [shape for shape in _frames(deck.slides[0]) if shape.has_chart]
    chart = frame.chart
    xml = chart._chartSpace
    plot = chart.plots[0]
    assert xml.xpath(".//c:barChart/c:grouping")[0].get("val") == grouping
    assert xml.xpath(".//c:barChart/c:barDir")[0].get("val") == ("bar" if kind == "bar" else "col")
    assert xml.xpath(".//c:barChart/c:overlap")[0].get("val") == "100"
    assert list(plot.categories) == CATEGORIES
    assert [list(series.values) for series in plot.series] == [[float(v) for v in values] for _, _, values in SERIES]
    assert [series.name for series in plot.series] == [name for name, _, _ in SERIES]
    fills = [node.get("val") for node in xml.xpath(".//c:ser/c:spPr/a:solidFill/a:srgbClr")]
    point_fills = {node.get("val") for node in xml.xpath(".//c:ser/c:dPt/c:spPr/a:solidFill/a:srgbClr")}
    assert {color.lstrip("#") for _, color, _ in SERIES} <= set(fills) | point_fills
    assert xml.xpath(".//c:barChart/c:dLbls/c:dLblPos[@val='ctr']")
    assert xml.xpath(".//c:barChart/c:dLbls/c:showVal[@val='1']")
    assert not xml.xpath(".//c:dLblPos[@val='outEnd']")


def test_gridlines_prove_the_plot_area_of_a_percent_stack(tmp_path):
    group = _group("column", "percentStacked")
    lines = "".join(f'<line x1="200" y1="{600 - EXTENT * i / 4}" x2="800" y2="{600 - EXTENT * i / 4}" '
                    'stroke="#CBD5E1" stroke-width="1"/>' for i in range(5))
    svg, content = _publish(group.replace("</g>", lines + "</g>", 1))
    assert content["svgReceipt"]["nativeObjects"]["accepted"] == 1
    payload = content["nativeObjects"]["objects"][0]["payload"]
    assert payload["grouping"] == "percentStacked" and "plot_layout" in payload and "gap_width" in payload
    deck, _ = _export(tmp_path, [svg], [content])
    (frame,) = [shape for shape in _frames(deck.slides[0]) if shape.has_chart]
    xml = frame.chart._chartSpace
    assert xml.xpath(".//c:plotArea/c:layout/c:manualLayout/c:layoutTarget[@val='inner']")
    assert xml.xpath(".//c:barChart/c:gapWidth")[0].get("val") == str(payload["gap_width"])


def _stretch(segments):  # still one contiguous stack, but series 1 is drawn 60% too tall
    x, y, w, h = segments[(1, 0)]
    segments[(1, 0)] = [x, y - h * .6, w, h * 1.6]
    x2, y2, w2, h2 = segments[(2, 0)]
    segments[(2, 0)] = [x2, y2 - h * .6, w2, h2]


def _gap(segments):  # a gap between the first two segments breaks the cumulative positions
    x, y, w, h = segments[(1, 1)]
    segments[(1, 1)] = [x, y - 14, w, h]


def _off_baseline(segments):  # one stack starts above the shared baseline
    for s in range(len(SERIES)):
        x, y, w, h = segments[(s, 2)]
        segments[(s, 2)] = [x, y - 12, w, h]


def _swapped(segments):  # series order inside one stack reversed: values no longer match positions
    a, b = segments[(0, 1)], segments[(1, 1)]
    segments[(0, 1)] = [a[0], b[1] + b[3] - a[3], a[2], a[3]]
    segments[(1, 1)] = [b[0], b[1] + b[3] - a[3] - b[3], b[2], b[3]]


@pytest.mark.parametrize("grouping", ["stacked", "percentStacked"])
@pytest.mark.parametrize("mutate", [_stretch, _gap, _off_baseline, _swapped])
def test_wrongly_drawn_stack_is_rejected_and_stays_drawn(grouping, mutate, tmp_path):
    svg, content = _publish(_group("column", grouping, mutate=mutate))
    receipt = content["svgReceipt"]["nativeObjects"]
    assert receipt["accepted"] == 0 and receipt["dropped"][0]["id"] == "native-chart-stack"
    if mutate is _stretch:
        assert receipt["dropped"][0]["reason"] in {"native_chart_value_geometry_mismatch",
                                                   "native_chart_stack_extent_mismatch"}
    assert "nativeObjects" not in content
    deck, evidence = _export(tmp_path, [svg], [content])
    assert not any(shape.has_chart for shape in _frames(deck.slides[0]))
    assert evidence["structuralReadback"]["nativeObjects"]["offered"] == 0


def test_percent_stacks_must_span_one_full_extent():
    def short(segments):  # the whole second stack is drawn at 80% of the others' full extent
        offset = 0.0
        for s in range(len(SERIES)):
            x, _, w, h = segments[(s, 1)]
            segments[(s, 1)] = [x, 600 - offset - h * .8, w, h * .8]
            offset += h * .8
    _, content = _publish(_group("column", "percentStacked", mutate=short))
    dropped = content["svgReceipt"]["nativeObjects"]["dropped"]
    assert [item["reason"] for item in dropped] == ["native_chart_stack_extent_mismatch"]


def test_outside_end_labels_are_rejected_for_a_stack():
    _, content = _publish(_group("column", "stacked", labels="outsideEnd"))
    assert content["svgReceipt"]["nativeObjects"]["accepted"] == 0


def test_stack_needs_two_series():
    group = _group("column", "stacked")
    declaration = json.loads(ET.fromstring(group).get("data-native-chart"))
    declaration["series"] = declaration["series"][:1]
    single = group.replace(ET.fromstring(group).get("data-native-chart"), json.dumps(declaration))
    _, content = _publish(single)
    assert content["svgReceipt"]["nativeObjects"]["accepted"] == 0


def test_clustered_declarations_keep_their_v1_payload_and_stack_needs_v3():
    clustered = {"chartType": "column", "grouping": "clustered", "categories": ["a", "b"],
                 "series": [{"name": "x", "values": [1, 2]}, {"name": "y", "values": [2, 3]}]}
    built = NativeObject.build(id="c1", kind="chart", anchor={"x": 0, "y": 0, "width": 10, "height": 10},
                               payload=clustered)
    assert built.payload["schemaVersion"] == "authored-native-chart/v1"
    with pytest.raises(NativeObjectsError):
        NativeObject.build(id="c1", kind="chart", anchor={"x": 0, "y": 0, "width": 10, "height": 10},
                           payload={**clustered, "grouping": "stacked"})
    stacked = NativeObject.build(id="c1", kind="chart", anchor={"x": 0, "y": 0, "width": 10, "height": 10},
                                 payload={**clustered, "grouping": "stacked",
                                          "schemaVersion": "authored-native-chart/v3"})
    assert stacked.payload["grouping"] == "stacked"
    with pytest.raises(NativeObjectsError):
        NativeObject.build(id="c1", kind="chart", anchor={"x": 0, "y": 0, "width": 10, "height": 10},
                           payload={**clustered, "schemaVersion": "authored-native-chart/v3"})
