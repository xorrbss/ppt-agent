"""V5 chart content rules: rounded and share value labels, annotation captions
and legend swatches. Every rule is exact (no tolerance) and V5-only."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg.export_chart_style import apply_drawn_chart_style
from services.generation_core.drivers.svg.export_projection import converter_payload
from services.generation_core.drivers.svg.native_chart_evidence import (
    _VALUE, _v5_label_match, chart_drawing_evidence,
)
from services.generation_core.drivers.svg.native_declarations import native_binding_digest
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V4 as V4, REQUIREMENTS_VERSION_V5 as V5,
)
from services.generation_core.native_objects import NativeObjects
from services.generation_core.quality.page_failure_kind import quality_keeps_content
from tests.unit.test_svg_native_chart_retention_v5 import (
    NS, _edit, _page, _polar_page, _proof, _quality, _text,
)

SMOKE = Path(__file__).resolve().parents[1] / "fixtures" / "svg_smoke"
SMOKE_DECLARATIONS = json.loads((SMOKE / "chart-declarations.json").read_text(encoding="utf-8"))


def _self_quality(page):
    # No source literal: only the drawing/declaration relationship is judged.
    return _quality(page, source="")


def _relabel(values, labels):
    def change(root, raw):
        for old, new in labels.items():
            _text(root, old).text = new
        raw["series"][0]["values"] = list(values)
    return change


def _smoke(slide, *, raw=None, svg=None):
    svg = svg or (SMOKE / f"slide-{slide}.svg").read_text(encoding="utf-8")
    root = ET.fromstring(svg)
    chart_id = next(node.get("id") for node in root.iter() if node.get("id", "").startswith("native-chart"))
    page = {"svg_content": svg, "nativeDeclarations": {
        "svgDigest": native_binding_digest(svg),
        "objects": [{"id": chart_id, "kind": "chart", "raw": raw or json.loads(json.dumps(SMOKE_DECLARATIONS[str(slide)]))}],
        "dropped": []}}
    return page, chart_id


def _smoke_geometry(slide, *, raw=None, svg=None, revision=V5):
    page, chart_id = _smoke(slide, raw=raw, svg=svg)
    return chart_drawing_evidence(page, page["svg_content"], revision=revision)[chart_id]


# -- 1. Rounded value labels -------------------------------------------------

@pytest.mark.parametrize(("value", "label"), [
    (60.345, "60.3"), (60.35, "60.4"), (60.5, "61"), (60.345, "60"), (60.345, "60.35"),
])
def test_rounded_label_matches_its_half_up_quantized_datum(value, label):
    page = _edit(_page(), _relabel([30, value, 90], {"60": label}))
    proof = _proof(page)
    assert proof["passed"], proof
    binding = next(item for item in proof["valueLabelBindings"] if item["categoryIndex"] == 1)
    assert binding["valueMatch"] == "rounded"
    assert quality_keeps_content(_self_quality(page))


@pytest.mark.parametrize(("value", "label"), [
    (60.36, "60.3"),   # wrong rounding
    (60.25, "60.2"),   # HALF_UP, never half-even
    (60.5, "60"),
    (60.345, "60.34"),  # truncation is not rounding
])
def test_wrongly_rounded_label_is_refused(value, label):
    page = _edit(_page(), _relabel([30, value, 90], {"60": label}))
    assert not _proof(page)["passed"]
    assert not quality_keeps_content(_self_quality(page))


def test_exact_labels_keep_their_original_binding_shape():
    proof = _proof(_page())
    assert proof["passed"] and all("valueMatch" not in item for item in proof["valueLabelBindings"])
    assert "annotations" not in proof


# -- 2. Share labels --------------------------------------------------------

@pytest.mark.parametrize("kind", ["pie", "donut"])
def test_polar_share_labels_state_each_datums_share_of_the_series_total(kind):
    page = _edit(_polar_page(kind), lambda root, raw: [
        setattr(_text(root, old), "text", new) for old, new in (("30", "16.7%"), ("60", "33.3%"), ("90", "50%"))])
    proof = _proof(page)
    assert proof["passed"], proof
    assert sorted(item.get("valueMatch") for item in proof["valueLabelBindings"]) == ["share"] * 3
    assert quality_keeps_content(_self_quality(page))


@pytest.mark.parametrize("label", ["16.6%", "16.8%", "17.0%", "16.7"])
def test_share_label_off_by_one_rounding_step_or_without_percent_is_refused(label):
    page = _edit(_polar_page("pie"), lambda root, raw: [
        setattr(_text(root, old), "text", new) for old, new in (("30", label), ("60", "33.3%"), ("90", "50%"))])
    assert not _proof(page)["passed"]


def test_share_label_on_a_column_chart_is_refused():
    # 30 of 30 + 60 + 90 is 16.7%, but a column chart has no whole to share.
    page = _edit(_page(), lambda root, raw: setattr(_text(root, "30"), "text", "16.7%"))
    assert not _proof(page)["passed"]
    assert not quality_keeps_content(_self_quality(page))


def _match(label, chart, series_index, category_index):
    match = _VALUE.fullmatch(label)
    number = Decimal(match[3].replace(",", "") + (match[4] or ""))
    point = {"seriesIndex": series_index, "categoryIndex": category_index,
             "value": chart["series"][series_index]["values"][category_index]}
    return _v5_label_match(number, match, point, chart)


@pytest.mark.parametrize("grouping", ["percentStacked", "stacked", "clustered"])
def test_share_of_category_total_only_for_percent_stacked(grouping):
    chart = {"chartType": "column", "grouping": grouping, "categories": ["A", "B"],
             "series": [{"name": "x", "values": [10, 1]}, {"name": "y", "values": [20, 2]}]}
    # Category A total 30: 10 is 33.3%, 20 is 66.7%.
    expected = "share" if grouping == "percentStacked" else None
    assert _match("33.3%", chart, 0, 0) == expected
    assert _match("66.7%", chart, 1, 0) == expected
    assert _match("33.4%", chart, 0, 0) is None
    assert _match("66.6%", chart, 1, 0) is None
    assert _match("$33.3%", chart, 0, 0) is None
    for chart_type in ("line", "area", "bar"):
        assert _match("33.3%", {**chart, "chartType": chart_type, "grouping": "clustered"}, 0, 0) is None


# -- 3. Annotation captions ---------------------------------------------------

def _caption(value, x, y, anchor="start"):
    def change(root, raw):
        node = ET.SubElement(root[0], NS + "text", {"x": str(x), "y": str(y), "font-size": "16",
                                                    "fill": "#222222", "text-anchor": anchor})
        node.text = value
    return change


@pytest.mark.parametrize("caption", ["단위: 억원", "Unit: USD", "출처: 통계청", "Source: OECD", "※ 잠정치",
                                     "주) 계열별 단위 (%)", "자료：한국은행", "Note kg/m²"])
def test_digit_free_caption_outside_the_plot_is_a_recorded_annotation(caption):
    page = _edit(_page(), _caption(caption, 60, 60))
    proof = _proof(page)
    assert proof["passed"], proof
    assert [item["text"] for item in proof["annotations"]] == [caption]
    assert quality_keeps_content(_self_quality(page))


@pytest.mark.parametrize("caption", ["※ 2025년 120억", "단위: 2024년 기준", "Source: OECD 2024", "Notebook sales"])
def test_caption_with_a_data_like_number_or_no_prefix_keeps_text_accounting(caption):
    proof = _proof(_edit(_page(), _caption(caption, 60, 60)))
    assert proof == {"passed": False, "reason": "native_chart_content_text_unaccounted"}


def test_annotation_prefixed_text_inside_the_plot_area_is_refused():
    # Between the first two bars, vertically within the bar extent.
    proof = _proof(_edit(_page(), _caption("Unit: kg", 325, 440, anchor="middle")))
    assert proof == {"passed": False, "reason": "native_chart_content_text_unaccounted"}


def test_real_smoke_unit_caption_is_an_annotation_and_export_keeps_it_drawn():
    evidence = _smoke_geometry(5)
    geometry = evidence["contentGeometry"]
    assert geometry["passed"], geometry
    assert geometry["annotations"] == [{"textIndex": 1, "text": "단위: 억원", "kind": "unit"}]
    page, chart_id = _smoke(5)
    wire, summary = page_native_objects(page, pt_per_px=.75, revision=V5)
    assert summary["accepted"] == 1
    item = NativeObjects.from_wire(wire).objects[0]
    projected = converter_payload(item)
    furniture = apply_drawn_chart_style(projected, item, ET.fromstring(page["svg_content"]))
    # Export keeps the caption as editable drawn text beside the native chart;
    # the native wire payload itself is unchanged.
    assert ["".join(node.itertext()) for node in furniture.iter() if node.tag.endswith("text")] == ["단위: 억원"]
    assert "valueAxis" not in item.payload or not item.payload["valueAxis"].get("title")


def test_v4_evidence_has_no_v5_content_rules():
    assert "contentGeometry" not in _smoke_geometry(5, revision=V4)


# -- 5. Legend swatches -------------------------------------------------------

def test_dual_axis_smoke_chart_reports_its_real_scale_mismatch():
    geometry = _smoke_geometry(3)["contentGeometry"]
    assert geometry["reason"] == "chart_content_bar_common_scale_or_baseline_mismatch"


def _qualified_slide5():
    raw = json.loads(json.dumps(SMOKE_DECLARATIONS["5"]))
    raw["series"][0]["name"] = "매출 (억원)"
    raw["series"][1]["name"] = "영업이익 (억원)"
    return raw


def test_short_legend_name_with_series_colour_is_a_legend_swatch():
    geometry = _smoke_geometry(5, raw=_qualified_slide5())["contentGeometry"]
    assert geometry["passed"], geometry
    assert geometry["seriesBindings"] == ["visible-legend", "visible-legend"]


@pytest.mark.parametrize("qualified", [False, True])
def test_legend_like_swatch_matching_no_series_colour_is_refused(qualified):
    svg = (SMOKE / "slide-5.svg").read_text(encoding="utf-8").replace(
        '<rect x="78" y="252" width="14" height="14" fill="#94A3B8" />',
        '<rect x="78" y="252" width="14" height="14" fill="#16A34A" />')
    assert "#16A34A" in svg
    geometry = _smoke_geometry(5, raw=_qualified_slide5() if qualified else None, svg=svg)["contentGeometry"]
    assert not geometry["passed"]
