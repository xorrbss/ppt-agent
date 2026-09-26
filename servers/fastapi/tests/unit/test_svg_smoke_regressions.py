"""Real S1 smoke drawings and their durable chart declarations."""
from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg.native_declarations import native_binding_digest
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.svg_contract import validate_svg
from services.svg_default_fonts import normalize_model_font_families


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "svg_smoke"
DECLARATIONS = json.loads((FIXTURES / "chart-declarations.json").read_text(encoding="utf-8"))
DRAWN_COLORS = {
    2: [["#2563EB"] * 4],
    3: [["#2563EB"] * 4, ["#94A3B8"] * 4],
    4: [["#93C5FD", "#60A5FA", "#3B82F6", "#2563EB"]],
    5: [["#94A3B8"] * 4, ["#2563EB"] * 4],
}


def _chart(slide: int, raw: dict | None = None, svg: str | None = None):
    svg = svg or (FIXTURES / f"slide-{slide}.svg").read_text(encoding="utf-8")
    root = ET.fromstring(svg)
    chart_id = next(node.get("id") for node in root.iter()
                    if node.get("id", "").startswith("native-chart"))
    payload = {"svg_content": svg, "nativeDeclarations": {
        "svgDigest": native_binding_digest(svg),
        "objects": [{"id": chart_id, "kind": "chart", "raw": raw or DECLARATIONS[str(slide)]}],
        "dropped": [],
    }}
    return page_native_objects(payload, pt_per_px=.75)


@pytest.mark.parametrize("slide", [2, 3, 4, 5])
def test_smoke_chart_uses_drawn_order_colors_names_and_labels(slide):
    wire, summary = _chart(slide)
    assert summary == {"declared": 1, "accepted": 1, "dropped": []}
    chart = wire["objects"][0]
    assert chart["payload"]["categories"] == ["1분기", "2분기", "3분기", "4분기"]
    assert [series["pointColors"] for series in chart["payload"]["series"]] == DRAWN_COLORS[slide]
    assert chart["svgAppearance"]["valueLabels"] == {
        "shown": True, "position": "insideEnd" if slide == 2 else "outsideEnd"}
    if slide == 3:
        assert [series["name"] for series in chart["payload"]["series"]] == ["매출", "영업이익"]


@pytest.mark.parametrize("slide", [2, 3, 4, 5])
def test_smoke_chart_rejects_a_wrong_declared_value(slide):
    raw = json.loads(json.dumps(DECLARATIONS[str(slide)]))
    raw["series"][0]["values"][0] += 10
    wire, summary = _chart(slide, raw)
    assert wire is None and summary["accepted"] == 0
    assert summary["dropped"][0]["reason"] in {
        "native_chart_text_unaccounted", "native_chart_value_geometry_mismatch",
        "native_chart_value_labels_mismatch"}


def test_smoke_chart_follows_drawing_when_declared_labels_or_colors_differ():
    raw = json.loads(json.dumps(DECLARATIONS["4"]))
    raw["valueLabels"] = {"shown": False, "position": None}
    raw["series"][0]["pointColors"] = ["#000000"] * 4
    wire, summary = _chart(4, raw)
    assert summary["accepted"] == 1
    chart = wire["objects"][0]
    assert chart["svgAppearance"]["valueLabels"] == {"shown": True, "position": "outsideEnd"}
    assert chart["payload"]["series"][0]["pointColors"] == DRAWN_COLORS[4][0]


def test_smoke_chart_hides_labels_when_the_drawing_has_none():
    root = ET.fromstring((FIXTURES / "slide-2.svg").read_text(encoding="utf-8"))
    group = next(node for node in root.iter() if node.get("id", "").startswith("native-chart"))
    for node in list(group):
        if node.tag.endswith("text") and "".join(node.itertext()) in {"120", "145", "160", "185"}:
            group.remove(node)
    wire, summary = _chart(2, svg=ET.tostring(root, encoding="unicode"))
    assert summary["accepted"] == 1
    assert wire["objects"][0]["svgAppearance"]["valueLabels"] == {"shown": False, "position": None}


def test_smoke_chart_rejects_a_wrong_category():
    raw = json.loads(json.dumps(DECLARATIONS["2"]))
    raw["categories"][0] = "5분기"
    wire, summary = _chart(2, raw)
    assert wire is None and summary["dropped"][0]["reason"] == "native_chart_label_not_drawn"


def test_smoke_chart_allows_numeric_axis_ticks_as_furniture():
    root = ET.fromstring((FIXTURES / "slide-2.svg").read_text(encoding="utf-8"))
    group = next(node for node in root.iter() if node.get("id", "").startswith("native-chart"))
    for y, value in ((400, "0"), (300, "200")):
        node = ET.SubElement(group, "{http://www.w3.org/2000/svg}text", {
            "x": "790", "y": str(y), "font-family": "Noto Sans KR", "font-size": "12", "fill": "#333333"})
        node.text = value
    wire, summary = _chart(2, svg=ET.tostring(root, encoding="unicode"))
    assert summary["accepted"] == 1 and wire is not None


def test_smoke_page_five_unbundled_family_is_substituted_with_an_advisory():
    svg = (FIXTURES / "slide-5.svg").read_text(encoding="utf-8")
    normalized, advisories = normalize_model_font_families(svg)
    assert advisories == [{"kind": "font_family_substituted", "requested_family": "N Sans KR",
                           "resolved_family": "Noto Sans KR"}]
    assert all(node.get("font-family") != "N Sans KR" for node in ET.fromstring(normalized).iter())
    validate_svg(normalized)
