"""Bound V5 chart values use geometry without erasing equal-valued prose."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V5 as V5, _charts, apply_requested_native_quality,
    requested_native_data, required_numeric_claims_with_native_charts,
)
from services.generation_core.drivers.svg.render import _native_source_content
from services.svg_presentation_checkpoint import _required_numeric_claims
from tests.unit.test_svg_native_chart_retention_v5 import (
    NS, _cartesian_page, _edit, _polar_page,
)


def _fixture(kind="column"):
    page = (_polar_page(kind, values=(10, 20, 30), shown=False) if kind in {"pie", "donut"}
            else _cartesian_page(kind, [[10, 20, 30]]))
    raw = page["nativeDeclarations"]["objects"][0]["raw"]
    source = "chart: " + raw["series"][0]["name"] + ": " + ", ".join(
        f"{category} {value}" for category, value in zip(raw["categories"], [10, 20, 30], strict=True))
    return page, source


def _claims(page, source, *, content=None, revision=V5, policy=None):
    return required_numeric_claims_with_native_charts(
        source if content is None else content, page, page["svg_content"], native_source=source,
        pt_per_px=.75, revision=revision, quality_policy=policy)


_LEGACY = json.loads((Path(__file__).resolve().parent / "fixtures" / "svg_v5_legacy_parity_v1.json")
                     .read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _LEGACY["numericRequestedWire"],
                         ids=lambda case: f"{case['revision']}:{case['source'][:24]}")
def test_internal_span_collection_preserves_exact_pre_v5_legacy_requested_wire(case):
    """Frozen outputs of the pre-V5 parser (fixture baselineCommit), not the checkout's HEAD."""
    def wire(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    assert wire(requested_native_data(case["source"], case["revision"])) == case["requested"]


@pytest.mark.parametrize("kind", ["bar", "column", "line", "area", "pie", "donut"])
def test_unlabeled_supported_charts_use_bound_requested_geometry_without_plaintext_values(kind):
    page, source = _fixture(kind)
    before = deepcopy(page)
    assert _required_numeric_claims(source) == ["10", "20", "30"]
    assert _claims(page, source) == []
    assert page == before


@pytest.mark.parametrize("revision", [None, "svg-native-requirements/v2", "svg-native-requirements/v3",
                                      "svg-native-requirements/v4", "unknown"])
def test_old_or_unknown_revisions_preserve_every_existing_numeric_claim(revision):
    page, source = _fixture()
    assert _claims(page, source, revision=revision) == _required_numeric_claims(source)


@pytest.mark.parametrize("attack", ["unbound", "wrong-value", "wrong-category", "wrong-geometry", "malformed"])
def test_unmatched_or_invalid_native_authority_keeps_all_literal_numbers(attack):
    page, source = _fixture()
    if attack == "unbound":
        page["nativeDeclarations"] = {}
    elif attack == "wrong-value":
        source = source.replace("10", "11", 1)
    elif attack == "wrong-category":
        source = source.replace("A 10", "Other 10", 1)
    elif attack == "wrong-geometry":
        _edit(page, lambda root, raw: next(root.iter(NS + "rect")).set("height", "5"))
    else:
        source = "chart: Series 1: A 10, B unreadable, C 30"
    assert _claims(page, source) == _required_numeric_claims(source)


def test_identical_numeric_prose_notes_and_chart_category_digits_are_not_value_authority():
    page, source = _fixture()
    prose = "Unrelated commitment 10. Donut hole 0.7; axis 0%, 50, 100."
    assert _claims(page, source, content=source + "\n" + prose) == _required_numeric_claims(prose)
    spans = {}
    text = "chart: Sales 2026: Category 2026 10 (note 77), Other 2027 20"
    charts = _charts(text, V5, value_spans=spans)
    assert len(charts) == 1
    assert [text[start:end] for start, end in spans["chart-1"]] == ["10", "20"]
    masked = list(text)
    for start, end in spans["chart-1"]:
        masked[start:end] = " " * (end - start)
    assert _required_numeric_claims("".join(masked)) == ["2026", "77", "2027"]


def test_policy_two_projection_offsets_keep_equal_prose_and_drop_only_chart_values():
    page, source = _fixture()
    content = ("# 원본 7페이지\n## 원문 전체 텍스트 (읽기 순서, 모든 수치와 정책 조건 보존)\n"
               + source + "\nUnrelated prose 10\n## 원본 시각 구성 근거\ncontext 999")
    assert _claims(page, source, content=content, policy={"version": 2}) == ["10"]


@pytest.mark.parametrize("source,values", [
    ("chart: Sales: A 1,000원, B 2,000원", ["1,000원", "2,000원"]),
    ("chart: Sales: A 30 (note 99); B 60", ["30", "60"]),
    ("chart: S1: A 10, B 20; S2: A 30, B 40", ["10", "20", "30", "40"]),
    ("prefix chart: Sales: A -1.5%, B +2.5%. unit: %", ["-1.5%", "+2.5%"]),
])
def test_internal_value_spans_are_exact_and_do_not_change_requested_chart_wire(source, values):
    spans = {}
    charts = _charts(source, V5, value_spans=spans)
    assert charts == _charts(source, V5)
    assert [source[start:end] for chart in charts for start, end in spans[chart["id"]]] == values


def test_failed_series_parse_never_publishes_partial_value_spans():
    spans = {}
    assert _charts("chart: S1: A 10, B 20; S2: A unreadable, B 40", V5, value_spans=spans) == []
    assert spans == {}


def test_point_units_are_part_of_value_spans_but_scientific_parse_uncertainty_is_not_exempt():
    page, source = _fixture()

    def title_with_unit(root, raw):
        raw["title"] = "Sales (%)"
        ET.SubElement(next(root.iter(NS + "g")), NS + "text", {
            "x": "400", "y": "80", "font-size": "16", "fill": "#222222"}).text = raw["title"]

    _edit(page, title_with_unit)
    source = source.replace("10", "10%", 1).replace("20", "20%", 1).replace("30", "30%", 1)
    assert _claims(page, source) == []
    scientific = "chart: Series 1: A 1.2e3, B 2.4e3"
    assert _claims(page, scientific) == _required_numeric_claims(scientific)


def test_equivalent_repeated_requests_consume_distinct_valid_chart_declarations():
    from services.generation_core.drivers.svg.native_declarations import (
        bind_native_declarations, extract_native_declarations,
    )

    page, source = _fixture()
    root = ET.fromstring(page["svg_content"])
    # The original legend is at x=900/925; the translated copy must fit too.
    root.set("viewBox", "0 0 1920 720")
    first = next(root.iter(NS + "g"))
    raw = deepcopy(page["nativeDeclarations"]["objects"][0]["raw"])
    first.set("data-native-chart", json.dumps(raw))
    second = deepcopy(first)
    second.set("id", "native-chart-copy")
    second.set("transform", "translate(600 0)")
    root.append(second)
    record = extract_native_declarations(root)
    page["svg_content"] = ET.tostring(root, encoding="unicode")
    bind_native_declarations(page, page["svg_content"], record)
    from services.generation_core.drivers.svg.native_chart_evidence import chart_drawing_evidence

    proofs = chart_drawing_evidence(page, page["svg_content"], revision=V5)
    assert len(proofs) == 2
    assert all(not proof["missingLabels"] and proof["contentGeometry"]["passed"] for proof in proofs.values())
    repeated = source + "\n" + source
    assert _claims(page, repeated) == []
    # A third occurrence cannot borrow either consumed declaration.
    assert _claims(page, repeated, content=repeated + "\n" + source) == ["10", "20", "30"]
    # Correct rebinding alone cannot authorize the original off-canvas copy.
    narrow_page = deepcopy(page)
    root.set("viewBox", "0 0 1280 720")
    narrow_page["svg_content"] = ET.tostring(root, encoding="unicode")
    bind_native_declarations(narrow_page, narrow_page["svg_content"], record)
    assert _claims(narrow_page, repeated) == ["10", "20", "30"]


def test_native_source_selection_matches_the_final_gate_and_preserves_fallback():
    request = SimpleNamespace(slides_markdown=["original 0", "original 1"])
    assert _native_source_content(request, 1, 2, "outline") == "original 1"
    assert _native_source_content(request, 1, 3, "outline") == "outline"


def test_static_numeric_authority_never_substitutes_for_the_same_capture_final_paint_gate():
    page, source = _fixture()
    assert _claims(page, source) == []
    quality = {"passed": True, "failures": [], "measurement_complete": True, "browser_metrics": {}}
    apply_requested_native_quality(quality, page, page["svg_content"], source,
        pt_per_px=.75, revision=V5, render_capture=None, post_render=True)
    assert quality["passed"] is False
    assert quality["requestedNativeObjects"]["contentRetentionProven"] is False
    assert any(item["kind"] == "measurement_incomplete" and item.get("scope") == "native_chart_paint"
               for item in quality["failures"])
