"""Server chart renderer revision four (``svg-server-chart/v4``): trusted, with a designed look."""
from __future__ import annotations

import json
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg import server_chart, server_chart_v4
from services.generation_core.drivers.svg.chart_paint_verification import (
    gated_chart_ids, verification_manifest, witness_source,
)
from services.generation_core.drivers.svg.native_declarations import (
    bind_native_declarations, extract_native_declarations,
)
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V4 as V4, REQUIREMENTS_VERSION_V5 as V5, apply_requested_native_quality,
)
from services.generation_core.drivers.svg.publish import svg_slide_content
from services.generation_core.native_objects import NativeObjects
from services.svg_contract import finalize_svg, validate_svg

NS = "{http://www.w3.org/2000/svg}"
R4 = server_chart.SERVER_CHART_REVISION_V4
ACCENT = "#0F766E"

COLUMN = {"chartType": "column", "categories": ["2022년", "2023년", "2024년", "2025년"],
          "series": [{"name": "매출", "values": [120, 150, 180, 210]}], "title": "연도별 매출 (억원)"}
BAR_MANY = {"chartType": "bar", "categories": ["국내영업", "해외영업", "온라인", "파트너", "공공", "리테일", "기타"],
            "series": [{"name": "매출", "values": [420, 315, 268, 142, 96, 88, 31]}], "title": "채널별 매출 (억원)"}
CLUSTERED = {"chartType": "column", "categories": ["1분기", "2분기", "3분기", "4분기"],
             "series": [{"name": "국내", "values": [55, 48, 62, 70]}, {"name": "해외", "values": [30, 32, 41, 45]},
                        {"name": "온라인", "values": [15, 20, 25, 38]}], "title": "분기별 매출 (억원)"}
STACKED = {"chartType": "column", "grouping": "stacked", "categories": ["2024년", "2025년", "2026년"],
           "series": [{"name": "국내", "values": [55, 48, 42]}, {"name": "해외", "values": [30, 32, 33]},
                      {"name": "온라인", "values": [15, 20, 25]}], "title": "채널 구성 (억원)"}
PERCENT = {"chartType": "bar", "grouping": "percentStacked", "categories": ["2025년", "2026년"],
           "series": [{"name": "국내", "values": [60, 55]}, {"name": "해외", "values": [40, 45]}],
           "title": "채널 구성 (%)"}
LINE = {"chartType": "line", "categories": ["2022년", "2023년", "2024년", "2025년"],
        "series": [{"name": "매출", "values": [120, 150, 180, 210]}], "title": "연도별 매출 (억원)"}
LINES_FEW = {"chartType": "line", "categories": ["1분기", "2분기", "3분기", "4분기"],
             "series": [{"name": "국내", "values": [30, 42, 51, 60]}, {"name": "해외", "values": [12, 20, 33, 48]}],
             "title": "분기별 매출 (억원)"}
LINES_MANY = {"chartType": "line", "categories": ["1월", "2월", "3월", "4월", "5월", "6월"],
              "series": [{"name": "국내", "values": [30, 42, 51, 60, 66, 71]},
                         {"name": "해외", "values": [12, 20, 33, 48, 52, 64]},
                         {"name": "온라인", "values": [28, 31, 33, 35, 47, 49]}], "title": "월별 매출 (억원)"}
AREA = {"chartType": "area", "categories": ["2022년", "2023년", "2024년", "2025년"],
        "series": [{"name": "방문자", "values": [40, 55, 70, 64]}], "title": "방문자 (명)"}
AREAS = {"chartType": "area", "categories": ["1월", "2월", "3월", "4월"],
         "series": [{"name": "신규", "values": [20, 35, 30, 50]}, {"name": "재방문", "values": [60, 70, 90, 110]}],
         "title": "방문자 (명)"}
PIE = {"chartType": "pie", "categories": ["국내", "해외", "온라인"], "series": [{"name": "비중", "values": [55, 30, 15]}],
       "title": "매출 비중 (%)"}
PIE_SMALL = {"chartType": "pie", "categories": ["국내", "해외", "온라인", "기타"],
             "series": [{"name": "비중", "values": [70, 22, 6, 2]}], "title": "매출 비중 (%)"}
DONUT = {"chartType": "donut", "categories": ["국내", "해외", "온라인", "기타"],
         "series": [{"name": "비중", "values": [48, 32, 15, 5]}], "title": "매출 비중 (%)"}
DONUT_LEGEND = {"chartType": "donut", "categories": ["제조업", "금융·보험업", "정보통신", "도소매", "건설", "운수·창고", "기타 서비스"],
                "series": [{"name": "비중", "values": [28, 19, 16, 12, 10, 8, 7]}], "title": "산업별 비중 (%)"}
ZERO = {"chartType": "column", "categories": ["1월", "2월", "3월"],
        "series": [{"name": "건수", "values": [12, 0, 7]}], "title": "신규 건수 (건)"}

#: ``(declaration, source literal)`` for every drawing case.
CASES = {
    "column": (COLUMN, "chart: 매출: 2022년 120억원, 2023년 150억원, 2024년 180억원, 2025년 210억원"),
    "bar_many": (BAR_MANY, "chart: 매출: 국내영업 420억원, 해외영업 315억원, 온라인 268억원, 파트너 142억원, 공공 96억원, "
                           "리테일 88억원, 기타 31억원"),
    "clustered": (CLUSTERED, "chart: 국내: 1분기 55, 2분기 48, 3분기 62, 4분기 70; 해외: 1분기 30, 2분기 32, 3분기 41, "
                             "4분기 45; 온라인: 1분기 15, 2분기 20, 3분기 25, 4분기 38. 단위: 억원"),
    "stacked": (STACKED, "chart: 국내: 2024년 55, 2025년 48, 2026년 42; 해외: 2024년 30, 2025년 32, 2026년 33; "
                         "온라인: 2024년 15, 2025년 20, 2026년 25. 단위: 억원"),
    "percent": (PERCENT, "chart: 국내: 2025년 60%, 2026년 55%; 해외: 2025년 40%, 2026년 45%"),
    "line": (LINE, "chart: 매출: 2022년 120억원, 2023년 150억원, 2024년 180억원, 2025년 210억원"),
    "lines_few": (LINES_FEW, "chart: 국내: 1분기 30, 2분기 42, 3분기 51, 4분기 60; 해외: 1분기 12, 2분기 20, 3분기 33, "
                             "4분기 48. 단위: 억원"),
    "lines_many": (LINES_MANY, "chart: 국내: 1월 30, 2월 42, 3월 51, 4월 60, 5월 66, 6월 71; 해외: 1월 12, 2월 20, 3월 33, "
                               "4월 48, 5월 52, 6월 64; 온라인: 1월 28, 2월 31, 3월 33, 4월 35, 5월 47, 6월 49. 단위: 억원"),
    "area": (AREA, "chart: 방문자: 2022년 40명, 2023년 55명, 2024년 70명, 2025년 64명"),
    "areas": (AREAS, "chart: 신규: 1월 20, 2월 35, 3월 30, 4월 50; 재방문: 1월 60, 2월 70, 3월 90, 4월 110. 단위: 명"),
    "pie": (PIE, "chart: 비중: 국내 55%, 해외 30%, 온라인 15%"),
    "pie_small": (PIE_SMALL, "chart: 비중: 국내 70%, 해외 22%, 온라인 6%, 기타 2%"),
    "donut": (DONUT, "chart: 비중: 국내 48%, 해외 32%, 온라인 15%, 기타 5%"),
    "donut_legend": (DONUT_LEGEND, "chart: 비중: 제조업 28%, 금융·보험업 19%, 정보통신 16%, 도소매 12%, 건설 10%, "
                                   "운수·창고 8%, 기타 서비스 7%"),
    "zero": (ZERO, "chart: 건수: 1월 12건, 2월 0건, 3월 7건"),
}


def _page_svg(raw, *, accent=ACCENT, render=R4, box="64 140 700 460", body="18", ink="#0F172A", extra=""):
    shapes = (f'<rect x="0" y="0" width="1280" height="14" fill="{accent}"/>'
              f'<rect x="64" y="40" width="8" height="48" fill="{accent}"/>') if accent else ""
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            f'<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>{shapes}'
            f'<text x="84" y="80" font-family="Noto Sans KR" font-size="{body}" fill="{ink}">Title</text>{extra}'
            f"<g id=\"native-chart-x\" data-native-chart='{json.dumps(raw, ensure_ascii=False)}' "
            f'data-chart-render="{render}" data-chart-box="{box}"/></svg>')


def _extract(svg):
    root = ET.fromstring(svg)
    record = extract_native_declarations(root)
    drawn = ET.tostring(root, encoding="unicode")
    page = {"svg_content": drawn}
    bind_native_declarations(page, drawn, record)
    return page, record


def _page(raw, **kwargs):
    return _extract(_page_svg(json.loads(json.dumps(raw)), **kwargs))


def _case(name, **kwargs):
    return _page(CASES[name][0], **kwargs)


def _group(page):
    return next(node for node in ET.fromstring(page["svg_content"]).iter(NS + "g") if node.get("id") == "native-chart-x")


def _trusted(page):
    return server_chart.trusted_server_chart_ids(page["nativeDeclarations"], page["svg_content"])


def _texts(page):
    return [node for node in _group(page).iter(NS + "text")]


def _text_boxes(page):
    return [(node.text or "".join(part.text or "" for part in node), server_chart._drawn_box(node, "text"))
            for node in _texts(page)]


# --- Registry ------------------------------------------------------------------------------

def test_revision_four_is_registered_trusted_current_and_has_its_own_rule():
    assert R4 == "svg-server-chart/v4"
    assert server_chart.RENDERERS[R4] is server_chart.render_server_chart_v4
    assert R4 in server_chart.TRUSTED_RENDERERS and server_chart.SERVER_CHART_CURRENT == server_chart.SERVER_CHART_REVISION_V7
    assert server_chart.server_chart_policy({"server_chart_render": R4})
    rule = server_chart.server_chart_rule({"server_chart_render": R4})
    assert rule == server_chart.SERVER_CHART_RULE_V4 and f'data-chart-render="{R4}"' in rule
    assert "svg-server-chart/v3" not in rule and "svg-server-chart/v2" not in rule
    assert "value axis" in rule and "legend" in rule and "zero or above" in rule and "a zero value" in rule
    for older in (server_chart.SERVER_CHART_REVISION, server_chart.SERVER_CHART_REVISION_V2,
                  server_chart.SERVER_CHART_REVISION_V3):
        assert older not in server_chart.server_chart_rule({"server_chart_render": R4})


# --- Every case: trusted replay, requirement checks, native export ---------------------------

def raw_type(name):
    return CASES[name][0]["chartType"]


@pytest.mark.parametrize("name", list(CASES))
def test_every_case_draws_and_replays_as_trusted(name):
    page, record = _case(name)
    assert record["dropped"] == [], record["dropped"]
    (item,) = record["objects"]
    assert item["render"] == R4 and set(item["style"]) - {"trendLabels"} == {"font", "ink", "size", "palette"}
    assert ("trendLabels" in item["style"]) is (raw_type(name) in ("line", "area"))
    assert _trusted(page) == {"native-chart-x"}
    assert not page["svg_content"].count("data-chart-render")


@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.parametrize("revision", [V4, V5])
def test_every_case_passes_the_requirement_checks_against_its_source_and_exports_natively(name, revision):
    raw, source = CASES[name]
    page, record = _case(name)
    assert record["dropped"] == []
    for content in (source, ""):
        quality = {"passed": True, "failures": []}
        apply_requested_native_quality(quality, page, page["svg_content"], content, pt_per_px=.75, revision=revision,
                                       paint_deferred=True)
        assert quality["failures"] == []
    wire, summary = page_native_objects(page, pt_per_px=.75, revision=revision)
    assert summary["accepted"] == 1 and summary["dropped"] == []
    (chart,) = wire["objects"]
    assert chart["kind"] == "chart" and chart["payload"]["chartType"] == raw["chartType"]
    assert [item["values"] for item in chart["payload"]["series"]] == [item["values"] for item in raw["series"]]
    assert chart["payload"]["categories"] == raw["categories"]
    # A drawing the unchanged proof accepts anchors at its own extent, else at the declared box.
    assert chart["anchor"]["x"] == 64.0 and chart["anchor"]["y"] == 140.0


@pytest.mark.parametrize("name", list(CASES))
def test_a_trusted_v4_chart_needs_no_paint_evidence(name):
    page, _ = _case(name)
    wire, _summary = page_native_objects(page, pt_per_px=.75, revision=V5)
    content = {"nativeObjects": wire, **{key: value for key, value in page.items() if key != "svg_content"}}
    content["nativeChartWitnessSource"] = witness_source(page, page["svg_content"], revision=V5)
    assert gated_chart_ids(content, NativeObjects.from_wire(wire), revision=V5, svg=page["svg_content"]) == []
    assert verification_manifest(content, page["svg_content"]) is None
    older, _ = _page(CASES[name][0], render=server_chart.SERVER_CHART_REVISION_V2)
    wire, _summary = page_native_objects(older, pt_per_px=.75, revision=V5)
    if wire is not None:  # the same chart declared with an untrusted revision still waits for evidence
        content = {"nativeObjects": wire, **{key: value for key, value in older.items() if key != "svg_content"}}
        content["nativeChartWitnessSource"] = witness_source(older, older["svg_content"], revision=V5)
        assert gated_chart_ids(content, NativeObjects.from_wire(wire), revision=V5, svg=older["svg_content"]) != []


@pytest.mark.parametrize("name", ["column", "stacked", "lines_many", "donut_legend", "zero"])
def test_the_payload_survives_when_the_drawing_proof_rejects_a_v4_drawing(name, monkeypatch):
    """The drawing has axis labels and legends the verifier cannot account for: the export builds from the JSON."""
    from services.generation_core.drivers.svg import native_publish
    page, _ = _case(name)
    wire, _ = page_native_objects(page, pt_per_px=.75, revision=V4)
    assert wire is not None
    untouched = wire["objects"][0]["payload"]
    from services.generation_core.drivers.svg.native_geometry import NativeRejection

    def reject(*_args, **_kwargs):
        raise NativeRejection("native_chart_text_unaccounted")
    monkeypatch.setattr(native_publish, "chart_object", reject)
    wire, summary = page_native_objects(page, pt_per_px=.75, revision=V4)
    assert summary["accepted"] == 1
    assert wire["objects"][0]["payload"]["series"][0]["values"] == untouched["series"][0]["values"]


# --- Trust is bound to the drawing ----------------------------------------------------------

@pytest.mark.parametrize("edit", ["bar_height", "label_text", "gridline", "point_colour", "style_missing", "stale"])
def test_v4_trust_never_outlives_the_drawing_it_was_granted_for(edit):
    page, _ = _case("clustered")
    root = ET.fromstring(page["svg_content"])
    group = next(node for node in root.iter(NS + "g") if node.get("id") == "native-chart-x")
    record = page["nativeDeclarations"]
    if edit == "bar_height":
        rect = next(node for node in group.iter(NS + "rect") if node.get("fill") == ACCENT and node.get("height") != "14")
        rect.set("width", str(float(rect.get("width")) - 3))
    elif edit == "label_text":
        next(node for node in group.iter(NS + "text") if node.text == "80").text = "90"
    elif edit == "gridline":
        next(node for node in group.iter(NS + "line")).set("stroke-opacity", "0.9")
    elif edit == "point_colour":
        record["objects"][0]["raw"]["series"][1]["pointColors"] = ["#FF0000"] * 4
        assert _trusted(page) == frozenset()
        return
    elif edit == "style_missing":
        record["objects"][0].pop("style")
        assert _trusted(page) == frozenset()
        return
    elif edit == "stale":
        page["svg_content"] = page["svg_content"].replace("Title", "Other")
        assert _trusted(page) == frozenset()
        return
    svg = ET.tostring(root, encoding="unicode")
    rebound = {"svg_content": svg}
    bind_native_declarations(rebound, svg, dict(record))
    assert _trusted(rebound) == frozenset()


def test_a_v4_style_record_with_a_v3_shape_or_a_broken_palette_is_not_replayable():
    page, _ = _case("column")
    record = page["nativeDeclarations"]
    good = record["objects"][0]["style"]
    for style in ({k: v for k, v in good.items() if k != "palette"}, {**good, "palette": []},
                  {**good, "palette": ["red"]}, {**good, "palette": good["palette"] * 3}, {**good, "extra": 1}):
        record["objects"][0]["style"] = style
        assert _trusted(page) == frozenset()


def test_trust_survives_template_composition_of_a_v4_page():
    page, _ = _case("donut_legend")
    root = ET.fromstring(page["svg_content"])
    chart = next(node for node in root.iter(NS + "g") if node.get("id") == "native-chart-x")
    for index, node in enumerate(chart.iter()):
        if node is not chart:
            node.set("id", f"body-node-{index}")
    root.remove(chart)
    body = ET.SubElement(root, NS + "g", {"id": "svg-system-template-body", "transform": "translate(0,40)"})
    body.append(chart)
    for _ in range(3):
        ET.SubElement(root, NS + "text", {"x": "1100", "y": "40", "font-size": "40", "font-family": "Frame Sans",
                                          "fill": "#FF0000"}).text = "frame"
    svg = ET.tostring(root, encoding="unicode")
    rebound = {"svg_content": svg}
    bind_native_declarations(rebound, svg, dict(page["nativeDeclarations"]))
    assert _trusted(rebound) == {"native-chart-x"}


# --- Palette from the deck ------------------------------------------------------------------

def _fills(page):
    return [node.get("fill") for node in _group(page).iter(NS + "rect")]


def test_the_page_accent_leads_the_series_colours_and_is_recorded_for_replay():
    page, record = _case("clustered", accent="#B45309")
    palette = record["objects"][0]["style"]["palette"]
    assert palette[0] == "#B45309" and len(palette) == len(set(palette)) == 8
    assert _fills(page).count("#B45309") >= 5  # four bars and the legend swatch
    raw = record["objects"][0]["raw"]
    assert [series["pointColors"][0] for series in raw["series"]] == palette[:3]
    assert _trusted(page) == {"native-chart-x"}


def test_without_an_accent_the_default_blue_leads_and_a_gray_page_has_no_accent():
    _, record = _case("column", accent=None)
    assert record["objects"][0]["style"]["palette"][0] == "#2563EB"
    gray = '<rect x="0" y="100" width="1280" height="40" fill="#CBD5E1"/><rect x="0" y="0" width="1280" height="20" fill="#111827"/>'
    _, record = _page(COLUMN, accent=None, extra=gray)
    assert record["objects"][0]["style"]["palette"][0] == "#2563EB"


def test_accents_are_ranked_by_painted_area_and_backgrounds_do_not_count():
    extra = ('<rect x="0" y="0" width="1280" height="720" fill="#DC2626"/>'  # a full-page background
             '<rect x="0" y="600" width="300" height="100" fill="#7C3AED"/>'
             '<rect x="400" y="600" width="60" height="60" fill="#16A34A"/>'
             '<rect x="500" y="600" width="55" height="55" fill="#16A34B"/>')  # a near duplicate
    _, record = _page(COLUMN, accent=None, extra=extra)
    palette = record["objects"][0]["style"]["palette"]
    assert palette[:2] == ["#7C3AED", "#16A34A"] and "#DC2626" not in palette


def test_the_palette_reads_the_page_deterministically_and_never_from_the_chart_itself():
    first = _case("pie", accent="#B45309")[1]["objects"][0]["style"]
    assert first == _case("pie", accent="#B45309")[1]["objects"][0]["style"]
    own = '<g id="native-chart-y"><rect x="0" y="0" width="900" height="900" fill="#FF00FF"/></g>'
    root = ET.fromstring(_page_svg(PIE, accent=None))
    chart = next(node for node in root.iter(NS + "g") if node.get("id") == "native-chart-x")
    chart.append(ET.fromstring(own.replace("<g ", f'<g xmlns="{NS[1:-1]}" ')))
    assert "#FF00FF" not in server_chart_v4.page_style(root, chart)[3]


def test_pie_slices_use_the_palette_in_order_and_declared_distinct_colours_win():
    page, record = _case("pie", accent="#B45309")
    palette = record["objects"][0]["style"]["palette"]
    assert record["objects"][0]["raw"]["series"][0]["pointColors"] == palette[:3]
    declared = json.loads(json.dumps(PIE))
    declared["series"][0]["pointColors"] = ["#111111", "#222222", "#333333"]
    _, record = _page(declared, accent="#B45309")
    assert record["objects"][0]["raw"]["series"][0]["pointColors"] == ["#111111", "#222222", "#333333"]


def test_a_declared_point_colour_highlight_is_kept():
    raw = json.loads(json.dumps(BAR_MANY))
    raw["series"][0]["pointColors"] = ["#DC2626"] + ["#94A3B8"] * 6
    page, record = _page(raw)
    assert record["objects"][0]["raw"]["series"][0]["pointColors"][0] == "#DC2626"
    assert "#DC2626" in _fills(page)


# --- Value axis ------------------------------------------------------------------------------

def _tick_texts(page):
    return [node.text for node in _texts(page) if node.text and node.text.replace(",", "").replace("%", "").isdigit()]


def _lines(page):
    return [node for node in _group(page).iter(NS + "line")]


def test_nice_ticks_use_one_two_five_steps_and_cover_the_data():
    for high, intervals, expected in ((210, 5, [0, 50, 100, 150, 200, 250]), (71, 6, [0, 20, 40, 60, 80]),
                                      (87.4, 5, [0, 20, 40, 60, 80, 100]), (3, 5, [0, 1, 2, 3]),
                                      (0.8, 5, [0, 0.2, 0.4, 0.6, 0.8]), (12000, 4, [0, 5000, 10000, 15000]),
                                      (1, 6, [0, 0.2, 0.4, 0.6, 0.8, 1])):
        maximum, ticks = server_chart_v4._ticks(high, intervals, "")
        assert [value for value, _ in ticks] == expected, high
        assert float(maximum) >= high and ticks[-1][0] == float(maximum)
    assert [label for _, label in server_chart_v4._ticks(2400, 3, "")[1]] == ["0", "1,000", "2,000", "3,000"]
    assert [label for _, label in server_chart_v4._ticks(100, 5, "%")[1]][-1] == "100%"


def test_a_single_series_of_few_categories_keeps_clean_bars_with_value_labels():
    page, record = _case("column")
    assert len(_lines(page)) == 1  # the baseline only: no gridlines, no ticks
    assert _tick_texts(page) == []
    assert {"120억원", "150억원", "180억원", "210억원"} <= {node.text for node in _texts(page)}
    assert record["objects"][0]["raw"]["valueLabels"] == {"shown": True, "position": "outsideEnd"}


def test_several_series_many_categories_and_hidden_labels_get_ticks_and_gridlines():
    page, _ = _case("stacked")
    assert _tick_texts(page) == ["0", "20", "40", "60", "80", "100"] and len(_lines(page)) == 6
    page, _ = _case("bar_many")  # seven categories
    assert _tick_texts(page)[0] == "0" and len(_lines(page)) > 3
    hidden = json.loads(json.dumps(COLUMN))
    hidden["valueLabels"] = {"shown": False, "position": "outsideEnd"}
    page, record = _page(hidden)
    assert _tick_texts(page) and record["objects"][0]["raw"]["valueLabels"]["shown"] is False
    assert not any(node.text and node.text.endswith("억원") for node in _texts(page))


def test_line_and_area_charts_always_have_an_axis_and_the_axis_starts_at_zero():
    for name in ("line", "area", "lines_few"):
        page, _ = _case(name)
        ticks = _tick_texts(page)
        assert ticks and ticks[0] == "0"
        assert len(_lines(page)) == len(ticks)  # a gridline per tick above zero, and the baseline


def test_percent_charts_carry_the_percent_sign_on_their_ticks_and_other_units_stay_in_the_title():
    page, _ = _case("percent")
    assert "100%" in _tick_texts(page) and "0%" in _tick_texts(page)
    page, _ = _case("stacked")
    assert all(not tick.endswith("%") for tick in _tick_texts(page))


def test_the_axis_covers_the_tallest_bar_and_bars_are_scaled_to_it():
    page, _ = _case("stacked")
    rects = [node for node in _group(page).iter(NS + "rect") if float(node.get("width")) > 20
             and node.get("fill") == ACCENT]
    assert rects
    tops = {round(float(node.get("y1")), 1) for node in _lines(page)}
    # The tallest column reaches the top gridline (a total of exactly 100).
    heights = {}
    for node in _group(page).iter(NS + "rect"):
        if float(node.get("width")) > 20:
            heights.setdefault(node.get("x"), 0.0)
            heights[node.get("x")] += float(node.get("height"))
    top, bottom = min(tops), max(tops)
    assert all(abs(total - (bottom - top)) < 0.5 for total in heights.values())


# --- Labels never collide ---------------------------------------------------------------------

def _overlaps(first, second):
    return (first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3])


@pytest.mark.parametrize("name", list(CASES))
def test_no_two_texts_of_a_drawing_overlap_and_all_stay_inside_the_box(name):
    page, _ = _case(name)
    boxes = [(text, box) for text, box in _text_boxes(page)]
    for index, (first_text, first) in enumerate(boxes):
        assert 64 - 1 <= first[0] and first[2] <= 764 + 1 and 140 - 1 <= first[1] and first[3] <= 600 + 1, first_text
        for second_text, second in boxes[index + 1:]:
            assert not _overlaps(first, second), (first_text, second_text)


def test_bars_that_cannot_carry_their_labels_drop_them_for_the_axis_instead_of_overprinting():
    page, record = _case("clustered")
    assert record["objects"][0]["raw"]["valueLabels"]["shown"] is False
    assert _tick_texts(page)[0] == "0"
    wide = json.loads(json.dumps(CLUSTERED))
    page, record = _page(wide, box="64 140 1100 460")
    assert record["objects"][0]["raw"]["valueLabels"]["shown"] is True
    assert {"55억원", "30억원", "15억원"} <= {node.text for node in _texts(page)}


def test_a_few_lines_label_every_point_beside_it_and_many_label_their_ends():
    page, record = _case("lines_few")
    raw = record["objects"][0]["raw"]
    assert raw["valueLabels"] == {"shown": True, "position": "above"} and raw["markers"] is True
    drawn = {node.text for node in _texts(page)}
    assert {"30억원", "42억원", "51억원", "60억원", "12억원", "48억원"} <= drawn
    page, record = _case("lines_many")
    raw = record["objects"][0]["raw"]
    assert raw["valueLabels"]["shown"] is False
    ends = [node for node in _texts(page) if node.text in ("71억원", "64억원", "49억원")]
    assert len(ends) == 3
    assert not any(node.text in ("30억원", "42억원") for node in _texts(page))
    # The end labels sit right of the last points, in the order of the lines.
    by_value = {node.text: float(node.get("y")) for node in ends}
    assert by_value["71억원"] < by_value["64억원"] < by_value["49억원"]


def test_end_labels_of_nearly_touching_lines_are_pushed_apart():
    raw = {"chartType": "line", "categories": ["a", "b", "c"], "title": "지수 (점)",
           "series": [{"name": "A", "values": [10, 20, 37]}, {"name": "B", "values": [10, 21, 38]},
                      {"name": "C", "values": [10, 20, 36]}, {"name": "D", "values": [9, 19, 35]}]}
    page, record = _page(raw)
    assert record["dropped"] == []
    ends = sorted(float(node.get("y")) for node in _texts(page) if node.text in ("35점", "36점", "37점", "38점"))
    assert len(ends) == 4 and all(second - first >= 18 for first, second in zip(ends, ends[1:], strict=False))


def test_line_points_are_marked_and_areas_are_not():
    page, _ = _case("line")
    assert len(list(_group(page).iter(NS + "circle"))) == 4
    page, record = _case("area")
    assert not list(_group(page).iter(NS + "circle")) and record["objects"][0]["raw"]["markers"] is False


# --- Fallbacks instead of refusals ---------------------------------------------------------------

def test_a_pie_with_colliding_slice_names_gets_a_legend_instead_of_a_refusal():
    crowded = {"chartType": "pie", "categories": ["국내영업", "해외영업", "온라인", "파트너", "공공", "리테일"],
               "series": [{"name": "비중", "values": [60, 3, 3, 3, 3, 28]}], "title": "비중 (%)"}
    v3, record = _extract(_page_svg(crowded, render=server_chart.SERVER_CHART_REVISION_V3))
    assert record["dropped"][0]["reason"] == "server_chart_slice_labels_collide"
    page, record = _page(crowded)
    assert record["dropped"] == []
    texts = [node.text for node in _texts(page)]
    assert {"국내영업", "60%", "리테일", "28%"} <= set(texts)
    assert len([node for node in _group(page).iter(NS + "rect")]) == 6  # one swatch per slice
    assert record["objects"][0]["raw"]["valueLabels"] == {"shown": True, "position": "outsideEnd"}
    assert _trusted(page) == {"native-chart-x"}


def test_more_than_six_slices_use_the_legend_layout_and_up_to_six_keep_their_names_beside_the_slices():
    page, _ = _case("donut_legend")
    assert len(list(_group(page).iter(NS + "rect"))) == 7
    page, _ = _case("donut")
    assert not list(_group(page).iter(NS + "rect"))


def test_a_legend_without_value_labels_lists_names_only():
    raw = json.loads(json.dumps(DONUT_LEGEND))
    raw["valueLabels"] = {"shown": False, "position": None}
    page, record = _page(raw)
    texts = {node.text for node in _texts(page)}
    assert "제조업" in texts and "28%" not in texts and record["objects"][0]["raw"]["valueLabels"]["shown"] is False


def test_a_long_slice_name_wraps_inside_the_legend():
    raw = {"chartType": "donut", "categories": ["정보통신 및 소프트웨어 서비스업", "금융 보험 및 부동산업", "제조업", "도소매업", "기타", "건설업", "운수업"],
           "series": [{"name": "비중", "values": [28, 19, 16, 12, 10, 8, 7]}], "title": "산업별 비중 (%)"}
    page, record = _page(raw, box="64 140 600 460")
    assert record["dropped"] == []
    wrapped = [node for node in _texts(page) if node.findall(NS + "tspan")]
    assert wrapped and all(len(node.findall(NS + "tspan")) == 2 for node in wrapped)
    assert "".join(part.text for part in wrapped[0].findall(NS + "tspan")).replace(" ", "") != ""


def test_category_names_wrap_to_three_lines_at_the_floor_and_are_never_changed():
    names = ["신규 고객 확보 캠페인 전략", "기존 고객 유지 프로그램 운영", "파트너 채널 확대 및 지원", "디지털 전환 투자 계획", "인재 채용 및 육성 방안"]
    raw = {"chartType": "column", "categories": names, "series": [{"name": "투자", "values": [40, 32, 28, 55, 21]}],
           "title": "투자 (억원)"}
    page, record = _page(raw, box="64 140 560 400")
    assert record["dropped"] == []
    drawn = []
    for node in _texts(page):
        parts = node.findall(NS + "tspan")
        if parts:
            drawn.append(" ".join(part.text for part in parts))
    assert sorted(drawn) == sorted(names)
    assert max(len(node.findall(NS + "tspan")) for node in _texts(page)) == 3
    assert record["objects"][0]["raw"]["categories"] == names


def test_korean_words_break_between_characters_only_as_a_last_resort_and_latin_words_never():
    assert server_chart_v4._fit_lines("가나다라마바사아", 16, 70, 3) == ["가나다라", "마바사아"]
    assert server_chart_v4._fit_lines("가나다 라마바", 16, 60, 3) == ["가나다", "라마바"]
    assert server_chart_v4._fit_lines("Internationalization", 16, 70, 3) is None
    assert server_chart_v4._fit_lines("가나다라마바사아자차카타", 16, 70, 2) is None
    assert server_chart_v4._fit_lines("가나다", 16, 100, 3) == ["가나다"]


def test_the_chart_shrinks_toward_the_floor_before_it_is_refused():
    names = ["이천이십이년실적입니다", "이천이십삼년실적입니다", "이천이십사년실적입니다", "이천이십오년실적입니다",
             "이천이십육년실적입니다", "이천이십칠년실적입니다"]
    raw = {"chartType": "column", "categories": names, "series": [{"name": "실적", "values": [1, 2, 3, 4, 5, 6]}],
           "title": "실적 (점)"}
    roomy, record = _page(raw, box="64 140 760 460", body="20")
    tight, tight_record = _page(raw, box="64 140 440 300", body="20")
    assert record["dropped"] == [] and tight_record["dropped"] == []
    size = lambda page: min(float(node.get("font-size")) for node in _texts(page))  # noqa: E731
    assert size(roomy) == 20.0 and 14.0 <= size(tight) < 20.0
    v3, dropped = _extract(_page_svg(raw, render=server_chart.SERVER_CHART_REVISION_V3, box="64 140 440 300",
                                     body="20"))
    assert dropped["dropped"], "revision three refuses what revision four draws"


def test_a_clustered_zero_value_is_drawn_as_an_empty_bar_with_its_label():
    page, record = _case("zero")
    assert record["dropped"] == []
    assert "0건" in {node.text for node in _texts(page)}
    assert len([node for node in _group(page).iter(NS + "rect")]) == 2  # no zero-height rect
    wire, _ = page_native_objects(page, pt_per_px=.75, revision=V4)
    assert wire["objects"][0]["payload"]["series"][0]["values"] == [12, 0, 7]


def test_a_title_wider_than_the_box_wraps_instead_of_overflowing():
    raw = json.loads(json.dumps(COLUMN))
    raw["title"] = "연도별 국내 및 해외 합산 매출 추이와 전년 대비 증감 현황 (억원)"
    page, record = _page(raw, box="64 140 400 400")
    assert record["dropped"] == []
    title = next(node for node in _texts(page) if node.get("font-weight") == "bold" and node.findall(NS + "tspan"))
    assert len(title.findall(NS + "tspan")) == 2
    assert server_chart._drawn_box(title, "text")[2] <= 464 + 1


# --- What is still refused, and why --------------------------------------------------------------

@pytest.mark.parametrize("mutate,box,reason", [
    (lambda raw: raw["series"][0]["values"].__setitem__(1, -5), None, "server_chart_value_negative"),
    (lambda raw: raw.update(grouping="stacked"), None, "server_chart_stack_needs_series"),
    (lambda raw: raw.update(chartType="radar"), None, "server_chart_type_unsupported"),
    (lambda raw: raw.update(categories=["a"], series=[{"name": "s", "values": [1]}], chartType="line"), None,
     "server_chart_trend_needs_categories"),
    (lambda raw: raw["series"][0].update(values=[1, 2]), None, "server_chart_shape_invalid"),
    (lambda raw: raw["series"][0]["values"].__setitem__(0, "많음"), None, "server_chart_value_invalid"),
    (lambda raw: raw["series"][0]["values"].__setitem__(0, "1e999"), None, "server_chart_value_invalid"),
    (lambda raw: raw["series"][0].update(values=[0, 0, 0, 0]), None, "server_chart_value_not_positive"),
    (lambda raw: None, "64 140 150 400", "server_chart_box_too_small"),
    (lambda raw: None, "64 140 700 100", "server_chart_box_too_small"),
    (lambda raw: None, "64 140 170 125", "server_chart_category_labels_too_wide"),
])
def test_undrawable_declarations_and_boxes_are_still_refused_with_a_reason(mutate, box, reason):
    raw = json.loads(json.dumps(COLUMN))
    mutate(raw)
    _, record = _page(raw, **({"box": box} if box else {}))
    assert record["dropped"] and record["dropped"][0]["reason"] == reason, record["dropped"]
    assert record["objects"] == []


@pytest.mark.parametrize("raw,reason", [
    ({"chartType": "pie", "categories": ["a", "b", "c"], "series": [{"name": "s", "values": [5, 0, 3]}]},
     "server_chart_value_not_positive"),
    ({"chartType": "donut", "categories": list("abcdefghi"), "series": [{"name": "s", "values": [1] * 9}]},
     "server_chart_too_many_slices"),
    ({"chartType": "pie", "categories": ["a", "b"], "series": [{"name": "s", "values": [1, 2]},
                                                              {"name": "t", "values": [1, 2]}]},
     "server_chart_polar_needs_one_series"),
    ({"chartType": "column", "grouping": "stacked", "categories": ["a", "b"],
      "series": [{"name": "s", "values": [1, 0]}, {"name": "t", "values": [1, 2]}]}, "server_chart_value_not_positive"),
    ({"chartType": "line", "categories": ["a", "b"], "grouping": "stacked", "series": [{"name": "s", "values": [1, 2]}]},
     "server_chart_grouping_unsupported"),
    ({"chartType": "column", "categories": ["Internationalization", "Transubstantiation", "Incomprehensibilities"],
      "series": [{"name": "s", "values": [1, 2, 3]}]}, "server_chart_category_labels_too_wide"),
])
def test_other_refusals_that_remain(raw, reason):
    _, record = _page(raw, box="64 140 300 300")
    assert record["dropped"][0]["reason"] == reason


def test_a_refused_v4_placeholder_is_left_empty_and_unmarked():
    root = ET.fromstring(_page_svg({**COLUMN, "series": [{"name": "매출", "values": [1, -2, 3, 4]}]}))
    record = extract_native_declarations(root)
    group = next(node for node in root.iter(NS + "g") if node.get("id") == "native-chart-x")
    assert record["dropped"][0]["reason"] == "server_chart_value_negative"
    assert len(group) == 0 and "data-chart-render" not in group.attrib and "data-chart-box" not in group.attrib


# --- Page integration -----------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(CASES))
def test_a_v4_page_passes_the_svg_contract(name):
    page, _ = _case(name)
    validate_svg(page["svg_content"])


def test_chart_text_follows_the_page_body_size_within_bounds():
    for body, expected in (("10", 14.0), ("17", 17.0), ("40", 20.0)):
        page, _ = _case("column", body=body)
        sizes = {float(node.get("font-size")) for node in _texts(page) if node.get("font-weight") != "bold"}
        assert min(sizes) == expected


def test_value_text_on_a_bar_keeps_contrast():
    raw = json.loads(json.dumps(COLUMN))
    raw["title"] = "매출"
    raw["series"][0]["pointColors"] = ["#FACC15", "#1E3A8A", "#FACC15", "#1E3A8A"]
    raw["valueLabels"] = {"shown": True, "position": "insideEnd"}
    page, record = _page(raw, box="64 140 700 460")
    assert record["objects"][0]["raw"]["valueLabels"]["position"] == "insideEnd"
    fills = {node.text: node.get("fill") for node in _texts(page) if node.text in ("120", "150", "180", "210")}
    assert fills == {"120": "#0F172A", "150": "#FFFFFF", "180": "#0F172A", "210": "#FFFFFF"}


def test_a_correction_sees_v4_charts_as_their_placeholders():
    page, record = _case("lines_many")
    view = server_chart.placeholder_view(page["svg_content"], record)
    group = next(node for node in ET.fromstring(view).iter(NS + "g") if node.get("id") == "native-chart-x")
    assert len(group) == 0 and group.get("data-chart-render") == R4
    assert json.loads(group.get("data-native-chart"))["chartType"] == "line"


def test_admission_pins_v4_for_a_fresh_deck_and_decks_with_older_revisions_keep_theirs():
    from services.generation_core.drivers.svg.server_chart import SERVER_CHART_REVISION_V3
    page, _ = _case("column")
    quality = {"passed": True, "failures": []}
    server_chart.apply_server_chart_admission(quality, page, page["svg_content"], {"server_chart_render": R4})
    assert quality["failures"] == []
    quality = {"passed": True, "failures": []}
    server_chart.apply_server_chart_admission(quality, page, page["svg_content"],
                                              {"server_chart_render": SERVER_CHART_REVISION_V3})
    assert quality["failures"][0]["reason"] == "server_chart_revision_not_admitted"


def test_something_painted_over_a_v4_chart_is_a_layout_failure():
    cover = '<rect x="300" y="300" width="200" height="100" fill="#FFFFFF"/>'
    root = ET.fromstring(_page_svg(COLUMN))
    root.append(ET.fromstring(cover.replace("<rect", f'<rect xmlns="{NS[1:-1]}"')))
    record = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, record)
    quality = {"passed": True, "failures": []}
    server_chart.apply_server_chart_admission(quality, page, svg, {"server_chart_render": R4})
    assert [item["kind"] for item in quality["failures"]] == ["server_chart_occluded"]


def test_grounding_still_checks_every_v4_value_against_the_source():
    page, _ = _case("pie")
    quality = {"passed": True, "failures": []}
    server_chart.apply_server_chart_grounding(quality, page, page["svg_content"], CASES["pie"][1])
    assert quality["failures"] == []
    server_chart.apply_server_chart_grounding(quality, page, page["svg_content"], "chart: 비중: 국내 55%, 해외 31%, 온라인 15%")
    assert quality["failures"][0]["reason"] == "server_chart_value_ungrounded"


# --- PPTX conversion -----------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(CASES))
def test_a_v4_page_converts_to_a_pptx_with_one_native_chart(name, tmp_path):
    from tests.unit.test_svg_native_objects_end_to_end import _export, _frames
    raw, _source = CASES[name]
    page, record = _case(name)
    assert record["dropped"] == []
    payload = {"svg_content": finalize_svg(page["svg_content"], index=0, n=1), "quality": {"passed": True},
               "nativeDeclarations": page["nativeDeclarations"]}
    bind_native_declarations(payload, payload["svg_content"], page["nativeDeclarations"])
    content = svg_slide_content(payload, role="content", source="", pt_per_px=0.75)
    assert content["svgReceipt"]["nativeObjects"] == {"declared": 1, "accepted": 1, "dropped": []}
    deck, evidence = _export(tmp_path, [payload["svg_content"]], [content])
    charts = [shape for shape in _frames(deck.slides[0]) if shape.has_chart]
    assert len(charts) == 1
    plot = charts[0].chart.plots[0]
    assert list(plot.categories) == raw["categories"]
    assert [list(series.values) for series in plot.series] == [[float(v) for v in s["values"]] for s in raw["series"]]
    assert evidence["structuralReadback"]["nativeObjects"] == {"offered": 1, "native": 1, "fellBack": 0, "reasons": []}


# --- Published output -----------------------------------------------------------------------------

#: Published output of revision four; an improvement is a new revision, never an edit of these.
V4_DIGESTS: dict[str, str] = {
    "column": "sha256:29e6429cf1fc0d3237117a798588997a4a87240d198f7a397fea925f7e055bd9",
    "bar_many": "sha256:452e1808b2fb3f313c96e369a58bf1ecbefe4155abd5009b1df257e9bf9f740e",
    "clustered": "sha256:dba826deaa9ec3943bdba8eb1fd21374f93a37dacbf803867049a6e762b600b6",
    "stacked": "sha256:5fce5b31f4409da623799bdb744f2f00bdd13200f38ca8fadd9c584382b18c5f",
    "percent": "sha256:c315cb09405f722fb98e7b0fea30a21c61e29ce7d2c8c2f5505ca1c96737feb1",
    "line": "sha256:304432ee0db9e39a2bc9e06678b9af6003b386d2da45150e6263681b19d141c8",
    "lines_few": "sha256:485afc02e5b3470480d40b2c4b8bbfbb75c17a1515b753f1c7a3ea171ff9063c",
    "lines_many": "sha256:5dcc1198f4e1ee250aa278e04dffcc706a631dc2e6d16aaa27e6f60745319856",
    "area": "sha256:df9fbd6370bf7dd15053052f79bfebc2a2b1579994c485581705009e6236c92a",
    "areas": "sha256:616f0f4c9cd980d22bd19075a44cd0429c717518cd90631842023d3a00874987",
    "pie": "sha256:44b934fb2f4d088e518a5ad773cfd694daf1b957c82fe87485581635af3629d8",
    "pie_small": "sha256:f738066f8a77f584499e9fd57a8cad87db0c3c17fd536d5e396ae9a3d1171da2",
    "donut": "sha256:f4cc4d0dc85038ac4dcb3429b90beef28d07aaa9a3de817099b146b2784643e6",
    "donut_legend": "sha256:545059b31718be5001b5f36e08815ca8c3ce63941b41850d3b78d1f8fc338cb7",
    "zero": "sha256:b965bd2c73e84a41fafc2e164eaa34995cc435e019b5970a238f76de73eb918b",
}


@pytest.mark.parametrize("name", list(V4_DIGESTS))
def test_revision_four_output_is_frozen(name):
    from services.svg_contract import source_digest
    page, _ = _case(name)
    assert source_digest(page["svg_content"]) == V4_DIGESTS[name]


def test_every_case_is_pinned():
    assert set(V4_DIGESTS) == set(CASES)

