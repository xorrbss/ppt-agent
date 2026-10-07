"""Publication of model-declared native tables/charts on hand-written SVG pages."""
from __future__ import annotations

import json
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg import native_declarations as declarations
from services.generation_core.drivers.svg.native_declarations import (
    bind_native_declarations, extract_native_declarations,
)
from services.generation_core.drivers.svg.native_publish import canvas_point_per_px, page_native_objects
from services.generation_core.drivers.svg.publish import svg_slide_content
from services.generation_core.native_objects import NativeObjects
from services.svg_contract import finalize_svg, validate_svg

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
TITLE = '<text x="80" y="80" font-family="Noto Sans KR" font-size="36" fill="#111111">지역별 매출</text>'
ROWS = [["지역", "매출(억원)", "비고"], ["서울", "1,200", "본사"], ["부산", "850", "지점\n신규"],
        ["대구", "430", ""]]


def _attr(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False).replace("&", "&amp;").replace("'", "&apos;")


def _cell(x, y, text, *, anchor="start", header=False):
    style = ('font-size="20" font-weight="bold" fill="#FFFFFF"' if header
             else 'font-size="18" fill="#222222"')
    return (f'<text x="{x}" y="{y}" font-family="Noto Sans KR" {style} text-anchor="{anchor}">'
            f"{text}</text>")


CARD = '<rect x="100" y="140" width="660" height="300" rx="12" fill="#1F4E79" fill-opacity="0.1"/>'
CARD_TINT = "#E9EDF2"  # 10% #1F4E79 over the white canvas


def _table_group(rows=ROWS, *, cells=None, extra="", before=CARD, grid=None):
    grid = grid if grid is not None else (
        '<rect x="0" y="0" width="600" height="60" fill="#1F4E79"/>'
        + "".join(f'<line x1="{x}" y1="0" x2="{x}" y2="240" stroke="#CCCCCC"/>' for x in (200, 400))
        + "".join(f'<line x1="0" y1="{y}" x2="600" y2="{y}" stroke="#CCCCCC"/>' for y in (120, 180))
        + '<line x1="0" y1="60" x2="600" y2="60" stroke="#1F4E79" stroke-width="2" stroke-opacity="0.5"/>'
        + '<rect x="0" y="0" width="600" height="240" fill="none" stroke="#333333" stroke-width="2"/>')
    default = (_cell(100, 38, "지역", anchor="middle", header=True)
               + _cell(300, 38, "매출(억원)", anchor="middle", header=True)
               + _cell(500, 38, "비고", anchor="middle", header=True)
               + _cell(12, 98, "서울") + _cell(388, 98, "1,200", anchor="end") + _cell(412, 98, "본사")
               + _cell(12, 158, "부산") + _cell(388, 158, "850", anchor="end")
               + '<text x="412" y="140" font-family="Noto Sans KR" font-size="18" fill="#222222">'
                 '<tspan x="412" y="140">지점</tspan><tspan x="412" y="165">신규</tspan></text>'
               + _cell(12, 218, "대구") + _cell(388, 218, "430", anchor="end"))
    declaration = _attr({"rows": rows, "headerRows": 1})
    return (f'{before}<g transform="translate(100,150)"><g id="native-table-sales" '
            f'data-native-table=\'{declaration}\' transform="translate(20,10)">'
            f'{grid}{default if cells is None else cells}{extra}</g></g>')


def _page(body: str, *, stale_body: str | None = None) -> tuple[dict, str]:
    """(payload, final svg) as the author and render steps leave them."""
    root = ET.fromstring(HEAD + TITLE + body + "</svg>")
    found = extract_native_declarations(root)
    authored = ET.tostring(root, encoding="unicode")
    payload: dict = {}
    bind_native_declarations(payload, authored, found)
    final_source = authored if stale_body is None else HEAD + TITLE + stale_body + "</svg>"
    payload["svg_content"] = finalize_svg(final_source, index=0, n=3)
    return payload, payload["svg_content"]


def _accepted_table(body: str) -> dict:
    payload, final = _page(body)
    assert "data-native" not in final
    validate_svg(final)
    wire, summary = page_native_objects(payload, pt_per_px=0.75)
    assert summary == {"declared": 1, "accepted": 1, "dropped": []}
    [entry] = NativeObjects.from_wire(wire).objects
    assert entry.id == "native-table-sales" and entry.kind == "table"
    assert entry.anchor.to_wire() == {"x": 120.0, "y": 160.0, "width": 600.0, "height": 240.0}
    return entry.payload


def test_korean_table_is_accepted_with_exact_anchor_and_payload():
    table = _accepted_table(_table_group())
    assert [[cell["text"] for cell in row] for row in table["rows"]] == ROWS
    assert table["headerRows"] == 1
    assert table["columnWidths"] == [200.0, 200.0, 200.0]
    assert table["rowHeightsPt"] == [45.0, 45.0, 45.0, 45.0]
    header, body = table["rows"][0][0], table["rows"][1][1]
    assert header == {"text": "지역", "fill": "#1F4E79", "textColor": "#FFFFFF", "bold": True,
                      "fontFamily": "Noto Sans KR", "fontSizePt": 15.0}
    assert body == {"text": "1,200", "fill": CARD_TINT, "textColor": "#222222", "bold": False,
                    "fontFamily": "Noto Sans KR", "fontSizePt": 13.5}
    assert table["rows"][3][2] == {"text": "", "fill": CARD_TINT}
    assert [style.get("horizontalAlign") for style in table["cellStyles"][1]] == ["left", "right", "left"]
    assert "horizontalAlign" not in table["cellStyles"][3][2]


def test_cell_borders_carry_the_drawn_stroke_colours_and_widths():
    styles = _accepted_table(_table_group())["cellStyles"]
    frame, grid = {"color": "#333333", "widthPt": 1.5}, {"color": "#CCCCCC", "widthPt": 0.75}
    rule_on_header = {"color": "#1F4E79", "widthPt": 1.5}  # 50% #1F4E79 over the header fill
    rule_on_body = {"color": "#849EB6", "widthPt": 1.5}  # 50% #1F4E79 over the card tint
    assert styles[0][0]["borders"] == {"top": frame, "right": grid, "bottom": rule_on_header, "left": frame}
    assert styles[1][1]["borders"] == {"top": rule_on_body, "right": grid, "bottom": grid, "left": grid}
    assert styles[3][2]["borders"] == {"top": grid, "right": frame, "bottom": frame, "left": grid}


def test_borderless_cell_rects_give_fills_and_no_borders():
    cells = "".join(f'<rect x="{c * 200}" y="{r * 60}" width="200" height="60" '
                    f'fill="{"#1F4E79" if r == 0 else "#FFFFFF"}"/>' for r in range(4) for c in range(3))
    table = _accepted_table(_table_group(grid=cells))
    assert all("borders" not in style for row in table["cellStyles"] for style in row)
    assert table["rows"][0][2]["fill"] == "#1F4E79" and table["rows"][2][0]["fill"] == "#FFFFFF"


def test_horizontal_rules_alone_do_not_determine_the_grid():
    rules = "".join(f'<line x1="0" y1="{y}" x2="600" y2="{y}" stroke="#333333"/>' for y in (0, 60, 120, 180, 240))
    wire, summary, _ = _dropped(_table_group(grid=rules))
    assert wire is None and summary["dropped"][0]["reason"] == "native_table_grid_undetermined"


def test_a_shape_painted_behind_a_cell_makes_the_background_not_solid():
    blob = '<path d="M 400 230 L 460 230 L 460 270 Z" fill="#FF8800"/>'  # covers row 1, column 1
    wire, summary, _ = _dropped(_table_group(before=CARD + blob))
    assert wire is None and summary["dropped"][0]["reason"] == "native_table_background_not_solid"
    covered = CARD.replace(' fill-opacity="0.1"', "").replace("#1F4E79", "#F4F6F8")
    table = _accepted_table(_table_group(before=blob + covered))  # an opaque card covers it again
    assert table["rows"][1][1]["fill"] == "#F4F6F8"


def test_a_stroke_covering_part_of_an_edge_is_rejected():
    stub = '<line x1="0" y1="120" x2="100" y2="120" stroke="#FF0000"/>'
    wire, summary, _ = _dropped(_table_group(extra=stub))
    assert wire is None and summary["dropped"][0]["reason"] == "native_table_border_partial"


def _dropped(body: str) -> tuple[dict | None, dict, str]:
    payload, final = _page(body)
    wire, summary = page_native_objects(payload, pt_per_px=0.75)
    return wire, summary, final


def test_json_text_that_differs_from_the_drawing_is_dropped_and_the_grid_kept():
    rows = [row[:] for row in ROWS]
    rows[1][1] = "1,300"
    wire, summary, final = _dropped(_table_group(rows))
    assert wire is None
    assert summary["accepted"] == 0
    assert summary["dropped"] == [{"id": "native-table-sales", "kind": "table",
                                   "reason": "native_table_text_unaccounted"}]
    validate_svg(final)
    assert final.count("<line") == 5 and ">1,200<" in final  # the drawn table is untouched


def test_a_cell_drawn_twice_is_dropped():
    wire, summary, _ = _dropped(_table_group(extra=_cell(12, 218, "서울")))
    assert wire is None and summary["dropped"][0]["reason"] == "native_table_cell_drawn_twice"


def test_extra_drawn_text_in_the_group_is_dropped():
    wire, summary, _ = _dropped(_table_group(extra=_cell(12, 230, "출처: 내부 자료")))
    assert wire is None and summary["dropped"][0]["reason"] == "native_table_text_unaccounted"


def test_a_cell_drawn_in_another_position_is_dropped():
    swapped = _table_group().replace('x="12" y="98"', 'x="12" y="TMP"').replace(
        'x="12" y="158"', 'x="12" y="98"').replace('x="12" y="TMP"', 'x="12" y="158"')
    wire, summary, _ = _dropped(swapped)
    assert wire is None and summary["dropped"][0]["reason"] == "native_table_cell_position_mismatch"


def test_malformed_json_is_dropped_at_extraction_and_the_page_stays_contract_clean():
    body = _table_group().replace("data-native-table='", "data-native-table='{broken ")
    wire, summary, final = _dropped(body)
    assert wire is None
    assert summary == {"declared": 1, "accepted": 0, "dropped": [
        {"id": "native-table-sales", "kind": "table", "reason": "native_declaration_json_invalid"}]}
    assert "data-native" not in final
    validate_svg(final)


def test_declarations_bound_to_another_drawing_are_ignored():
    corrected = _table_group().replace(">서울<", ">서울특별시<")
    payload, _ = _page(_table_group(), stale_body=corrected.replace(
        " data-native-table='" + _attr({"rows": ROWS, "headerRows": 1}) + "'", ""))
    wire, summary = page_native_objects(payload, pt_per_px=0.75)
    assert wire is None
    assert summary["dropped"] == [{"id": "native-table-sales", "kind": "table",
                                   "reason": "native_declaration_stale_svg"}]


def test_pages_without_declarations_publish_nothing():
    payload = {"svg_content": finalize_svg(HEAD + TITLE + "</svg>", index=0, n=1)}
    assert page_native_objects(payload, pt_per_px=0.75) == (None, None)


@pytest.mark.parametrize("declared", [True, False])
def test_explicit_requested_table_requires_validated_exact_native_rows(declared):
    from services.generation_core.drivers.svg.native_requirements import apply_requested_native_quality
    source = "정확한 표: 지역 | 매출(억원) | 비고\n서울 | 1,200 | 본사\n부산 | 850 | 지점 신규\n대구 | 430 |"
    payload, svg = _page(_table_group())
    if not declared:
        payload.pop("nativeDeclarations")
    quality = {"passed": True}
    apply_requested_native_quality(quality, payload, svg, source, pt_per_px=.75)
    assert quality["passed"] is declared
    assert quality["requestedNativeObjects"]["passed"] is declared
    if not declared:
        assert quality["failures"][0]["kind"] == "native_requested_table_missing"


def test_requested_chart_does_not_accept_unrelated_native_series_or_plain_bars():
    from services.generation_core.drivers.svg.native_requirements import apply_requested_native_quality, requested_native_data
    spec = {"chartType": "bar", "categories": ["A", "B"], "series": [{"name": "S", "values": [30, 60]}]}
    text = 'font-family="Inter" font-size="16" fill="#333333"'
    body = (f'<g id="native-chart-two" data-native-chart=\'{_attr(spec)}\'>'
            f'<rect x="100" y="200" width="100" height="40" fill="#2563EB"/>'
            f'<rect x="100" y="300" width="200" height="40" fill="#2563EB"/>'
            f'<text x="60" y="225" {text}>A</text><text x="60" y="325" {text}>B</text>'
            f'<text x="400" y="180" {text}>S</text></g>')
    payload, svg = _page(body)
    accepted, _ = page_native_objects(payload, pt_per_px=.75)
    assert accepted is not None
    for source, passed in [("막대 차트: A 30, B 60.", True), ("막대 차트: A 45, B 60.", False),
                           ("관계도: A에서 B로 이동하고 30에서 60으로 개선", True)]:
        quality = {"passed": True}
        apply_requested_native_quality(quality, payload, svg, source, pt_per_px=.75)
        assert quality["passed"] is passed
    assert requested_native_data("막대 차트: 1분기 30, 2분기 45, 3분기 60. 단위 억원.")["charts"][0]["values"] == ["30", "45", "60"]
    payload.pop("nativeDeclarations")
    quality = {"passed": True}
    apply_requested_native_quality(quality, payload, svg, "막대 차트: A 30, B 60.", pt_per_px=.75)
    assert quality["failures"][0]["kind"] == "native_requested_chart_missing"


def test_non_table_elements_in_the_group_drop_the_declaration():
    wire, summary, _ = _dropped(_table_group(extra='<circle cx="10" cy="10" r="4" fill="#FF0000"/>'))
    assert wire is None and summary["dropped"][0]["reason"] == "native_group_element_unsupported"


def test_simple_bar_chart_is_accepted():
    spec = {"chartType": "column", "categories": ["1분기", "2분기", "3분기"],
            "series": [{"name": "매출", "values": [120, 95, 140]}], "title": "분기 매출"}
    text = 'font-family="Noto Sans KR" font-size="16" fill="#333333" text-anchor="middle"'
    bars = "".join(f'<rect x="{60 + i * 120}" y="{260 - v}" width="60" height="{v}" fill="#2563EB"/>'
                   f'<text x="{90 + i * 120}" y="285" {text}>{label}</text>'
                   f'<text x="{90 + i * 120}" y="{250 - v}" {text}>{v}</text>'
                   for i, (label, v) in enumerate(zip(spec["categories"], spec["series"][0]["values"],
                                                      strict=True)))
    body = (f'<g id="native-chart-sales" data-native-chart=\'{_attr(spec)}\' transform="translate(600,200)">'
            f'<text x="200" y="30" {text}>분기 매출</text>'
            f'<line x1="40" y1="260" x2="420" y2="260" stroke="#999999"/>{bars}'
            f'<rect x="300" y="40" width="12" height="12" fill="#2563EB"/><text x="340" y="52" {text}>매출</text></g>')
    wire, summary, final = _dropped(body)
    assert summary == {"declared": 1, "accepted": 1, "dropped": []}
    [entry] = NativeObjects.from_wire(wire).objects
    assert entry.kind == "chart"
    assert entry.payload["series"] == [{"name": "매출", "values": [120, 95, 140], "color": "#2563EB"}]
    assert entry.payload["title"] == "분기 매출" and entry.payload["fontFamily"] == "Noto Sans KR"
    assert entry.anchor.x >= 600 and entry.anchor.y >= 200
    validate_svg(final)


def _small_chart(spec: dict, extra: str = "") -> str:
    text = 'font-family="Inter" font-size="16" fill="#333333"'
    return (f'<g id="native-chart-a" data-native-chart=\'{_attr(spec)}\'>'
            f'<rect x="100" y="300" width="200" height="40" fill="#2563EB"/>'
            f'<text x="100" y="360" {text}>A</text><text x="320" y="330" {text}>S</text>{extra}</g>')


_BAR = {"chartType": "bar", "categories": ["A"], "series": [{"name": "S", "values": [3]}]}


@pytest.mark.parametrize(("spec", "extra", "reason"), [
    (_BAR, '<text x="100" y="400" font-family="Inter" font-size="16" fill="#333333">50</text>',
     "native_chart_text_unaccounted"),
    ({**_BAR, "title": "제목"}, "", "native_chart_label_not_drawn"),
    ({**_BAR, "chartType": "pie", "series": [{"name": "S", "values": [3]}, {"name": "S", "values": [4]}]},
     "", "native_chart_pie_series_invalid"),
    ({**_BAR, "valueAxis": {"title": "억원"}}, "", "native_chart_json_invalid"),
])
def test_chart_declarations_the_drawing_does_not_support_are_dropped(spec, extra, reason):
    wire, summary, _ = _dropped(_small_chart(spec, extra))
    assert wire is None and summary["dropped"][0]["reason"] == reason


def test_value_labels_with_separators_and_units_are_accounted_for():
    spec = {**_BAR, "series": [{"name": "S", "values": [1200.5]}]}
    label = '<text x="320" y="360" font-family="Inter" font-size="16" fill="#333333">1,200.5억원</text>'
    wire, summary, _ = _dropped(_small_chart(spec, label))
    assert summary["accepted"] == 1 and wire is not None


def test_charts_are_dropped_while_not_offered(monkeypatch):
    monkeypatch.setattr(declarations, "NATIVE_CHARTS_OFFERED", False)
    spec = {"chartType": "bar", "categories": ["A"], "series": [{"name": "S", "values": [3]}]}
    body = (f'<g id="native-chart-a" data-native-chart=\'{_attr(spec)}\'>'
            '<text x="100" y="360" font-family="Inter" font-size="16" fill="#333333">A</text></g>')
    wire, summary, final = _dropped(body)
    assert wire is None and summary["dropped"][0]["reason"] == "native_chart_not_offered"
    assert "data-native" not in final


@pytest.mark.parametrize(("canvas", "expected"), [
    ("document", 9000 / 12700), ("authored", 0.75)])
def test_point_per_px_follows_the_deck_canvas(canvas, expected):
    if canvas == "document":
        from services.document_canvas import document_canvas_for_orientation
        wire = document_canvas_for_orientation("landscape").model_dump(mode="json", by_alias=True)
    else:
        from services.authored_canvas import LANDSCAPE_ASPECT_RATIO, canvas_metadata_for_aspect_ratio
        wire = canvas_metadata_for_aspect_ratio(LANDSCAPE_ASPECT_RATIO).model_dump(mode="json", by_alias=True)
    assert canvas_point_per_px(wire) == pytest.approx(expected)


def test_slide_content_carries_the_sidecar_and_only_a_receipt_summary():
    payload, _ = _page(_table_group())
    payload.update(quality={"passed": True}, attempts=[{"receipt": {}}])
    content = svg_slide_content(payload, role="content", source="# 표", pt_per_px=0.75)
    assert NativeObjects.from_wire(content["nativeObjects"]).by_id()["native-table-sales"].kind == "table"
    assert "nativeDeclarations" not in content["svgReceipt"]
    assert content["svgReceipt"]["nativeObjects"] == {"declared": 1, "accepted": 1, "dropped": []}
    assert content["svgReceipt"]["quality"] == {"passed": True}
    plain = svg_slide_content({"svg_content": payload["svg_content"], "quality": {}}, role="content",
                              source="", pt_per_px=0.75)
    assert "nativeObjects" not in plain and "nativeObjects" not in plain["svgReceipt"]
