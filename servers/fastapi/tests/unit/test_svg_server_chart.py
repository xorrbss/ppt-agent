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
    assert labels["420"] == "#FFFFFF" and labels["315"] == "#0F172A"


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
    "shares": "sha256:22dc71c1d216b60389cd4a043210e98bd6a7d3164ff80082698b655e563bc65e",
    "sales": "sha256:96207f11d95f3734759532564b62d15bce01641620d253d8a5f50cb9030cc4e6",
    "channels": "sha256:9114d0d2c574ebd39a783287d38dfa199e74a2f05d298a4232b23740a20a8977",
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
