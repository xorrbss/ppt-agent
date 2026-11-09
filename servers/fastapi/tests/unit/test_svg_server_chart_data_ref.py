"""Server charts by reference to the slide's chart literal (server_chart_data_ref)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg import server_chart
from services.generation_core.drivers.svg import server_chart_data_ref as data_ref
from services.generation_core.drivers.svg.native_declarations import (
    bind_native_declarations, extract_native_declarations,
)
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V4 as V4, REQUIREMENTS_VERSION_V5 as V5, apply_requested_native_quality,
)

NS = "{http://www.w3.org/2000/svg}"
V3 = server_chart.SERVER_CHART_REVISION_V3
PINNED = {"server_chart_render": V3, "server_chart_data_ref": data_ref.DATA_REF_REVISION,
          "native_requirements_revision": V4}
UNPINNED = {"server_chart_render": V3, "native_requirements_revision": V4}
SINGLE = "# 매출\nchart: 2023년 1,200억원, 2024년 1,450.5억원, 2025년 1,610억원"
MULTI = ("# 채널\nchart: 국내: 2024년 55%, 2025년 48%, 2026년 42%; 해외: 2024년 30%, 2025년 32%, 2026년 33%"
         "\nchart: 점수: 품질 87.4점, 배송 82.1점")


def _placeholder(declared: dict, ref: str | None, *, render=V3, slug="x") -> str:
    reference = f' data-chart-ref="{ref}"' if ref is not None else ""
    render_attribute = f' data-chart-render="{render}" data-chart-box="64 140 700 460"' if render else ""
    return (f"<g id=\"native-chart-{slug}\" data-native-chart='{json.dumps(declared, ensure_ascii=False)}'"
            f"{reference}{render_attribute}/>")


def _raw_svg(*groups: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            '<text x="64" y="80" font-family="Noto Sans KR" font-size="18" fill="#0F172A">Title</text>'
            + "".join(groups) + "</svg>")


def _extract(raw_svg: str, snapshot=PINNED, content=SINGLE):
    root = ET.fromstring(raw_svg)
    record = extract_native_declarations(root, chart_literals=data_ref.chart_literals(snapshot, content))
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, record)
    return page, record


def test_a_single_series_reference_is_filled_exactly_from_the_literal_with_its_unit():
    page, record = _extract(_raw_svg(_placeholder({"chartType": "column", "title": "연도별 매출"}, "chart-1")))
    (item,) = record["objects"]
    assert record["dropped"] == [] and item["dataRef"] == "chart-1" and item["render"] == V3
    assert item["raw"]["categories"] == ["2023년", "2024년", "2025년"]
    assert item["raw"]["title"] == "연도별 매출 (억원)"
    (series,) = item["raw"]["series"]
    assert series["values"] == [1200, 1450.5, 1610] and type(series["values"][0]) is int
    assert series["name"] == "연도별 매출 (억원)"
    labels = {node.text for node in ET.fromstring(page["svg_content"]).iter(NS + "text")}
    assert {"1,200억원", "1,450.5억원", "1,610억원"} <= labels
    assert "data-chart" not in page["svg_content"]


def test_a_multi_series_reference_keeps_every_series_name_and_its_presentation_fields():
    declared = {"chartType": "column", "title": "채널 비중 (%)", "colors": ["#1E3A8A", "#38BDF8"],
                "categories": ["typed", "by", "mistake"], "series": [{"name": "x", "values": [1, 2, 3]}]}
    _page, record = _extract(_raw_svg(_placeholder(declared, "chart-1")), content=MULTI)
    raw = record["objects"][0]["raw"]
    assert raw["categories"] == ["2024년", "2025년", "2026년"] and raw["title"] == "채널 비중 (%)"
    assert [(item["name"], item["values"], item["pointColors"][0]) for item in raw["series"]] == [
        ("국내", [55, 48, 42], "#1E3A8A"), ("해외", [30, 32, 33], "#38BDF8")]
    _page, record = _extract(_raw_svg(_placeholder({"chartType": "bar", "title": "고객 점수",
                                                     "seriesName": "점수"}, "chart-2")), content=MULTI)
    raw = record["objects"][0]["raw"]
    assert raw["title"] == "고객 점수 (점)" and raw["series"][0]["name"] == "점수"
    assert raw["series"][0]["values"] == [87.4, 82.1]


@pytest.mark.parametrize("ref,declared,reason", [
    ("chart-9", {"chartType": "column", "title": "매출"}, "server_chart_data_ref_unknown"),
    ("chart-1", {"chartType": "column"}, "server_chart_data_ref_title_missing"),
    ("chart-1", {"chartType": "radar", "title": "매출"}, "server_chart_type_unsupported"),
])
def test_an_unresolvable_reference_is_dropped_with_a_reason_and_left_empty(ref, declared, reason):
    page, record = _extract(_raw_svg(_placeholder(declared, ref)))
    assert record["objects"] == [] and record["dropped"] == [
        {"id": "native-chart-x", "kind": "chart", "reason": reason}]
    group = next(node for node in ET.fromstring(page["svg_content"]).iter(NS + "g") if node.get("id"))
    assert len(group) == 0 and not any(key.startswith("data-") for key in group.attrib)


def test_literals_with_units_bound_per_series_or_unsupported_cannot_be_referenced():
    mixed = "chart: 매출: 2024년 10억원, 2025년 12억원; 고객: 2024년 3명, 2025년 4명"
    assert data_ref.referenceable_ids(data_ref.chart_literals(PINNED, mixed)) == []
    _page, record = _extract(_raw_svg(_placeholder({"chartType": "column", "title": "t"}, "chart-1")),
                             content=mixed)
    assert record["dropped"][0]["reason"] == "server_chart_data_ref_units_mixed"


def test_a_reference_on_an_author_drawn_chart_is_dropped_only_for_pinned_decks():
    raw = _raw_svg(_placeholder({"chartType": "column", "title": "t"}, "chart-1", render=None))
    _page, record = _extract(raw)
    assert record["dropped"][0]["reason"] == "server_chart_data_ref_not_server_chart"
    # Without the policy the attribute is never read: extraction is exactly as before.
    root = ET.fromstring(raw)
    assert extract_native_declarations(root, chart_literals=data_ref.chart_literals(UNPINNED, SINGLE)) == \
        extract_native_declarations(ET.fromstring(raw))


def test_decks_admitted_without_the_policy_never_resolve_references():
    assert data_ref.chart_literals(UNPINNED, SINGLE) is None
    assert data_ref.chart_literals({"server_chart_data_ref": data_ref.DATA_REF_REVISION}, SINGLE) is None
    raw = _raw_svg(_placeholder({"chartType": "column", "title": "매출"}, "chart-1"))
    _page, record = _extract(raw, snapshot=UNPINNED)
    # The inline contract applies: the placeholder has no data to draw.
    assert record["objects"] == [] and record["dropped"][0]["reason"] == "server_chart_shape_invalid"


def test_grounding_is_skipped_for_references_and_kept_for_inline_charts():
    inline = {"chartType": "column", "title": "매출 (억원)", "categories": ["2023년", "2024년"],
              "series": [{"name": "매출", "values": [999, 1450.5]}]}
    page, record = _extract(_raw_svg(_placeholder({"chartType": "column", "title": "매출"}, "chart-1", slug="a"),
                                     _placeholder(inline, None, slug="b")))
    assert [item.get("dataRef") for item in record["objects"]] == ["chart-1", None]
    quality = {"passed": True, "failures": []}
    server_chart.apply_server_chart_grounding(quality, page, page["svg_content"], ["unrelated prose"])
    assert [item["declarationId"] for item in quality["failures"]] == ["native-chart-b"]


@pytest.mark.parametrize("revision", [V4, V5])
@pytest.mark.parametrize("content,ref,declared,required", [
    (SINGLE, "chart-1", {"chartType": "column", "title": "연도별 매출"}, SINGLE),
    (MULTI, "chart-1", {"chartType": "column", "title": "채널 비중"}, MULTI.split("\nchart: 점수")[0]),
    (MULTI, "chart-2", {"chartType": "bar", "title": "점수"}, MULTI.splitlines()[-1]),
])
def test_a_referenced_chart_is_trusted_and_matches_the_requirement(revision, content, ref, declared, required):
    snapshot = {**PINNED, "native_requirements_revision": revision}
    page, record = _extract(_raw_svg(_placeholder(declared, ref)), snapshot=snapshot, content=content)
    assert record["dropped"] == []
    assert server_chart.trusted_server_chart_ids(page["nativeDeclarations"], page["svg_content"]) == {
        "native-chart-x"}
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], required, pt_per_px=.75,
                                   revision=revision, paint_deferred=True)
    assert quality["failures"] == [] and quality["requestedNativeObjects"]["requiredCharts"] == 1


def test_a_correction_sees_a_referenced_chart_as_its_reference():
    page, record = _extract(_raw_svg(_placeholder({"chartType": "column", "title": "연도별 매출",
                                                    "colors": ["#1E3A8A"]}, "chart-1")))
    view = server_chart.placeholder_view(page["svg_content"], page["nativeDeclarations"])
    group = next(node for node in ET.fromstring(view).iter(NS + "g") if node.get("id"))
    assert group.get("data-chart-ref") == "chart-1"
    shown = json.loads(group.get("data-native-chart"))
    assert "categories" not in shown and "series" not in shown and shown["colors"] == ["#1E3A8A"] * 3
    # Replaying the view draws the same chart again.
    _again, replayed = _extract(view)
    assert replayed["objects"][0]["raw"] == record["objects"][0]["raw"]


def test_the_rule_and_the_page_ids_are_sent_only_to_pinned_decks():
    assert data_ref.data_ref_rule(UNPINNED) == "" and data_ref.data_ref_prompt(UNPINNED, MULTI) == ""
    assert "data-chart-ref" in data_ref.data_ref_rule(PINNED)
    assert data_ref.data_ref_prompt(PINNED, MULTI) == (
        "\nCHART REFERENCES: chart-1, chart-2. Draw each of these charts by reference (data-chart-ref) as the "
        "rules describe.\n")
    assert data_ref.data_ref_prompt(PINNED, "# no literal") == ""
    from services.generation_core.authoring.author_rules_v10 import svg_author_rules_v10
    from services.generation_core.authoring.author_slide_svg import svg_author_rules
    canvas = {"canvas": {"width": 1280, "height": 720}}
    assert data_ref.DATA_REF_RULE in svg_author_rules({**canvas, **PINNED}, 16)
    assert data_ref.DATA_REF_RULE not in svg_author_rules({**canvas, **UNPINNED}, 16)
    for snapshot, sent in ((PINNED, True), (UNPINNED, False)):
        rules = svg_author_rules_v10({**canvas, **snapshot}, {}, 16, physical_rules="", writing_policy="",
                                     header_applied=False)
        assert (data_ref.DATA_REF_RULE in rules) is sent


def test_admission_pins_the_policy_with_the_renderer_for_fresh_non_source_decks():
    import inspect
    from services import svg_generation_admission
    source = inspect.getsource(svg_generation_admission).split("if get_svg_server_charts_env():\n", 1)[1][:1200]
    assert 'if getattr(request, "source_mode", None) is None:' in source
    assert "frozen[DATA_REF_KEY] = DATA_REF_REVISION" in source
    assert data_ref.SNAPSHOT_KEY == "server_chart_data_ref"


def _author(monkeypatch, response: str, content: str, snapshot: dict):
    from services.generation_core.authoring import author_slide_svg as module
    from tests.unit.test_author_slide_svg_asset_offer import _stub_provider

    seen: dict = {}
    _stub_provider(monkeypatch, response, seen)
    style = {"contract_version": module.SVG_CONTRACT_VERSION, "digest": "d", "references": {}, **snapshot}
    return asyncio.run(module.author_slide_svg(content, style, "content", 1, 3)), seen


def test_every_replay_of_the_raw_response_resolves_and_draws_the_identical_tree(monkeypatch):
    from services.svg_author_output_view import parse_author_response
    from services.svg_partial_recovery import _replay_chart_literals

    response = _raw_svg(_placeholder({"chartType": "column", "title": "연도별 매출"}, "chart-1"))
    result, seen = _author(monkeypatch, response, SINGLE, PINNED)
    prompt = "\n".join(str(message) for message in seen["messages"])
    assert "CHART REFERENCES: chart-1." in prompt and '"id":"chart-1"' in prompt
    assert result.receipt["validation_error"] is None
    assert result.native_declarations["objects"][0]["dataRef"] == "chart-1"
    # Partial recovery replays from the pinned outline content of the same page.
    job = SimpleNamespace(svg_style_snapshot=PINNED,
                          authored_outline={"slides": [{"content": "x"}, {"content": SINGLE}]})
    root, _ = parse_author_response(result.raw_response, view=None, source_facts=None)
    replayed = extract_native_declarations(root, chart_literals=_replay_chart_literals(job, 1))
    assert replayed == result.native_declarations
    assert ET.tostring(root, encoding="unicode") == result.svg_content
    unpinned_job = SimpleNamespace(svg_style_snapshot=UNPINNED, authored_outline=None)
    assert _replay_chart_literals(unpinned_job, 1) is None
    # The page remake resolves from the content its author is given.
    import inspect
    from services import svg_page_remake
    assert "chart_literals=chart_literals(snapshot, source)" in inspect.getsource(svg_page_remake)


def test_an_unpinned_deck_author_prompt_and_extraction_are_unchanged(monkeypatch):
    response = _raw_svg(_placeholder({"chartType": "column", "title": "연도별 매출"}, "chart-1"))
    result, seen = _author(monkeypatch, response, SINGLE, UNPINNED)
    assert "CHART REFERENCES" not in "\n".join(str(message) for message in seen["messages"])
    assert result.native_declarations["dropped"][0]["reason"] == "server_chart_shape_invalid"
