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


def _raw_svg(raw, *, render=server_chart.SERVER_CHART_REVISION, box="64 140 700 460"):
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
    assert labels["420억원"] == "#FFFFFF" and labels["315억원"] == "#0F172A"


@pytest.mark.parametrize("raw,box,reason", [
    ({**SALES, "chartType": "line"}, "64 140 700 460", "server_chart_type_unsupported"),
    ({**CHANNELS, "grouping": "overlap"}, "64 140 700 460", "server_chart_grouping_unsupported"),
    ({**SALES, "grouping": "stacked"}, "64 140 700 460", "server_chart_stack_needs_series"),
    ({**SALES, "series": [{"name": "s", "values": [3, -1, 2, 1]}]}, "64 140 700 460",
     "server_chart_value_not_positive"),
    (SALES, "64 140 100 80", "server_chart_box_too_small"),
    (SALES, "64 140 wide", "server_chart_box_invalid"),
])
def test_unsupported_placeholders_are_dropped_with_a_reason_and_left_empty(raw, box, reason):
    page, record = _extract(_raw_svg(raw, box=box))
    assert record["objects"] == [] and record["dropped"][0]["reason"] == reason
    group = next(node for node in ET.fromstring(page["svg_content"]).iter(NS + "g") if node.get("id"))
    assert len(group) == 0 and group.attrib == {"id": "native-chart-x"}


def test_a_drawn_chart_without_the_marker_is_extracted_exactly_as_before():
    raw = _raw_svg(SALES, render=None, box=None)
    root = ET.fromstring(raw)
    record = extract_native_declarations(root)
    assert record["objects"] == [{"id": "native-chart-x", "kind": "chart", "raw": SALES}]


@pytest.mark.parametrize("render", ["server", "client", "svg-server-chart/v0"])
def test_only_a_published_renderer_revision_draws(render):
    page, record = _extract(_raw_svg(SALES, render=render))
    assert record["objects"] == [] and record["dropped"][0]["reason"] == "server_chart_render_invalid"
    assert "data-chart" not in page["svg_content"]


def _grounding(raw, sources):
    page, _ = _extract(_raw_svg(raw))
    quality = {"passed": True, "failures": []}
    server_chart.apply_server_chart_grounding(quality, page, page["svg_content"], sources)
    return quality


def test_each_value_must_be_stated_for_its_own_category():
    stated = "1. 사업부별 매출 (단위: 억원): 국내영업 420, 해외영업 315, 온라인 268, 파트너 142."
    assert _grounding(SALES, [stated]) == {"passed": True, "failures": []}
    swapped = "국내영업 315, 해외영업 420, 온라인 268, 파트너 142"
    quality = _grounding(SALES, [swapped])
    assert quality["passed"] is False
    assert quality["failures"][0]["values"] == ["국내영업=420", "해외영업=315"]
    assert quality["failures"][0]["reason"] == "server_chart_value_ungrounded"


def test_any_one_source_may_state_a_value():
    outline = "국내영업이 420억원으로 가장 크고 해외영업은 315억원이다."
    request = "온라인 268, 파트너 142"
    assert _grounding(SALES, [outline, request])["failures"] == []
    assert _grounding(SALES, [outline])["passed"] is False


def test_several_series_bind_each_value_to_category_and_series():
    stated = ("2024년 국내 55%, 해외 30%, 온라인 15%; 2025년 국내 48%, 해외 32%, 온라인 20%; "
              "2026년 국내 42%, 해외 33%, 온라인 25%.")
    assert _grounding(CHANNELS, [stated])["failures"] == []
    wrong = stated.replace("해외 32%", "해외 23%")
    assert _grounding(CHANNELS, [wrong])["failures"][0]["values"] == ["해외/2025년=32"]


def test_several_series_may_name_the_series_first():
    brands = {"chartType": "column", "categories": ["A사", "B사", "C사"],
              "series": [{"name": "2025년", "values": [61, 48, 33]}, {"name": "2026년", "values": [66, 52, 41]}]}
    stated = "2025년 인지도는 A사 61%, B사 48%, C사 33%였고, 2026년에는 A사 66%, B사 52%, C사 41%로 올랐다."
    assert _grounding(brands, [stated])["failures"] == []
    assert _grounding(brands, [stated.replace("B사 52%", "B사 25%")])["failures"][0]["values"] == ["2026년/B사=52"]


def test_a_value_written_before_its_category_counts():
    stated = "420억원 국내영업, 315억원 해외영업, 268억원 온라인, 142억원 파트너"
    assert _grounding(SALES, [stated])["failures"] == []


def test_source_numbers_read_thousands_separators_and_decimals():
    assert server_chart.source_numbers("총 1,145억원, 87.4점, 2026년") == {
        server_chart.Decimal("1145"), server_chart.Decimal("87.4"), server_chart.Decimal("2026")}


def test_the_author_rule_is_sent_only_to_decks_admitted_with_server_charts():
    assert server_chart.server_chart_rule({}) == ""
    assert server_chart.server_chart_rule({"server_chart_render": "other"}) == ""
    rule = server_chart.server_chart_rule({"server_chart_render": server_chart.SERVER_CHART_REVISION})
    assert f'data-chart-render=\"{server_chart.SERVER_CHART_REVISION}\"' in rule


@pytest.mark.parametrize("value,expected", [("on", True), ("1", True), ("", False), ("off", False)])
def test_server_charts_setting_is_default_off(monkeypatch, value, expected):
    from utils.get_env_concurrency import get_svg_server_charts_env
    monkeypatch.setenv("SVG_SERVER_CHARTS", value)
    assert get_svg_server_charts_env() is expected


def _group(page):
    return next(node for node in ET.fromstring(page["svg_content"]).iter(NS + "g") if node.get("id"))


@pytest.mark.parametrize("chart_type", ["bar", "column"])
def test_an_inside_label_that_does_not_fit_its_bar_moves_every_label_outside(chart_type):
    raw = {"chartType": chart_type, "categories": ["A", "B"], "series": [{"name": "S", "values": [420, 9]}],
           "valueLabels": {"shown": True, "position": "insideEnd"}}
    page, record = _extract(_raw_svg(raw))
    assert record["objects"][0]["raw"]["valueLabels"] == {"shown": True, "position": "outsideEnd"}
    labels = {node.text: node.get("fill") for node in _group(page).iter(NS + "text")}
    assert labels["9"] == "#0F172A" and labels["420"] == "#0F172A"
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], "", pt_per_px=.75, revision=V4)
    assert quality["failures"] == []


def test_the_legend_wraps_inside_the_box():
    from services.generation_core.drivers.svg.fallback import _text_em
    raw = {"chartType": "column", "categories": ["A", "B"],
           "series": [{"name": f"아주 긴 계열 이름 {index}", "values": [index + 1, index + 2]} for index in range(6)]}
    page, record = _extract(_raw_svg(raw, box="64 140 420 400"))
    assert record["dropped"] == []
    right = max(float(node.get("x")) + _text_em(node.text) * float(node.get("font-size"))
                for node in _group(page).iter(NS + "text") if node.text and node.text.startswith("아주"))
    assert right <= 64 + 420


@pytest.mark.parametrize("chart_type", ["bar", "column"])
def test_long_category_names_wrap_onto_two_lines_and_still_prove(chart_type):
    raw = {"chartType": chart_type, "categories": ["수도권 대형 유통 채널", "지방 중소 유통 채널", "온라인 자사몰", "해외 파트너사"],
           "series": [{"name": "매출", "values": [4, 3, 2, 1]}]}
    page, record = _extract(_raw_svg(raw, box="64 140 360 400").replace('font-size="36"', 'font-size="16"'))
    assert record["dropped"] == []
    assert any(len(list(node.iter(NS + "tspan"))) == 2 for node in _group(page).iter(NS + "text"))
    for revision in (V4, V5):
        quality = {"passed": True, "failures": []}
        apply_requested_native_quality(quality, page, page["svg_content"], "", pt_per_px=.75, revision=revision,
                                       paint_deferred=True)
        assert quality["failures"] == []


def test_value_labels_carry_the_title_unit_so_source_literals_stay_on_the_page():
    page, _ = _extract(_raw_svg(json.loads(json.dumps(CHANNELS))))
    texts = {node.text for node in _group(page).iter(NS + "text")}
    assert {"55%", "30%", "15%"} <= texts
    unitless, _ = _extract(_raw_svg(json.loads(json.dumps(SCORES))))
    assert "87.4" in {node.text for node in _group(unitless).iter(NS + "text")}


def test_chart_text_follows_the_pages_body_size_within_bounds():
    def sizes(body):
        svg = _raw_svg(SALES).replace('font-size="36"', f'font-size="{body}"')
        page, _ = _extract(svg)
        return {node.get("font-size") for node in _group(page).iter(NS + "text")}
    assert sizes(18) == {"18", "20"}
    assert sizes(40) == {"20", "22"}
    assert sizes(10) == {"14", "16"}


def test_a_correction_sees_server_charts_as_their_placeholders():
    page, record = _extract(_raw_svg(SALES))
    view = server_chart.placeholder_view(page["svg_content"], page["nativeDeclarations"])
    group = next(node for node in ET.fromstring(view).iter(NS + "g") if node.get("id"))
    assert len(group) == 0 and group.get(server_chart.RENDER_ATTRIBUTE) == server_chart.SERVER_CHART_REVISION
    assert group.get(server_chart.BOX_ATTRIBUTE) == "64 140 700 460"
    # The placeholder the model sees re-renders to the identical page.
    again, _ = _extract(view)
    assert again["svg_content"] == page["svg_content"]
    assert server_chart.placeholder_view(page["svg_content"], None) == page["svg_content"]


#: Published output of revision one. A change here means a stored deck would
#: replay differently: publish a new revision instead of editing v1.
V1_DIGESTS = {
    "shares": "sha256:3bceaa3ac3b02208b4afd3a1fa91c8792b9f3bacdfcba155a249039645b45c11",
    "sales": "sha256:300e84101fffa9c0c3077541c39c1f141aaee4f3d0ed1a802db07354f1da2bc7",
    "channels": "sha256:5bd8090c4e0bff662b29a529fc316d7f6426bc3087f9a0a3637c7eb2689f9962",
    "scores": "sha256:0083fcc4371df58bbccd9c9c6156330ee8b672f9ed4e4f5e295f5f50732472b0",
}


@pytest.mark.parametrize("name,raw", [("sales", SALES), ("channels", CHANNELS), ("scores", SCORES),
                                      ("shares", "SHARES")])
def test_revision_one_output_is_frozen(name, raw):
    raw = json.loads(json.dumps(SHARES if raw == "SHARES" else raw))
    from services.svg_contract import source_digest
    page, _ = _extract(_raw_svg(raw))
    assert source_digest(page["svg_content"]) == V1_DIGESTS[name]


SHARES = {"chartType": "bar", "categories": ["2024년", "2025년", "2026년"], "grouping": "percentStacked",
          "series": [{"name": "국내", "values": [55, 48, 42], "pointColors": ["#2563EB", "#000000", "#000000"]},
                     {"name": "해외", "values": [30, 32, 33]}, {"name": "온라인", "values": [15, 20, 25]}],
          "valueLabels": {"shown": True, "position": "outsideEnd"}, "title": "채널 비중 (%)"}


@pytest.mark.parametrize("grouping", ["stacked", "percentStacked"])
@pytest.mark.parametrize("chart_type", ["bar", "column"])
@pytest.mark.parametrize("revision", [V4, V5])
def test_stacks_pass_the_unchanged_native_contract(grouping, chart_type, revision):
    raw = {**SHARES, "grouping": grouping, "chartType": chart_type}
    page, record = _extract(_raw_svg(json.loads(json.dumps(raw))))
    assert record["dropped"] == []
    declared = record["objects"][0]["raw"]
    assert declared["grouping"] == grouping and declared["valueLabels"]["position"] == "center"
    assert all(len(set(series["pointColors"])) == 1 for series in declared["series"])
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], "", pt_per_px=.75, revision=revision,
                                   paint_deferred=True)
    assert quality["failures"] == []
    wire, _summary = page_native_objects(page, pt_per_px=.75, revision=revision)
    assert len(wire["objects"]) == 1


def test_a_stack_whose_segments_cannot_hold_their_labels_drops_the_labels():
    raw = {**json.loads(json.dumps(SHARES)), "series": [
        {"name": "큰 몫", "values": [97, 96, 95]}, {"name": "작은 몫", "values": [3, 4, 5]}]}
    page, record = _extract(_raw_svg(raw, box="64 140 420 300"))
    assert record["objects"][0]["raw"]["valueLabels"]["shown"] is False
    texts = {node.text for node in _group(page).iter(NS + "text")}
    assert "97" not in texts and "3" not in texts
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], "", pt_per_px=.75, revision=V4)
    assert quality["failures"] == []


def test_value_labels_carry_a_title_unit_outside_the_v4_unit_list():
    raw = {"chartType": "bar", "categories": ["가", "나", "다"], "title": "연간 방문자 수 (만명)",
           "series": [{"name": "방문자", "values": [128.4, 46.2, 39.8]}]}
    page, _ = _extract(_raw_svg(raw))
    assert {"128.4만명", "46.2만명", "39.8만명"} <= {node.text for node in _group(page).iter(NS + "text")}
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], "", pt_per_px=.75, revision=V4)
    assert quality["failures"] == []


def test_admission_pins_server_charts_for_v4_decks_only():
    import inspect
    from services import svg_generation_admission
    source = inspect.getsource(svg_generation_admission)
    assert "get_svg_server_charts_env() and frozen[SNAPSHOT_REVISION_KEY] != REQUIREMENTS_VERSION_V5" in source


# --- Revision two: line, area, pie and donut ------------------------------------------------

V2_CASES = {
    "line": ({"chartType": "line", "categories": ["2022년", "2023년", "2024년", "2025년"],
              "series": [{"name": "매출", "values": [120, 150, 180, 210]}], "title": "연도별 매출 (억원)"},
             "chart: 매출: 2022년 120억원, 2023년 150억원, 2024년 180억원, 2025년 210억원"),
    "lines": ({"chartType": "line", "categories": ["1분기", "2분기", "3분기", "4분기"],
               "series": [{"name": "국내", "values": [30, 42, 51, 60]}, {"name": "해외", "values": [12, 20, 33, 48]}],
               "title": "분기별 매출 (억원)"},
              "chart: 국내: 1분기 30, 2분기 42, 3분기 51, 4분기 60; 해외: 1분기 12, 2분기 20, 3분기 33, 4분기 48. 단위: 억원"),
    "area": ({"chartType": "area", "categories": ["2022년", "2023년", "2024년", "2025년"],
              "series": [{"name": "방문자", "values": [40, 55, 70, 64]}], "title": "방문자 (명)"},
             "chart: 방문자: 2022년 40명, 2023년 55명, 2024년 70명, 2025년 64명"),
    "areas": ({"chartType": "area", "categories": ["1월", "2월", "3월", "4월"],
               "series": [{"name": "신규", "values": [20, 35, 30, 50]}, {"name": "재방문", "values": [60, 70, 90, 110]}],
               "title": "방문자 (명)"},
              "chart: 신규: 1월 20, 2월 35, 3월 30, 4월 50; 재방문: 1월 60, 2월 70, 3월 90, 4월 110. 단위: 명"),
    "pie": ({"chartType": "pie", "categories": ["국내", "해외", "온라인"],
             "series": [{"name": "비중", "values": [55, 30, 15]}], "title": "매출 비중 (%)"},
            "chart: 비중: 국내 55%, 해외 30%, 온라인 15%"),
    "pie_small_slices": ({"chartType": "pie", "categories": ["국내", "해외", "온라인", "기타"],
                          "series": [{"name": "비중", "values": [70, 22, 6, 2]}], "title": "매출 비중 (%)"},
                         "chart: 비중: 국내 70%, 해외 22%, 온라인 6%, 기타 2%"),
    "donut": ({"chartType": "donut", "categories": ["국내", "해외", "온라인", "기타"],
               "series": [{"name": "비중", "values": [48, 32, 15, 5]}], "title": "매출 비중 (%)"},
              "chart: 비중: 국내 48%, 해외 32%, 온라인 15%, 기타 5%"),
}
V2 = server_chart.SERVER_CHART_REVISION_V2


def _v2_page(name, box="64 140 700 460"):
    raw, _source = V2_CASES[name]
    return _extract(_raw_svg(json.loads(json.dumps(raw)), render=V2, box=box).replace(
        'font-size="36"', 'font-size="18"'))


@pytest.mark.parametrize("name", list(V2_CASES))
@pytest.mark.parametrize("revision", [V4, V5])
def test_revision_two_passes_the_unchanged_native_contract_against_the_source(name, revision):
    page, record = _v2_page(name)
    assert record["dropped"] == [] and record["objects"][0]["render"] == V2
    for content in (V2_CASES[name][1], ""):
        quality = {"passed": True, "failures": []}
        apply_requested_native_quality(quality, page, page["svg_content"], content, pt_per_px=.75,
                                       revision=revision, paint_deferred=True)
        assert quality["failures"] == []
    wire, _summary = page_native_objects(page, pt_per_px=.75, revision=revision)
    (chart,) = wire["objects"]
    assert chart["kind"] == "chart" and chart["payload"]["chartType"] == V2_CASES[name][0]["chartType"]


@pytest.mark.parametrize("raw", [SALES, CHANNELS, SCORES, SHARES])
def test_revision_two_draws_bar_and_column_charts_exactly_as_revision_one(raw):
    first, first_record = _extract(_raw_svg(json.loads(json.dumps(raw))))
    second, second_record = _extract(_raw_svg(json.loads(json.dumps(raw)), render=V2))
    assert first["svg_content"] == second["svg_content"]
    assert first_record["objects"][0]["raw"] == second_record["objects"][0]["raw"]


def test_several_lines_carry_no_value_labels():
    page, record = _v2_page("lines")
    assert record["objects"][0]["raw"]["valueLabels"] == {"shown": False, "position": None}
    assert not any(node.text and node.text[0].isdigit() and node.text[-1] != "기"
                   for node in _group(page).iter(NS + "text"))


def test_one_line_and_areas_label_every_value_above_its_point():
    for name in ("line", "areas"):
        _page, record = _v2_page(name)
        assert record["objects"][0]["raw"]["valueLabels"] == {"shown": True, "position": "above"}


def test_a_slice_too_small_for_its_value_moves_every_value_beside_its_name():
    _page, record = _v2_page("pie_small_slices")
    assert record["objects"][0]["raw"]["valueLabels"] == {"shown": True, "position": "outsideEnd"}
    _page, record = _v2_page("pie")
    assert record["objects"][0]["raw"]["valueLabels"] == {"shown": True, "position": "center"}


def test_polar_charts_are_clockwise_with_one_distinct_colour_per_slice():
    _page, record = _v2_page("donut")
    raw = record["objects"][0]["raw"]
    assert raw["categoryOrder"] == "clockwise" and "grouping" not in raw
    assert len(set(raw["series"][0]["pointColors"])) == 4


@pytest.mark.parametrize("raw,reason", [
    ({**V2_CASES["line"][0], "series": [{"name": "s", "values": [3, -1, 2, 1]}]}, "server_chart_value_negative"),
    ({**V2_CASES["line"][0], "series": [{"name": "s", "values": [0, 0, 0, 0]}]}, "server_chart_value_not_positive"),
    ({**V2_CASES["line"][0], "categories": ["2025년"], "series": [{"name": "s", "values": [3]}]},
     "server_chart_trend_needs_categories"),
    ({**V2_CASES["area"][0], "grouping": "stacked"}, "server_chart_grouping_unsupported"),
    ({**V2_CASES["pie"][0], "series": [{"name": "a", "values": [1, 2, 3]}, {"name": "b", "values": [1, 2, 3]}]},
     "server_chart_polar_needs_one_series"),
    ({**V2_CASES["pie"][0], "series": [{"name": "a", "values": [5, 0, 3]}]}, "server_chart_value_not_positive"),
    ({"chartType": "donut", "categories": [f"항목{index}" for index in range(9)],
      "series": [{"name": "a", "values": [1] * 9}]}, "server_chart_too_many_slices"),
    ({"chartType": "pie", "categories": ["가", "나", "다", "라", "마", "바"],
      "series": [{"name": "비중", "values": [90, 2, 2, 2, 2, 2]}]}, "server_chart_slice_labels_collide"),
    ({**V2_CASES["pie"][0], "chartType": "radar"}, "server_chart_type_unsupported"),
])
def test_revision_two_drops_what_it_cannot_draw_with_a_reason(raw, reason):
    page, record = _extract(_raw_svg(json.loads(json.dumps(raw)), render=V2))
    assert record["objects"] == [] and record["dropped"][0]["reason"] == reason
    assert len(_group(page)) == 0


def test_revision_one_still_leaves_line_charts_to_the_author():
    page, record = _extract(_raw_svg(json.loads(json.dumps(V2_CASES["line"][0]))))
    assert record["dropped"][0]["reason"] == "server_chart_type_unsupported"


def test_a_correction_sees_revision_two_charts_as_their_placeholders():
    page, _record = _v2_page("donut")
    view = server_chart.placeholder_view(page["svg_content"], page["nativeDeclarations"])
    group = next(node for node in ET.fromstring(view).iter(NS + "g") if node.get("id"))
    assert len(group) == 0 and group.get(server_chart.RENDER_ATTRIBUTE) == V2
    again, _ = _extract(view)
    assert again["svg_content"] == page["svg_content"]


def test_pie_values_are_grounded_against_their_category():
    page, _record = _v2_page("pie")
    quality = {"passed": True, "failures": []}
    server_chart.apply_server_chart_grounding(quality, page, page["svg_content"], ["국내 55%, 해외 30%, 온라인 15%"])
    assert quality["failures"] == []
    server_chart.apply_server_chart_grounding(quality, page, page["svg_content"], ["국내 30%, 해외 55%, 온라인 15%"])
    assert quality["failures"][0]["values"] == ["국내=55", "해외=30"]


def test_each_deck_gets_the_rule_of_the_revision_it_was_admitted_with():
    assert server_chart.server_chart_rule({"server_chart_render": server_chart.SERVER_CHART_REVISION}) == (
        server_chart.SERVER_CHART_RULE)
    rule = server_chart.server_chart_rule({"server_chart_render": V2})
    assert rule == server_chart.SERVER_CHART_RULE_V2 and f'data-chart-render="{V2}"' in rule
    assert server_chart.server_chart_policy({"server_chart_render": V2})
    assert server_chart.SERVER_CHART_CURRENT == V2


#: Published output of revision two for the families it adds (bar and column are
#: pinned through revision one, which revision two reproduces).
V2_DIGESTS = {
    "line": "sha256:71ed8456b46b76c125a609d9ce30f154d29bd6a7e71bd51bae1c16cda4202335",
    "lines": "sha256:540ab8c446641729ac86723a8eb40d18ffd494987cbe5d93ea782b9df02d2c4f",
    "area": "sha256:6968cfb36a327526560028f90c973f9a666a4ce0d5fc5306bd6e73f620a265c8",
    "areas": "sha256:89f130a31a7e4998d5f781a6427d11de6d00303e4de6333f2737cdd427b4fc9b",
    "pie": "sha256:a6f4448094bb53d858e1878f14000a93e57bb4ab22aedcb3b4a7c4ba157055a6",
    "pie_small_slices": "sha256:bab96b184128623554a5ca46fbda5d584eead59a26f8938d19fe004b44873596",
    "donut": "sha256:9fd19833e712844d7f9c8dcd4c02f65bf6745135d8c47becfad62248b44b8b68",
}


@pytest.mark.parametrize("name", list(V2_DIGESTS))
def test_revision_two_output_is_frozen(name):
    from services.svg_contract import source_digest
    page, _ = _v2_page(name)
    assert source_digest(page["svg_content"]) == V2_DIGESTS[name]
