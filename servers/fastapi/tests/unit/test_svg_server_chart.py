"""Server-rendered bar/column charts (SVG_SERVER_CHARTS prototype)."""
from __future__ import annotations

import json
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg import server_chart
from services.generation_core.drivers.svg.native_declarations import (
    bind_native_declarations, extract_native_declarations,
)
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V4 as V4, REQUIREMENTS_VERSION_V5 as V5, apply_requested_native_quality,
)

NS = "{http://www.w3.org/2000/svg}"
SALES = {"chartType": "bar", "categories": ["국내영업", "해외영업", "온라인", "파트너"],
         "series": [{"name": "매출(억원)", "values": [420, 315, 268, 142],
                     "pointColors": ["#2563EB", "#94A3B8", "#94A3B8", "#94A3B8"]}],
         "valueLabels": {"shown": True, "position": "insideEnd"}, "title": "매출(억원)"}
CHANNELS = {"chartType": "column", "categories": ["2024년", "2025년", "2026년"],
            "series": [{"name": "국내", "values": [55, 48, 42]}, {"name": "해외", "values": [30, 32, 33]},
                       {"name": "온라인", "values": [15, 20, 25]}], "title": "채널 비중 (%)"}
SCORES = {"chartType": "bar", "categories": ["제품 품질", "배송"], "series": [{"name": "점수", "values": [87.4, 82.1]}],
          "valueLabels": {"shown": True, "position": "center"}}


def _raw_svg(raw, *, render="server", box="64 140 700 460"):
    attributes = f" data-chart-render=\"{render}\"" if render is not None else ""
    attributes += f" data-chart-box=\"{box}\"" if box is not None else ""
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            '<text x="64" y="80" font-family="Noto Sans KR" font-size="36" fill="#0F172A">Title</text>'
            f"<g id=\"native-chart-x\" data-native-chart='{json.dumps(raw, ensure_ascii=False)}'{attributes}/></svg>")


def _extract(raw_svg):
    root = ET.fromstring(raw_svg)
    record = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, record)
    return page, record


@pytest.mark.parametrize("raw", [SALES, CHANNELS, SCORES])
@pytest.mark.parametrize("revision", [V4, V5])
def test_server_drawing_passes_the_unchanged_native_contract_and_exports_natively(raw, revision):
    page, record = _extract(_raw_svg(raw))
    assert record["dropped"] == [] and record["objects"][0]["render"] == server_chart.SERVER_CHART_REVISION
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], "", pt_per_px=.75, revision=revision,
                                   paint_deferred=True)
    assert quality["failures"] == []
    wire, _summary = page_native_objects(page, pt_per_px=.75, revision=revision)
    (chart,) = wire["objects"]
    assert chart["kind"] == "chart"


def test_every_replay_of_a_raw_response_draws_the_identical_tree():
    raw = _raw_svg(CHANNELS)
    first, _ = _extract(raw)
    second, _ = _extract(raw)
    assert first["svg_content"] == second["svg_content"]
    root = ET.fromstring(first["svg_content"])
    group = next(node for node in root.iter(NS + "g") if node.get("id") == "native-chart-x")
    assert len(group) > 9 and not any(key.startswith("data-") for key in group.attrib)


def test_axis_is_drawn_before_the_bars_and_value_text_keeps_contrast():
    page, _ = _extract(_raw_svg(SALES))
    group = next(node for node in ET.fromstring(page["svg_content"]).iter(NS + "g"))
    tags = [child.tag.removeprefix(NS) for child in group]
    assert tags.index("line") < tags.index("rect")
    labels = {node.text: node.get("fill") for node in group.iter(NS + "text")}
    assert labels["420"] == "#FFFFFF" and labels["315"] == "#0F172A"


@pytest.mark.parametrize("raw,box,reason", [
    ({**SALES, "chartType": "line"}, "64 140 700 460", "server_chart_type_unsupported"),
    ({**CHANNELS, "grouping": "stacked"}, "64 140 700 460", "server_chart_grouping_unsupported"),
    ({**SALES, "series": [{"name": "s", "values": [3, -1, 2, 1]}]}, "64 140 700 460",
     "server_chart_value_not_positive"),
    (SALES, "64 140 100 80", "server_chart_box_too_small"),
    (SALES, "64 140 wide", "server_chart_box_invalid"),
])
def test_unsupported_placeholders_are_dropped_with_a_reason_and_left_empty(raw, box, reason):
    page, record = _extract(_raw_svg(raw, box=box))
    assert record["objects"] == [] and record["dropped"][0]["reason"] == reason
    group = next(node for node in ET.fromstring(page["svg_content"]).iter(NS + "g"))
    assert len(group) == 0 and group.attrib == {"id": "native-chart-x"}


def test_a_drawn_chart_without_the_marker_is_extracted_exactly_as_before():
    raw = _raw_svg(SALES, render=None, box=None)
    root = ET.fromstring(raw)
    record = extract_native_declarations(root)
    assert record["objects"] == [{"id": "native-chart-x", "kind": "chart", "raw": SALES}]


def test_an_unknown_render_value_is_dropped():
    _page, record = _extract(_raw_svg(SALES, render="client"))
    assert record["dropped"][0]["reason"] == "server_chart_render_invalid"


def test_values_must_be_numbers_the_source_states():
    page, _ = _extract(_raw_svg(SALES))
    grounded = {"passed": True, "failures": []}
    server_chart.apply_server_chart_grounding(grounded, page, page["svg_content"],
                                              "국내영업 420, 해외영업 315, 온라인 268, 파트너 142억원")
    assert grounded == {"passed": True, "failures": []}
    ungrounded = {"passed": True, "failures": []}
    server_chart.apply_server_chart_grounding(ungrounded, page, page["svg_content"], "국내영업 420, 해외 315")
    assert ungrounded["passed"] is False
    assert ungrounded["failures"] == [{"kind": "native_requested_chart_missing", "declarationId": "native-chart-x",
                                       "reason": "server_chart_value_ungrounded", "values": ["142", "268"]}]


def test_source_numbers_read_thousands_separators_and_decimals():
    assert server_chart.source_numbers("총 1,145억원, 87.4점, 2026년") == {
        server_chart.Decimal("1145"), server_chart.Decimal("87.4"), server_chart.Decimal("2026")}


def test_the_author_rule_is_sent_only_to_decks_admitted_with_server_charts():
    assert server_chart.server_chart_rule({}) == ""
    assert server_chart.server_chart_rule({"server_chart_render": "other"}) == ""
    assert "data-chart-render" in server_chart.server_chart_rule({"server_chart_render": server_chart.SERVER_CHART_REVISION})


@pytest.mark.parametrize("value,expected", [("on", True), ("1", True), ("", False), ("off", False)])
def test_server_charts_setting_is_default_off(monkeypatch, value, expected):
    from utils.get_env_concurrency import get_svg_server_charts_env
    monkeypatch.setenv("SVG_SERVER_CHARTS", value)
    assert get_svg_server_charts_env() is expected
