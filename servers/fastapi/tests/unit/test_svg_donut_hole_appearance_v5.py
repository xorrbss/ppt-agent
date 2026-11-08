"""Source-proved V5 donut holes, legacy HEAD parity, and OOXML bounds."""
from __future__ import annotations

from copy import deepcopy
import importlib
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from pptx import Presentation

from services.generation_core.drivers.svg.export_projection import (
    NativeChartDonutHoleUnrepresentable, converter_payload,
)
from services.generation_core.drivers.svg.native_declarations import (
    bind_native_declarations, bound_declarations, extract_native_declarations,
)
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.native_objects import NativeObject, NativeObjectsError

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parents[1]
VENDOR = BACKEND / "vendor" / "slide_master"
V5 = "svg-native-requirements/v5"
SVG_NS = "http://www.w3.org/2000/svg"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
ANCHOR = {"x": 100, "y": 100, "width": 400, "height": 400}


@pytest.fixture
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(VENDOR))
    return (importlib.import_module("svg_to_pptx.native_objects.chart_data"),
            importlib.import_module("svg_to_pptx.native_objects.chart_xml"))


def _payload(chart_type="donut"):
    return {"chartType": chart_type, "categories": ["A"],
            "series": [{"name": "Values", "values": [1]}]}


def _appearance(**extra):
    return {"categoryOrder": "clockwise", "valueLabels": {"shown": False, "position": None},
            "markers": False, **extra}


def _page(ratio=.7, *, sectors=False, requested=None, transform=None, spelling="mlz", mutation=None, reverse_contour=False):
    root = ET.Element(f"{{{SVG_NS}}}svg", {"viewBox": "0 0 800 600"})
    raw = _payload()
    if sectors:
        raw.update(categories=["A", "B", "C"], series=[{"name": "Values", "values": [1, 1, 1]}],
                   **_appearance())
    if requested is not None:
        raw["holeSize"] = requested
    group = ET.SubElement(root, f"{{{SVG_NS}}}g", {
        "id": "donut", "data-native-chart": json.dumps(raw),
        **({"transform": transform} if transform else {}),
    })

    def text(value, x, y):
        node = ET.SubElement(group, f"{{{SVG_NS}}}text", {
            "x": str(x), "y": str(y), "font-size": "16", "font-family": "Arial",
            "text-anchor": "middle", "fill": "#222222",
        })
        node.text = value

    text("Values", 400, 140)
    if sectors:
        def point(radius, angle):
            return (400 + radius * math.cos(angle), 300 + radius * math.sin(angle))
        for index, color in enumerate(("#2563EB", "#16A34A", "#EA580C")):
            start, end = -math.pi / 2 + index * math.tau / 3, -math.pi / 2 + (index + 1) * math.tau / 3
            # Keep the original valid closed MLZ reproducer as the default;
            # the equivalent existing polygon spelling is additive coverage.
            steps = max(2, math.ceil((end - start) / .06))
            outer = [point(100, start + (end - start) * step / steps) for step in range(steps + 1)]
            inner = [point(100 * ratio, start + (end - start) * step / steps)
                     for step in reversed(range(steps + 1))]
            if index == 0 and mutation == "inner_reverse":
                inner.reverse()
            if index == 0 and mutation == "internal_backtrack":
                outer.insert(len(outer) // 2, outer[0])
            if index == 0 and mutation == "extra_chord":
                inner.insert(len(inner) // 2, point(85, (start + end) / 2))
            vertices = [*outer, *inner]
            if reverse_contour:
                vertices.reverse()
            if spelling == "polygon":
                ET.SubElement(group, f"{{{SVG_NS}}}polygon", {
                    "points": " ".join(f"{x:.8f},{y:.8f}" for x, y in vertices), "fill": color,
                })
            else:
                contours = [outer, inner] if index == 0 and mutation == "disconnected" else [vertices]
                path_data = " ".join("M " + " L ".join(f"{x:.8f} {y:.8f}" for x, y in contour) + " Z"
                                     for contour in contours)
                ET.SubElement(group, f"{{{SVG_NS}}}path", {"d": path_data, "fill": color})
            x, y = point(135, (start + end) / 2)
            text(raw["categories"][index], x, y)
    else:
        ET.SubElement(group, f"{{{SVG_NS}}}circle", {"cx": "400", "cy": "300", "r": "100", "fill": "#2563EB"})
        ET.SubElement(group, f"{{{SVG_NS}}}circle", {"cx": "400", "cy": "300", "r": str(100 * ratio), "fill": "#FFFFFF"})
        text("A", 550, 300)
    record = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, record)
    return page


def _item(page, revision=V5):
    wire, summary = page_native_objects(page, pt_per_px=.75, revision=revision)
    assert summary == {"declared": 1, "accepted": 1, "dropped": []}
    return NativeObject.build(**wire["objects"][0])


def _expanded_xml_tree(node):
    """Compare expanded XML names, attributes, and every nonempty text/tail."""
    def meaningful(value):
        return value if value is not None and value.strip() else None
    return (node.tag, tuple(sorted(node.attrib.items())), meaningful(node.text), meaningful(node.tail),
            tuple(_expanded_xml_tree(child) for child in node))


def _xml(payload, vendor):
    data_module, xml_module = vendor
    return xml_module._chart_xml(ET.Element("g"), payload, chart_rels_id="rId1",
                                 chart_data=data_module._chart_data(payload))


_LEGACY = json.loads((Path(__file__).resolve().parent / "fixtures" / "svg_v5_legacy_parity_v1.json")
                     .read_text(encoding="utf-8"))


def _text(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value


@pytest.mark.parametrize("case", _LEGACY["donutLegacy"], ids=lambda case: f"{case['revision']}-{case['sectors']}")
def test_legacy_absent_override_matches_frozen_pre_v5_wire_projection_and_xml(case, vendor):
    """Frozen outputs of the pre-V5 modules (fixture baselineCommit), not the checkout's HEAD."""
    page = _page(sectors=case["sectors"])
    actual = page_native_objects(deepcopy(page), pt_per_px=.75, revision=case["revision"])
    assert actual[1]["accepted"] == 1
    assert json.dumps(actual, ensure_ascii=False) == case["wire"]
    current = NativeObject.build(**actual[0]["objects"][0])
    assert current.svg_appearance is None or "holeRatio" not in current.svg_appearance
    projected = converter_payload(current)
    assert "hole_size" not in projected
    assert json.dumps(projected, ensure_ascii=False, separators=(",", ":")) == case["projected"]
    assert repr(vendor[0]._chart_data(projected)) == case["chartData"]
    assert _text(_xml(projected, vendor)) == case["xml"]
    assert ET.fromstring(_xml(projected, vendor)).find(f".//{{{C_NS}}}holeSize").get("val") == "75"


def test_non_svg_absence_matches_frozen_pre_v5_xml(vendor):
    item = NativeObject.build(id="donut", kind="chart", anchor=ANCHOR, payload=_payload())
    assert "svgAppearance" not in item.to_wire()
    payload = converter_payload(item)
    assert "hole_size" not in payload
    assert _text(_xml(payload, vendor)) == _LEGACY["nonSvgDonutXml"]


@pytest.mark.parametrize("sectors", [False, True])
def test_genuine_v5_source_seventy_reaches_pptx_readback(sectors, vendor, tmp_path, monkeypatch):
    page = _page(sectors=sectors)
    frozen = deepcopy(page)
    item = _item(page)
    assert page == frozen
    assert item.svg_appearance["holeRatio"] == pytest.approx(.7, abs=1e-6)
    payload = converter_payload(item)
    assert payload["hole_size"] == 70
    assert "holeRatio" not in item.payload
    from svg_to_pptx import create_pptx_with_native_svg
    from svg_to_pptx.conversion_context import ConversionContext
    root = ET.Element(f"{{{SVG_NS}}}svg", {"viewBox": "0 0 800 600", "width": "800", "height": "600"})
    ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": "donut", "data-pptx-native": "chart", "data-pptx-json": json.dumps(payload)})
    source, target = tmp_path / "source.svg", tmp_path / "donut.pptx"
    source.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    assert create_pptx_with_native_svg([source], target, verbose=False, transition=None,
        use_native_shapes=True, notes={source.stem: ""}, workers=1, merge_paragraphs=False,
        image_optimize=False, pptx_structure="flat", use_compat_mode=False,
        conversion_context=ConversionContext(800, 600, 800 * 9525, 600 * 9525, legacy=True), native_objects=True)
    chart = next(shape.chart for shape in Presentation(str(target)).slides[0].shapes if shape.has_chart)
    loaded_chart_bytes = chart.part.blob
    loaded_chart_xml = ET.fromstring(loaded_chart_bytes)
    assert loaded_chart_xml.find(f".//{{{C_NS}}}holeSize").get("val") == "70"
    # Bind ZIP readback to the chart reached through the loaded slide shape.
    chart_part_name = str(chart.part.partname).lstrip("/")
    with ZipFile(target) as package:
        stored_chart_bytes = package.read(chart_part_name)
    xml = ET.fromstring(stored_chart_bytes)
    # Loading may reserialize indentation; retain all business text and tails.
    assert _expanded_xml_tree(xml) == _expanded_xml_tree(loaded_chart_xml)
    assert xml.find(f".//{{{C_NS}}}holeSize").get("val") == "70"


@pytest.mark.parametrize("ratio", [True, False, "0.7", None, float("nan"), float("inf"), -float("inf"), 0, -0.1, 1, 1.1, 10**1000])
@pytest.mark.parametrize("full", [False, True])
def test_invalid_source_ratio_is_rejected(ratio, full):
    appearance = _appearance(holeRatio=ratio) if full else {"holeRatio": ratio}
    with pytest.raises(NativeObjectsError, match="native_chart_appearance_invalid"):
        NativeObject.build(id="donut", kind="chart", anchor=ANCHOR, payload=_payload(), svgAppearance=appearance)


@pytest.mark.parametrize("chart_type", ["pie", "column", "bar", "line", "area"])
def test_hole_ratio_is_donut_only(chart_type):
    with pytest.raises(NativeObjectsError, match="native_chart_appearance_invalid"):
        NativeObject.build(id="chart", kind="chart", anchor=ANCHOR, payload=_payload(chart_type), svgAppearance={"holeRatio": .7})


@pytest.mark.parametrize("ratio, expected", [(.7, 70), (.695, 70), (.705, 71), (.095, 10), (.9049, 90)])
def test_percent_quantization_is_decimal_half_up(ratio, expected):
    item = NativeObject.build(id="donut", kind="chart", anchor=ANCHOR, payload=_payload(), svgAppearance={"holeRatio": ratio})
    assert converter_payload(item)["hole_size"] == expected


@pytest.mark.parametrize("ratio, expected", [(.05, 5), (.0949, 9), (.905, 91), (.95, 95)])
def test_valid_source_unrepresentability_is_export_only_with_receipt(ratio, expected):
    item = _item(_page(ratio))
    assert item.svg_appearance["holeRatio"] == pytest.approx(ratio)
    with pytest.raises(NativeChartDonutHoleUnrepresentable) as caught:
        converter_payload(item)
    assert caught.value.receipt == {
        "reason": "native_chart_donut_hole_unrepresentable", "holeRatio": item.svg_appearance["holeRatio"],
        "quantizedHolePercent": expected, "minimumHolePercent": 10, "maximumHolePercent": 90,
        "quantization": "decimal-half-up-integer-percent",
    }


@pytest.mark.parametrize("hole_size", [True, False, 70.0, "70", None, float("nan"), float("inf"), 9, 91])
def test_vendor_rejects_invalid_hole_size(hole_size, vendor):
    payload = converter_payload(NativeObject.build(id="donut", kind="chart", anchor=ANCHOR, payload=_payload()))
    payload["hole_size"] = hole_size
    with pytest.raises(RuntimeError, match="hole_size"):
        vendor[0]._chart_data(payload)


@pytest.mark.parametrize("kind", ["pie", "column", "scatter", "combo"])
def test_vendor_rejects_hole_size_on_wrong_chart_kind(kind, vendor):
    with pytest.raises(RuntimeError, match="hole_size"):
        vendor[0]._chart_data({"type": kind, "hole_size": 70})


def test_requested_hole_size_does_not_supply_source_geometry():
    wire, summary = page_native_objects(_page(requested=70), pt_per_px=.75, revision=V5)
    assert wire is None
    assert summary["dropped"][0]["reason"] == "native_chart_json_invalid"


def test_hole_ratio_cannot_be_attached_to_table():
    with pytest.raises(NativeObjectsError, match="native_chart_appearance_invalid"):
        NativeObject.build(id="table", kind="table", anchor=ANCHOR,
                           payload={"rows": [[{"text": "A"}]]}, svgAppearance={"holeRatio": .7})


@pytest.mark.parametrize("appearance", [
    {"holeRatio": .7, "markers": False},
    {"holeRatio": .7, "categoryOrder": "clockwise"},
    {"holeRatio": .7, "unknown": True},
])
def test_hole_only_exception_does_not_accept_partial_or_unknown_appearance(appearance):
    with pytest.raises(NativeObjectsError, match="native_chart_appearance_invalid"):
        NativeObject.build(id="donut", kind="chart", anchor=ANCHOR, payload=_payload(), svgAppearance=appearance)


def test_canonical_source_reader_is_the_ratio_authority(monkeypatch):
    from services.generation_core.drivers.svg import chart_content_polar, native_chart_evidence
    actual, actual_scene = chart_content_polar.prove_polar, native_chart_evidence._v5_scene
    calls, scenes = [], []
    def record_scene(*args, **kwargs):
        result = actual_scene(*args, **kwargs)
        scenes.append(result[0])
        return result
    def record(raw, chart, prims):
        calls.append((deepcopy(chart), prims))
        return actual(raw, chart, prims)
    monkeypatch.setattr(native_chart_evidence, "_v5_scene", record_scene)
    monkeypatch.setattr(chart_content_polar, "prove_polar", record)
    item = _item(_page(sectors=True, transform="translate(10 10) scale(.8)"))
    assert item.svg_appearance["holeRatio"] == pytest.approx(.7, abs=1e-6)
    assert len(calls) == 1
    chart, prims = calls[0]
    assert "fontFamily" not in chart
    assert "pointColors" not in chart["series"][0]
    assert len(scenes) == 1
    canonical_shapes = [record["shape"] for record in scenes[0] if "shape" in record
                        and any(node.get("id") == "donut" for node in record["ancestors"])]
    assert len(canonical_shapes) == len(prims.shapes) == 3
    assert all(actual is canonical for actual, canonical in zip(prims.shapes, canonical_shapes, strict=True))


def test_failed_polar_proof_never_produces_a_hole_override(monkeypatch):
    from services.generation_core.drivers.svg import chart_content_polar
    monkeypatch.setattr(chart_content_polar, "prove_polar", lambda *args: {"passed": False, "reason": "unproved"})
    item = _item(_page())
    assert item.svg_appearance is None
    assert "hole_size" not in converter_payload(item)


def _bound_chart_raw(page, group_id):
    record, current = bound_declarations(page, page["svg_content"])
    assert current is True
    matches = [entry for entry in record["objects"]
               if entry["id"] == group_id and entry["kind"] == "chart"]
    assert len(matches) == 1
    return deepcopy(matches[0]["raw"])


def _canonical_proof(page):
    from services.generation_core.drivers.svg.chart_content_polar import prove_polar
    from services.generation_core.drivers.svg.native_chart import _model_chart
    from services.generation_core.drivers.svg.native_chart_evidence import _v5_scene
    root = ET.fromstring(page["svg_content"])
    group = next(node for node in root if node.get("id") == "donut")
    raw = _bound_chart_raw(page, group.get("id"))
    records, primitive_type = _v5_scene(root)
    local = [record for record in records if group in record["ancestors"]]
    prims = primitive_type(tuple(record["shape"] for record in local if "shape" in record),
                           tuple(record["text"] for record in local if "text" in record), frozenset())
    original = deepcopy(prims)
    result = prove_polar(raw, _model_chart(raw), prims)
    # Angular normalization must leave canonical source topology intact.
    assert prims == original
    return result


def _full_circle_page(kind, *, separate=False, repeated_closure=1):
    page = _page()
    root = ET.fromstring(page["svg_content"])
    group = next(node for node in root if node.get("id") == "donut")
    raw = _bound_chart_raw(page, group.get("id"))
    raw["chartType"] = kind
    group.set("data-native-chart", json.dumps(raw))
    for node in list(group):
        if node.tag == f"{{{SVG_NS}}}circle":
            group.remove(node)
    steps = math.ceil(math.tau / .06)
    def ring(radius, direction):
        points = [(400 + radius * math.cos(-math.pi / 2 + direction * math.tau * step / steps),
                   300 + radius * math.sin(-math.pi / 2 + direction * math.tau * step / steps))
                  for step in range(steps)]
        return [*points, *([points[0]] * repeated_closure)]
    outer, inner = ring(100, 1), ring(70, -1)
    contours = ([outer, inner] if separate else [[*outer, *inner]]) if kind == "donut" else [outer]
    path_data = " ".join("M " + " L ".join(f"{x:.8f} {y:.8f}" for x, y in contour) + " Z"
                         for contour in contours)
    ET.SubElement(group, f"{{{SVG_NS}}}path", {"d": path_data, "fill": "#2563EB"})
    declarations = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    result = {"svg_content": svg}
    bind_native_declarations(result, svg, declarations)
    return result


@pytest.mark.parametrize("transform", [None, "translate(10 10) scale(.8)"])
def test_original_closed_mlz_annular_sectors_keep_source_seventy(transform):
    page = _page(sectors=True, transform=transform)
    proof = _canonical_proof(page)
    assert proof["passed"] is True
    assert proof["holeRatio"] == pytest.approx(.7, abs=1e-6)
    assert converter_payload(_item(page))["hole_size"] == 70


def test_equivalent_polygon_source_is_additional_supported_coverage():
    page = _page(sectors=True, spelling="polygon")
    assert _canonical_proof(page)["passed"] is True
    assert converter_payload(_item(page))["hole_size"] == 70


def test_whole_contour_reversal_preserves_valid_annular_fill():
    page = _page(sectors=True, reverse_contour=True)
    proof = _canonical_proof(page)
    assert proof["passed"] is True
    assert proof["holeRatio"] == pytest.approx(.7, abs=1e-6)
    assert converter_payload(_item(page))["hole_size"] == 70


@pytest.mark.parametrize("kind,separate", [("pie", False), ("donut", False), ("donut", True)])
@pytest.mark.parametrize("repeated_closure", [1, 3])
def test_full_circle_closing_cycle_retains_complete_coverage(kind, separate, repeated_closure):
    proof = _canonical_proof(_full_circle_page(kind, separate=separate, repeated_closure=repeated_closure))
    assert proof["passed"] is True
    assert proof["holeRatio"] == pytest.approx(.7 if kind == "donut" else 0, abs=1e-6)
    assert proof["points"][0]["sweepAngle"] == pytest.approx(math.tau, abs=1e-6)


@pytest.mark.parametrize("mutation", ["inner_reverse", "internal_backtrack", "extra_chord", "disconnected"])
def test_closed_mlz_normalization_preserves_invalid_topology_rejections(mutation):
    proof = _canonical_proof(_page(sectors=True, mutation=mutation))
    assert proof["passed"] is False
    assert proof["reason"] in {"native_chart_polar_sector_invalid", "native_chart_polar_common_geometry_mismatch"}


def test_expanded_xml_readback_ignores_only_empty_indentation():
    plain = ET.fromstring('<c:root xmlns:c="urn:chart"><c:value key="x"> A </c:value>tail</c:root>')
    indented = ET.fromstring('<c:root xmlns:c="urn:chart">\n  <c:value key="x"> A </c:value>tail</c:root>')
    assert _expanded_xml_tree(plain) == _expanded_xml_tree(indented)
    changed_text = ET.fromstring('<c:root xmlns:c="urn:chart"><c:value key="x">A</c:value>tail</c:root>')
    changed_tail = ET.fromstring('<c:root xmlns:c="urn:chart"><c:value key="x"> A </c:value> tail</c:root>')
    assert _expanded_xml_tree(plain) != _expanded_xml_tree(changed_text)
    assert _expanded_xml_tree(plain) != _expanded_xml_tree(changed_tail)


@pytest.mark.parametrize("ratio", [.05, .95])
def test_export_keeps_an_unrepresentable_hole_drawn_instead_of_failing_the_deck(ratio, vendor):
    from services.generation_core.drivers.svg.export_scene import SvgPptxInputs, _annotate

    page = _page(ratio)
    item = _item(page)
    root = ET.fromstring(page["svg_content"])
    inputs = SvgPptxInputs()
    marked = _annotate(root, [(item, False)], 0, inputs, width=800, height=600)
    assert marked == 0
    assert [offer.reason for offer in inputs.offers] == ["native_chart_donut_hole_unrepresentable"]
    group = next(node for node in root.iter() if node.get("id") == "donut")
    assert "data-pptx-native" not in group.attrib
