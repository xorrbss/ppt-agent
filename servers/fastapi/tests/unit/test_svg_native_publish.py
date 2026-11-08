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

    # A bounded visible merge must preserve every source cell, including blanks.
    grid = '<rect x="0" y="0" width="600" height="60" fill="#1F4E79"/>'
    grid += '<rect x="0" y="0" width="600" height="240" fill="none" stroke="#333333"/>'
    grid += '<line x1="200" y1="0" x2="200" y2="240" stroke="#CCCCCC"/>'
    grid += '<line x1="400" y1="0" x2="400" y2="180" stroke="#CCCCCC"/>'
    grid += ''.join(f'<line x1="0" y1="{y}" x2="600" y2="{y}" stroke="#CCCCCC"/>' for y in (60,120,180))
    import xml.etree.ElementTree as ElementTree
    merged_group = ElementTree.fromstring("<g>" + _table_group(grid=grid) + "</g>")
    for drawn in merged_group.iter():
        if drawn.tag.endswith('text') and drawn.text == '430':
            drawn.set("x", "400")
            drawn.set("text-anchor", "middle")
    merged_table = _accepted_table(ElementTree.tostring(merged_group, encoding='unicode'))
    assert merged_table['mergedCells'] == [{'row':3,'column':1,'rowSpan':1,'columnSpan':2}]
    assert [[cell['text'] for cell in row] for row in merged_table['rows']] == ROWS
    # Compound stroke-only paths are the same grid, including translated groups.
    for source, expected in ((_table_group(), table),
                             (ElementTree.tostring(merged_group, encoding='unicode'), merged_table)):
        parsed = ElementTree.fromstring('<g>' + source + '</g>')
        for parent in parsed.iter():
            lines = [child for child in parent if child.tag.rsplit('}', 1)[-1] == 'line']
            batches = {}
            for line in lines:
                attributes = {key: value for key, value in line.attrib.items()
                              if key not in {'x1', 'y1', 'x2', 'y2'}}
                key = tuple(sorted(attributes.items()))
                batches.setdefault(key, []).append(line)
            for attributes, batch in batches.items():
                drawing = []
                for line in batch:
                    drawing.append('M ' + line.get('x1') + ',' + line.get('y1'))
                    drawing.append(('H ' + line.get('x2')) if line.get('y1') == line.get('y2')
                                   else ('V ' + line.get('y2')))
                path = ElementTree.Element('path', {**dict(attributes), 'fill': 'none', 'd': ' '.join(drawing)})
                parent.insert(list(parent).index(batch[0]), path)
                for line in batch:
                    parent.remove(line)
        compound = ElementTree.tostring(parsed, encoding='unicode')
        assert _accepted_table(compound) == expected
        payload, _ = _page(compound)
        from copy import deepcopy
        frozen = deepcopy(payload)
        page_native_objects(payload, pt_per_px=0.75)
        assert payload == frozen
        from services.generation_core.drivers.svg.native_table import apply_drawn_table_style
        [entry] = NativeObjects.from_wire(page_native_objects(payload, pt_per_px=0.75)[0]).objects
        from services.generation_core.drivers.svg.export_projection import converter_payload
        projected = converter_payload(entry)
        apply_drawn_table_style(projected, entry, ElementTree.fromstring(payload['svg_content']))
        assert payload == frozen
        import pytest
        from services.generation_core.drivers.svg.native_table import TABLE_ELEMENTS, table_path_segments
        from services.generation_core.drivers.svg.native_geometry import NativeRejection, collect_primitives, find_group
        for invalid_d, invalid_fill in (('M 0 0 L 10 10', 'none'),
                ('M 0 0 C 0 1 1 1 1 0', 'none'), ('M 0 0 H 200 Z', 'none'),
                ('M 0 0 H 200 garbage', 'none'), ('M 0 0 H', 'none'),
                ('M 0 0 H 200', '#FFFFFF')):
            invalid = ElementTree.fromstring(compound)
            path = next(node for node in invalid.iter() if node.tag.rsplit('}', 1)[-1] == 'path')
            path.set('d', invalid_d)
            path.set('fill', invalid_fill)
            with pytest.raises(NativeRejection, match='native_table_path_unsupported'):
                table_path_segments(path, dict(path.attrib))
        for key, value in (('stroke-dasharray', '2 3'), ('stroke-linecap', 'round'),
                ('marker-start', 'url(#arrow)'), ('style', 'stroke-dasharray:2 3'),
                ('filter', 'url(#shadow)')):
            invalid = ElementTree.fromstring(compound)
            invalid.set(key, value)
            with pytest.raises(NativeRejection, match='native_table_path_unsupported'):
                collect_primitives(*find_group(invalid, 'native-table-sales'), allowed=TABLE_ELEMENTS)
    # Mixed tspan styles retain the drawn text base style after exact cell matching.
    multiline_rows = [list(row) for row in ROWS]
    multiline_group = ElementTree.fromstring(_table_group(before=""))
    text_nodes = [node for node in multiline_group.iter() if node.tag.endswith("text")]
    cell_text = text_nodes[-1]
    original_cell_text = "".join(cell_text.itertext())
    target_cells = [(r, c) for r, row in enumerate(multiline_rows) for c, value in enumerate(row) if value == original_cell_text]
    assert len(target_cells) == 1
    target_row, target_column = target_cells[0]
    multiline_rows[target_row][target_column] = "본문\n보조"
    cell_text.text = None
    cell_text.set("font-size", "18")
    cell_text.set("font-family", "Malgun Gothic")
    main_span = ElementTree.SubElement(cell_text, "tspan", {"x": cell_text.get("x"), "y": cell_text.get("y")})
    main_span.text = "본문"
    secondary_span = ElementTree.SubElement(cell_text, "tspan", {"x": cell_text.get("x"), "y": str(float(cell_text.get("y")) + 10), "font-size": "16"})
    secondary_span.text = "보조"
    multiline_cells = "".join(ElementTree.tostring(node, encoding="unicode") for node in text_nodes)
    multiline_table = _accepted_table(_table_group(rows=multiline_rows, cells=multiline_cells, before=""))
    assert [[cell["text"] for cell in row] for row in multiline_table["rows"]] == multiline_rows
    assert multiline_table["rows"][target_row][target_column]["fontSizePt"] == 13.5
    assert multiline_table["rows"][target_row][target_column]["fontFamily"] == "Malgun Gothic"

    from services.generation_core.drivers.svg.native_geometry import NativeRejection, collect_primitives, find_group
    import pytest
    body = _table_group(before="")
    original_group = ET.fromstring(body)
    identifier = next(node.get("id") for node in original_group.iter() if node.get("id"))
    allowed = {"g", "rect", "line", "text", "path", "polyline"}
    original_geometry = collect_primitives(*find_group(original_group, identifier), allowed)
    fitted_root = ET.fromstring('<svg><g transform="translate(40 30) scale(0.5)">' + body + '</g></svg>')
    fitted_geometry = collect_primitives(*find_group(fitted_root, identifier), allowed)
    from dataclasses import replace
    from services.generation_core.drivers.svg.native_table import _text_style
    large_text = replace(original_geometry.texts[0], size=144.0)
    assert _text_style(large_text, 0.75)["fontSizePt"] == 108.0
    from copy import deepcopy
    from services.generation_core.native_objects.payload import NativePayloadError, parse_table_payload
    for font_size in (1.0, 3.375, 108.0, 4000.0):
        scaled_payload = deepcopy(table)
        scaled_payload["rows"][0][0]["fontSizePt"] = font_size
        assert parse_table_payload(scaled_payload)["rows"][0][0]["fontSizePt"] == font_size
    for invalid_font_size in (0.0, -1.0, 0.999, 4000.001, float("nan"), float("inf"), True):
        invalid_payload = deepcopy(table)
        invalid_payload["rows"][0][0]["fontSizePt"] = invalid_font_size
        with pytest.raises(NativePayloadError, match="table_cell_font_size_invalid"):
            parse_table_payload(invalid_payload)
    assert "fontSizePt" not in _text_style(replace(large_text, size=float("nan")), 0.75)
    for before_shape, after_shape in zip(original_geometry.shapes, fitted_geometry.shapes, strict=False):
        assert after_shape.box.x0 == pytest.approx(40 + before_shape.box.x0 * 0.5)
        assert after_shape.box.y0 == pytest.approx(30 + before_shape.box.y0 * 0.5)
        assert after_shape.stroke_width == pytest.approx(before_shape.stroke_width * 0.5)
    for before_text, after_text in zip(original_geometry.texts, fitted_geometry.texts, strict=False):
        assert after_text.size == pytest.approx(before_text.size * 0.5)
        assert after_text.points == tuple((40 + x * 0.5, 30 + y * 0.5) for x, y in before_text.points)
    from services.generation_core.drivers.svg.native_paint import background_layers, effective_fill
    background_body = '<rect x="10" y="20" width="200" height="100" fill="#00ffaa"/><g id="paint-target"/>'
    plain_background = ET.fromstring('<svg>' + background_body + '</svg>')
    fitted_background = ET.fromstring('<svg><g transform="matrix(0.5 0 0 0.5 40 30)">' + background_body + '</g></svg>')
    plain_layers = background_layers(plain_background, next(node for node in plain_background.iter() if node.get("id") == "paint-target"))
    fitted_layers = background_layers(fitted_background, next(node for node in fitted_background.iter() if node.get("id") == "paint-target"))
    assert effective_fill(plain_layers, 100, 60) == effective_fill(fitted_layers, 90, 60)
    assert effective_fill(plain_layers, 500, 500) == effective_fill(fitted_layers, 290, 280)
    for transform in ["rotate(5)", "skewX(5)", "scale(1 2)", "scale(-1)", "matrix(1 0.2 0 1 0 0)", "matrix(nan 0 0 nan 0 0)", "translate(4 garbage)", "translate(1,,2)", "scale(1e309)"]:
        rejected_root = ET.fromstring('<svg><g transform="' + transform + '">' + body + '</g></svg>')
        with pytest.raises(NativeRejection):
            find_group(rejected_root, identifier)


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


def _requested_quality(monkeypatch, source, entries):
    from services.generation_core.drivers.svg import native_requirements

    monkeypatch.setattr(native_requirements, "page_native_objects",
                        lambda *_args, **_kwargs: ({"objects": entries}, {"accepted": len(entries)}))
    quality = {"passed": True}
    native_requirements.apply_requested_native_quality(quality, {}, "unused", source, pt_per_px=.75)
    return quality


def _requested_table_entry(rows, identifier):
    return {"id": identifier, "kind": "table",
            "payload": {"rows": [[{"text": cell} for cell in row] for row in rows]}}


@pytest.mark.parametrize("second", [
    [["분기", "금액"], ["2분기", "250"]],
    [["분기", "금액", "비고"], ["2분기", "250", "확정"]],
])
def test_independent_tables_require_separate_objects_without_merging(monkeypatch, second):
    from services.generation_core.drivers.svg.native_requirements import requested_native_data

    first = [["지역", "매출"], ["서울", "1,200"]]
    source = "\n\n".join("\n".join(" | ".join(row) for row in rows) for rows in [first, second])
    requirements = requested_native_data(source)
    assert [table["rows"] for table in requirements["tables"]] == [first, second]
    entries = [_requested_table_entry(first, "one"), _requested_table_entry(second, "two")]
    quality = _requested_quality(monkeypatch, source, entries)
    assert quality["passed"]
    assert quality["requestedNativeObjects"]["requiredTables"] == 2
    merged = _requested_quality(monkeypatch, source, [_requested_table_entry(first + second, "merged")])
    assert not merged["passed"]
    assert len(merged["failures"]) == 2


@pytest.mark.parametrize("single_column", [False, True])
@pytest.mark.parametrize("source_cell, expected_cell, drawn_cell, passed", [
    ("A \\| B", "A | B", "A | B", True),
    ("요청<br>조합 가능", "요청\n조합 가능", "요청\n조합 가능", True),
    ("요청<br/>조합 가능", "요청\n조합 가능", "요청 조합 가능", False),
    ("요청<br />조합 가능", "요청\n조합 가능", "요청\n조합\n가능", True),
    ("요청<br>조합 가능", "요청\n조합 가능", "요청 조합\n가능", False),
    ("요청<br><br>조합", "요청\n\n조합", "요청\n조합", False),
    ("요청<br><br>조합", "요청\n\n조합", "요청\n\n조합", True),
    ("요청 조합 가능", "요청 조합 가능", "요청\n조합\n가능", True),
    ("&lt;br&gt;", "&lt;br&gt;", "&lt;br&gt;", True),
    ("<br class='x'>", "<br class='x'>", "<br class='x'>", True),
    ("<br onload='x'>", "<br onload='x'>", "\n", False),
])
def test_unlabelled_markdown_preserves_empty_cells_colons_and_escaped_pipes(
        monkeypatch, source_cell, expected_cell, drawn_cell, passed, single_column):
    from services.generation_core.drivers.svg.native_requirements import (
        format_requested_native_data, requested_native_data,
    )

    source = f"| 항목 | 시간 | 비고 |\n| --- | :---: | ---: |\n| {source_cell} | 12:30 | |"
    rows = [["항목", "시간", "비고"], [expected_cell, "12:30", ""]]
    if single_column:
        source = f"| 항목 |\n| --- |\n| {source_cell} |"
        rows = [["항목"], [expected_cell]]
        assert not requested_native_data("| 단독 |\n| 문장 |")
        assert not requested_native_data("| 항목 |\n| - |\n| 문장 |")
        assert not requested_native_data("| 항목 |\n| --- | --- |\n| 문장 |")
        assert not requested_native_data("|---|\n| 단독 |\n| 문장 |")
    assert requested_native_data(source)["tables"][0]["rows"] == rows
    grounding = format_requested_native_data(source)
    assert json.loads(next(line for line in grounding.splitlines() if line.startswith("{")))["tables"][0]["rows"] == rows
    drawn_rows = [rows[0], [drawn_cell] if single_column else [drawn_cell, "12:30", ""]]
    assert _requested_quality(monkeypatch, source, [_requested_table_entry(drawn_rows, "unlabelled")])["passed"] is passed


@pytest.mark.parametrize("kind", ["table", "chart"])
def test_repeated_literal_requests_require_distinct_native_objects(monkeypatch, kind):
    if kind == "table":
        source = "항목 | 값\nA | 10"
        entry = _requested_table_entry([["항목", "값"], ["A", "10"]], "one")
    else:
        source = "차트: A 10, B 20"
        entry = {"id": "one", "kind": "chart", "payload": {
            "categories": ["A", "B"], "series": [{"name": "S", "values": [10, 20]}]}}
    source = source + "\n\n" + source
    missing = _requested_quality(monkeypatch, source, [entry])
    assert not missing["passed"]
    assert len(missing["failures"]) == 1
    complete = _requested_quality(monkeypatch, source, [entry, {**entry, "id": "two"}])
    assert complete["passed"]


@pytest.mark.parametrize("mutation", [None, "scaled_percent", "missing_series", "wrong_name",
                                       "reversed_series", "reversed_categories", "missing_unit",
                                       "swapped_units", "unpaired_units"])
def test_explicit_named_series_preserve_amounts_percent_scale_and_currency(monkeypatch, mutation):
    from services.generation_core.drivers.svg.native_requirements import requested_native_data

    source = "차트: 전환율: A 12.5%, B 30%; 매출: A $1,200, B $1,800"
    expected = requested_native_data(source)["charts"][0]
    assert expected["categories"] == ["A", "B"]
    assert [(series["name"], series["values"], series["units"]) for series in expected["series"]] == [
        ("전환율", ["12.5", "30"], ["%", "%"]), ("매출", ["1200", "1800"], ["$", "$"])]
    chart = {"categories": ["A", "B"], "title": "전환율 (%), 매출 ($)", "series": [
        {"name": "전환율", "values": [12.5, 30.0]}, {"name": "매출", "values": [1200, 1800]}]}
    if mutation == "scaled_percent":
        chart["series"][0]["values"] = [.125, .3]
    elif mutation == "missing_series":
        chart["series"].pop()
    elif mutation == "wrong_name":
        chart["series"][0]["name"] = "유입률"
    elif mutation == "reversed_series":
        chart["series"].reverse()
    elif mutation == "reversed_categories":
        chart["categories"].reverse()
    elif mutation == "missing_unit":
        chart["title"] = "전환율 (%), 매출"
    elif mutation == "swapped_units":
        chart["title"] = "전환율 ($), 매출 (%)"
    elif mutation == "unpaired_units":
        chart["title"] = "전환율과 매출 비교 (%, $)"
    quality = _requested_quality(monkeypatch, source, [{"id": "chart", "kind": "chart", "payload": chart}])
    assert quality["passed"] is (mutation is None)


@pytest.mark.parametrize("source,title,passed", [
    ("차트: A 30, B 60. 단위 억원.", "매출 (억원)", True),
    ("차트: A 30, B 60. 단위 억원.", "매출 (억 원)", True),
    ("차트: A 30, B 60. 단위 억원.", "매출 (백억원)", False),
    ("차트: A 30, B 60. 단위 억원.", "매출", False),
    ("차트: A 30％, B 60％", "비율 (%)", True),
    ("차트: A 30%, B 60%", "비율 (‰)", False),
])
def test_chart_units_survive_native_payload_with_formatting_tolerance(monkeypatch, source, title, passed):
    chart = {"categories": ["A", "B"], "title": title, "series": [{"name": "S", "values": [30, 60]}]}
    quality = _requested_quality(monkeypatch, source, [{"id": "chart", "kind": "chart", "payload": chart}])
    assert quality["passed"] is passed


def test_first_attempt_grounding_contains_independent_exact_data_without_prose_inference():
    from services.generation_core.drivers.svg.native_requirements import format_requested_native_data

    source = "| 지역 | 매출 |\n| --- | --- |\n| 서울 | 1,200 |\n\n차트: A 30%, B 60%"
    grounding = format_requested_native_data(source)
    data = json.loads(next(line for line in grounding.splitlines() if line.startswith("{")))
    assert data["tables"][0]["rows"] == [["지역", "매출"], ["서울", "1,200"]]
    assert data["charts"][0]["categories"] == ["A", "B"]
    assert data["charts"][0]["values"] == ["30", "60"]
    assert data["charts"][0]["units"] == ["%", "%"]
    assert data["charts"][0]["requiredUnitLabels"] == [{"text": "%", "unit": "%"}]
    assert format_requested_native_data("서울 매출은 1,200이며 전환율은 30%에서 60%로 개선됐다.") == ""


def test_unit_bearing_source_series_names_keep_their_exact_attribution(monkeypatch):
    from services.generation_core.drivers.svg.native_requirements import format_requested_native_data

    source = "차트: 전환율(%): A 10, B 20; 매출(억원): A 30, B 40"
    grounding = format_requested_native_data(source)
    data = json.loads(next(line for line in grounding.splitlines() if line.startswith("{")))
    assert data["charts"][0]["requiredUnitLabels"] == [
        {"seriesName": "전환율(%)", "unit": "%", "text": "전환율(%)"},
        {"seriesName": "매출(억원)", "unit": "억원", "text": "매출(억원)"}]
    chart = {"categories": ["A", "B"], "title": "전환율(%) 및 매출(억 원)", "series": [
        {"name": "전환율(%)", "values": [10, 20]}, {"name": "매출(억원)", "values": [30, 40]}]}
    entry = {"id": "chart", "kind": "chart", "payload": chart}
    assert _requested_quality(monkeypatch, source, [entry])["passed"]
    chart["title"] = "전환율(억원) 및 매출(%)"
    assert not _requested_quality(monkeypatch, source, [entry])["passed"]


@pytest.mark.parametrize("source,reason", [
    ("차트: A 30%, B 60억원", "mixed_point_units"),
    ("차트: 실적: A 30%, B 60억원; 예산: A 40, B 80", "mixed_point_units"),
    ("차트: 매출(억원): A $30, B $60", "conflicting_series_units"),
])
def test_unrepresentable_units_are_reported_as_unsupported_instead_of_attested(monkeypatch, source, reason):
    from services.generation_core.drivers.svg.native_requirements import format_requested_native_data

    grounding = format_requested_native_data(source)
    data = json.loads(next(line for line in grounding.splitlines() if line.startswith("{")))
    assert data["charts"][0]["unsupportedReason"] == reason
    chart = {"categories": ["A", "B"], "title": "실적 (%, 억원, $)", "series": [{"name": "실적", "values": [30, 60]}]}
    quality = _requested_quality(monkeypatch, source, [{"id": "chart", "kind": "chart", "payload": chart}])
    assert not quality["passed"]
    assert quality["failures"][0]["kind"] == "native_requested_chart_unsupported"
    assert quality["failures"][0]["unsupportedReason"] == reason


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
    # An axis title is a supported declaration, so an undrawn one is a missing label.
    ({**_BAR, "valueAxis": {"title": "억원"}}, "", "native_chart_label_not_drawn"),
    ({**_BAR, "valueAxis": {"title": 5}}, "", "native_chart_json_invalid"),
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
    from copy import deepcopy
    optional_payload = deepcopy(payload)
    optional_payload["source_preservation"] = None
    optional_before = deepcopy(optional_payload)
    optional_content = svg_slide_content(optional_payload, role="content", source="# 표", pt_per_px=0.75)
    assert optional_content["nativeObjects"] == content["nativeObjects"]
    assert optional_content["svgReceipt"]["nativeObjects"] == content["svgReceipt"]["nativeObjects"]
    assert optional_content["__svg__"] == content["__svg__"]
    assert optional_payload == optional_before
    source_required_payload = deepcopy(payload)
    source_required_payload["source_preservation"] = {"source_native_charts": [{"element_id": "missing-source-chart"}]}
    with pytest.raises(ValueError, match="svg_source_native_chart_unprojectable"):
        svg_slide_content(source_required_payload, role="content", source="# 표", pt_per_px=0.75)


def test_wrapped_multi_paragraph_cell_matches_only_on_drawn_line_breaks():
    """A multi-paragraph cell may wrap a paragraph; each paragraph break must be a line break."""
    from services.generation_core.drivers.svg.native_table import _wrapped_cell_matches

    declared = "사용자 심화\n(AI · 바이브 코딩)"
    assert _wrapped_cell_matches(declared, ["사용자 심화", "(AI · 바이브", "코딩)"])
    assert _wrapped_cell_matches(declared, ["사용자 심화", "(AI · 바이브 코딩)"])
    assert not _wrapped_cell_matches(declared, ["사용자 심화 (AI · 바이브", "코딩)"])
    assert not _wrapped_cell_matches(declared, ["사용자 심화", "(AI · 바이", "코딩)"])
    assert not _wrapped_cell_matches(declared, ["사용자", "심화", "(AI · 바이브 코딩)", "추가"])
    assert not _wrapped_cell_matches(declared, ["사용자 심화\n(AI · 바이브 코딩)"])
    assert _wrapped_cell_matches("실습 완료 여부", ["실습 완료", "여부"])
    assert not _wrapped_cell_matches("실습 완료 여부", ["실습 완료", "여부", "추가"])

def test_source_text_lines_join_an_unpositioned_inline_run_to_its_line():
    """A positioned line followed by an unpositioned tspan run is one drawn line (readback)."""
    import xml.etree.ElementTree as ET

    from services.svg_export_whole_page_adapter import fragment_lines_match, source_text_lines

    ns = "http://www.w3.org/2000/svg"
    node = ET.fromstring(
        f'<text xmlns="{ns}" x="70" y="166"><tspan x="70" y="166">업력 20년의 기업으로 </tspan>'
        '<tspan x="70" y="205" fill="#2563EB">플랫폼 자체 개발</tspan><tspan>과 SI 구축 수행</tspan></text>')
    lines = source_text_lines(node)
    assert lines == ["업력 20년의 기업으로", "플랫폼 자체 개발과 SI 구축 수행"]
    assert fragment_lines_match(lines, "업력 20년의 기업으로 플랫폼 자체 개발과 SI 구축 수행")
    spaced = ET.fromstring(
        f'<text xmlns="{ns}"><tspan x="1" y="2">첫 줄</tspan><tspan x="1" y="30">둘째</tspan> '
        '<tspan fill="#000">강조</tspan></text>')
    assert source_text_lines(spaced) == ["첫 줄", "둘째 강조"]  # the whitespace tail separates the runs
    single = ET.fromstring(f'<text xmlns="{ns}" x="1" y="2">한 줄 <tspan fill="#000">강조</tspan></text>')
    assert source_text_lines(single) is None
    relative = ET.fromstring(f'<text xmlns="{ns}"><tspan x="1" y="2">가</tspan><tspan dy="20">나</tspan></text>')
    assert source_text_lines(relative) is None


def test_new_svg_publication_shell_has_valid_profile_before_first_flush(monkeypatch):
    """Exercise actual new-row publication through the real profile flush gate."""
    import asyncio
    from types import SimpleNamespace
    from uuid import uuid4

    from models.sql.presentation import PresentationModel
    from services import account_deletion_service
    from services.generation_core.drivers.svg.publish import _publish
    from services.presentation_profile_persistence import stamp_presentation_profile
    from utils.owner_context import reset_current_owner_id, set_current_owner_id

    class FirstFlushReached(Exception):
        pass

    owner, presentation_id = uuid4(), uuid4()
    calls = []

    class NewRowSession:
        def __init__(self):
            self.rows = []

        async def rollback(self):
            calls.append("rollback")

        async def scalar(self, statement):
            calls.append("select-new-row")
            return None

        def add(self, row):
            assert isinstance(row, PresentationModel)
            self.rows.append(row)

        async def flush(self):
            calls.append("first-flush")
            assert len(self.rows) == 1
            row = self.rows[0]
            # This is the same real stamper used by the before_flush listener.
            # An unstamped default shell raises PresentationProfileError here.
            profile = stamp_presentation_profile(row)
            assert profile.driver_id == "svg-v1"
            assert row.profile_driver_id == "svg-v1" and row.mode == "svg"
            assert row.id == presentation_id and row.owner_id == owner
            assert row.n_slides == 1
            raise FirstFlushReached

    session = NewRowSession()

    async def active_account(db_session, *, user_id, expected_generation=None):
        assert db_session is session and user_id == owner
        assert calls == ["rollback"]
        calls.append("active-account")

    # Only the account DB boundary is replaced; actual profile derivation and
    # the publication constructor/stamp/add/flush order remain exercised.
    monkeypatch.setattr(account_deletion_service, "require_active_account_generation", active_account)
    token = set_current_owner_id(owner)
    try:
        with pytest.raises(FirstFlushReached):
            asyncio.run(_publish(
                SimpleNamespace(content="New SVG publication"), presentation_id,
                SimpleNamespace(), "ko", session, None, {}, [],
                [({}, b"not-read-before-first-flush")],
            ))
    finally:
        reset_current_owner_id(token)
    assert calls == ["rollback", "active-account", "select-new-row", "first-flush"]
