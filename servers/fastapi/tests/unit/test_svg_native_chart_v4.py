"""Revision-pinned title proof for one drawn native chart series."""
from __future__ import annotations

from copy import deepcopy
import json
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg.native_declarations import (
    bind_native_declarations, extract_native_declarations,
)
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION, REQUIREMENTS_VERSION_V3, REQUIREMENTS_VERSION_V4,
)

_NS = "http://www.w3.org/2000/svg"
_VALUES = [30, 60, 90]


def _page(*, title="분기별 매출 추이", name="매출", legend=False, mutation=None):
    root = ET.Element(f"{{{_NS}}}svg", {"viewBox": "0 0 1280 720"})
    raw = {
        "chartType": "column", "categories": ["1분기", "2분기", "3분기"],
        "series": [{"name": name, "values": _VALUES}], "title": title,
        "categoryOrder": "leftToRight", "valueLabels": {"shown": True, "position": "outsideEnd"},
        "markers": False,
    }
    if mutation == "multiple_series":
        raw["series"].append({"name": "이익", "values": [10, 20, 30]})
    group = ET.SubElement(root, f"{{{_NS}}}g", {
        "id": "native-chart-sales", "data-native-chart": json.dumps(raw, ensure_ascii=False),
    })

    def text(value, x, y):
        node = ET.SubElement(group, f"{{{_NS}}}text", {
            "x": str(x), "y": str(y), "font-family": "Noto Sans KR", "font-size": "16",
            "fill": "#222222", "text-anchor": "middle",
        })
        node.text = value
        return node

    if mutation != "missing_title":
        text(title, 400, 120)
    if mutation == "duplicate_title":
        text(title, 400, 160)
    if mutation == "external_title":
        root.append(group[0])
        group.remove(group[0])
    if legend:
        text(name, 650, 200)
    for index, (label, value) in enumerate(zip(raw["categories"], _VALUES, strict=True)):
        height = value * 2 if mutation != "wrong_geometry" or index != 1 else value
        x = 250 + index * 150
        ET.SubElement(group, f"{{{_NS}}}rect", {
            "x": str(x - 30), "y": str(450 - height), "width": "60", "height": str(height),
            "fill": "#2563EB",
        })
        if mutation != "missing_category" or index != 1:
            text(label, x, 480)
        if mutation != "missing_value" or index != 1:
            text("77" if mutation == "wrong_value" and index == 1 else str(value), x, 438 - height)
    record = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    payload = {"svg_content": svg}
    bind_native_declarations(payload, svg, record)
    return payload


def _project(payload, revision=REQUIREMENTS_VERSION_V4):
    return page_native_objects(payload, pt_per_px=.75, revision=revision)


@pytest.mark.parametrize(("title", "name"), [
    ("분기별 매출 추이", "매출"),
    ("분기별 매출 (억원)", "매출(억원)"),
    ("Quarterly Revenue", "Revenue"),
    ("Revenue: quarterly trend", "Revenue"),
    ("분기별  매출 추이", "매출"),
])
def test_v4_unique_single_series_title_supplies_the_drawn_name(title, name):
    page = _page(title=title, name=name)
    frozen = deepcopy(page)
    wire, summary = _project(page)
    assert summary == {"declared": 1, "accepted": 1, "dropped": []}
    chart = wire["objects"][0]["payload"]
    assert chart["series"][0]["name"] == name
    assert chart["series"][0]["values"] == _VALUES
    assert chart["title"] == title
    assert page == frozen


@pytest.mark.parametrize("revision", [None, REQUIREMENTS_VERSION, REQUIREMENTS_VERSION_V3])
def test_older_revisions_still_require_a_separate_drawn_series_name(revision):
    wire, summary = _project(_page(), revision)
    assert wire is None
    assert summary["dropped"][0]["reason"] == "native_chart_label_not_drawn"


@pytest.mark.parametrize(("title", "legend"), [
    ("분기별 매출 추이", True),
    ("매출 · 단위: 억원", False),
])
def test_v3_publication_remains_byte_identical_to_legacy_default(title, legend):
    page = _page(title=title, legend=legend)
    legacy = page_native_objects(page, pt_per_px=.75)
    assert legacy[1]["accepted"] == 1
    assert json.dumps(_project(page, REQUIREMENTS_VERSION_V3), ensure_ascii=False) == json.dumps(
        legacy, ensure_ascii=False,
    )
    assert _project(page) == legacy


@pytest.mark.parametrize(("title", "name", "mutation"), [
    ("분기별 이익 추이", "매출", None),
    ("분기별 매출액 추이", "매출", None),
    ("Quarterly Prerevenue", "revenue", None),
    ("Revenue / Revenue", "Revenue", None),
    ("분기별 매출 추이", "매출", "duplicate_title"),
    ("분기별 매출 추이", "매출", "missing_title"),
    ("분기별 매출 추이", "매출", "external_title"),
    ("매출 및 이익", "매출", "multiple_series"),
])
def test_v4_does_not_borrow_an_ambiguous_unrelated_or_undrawn_title(title, name, mutation):
    wire, summary = _project(_page(title=title, name=name, mutation=mutation))
    assert wire is None
    assert summary["dropped"][0]["reason"] == "native_chart_label_not_drawn"


@pytest.mark.parametrize(("mutation", "reason"), [
    ("missing_category", "native_chart_label_not_drawn"),
    ("wrong_value", "native_chart_text_unaccounted"),
    ("missing_value", "native_chart_value_labels_mismatch"),
    ("wrong_geometry", "native_chart_value_geometry_mismatch"),
])
def test_v4_title_proof_preserves_category_value_and_geometry_checks(mutation, reason):
    wire, summary = _project(_page(mutation=mutation))
    assert wire is None
    assert summary["dropped"][0]["reason"] == reason


@pytest.mark.parametrize("revision", [None, REQUIREMENTS_VERSION_V3, REQUIREMENTS_VERSION_V4])
def test_slide_publication_uses_the_pinned_chart_projection_revision(revision):
    from services.generation_core.drivers.svg.publish import svg_slide_content

    page = _page()
    content = svg_slide_content(page, role="content", source="Synthetic chart", pt_per_px=.75,
                                revision=revision)
    summary = content["svgReceipt"]["nativeObjects"]
    assert summary["accepted"] == (1 if revision == REQUIREMENTS_VERSION_V4 else 0)
    if revision == REQUIREMENTS_VERSION_V4:
        assert content["nativeObjects"]["objects"][0]["payload"]["series"][0]["values"] == _VALUES
    else:
        assert "nativeObjects" not in content
        assert summary["dropped"][0]["reason"] == "native_chart_label_not_drawn"


@pytest.mark.parametrize("revision", [None, REQUIREMENTS_VERSION_V3, REQUIREMENTS_VERSION_V4])
def test_page_remake_uses_pinned_revision_for_quality_and_chart_projection(revision):
    from services.authored_canvas import canvas_metadata_for_aspect_ratio
    from services.generation_core.drivers.svg.native_declarations import PAYLOAD_KEY
    from services.generation_core.drivers.svg.native_requirements import requested_native_data
    from services.svg_page_remake import _native_candidate, _native_projection

    page = _page()
    svg = page["svg_content"]
    native = _native_candidate(svg, svg, page[PAYLOAD_KEY])
    metadata = canvas_metadata_for_aspect_ratio("16:9")
    source = "차트: 매출: 1분기 30, 2분기 60, 3분기 90"
    assert len(requested_native_data(source, REQUIREMENTS_VERSION_V4)["charts"]) == 1
    if revision == REQUIREMENTS_VERSION_V4:
        wire, summary = _native_projection(svg, native, metadata, source, revision=revision)
        assert summary["accepted"] == 1
        assert wire["objects"][0]["payload"]["series"][0]["values"] == _VALUES
    else:
        with pytest.raises(ValueError, match="^svg_page_remake_requested_native_missing$"):
            _native_projection(svg, native, metadata, source, revision=revision)
