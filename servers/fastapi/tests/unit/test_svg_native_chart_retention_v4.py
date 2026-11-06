"""Content-safe v4 retention and actionable native chart repair evidence."""
from copy import deepcopy
import json
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg.native_declarations import bind_native_declarations
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V3 as V3, REQUIREMENTS_VERSION_V4 as V4,
    SNAPSHOT_REVISION_KEY, apply_requested_native_quality, native_chart_repair_hint,
)
from services.generation_core.quality.page_failure_kind import quality_keeps_content
from services.svg_generation_admission import pin_svg_style
from tests.unit.test_svg_native_chart_v4 import _page

SOURCE = "chart: 매출: 1분기 30, 2분기 60, 3분기 90"


def _quality(page, source=SOURCE, revision=V4):
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], source,
                                   pt_per_px=.75, revision=revision)
    return quality


def _edit(page, edit):
    root = ET.fromstring(page["svg_content"])
    edit(root)
    page["svg_content"] = ET.tostring(root, encoding="unicode")
    bind_native_declarations(page, page["svg_content"], page["nativeDeclarations"])
    return page


def test_v4_keeps_drawn_content_when_only_native_geometry_is_unprojectable():
    page = _page(legend=True, mutation="wrong_geometry")
    unchanged = deepcopy(page)
    quality = _quality(page)
    assert quality["passed"] is False
    assert quality["failures"][0]["kind"] == "native_requested_chart_not_editable"
    assert quality["requestedNativeObjects"]["projection"]["accepted"] == 0
    assert quality_keeps_content(quality)
    assert page == unchanged
    legacy = _quality(page, revision=V3)
    assert legacy["failures"][0]["kind"] == "native_requested_chart_missing"
    assert not quality_keeps_content(legacy)


def test_v4_title_projection_satisfies_the_requirement_without_any_quality_failure():
    quality = _quality(_page())
    assert quality["passed"] is True
    assert quality["requestedNativeObjects"]["projection"]["accepted"] == 1
    assert quality["failures"] == []


@pytest.mark.parametrize("mutation", ["missing_category", "missing_value", "wrong_value"])
def test_missing_or_changed_content_still_requires_fallback(mutation):
    quality = _quality(_page(legend=True, mutation=mutation))
    assert quality["failures"][0]["kind"] == "native_requested_chart_missing"
    assert not quality_keeps_content(quality)


@pytest.mark.parametrize(("attribute", "value"), [
    ("opacity", "0"), ("fill-opacity", "0"), ("fill", "none"),
    ("display", "none"), ("visibility", "hidden"), ("clip-path", "url(#clip)"),
    ("font-size", "0"),
])
def test_hidden_value_never_proves_preserved_content(attribute, value):
    page = _page(legend=True, mutation="wrong_geometry")
    def hide(root):
        next(node for node in root.iter() if node.text == "60").set(attribute, value)
    quality = _quality(_edit(page, hide))
    assert not quality_keeps_content(quality)


def test_sibling_text_cannot_supply_a_missing_chart_value():
    page = _page(legend=True, mutation="missing_value")
    def move(root):
        sample = next(node for node in root.iter() if node.text == "30")
        sibling = ET.SubElement(root, sample.tag, sample.attrib)
        sibling.text = "60"
    assert not quality_keeps_content(_quality(_edit(page, move)))


@pytest.mark.parametrize("container", ["defs", "symbol", "switch", "mask", "clipPath"])
def test_non_rendering_ancestor_never_proves_visible_chart_data(container):
    page = _page(legend=True, mutation="wrong_geometry")
    def wrap(root):
        group = root[0]
        root.remove(group)
        parent = ET.SubElement(root, "{http://www.w3.org/2000/svg}" + container)
        parent.append(group)
    assert not quality_keeps_content(_quality(_edit(page, wrap)))


def test_invented_extra_numeric_label_remains_a_content_failure():
    page = _page(legend=True)
    def invent(root):
        sample = next(node for node in root.iter() if node.text == "30")
        extra = ET.SubElement(root[0], sample.tag, {**sample.attrib, "x": "800"})
        extra.text = "77"
    assert not quality_keeps_content(_quality(_edit(page, invent)))


def test_repeated_value_requires_each_drawn_occurrence():
    page = _page(legend=True, mutation="wrong_geometry")
    page["nativeDeclarations"]["objects"][0]["raw"]["series"][0]["values"][1] = 30
    def remove(root):
        node = next(node for node in root.iter() if node.text == "60")
        root[0].remove(node)
    quality = _quality(_edit(page, remove), SOURCE.replace("2분기 60", "2분기 30"))
    assert not quality_keeps_content(quality)


def test_repeated_category_requires_each_drawn_occurrence():
    page = _page(legend=True, mutation="wrong_geometry")
    page["nativeDeclarations"]["objects"][0]["raw"]["categories"][1] = "1분기"
    def remove(root):
        node = next(node for node in root.iter() if node.text == "2분기")
        root[0].remove(node)
    quality = _quality(_edit(page, remove), SOURCE.replace("2분기", "1분기"))
    assert not quality_keeps_content(quality)


def test_heterogeneous_series_units_do_not_borrow_one_anothers_values():
    from services.generation_core.drivers.svg.native_chart_evidence import chart_value_differences
    expected = {"series": [{"values": [30]}, {"values": [60]}],
        "requiredUnitLabels": [{"seriesName": "매출", "unit": "억원"},
                               {"seriesName": "인원", "unit": "명"}]}
    differences = chart_value_differences({"valueTexts": ["30명", "60억원"]}, expected)
    assert differences["missingValues"] == ["30", "60"]


def test_stale_or_absent_declarations_cannot_authorize_retention():
    page = _page(legend=True, mutation="wrong_geometry")
    page["nativeDeclarations"]["svgDigest"] = "stale"
    assert not quality_keeps_content(_quality(page))
    page.pop("nativeDeclarations")
    assert not quality_keeps_content(_quality(page))


@pytest.mark.parametrize("source", [
    SOURCE.replace("60", "61"), SOURCE.replace("2분기", "4분기"),
    SOURCE.replace("매출:", "이익:"), SOURCE + ". 단위: 억원",
])
def test_declared_data_or_unit_mismatch_stays_a_content_failure(source):
    assert not quality_keeps_content(_quality(_page(legend=True, mutation="wrong_geometry"), source))


def test_other_content_failures_cannot_be_downgraded_by_a_drawn_chart():
    quality = _quality(_page(legend=True, mutation="wrong_geometry"))
    quality["failures"].append({"kind": "required_text_missing"})
    assert not quality_keeps_content(quality)


def test_retry_hint_names_projection_reason_and_undrawn_labels_and_values():
    quality = _quality(_page(legend=True, mutation="missing_category"))
    hint = json.loads(native_chart_repair_hint(quality, revision=V4))
    assert hint["dropped"][0]["reason"] == "native_chart_label_not_drawn"
    assert hint["dropped"][0]["missingLabels"] == ["2분기"]
    quality = _quality(_page(legend=True, mutation="missing_value"))
    hint = json.loads(native_chart_repair_hint(quality, revision=V4))
    assert hint["dropped"][0]["missingValues"] == ["60"]
    assert native_chart_repair_hint(quality, revision=V3) is None


def test_large_retry_hint_is_complete_json_with_room_for_geometry_witnesses():
    quality = _quality(_page(legend=True, mutation="missing_value"))
    drop = quality["requestedNativeObjects"]["projection"]["dropped"][0]
    drop["missingLabels"] = ["Long category " * 100] * 50
    quality["requestedNativeObjects"]["projection"]["dropped"] = [deepcopy(drop) for _ in range(25)]
    quality["failures"] = [{"kind": "text_overlap", "pairs": ["long"] * 1000}]
    hint = native_chart_repair_hint(quality, revision=V4)
    assert len(hint) <= 2400
    parsed = json.loads(hint)
    assert parsed["dropped"][0]["reason"] == drop["reason"]
    assert parsed["dropped"][0]["missingValues"] == ["60"]
    assert parsed["omittedCharts"] > 0


def test_v3_frozen_admission_snapshot_remains_byte_identical():
    snapshot = {SNAPSHOT_REVISION_KEY: V3, "digest": "stored-style-digest"}
    before = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()
    request = SimpleNamespace(_svg_style_snapshot=snapshot)
    assert pin_svg_style(request) is snapshot
    assert json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode() == before
