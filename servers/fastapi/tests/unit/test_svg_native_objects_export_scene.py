"""NativeObjects sidecars reach PPTX as native tables/charts through the svg export scene."""

from __future__ import annotations

from html import escape
from types import SimpleNamespace

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from services.document_canvas import document_canvas_for_orientation
from services.generation_core.drivers.svg.export_native_wire import (
    PageNativeObjects,
    bundle_entry,
    parse_bundle_native_objects,
)
from services.generation_core.export.svg_evidence import build_svg_export_evidence_digests
from services.generation_core.native_objects import NativeObjects
from services.svg_contract import validate_svg
from services.svg_export_bundle import build_svg_export_bundle
from workers.public_svg_export_worker import convert_pptx

CANVAS = document_canvas_for_orientation("landscape")
EMU_PER_PX = 9000
FONT = 'font-family="Noto Sans KR" font-size="16"'
ROWS = [["지역", "매출(억원)", "성장률"], ["서울", "1,234", "12.5%"], ["부산", "567", "-3.2%"], ["대구", "89", "0.0%"]]
TABLE_BOX = (100.0, 150.0, 600.0, 200.0)
CHART_BOX = (120.0, 120.0, 640.0, 400.0)


def test_native_capture_alias_requires_one_actual_child_and_identical_return(tmp_path):
    import json
    from xml.etree import ElementTree as ET

    from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
    from services.svg_contract import source_digest
    from workers.public_svg_whole_page_convert import NativeOoxmlCapture

    for mode in ("one", "two", "tamper"):
        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        native_shapes = [slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, 100, 100, 200, 100)
                         for _ in range(2 if mode == "two" else 1)]
        target = tmp_path / f"{mode}.pptx"
        deck.save(target)
        source = ('<svg xmlns="http://www.w3.org/2000/svg"><g id="parent"><g id="empty"/>'
                  '<metadata/><rect id="first"/>'
                  + ('<rect id="second"/>' if mode == "two" else '') + '</g></svg>')
        parent = ET.fromstring(source)[0]
        events = []
        converter = SimpleNamespace(_extract_background_candidate=lambda root, context: (None, None))

        def convert(element, context, converter=converter, mode=mode, native_shapes=native_shapes, events=events):
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "g":
                children = [converter.convert_element(child, context) for child in element]
                returned = [child for child in children if child is not None]
                # The two-child case intentionally drops output: capture must
                # never grant this the legitimate single-child alias proof.
                result = returned[0] if returned else None
                if result is not None and mode == "tamper":
                    changed = ET.fromstring(result.xml)
                    identity = changed.find("{http://schemas.openxmlformats.org/presentationml/2006/main}nvSpPr/"
                                            "{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr")
                    identity.set("name", "tampered")
                    result = SimpleNamespace(xml=ET.tostring(changed, encoding="unicode"), bounds_emu=result.bounds_emu)
            elif tag == "rect":
                shape = native_shapes[1 if element.get("id") == "second" else 0]
                result = SimpleNamespace(xml=shape._element.xml, bounds_emu=(100, 100, 300, 200))
            else:
                result = None
            if result is None:
                events.append({"tag": tag, "decision": "skip", "reason": "empty-or-non-rendering"})
            else:
                output = ET.fromstring(result.xml)
                identity = output.find("{http://schemas.openxmlformats.org/presentationml/2006/main}nvSpPr/"
                                       "{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr")
                events.append({"tag": tag, "decision": "native", "shape_id": int(identity.get("id")),
                               "bounds_emu": list(result.bounds_emu)})
            return result

        converter.convert_element = convert
        capture = NativeOoxmlCapture(converter)
        capture.install()
        try:
            if mode == "tamper":
                with pytest.raises(ValueError, match="svg_native_ooxml_expected_result_conflict"):
                    converter.convert_element(parent, SimpleNamespace(slide_num=1))
                assert not capture.group_aliases
                continue
            converter.convert_element(parent, SimpleNamespace(slide_num=1))
            trace = tmp_path / f"{mode}.json"
            trace.write_text(json.dumps({"slide_count": 1, "slides": [{"slide_num": 1, "events": events}]}), encoding="utf-8")
            receipts = [{"svg_digest": source_digest(source), "source_page_id": "page-1"}]
            if mode == "two":
                assert not capture.group_aliases
                with pytest.raises(ValueError, match="svg_native_ooxml_primitive_owner_duplicate"):
                    capture.verify(target, trace, [source], receipts)
            else:
                assert capture.group_aliases == {1: {native_shapes[0].shape_id: 1}}
                evidence = capture.verify(target, trace, [source], receipts)
                assert evidence["passed"] is True
                assert evidence["measurements"]["verifiedGroupAliases"] == 1
        finally:
            capture.restore()


def _svg(body: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1188" height="840" viewBox="0 0 1188 840">'
            f"{body}</svg>")


def _table_group(group_id: str, *, wrapper: str = "") -> str:
    x, y, width, height = TABLE_BOX
    parts = [f'<g id="{group_id}"><rect x="{x}" y="{y}" width="{width}" height="{height}" fill="#FFFFFF" '
             'stroke="#CCCCCC" stroke-width="1"/>']
    for r, row in enumerate(ROWS):
        for c, text in enumerate(row):
            parts.append(f'<text x="{x + c * 200 + 10}" y="{y + r * 50 + 32}" {FONT} fill="#111111">'
                         f"{escape(text)}</text>")
    parts.append("</g>")
    body = "".join(parts)
    return f"<g {wrapper}>{body}</g>" if wrapper else body


def _chart_group(group_id: str, title: str | None) -> str:
    x, y, _, _ = CHART_BOX
    bars = "".join(f'<rect x="{x + 40 + i * 140}" y="{y + 340 - v * 10}" width="80" height="{v * 10}" '
                   'fill="#2457D6"/>' for i, v in enumerate((10, 20, 15, 30)))
    labels = "".join(f'<text x="{x + 60 + i * 140}" y="{y + 380}" {FONT} fill="#333333">Q{i + 1}</text>'
                     for i in range(4))
    heading = f'<text x="{x + 20}" y="{y + 30}" {FONT} fill="#111111">{escape(title)}</text>' if title else ""
    return f'<g id="{group_id}">{heading}{bars}{labels}</g>'


def _table_object(object_id: str, box=TABLE_BOX) -> dict:
    x, y, width, height = box
    return {"id": object_id, "kind": "table", "anchor": {"x": x, "y": y, "width": width, "height": height},
            "payload": {"rows": [[{"text": text, "fill": "#1F3A5F" if r == 0 else "#FFFFFF",
                                   "textColor": "#FFFFFF" if r == 0 else "#111111"} for text in row]
                                 for r, row in enumerate(ROWS)],
                        "headerRows": 1, "fontFamily": "Noto Sans KR", "fontSizePt": 12}}


def _chart_object(object_id: str, title: str) -> dict:
    x, y, width, height = CHART_BOX
    return {"id": object_id, "kind": "chart", "anchor": {"x": x, "y": y, "width": width, "height": height},
            "payload": {"chartType": "column", "categories": ["Q1", "Q2", "Q3", "Q4"], "title": title,
                        "series": [{"name": "매출", "values": [10, 20, 15, 30], "color": "#2457D6"}]}}


def _sidecar(*objects: dict) -> PageNativeObjects:
    return PageNativeObjects(sidecar=NativeObjects.from_wire({"schemaVersion": "native-objects/v1",
                                                              "objects": list(objects)}))


def _convert(tmp_path, svgs, pages):
    paths = []
    for index, svg in enumerate(svgs, 1):
        validate_svg(svg, width=1188, height=840)
        path = tmp_path / f"slide-{index:03d}.svg"
        path.write_text(svg, encoding="utf-8")
        paths.append(path)
    target = tmp_path / "deck.pptx"
    readback = convert_pptx(paths, target, [""] * len(paths), CANVAS, native_objects=pages)
    return readback, Presentation(str(target))


def _shapes(shapes):
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _shapes(shape.shapes)


def _texts(slide) -> list[str]:
    return [shape.text_frame.text for shape in _shapes(slide.shapes) if shape.has_text_frame and shape.text_frame.text]


def test_sidecar_tables_and_charts_become_native_with_per_object_fallback(tmp_path):
    svgs = [
        _svg(_table_group("native-table-sales")
             + '<g id="native-table-wide"><rect x="900" y="150" width="200" height="200" fill="#EEEEEE"/></g>'),
        _svg(_chart_group("native-chart-revenue", "분기별 매출") + _chart_group("native-chart-hidden", None)),
    ]
    pages = [
        _sidecar(_table_object("native-table-sales"), _table_object("native-table-gone"),
                 _table_object("native-table-wide", box=(900.0, 150.0, 600.0, 200.0))),
        _sidecar(_chart_object("native-chart-revenue", "분기별 매출"),
                 _chart_object("native-chart-hidden", "그려지지 않은 제목")),
    ]
    readback, deck = _convert(tmp_path, svgs, pages)

    receipt = readback["nativeObjects"]
    assert (receipt["offered"], receipt["native"], receipt["fellBack"]) == (5, 2, 3)
    reasons = {entry["id"]: (entry["page"], entry["kind"], entry["reason"]) for entry in receipt["reasons"]}
    assert reasons["native-table-gone"] == (0, "table", "group_missing")
    assert reasons["native-table-wide"] == (0, "table", "anchor_outside_canvas")
    assert reasons["native-chart-hidden"][:2] == (1, "chart")
    assert reasons["native-chart-hidden"][2].startswith("converter_rejected:")
    assert "rasterRedesign" not in readback

    tables = [shape for shape in _shapes(deck.slides[0].shapes) if shape.has_table]
    assert len(tables) == 1
    table = tables[0]
    assert [[cell.text for cell in row.cells] for row in table.table.rows] == ROWS
    assert (table.left, table.top, table.width, table.height) == tuple(int(v * EMU_PER_PX) for v in TABLE_BOX)
    assert not any(text in ROWS[1] for text in _texts(deck.slides[0]))

    charts = [shape for shape in _shapes(deck.slides[1].shapes) if shape.has_chart]
    assert len(charts) == 1
    chart = charts[0]
    assert (chart.left, chart.top, chart.width, chart.height) == tuple(int(v * EMU_PER_PX) for v in CHART_BOX)
    plot = chart.chart.plots[0]
    assert list(plot.categories) == ["Q1", "Q2", "Q3", "Q4"]
    assert list(plot.series[0].values) == [10, 20, 15, 30]
    # The rejected chart keeps its drawn fallback: its category labels stay text.
    assert _texts(deck.slides[1]).count("Q1") == 1


def test_invalid_sidecar_falls_back_every_named_object_on_record(tmp_path):
    page = PageNativeObjects(invalid_reason="native_object_anchor_invalid",
                             invalid_objects=(("native-table-sales", "table"),))
    readback, deck = _convert(tmp_path, [_svg(_table_group("native-table-sales"))], [page])
    assert readback["nativeObjects"] == {"offered": 1, "native": 0, "fellBack": 1, "reasons": [{
        "page": 0, "id": "native-table-sales", "kind": "table",
        "reason": "sidecar_invalid:native_object_anchor_invalid"}]}
    assert not any(shape.has_table for shape in _shapes(deck.slides[0].shapes))
    assert "서울" in _texts(deck.slides[0])


def test_converter_failure_restores_every_group_and_records_the_reason(tmp_path):
    # Group opacity passes marker pre-validation but fails the native converter run.
    svg = _svg(_table_group("native-table-sales", wrapper='opacity="0.5"'))
    readback, deck = _convert(tmp_path, [svg], [_sidecar(_table_object("native-table-sales"))])
    receipt = readback["nativeObjects"]
    assert (receipt["native"], receipt["fellBack"]) == (0, 1)
    assert "opacity" in receipt["reasons"][0]["reason"].lower()
    assert not any(shape.has_table for shape in _shapes(deck.slides[0].shapes))


def test_decks_without_native_objects_record_an_empty_receipt(tmp_path):
    readback, deck = _convert(tmp_path, [_svg(_table_group("plain"))], None)
    assert readback["nativeObjects"] == {"offered": 0, "native": 0, "fellBack": 0, "reasons": []}
    assert not any(shape.has_table for shape in _shapes(deck.slides[0].shapes))


def test_bundle_entries_round_trip_and_reject_malformed_wire():
    valid = {"nativeObjects": {"schemaVersion": "native-objects/v1", "objects": [_table_object("t1")]}}
    invalid = {"nativeObjects": {"schemaVersion": "native-objects/v1", "objects": [
        {"id": "t1", "kind": "table", "anchor": {"x": 0, "y": 0, "width": 0, "height": 1}, "payload": {}}]}}
    entries = [bundle_entry(valid), bundle_entry(invalid), bundle_entry({"other": 1})]
    assert entries[0]["status"] == "valid" and entries[2] is None
    assert entries[1] == {"status": "invalid", "reason": "native_object_anchor_invalid",
                          "objects": [{"id": "t1", "kind": "table"}]}
    pages = parse_bundle_native_objects(entries, 3)
    assert pages[0].sidecar.by_id()["t1"].kind == "table"
    assert pages[1].invalid_objects == (("t1", "table"),) and pages[2] == PageNativeObjects()
    for bad in ([entries[0]], [{"status": "valid"}, None, None], [{**entries[1], "extra": 1}, None, None]):
        with pytest.raises(ValueError, match="svg_export_native_objects_invalid"):
            parse_bundle_native_objects(bad, 3)


def test_native_objects_bind_pptx_bundle_identity_and_semantic_digest_only():
    claim = SimpleNamespace(expected_revision=3, presentation_id="p")
    entries = [bundle_entry({"nativeObjects": {"schemaVersion": "native-objects/v1",
                                               "objects": [_table_object("t1")]}})]

    def bundle(fmt, native):
        return build_svg_export_bundle(claim=claim, slide_refs=[{"ref": 1}], source_digests=["a" * 64],
                                       notes=[""], canvas={"w": 1}, export_format=fmt, font_embedding=False,
                                       assets=[None], native_objects=native)

    plain, native = bundle("pptx", None), bundle("pptx", entries)
    assert "nativeObjects" not in plain and native["nativeObjects"] == entries
    assert native["canonicalExportIdentity"] != plain["canonicalExportIdentity"]
    assert bundle("pdf", entries) == bundle("pdf", None)  # vector PDF: no native objects

    def digests(native):
        return build_svg_export_evidence_digests(svgs=["<svg/>"], notes=[""], canvas={"w": 1}, native_objects=native,
                                                 quality_inputs={}, converter_version="v", converter_digest="d",
                                                 contract_version="c")

    without, with_native = digests(None), digests(entries)
    assert without.semantic_digest == without.scene_digest
    assert with_native.scene_digest == without.scene_digest
    assert with_native.semantic_digest != without.semantic_digest


def test_raster_grid_projects_to_the_converter_payload_the_grid_draws(monkeypatch, tmp_path):
    import xml.etree.ElementTree as ET

    from services.generation_core.drivers.svg.export_projection import converter_payload
    from services.source_content import raster_redesign_table as grid
    from services.source_content.raster_redesign_pptx import redesign_native_objects

    rows = [["구분", "2024", "비고"], ["국내", "1,234억", "신규 채널 확대 및 파트너십 강화"], ["해외", "845억", ""]]
    layout = grid.layout_table(rows, 700, 14.25)
    body = "".join(grid.render_table(layout, 60, 80, id_prefix="rd-0123456789", family="Noto Sans KR",
                                     escape=escape))
    root = ET.fromstring('<svg xmlns="http://www.w3.org/2000/svg" id="svg-raster-redesign" width="1188" '
                         f'height="840" viewBox="0 0 1188 840">{body}</svg>')
    (item,), skipped = redesign_native_objects(root)
    payload = converter_payload(item)
    assert skipped == 0 and payload["header_rows"] == 1 and payload["strict_grid"] is True
    assert payload["style"] == {"font_family": "Noto Sans KR", "font_size": 14.25, "header_font_size": 14.25}
    assert payload["column_widths"] == [round(width, 3) for width in payload["column_widths"]]
    assert sum(payload["row_heights"]) == pytest.approx(layout.height, abs=0.01)
    rule = {"style": "solid", "color": grid.RULE, "width": 1.0}
    for r, row in enumerate(payload["rows"]):
        for cell in row:
            assert cell["fill"] == (grid.HEADER_FILL if r == 0 else grid.BAND_FILL if r % 2 == 0 else "#FFFFFF")
            assert cell["color"] == (grid.HEADER_TEXT if r == 0 else grid.BODY_TEXT)
            assert cell["bold"] is (r == 0) and cell["valign"] == "middle"
            assert cell["borders"] == {edge: rule for edge in ("top", "right", "bottom", "left")}
    assert [cell["align"] for cell in payload["rows"][0]] == ["ctr", "ctr", "ctr"]
    assert [cell["align"] for cell in payload["rows"][1]] == ["l", "r", "l"]
    for node in root.iter("{http://www.w3.org/2000/svg}text"):
        r, c = (int(v) for v in node.get("id").rsplit("-r", 1)[1].split("c"))
        spans = [span.text for span in node]
        cell = payload["rows"][r][c]
        assert cell.get("paragraphs", [cell.get("text")]) == spans

    # Legacy projection is unchanged; explicit custom physical points invert exactly.
    assert converter_payload(item) == converter_payload(item, pt_per_px=0.75)
    from copy import deepcopy
    from services.generation_core.drivers.svg.export_scene import _source_table_vertical_padding
    from services.generation_core.drivers.svg.native_publish import _entry
    from services.generation_core.drivers.svg.native_declarations import native_binding_digest
    from workers.public_svg_pptx_convert import VENDOR_ROOT
    monkeypatch.syspath_prepend(str(VENDOR_ROOT))
    from svg_to_pptx.conversion_context import ConversionContext, scoped_conversion_context
    from svg_to_pptx.native_objects.table import _table_padding_attrs

    factor = 400 * 72 / 25.4 / 1280
    source_rows = [["월", "주문 수(건)", "정시 배송률(%)", "평균 배송 시간(분)"],
                   ["1월", "240", "88", "62"], ["2월", "360", "91", "56"],
                   ["3월", "520", "93", "50"], ["4월", "740", "95", "45"]]
    xs, ys = [32, 112, 272, 442, 632], [95, 127, 154, 181, 208, 235]
    body = ['<g id="table-font-custom"><rect x="32" y="95" width="600" height="140" fill="#FFFFFF"/>']
    body += [f'<line x1="{x}" y1="95" x2="{x}" y2="235" stroke="#DFE8EA"/>' for x in xs]
    body += [f'<line x1="32" y1="{y}" x2="632" y2="{y}" stroke="#DFE8EA"/>' for y in ys]
    for r, row in enumerate(source_rows):
        for c, text in enumerate(row):
            size, baseline = (16, 118) if r == 0 else (18, ys[r] + 19)
            body.append(f'<text x="{(xs[c] + xs[c + 1]) / 2}" y="{baseline}" '
                        f'font-family="Noto Sans KR" font-size="{size}" text-anchor="middle">{text}</text>')
    body.append('</g>')
    custom_root = ET.fromstring('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 320">' + ''.join(body) + '</svg>')
    custom_item = _entry(custom_root, {"id": "table-font-custom", "kind": "table",
                         "raw": {"rows": source_rows, "headerRows": 1}},
                         width=1280, height=320, pt_per_px=factor,
                         svg=ET.tostring(custom_root, encoding="unicode"), source_preservation=None,
                         native_group_ids=frozenset({"table-font-custom"}), native_group_ids_complete=True,
                         binding_digest=native_binding_digest(ET.tostring(custom_root, encoding="unicode")))
    frozen_payload = deepcopy(custom_item.payload)
    projected = converter_payload(custom_item, pt_per_px=factor)
    assert projected["rows"][0][0]["font_size"] == 16
    assert projected["rows"][1][0]["font_size"] == 18
    _source_table_vertical_padding(custom_root, custom_item, projected, pt_per_px=factor)
    padding = projected["rows"][1][0]["padding"]
    assert padding["top"] == 1
    assert padding["bottom"] == 3.5
    assert padding["left"] == pytest.approx(7.2 / factor)
    assert padding["right"] == pytest.approx(7.2 / factor)
    @scoped_conversion_context
    def padding_attrs(*, conversion_context, pptx_structure="flat"):
        return _table_padding_attrs(projected['rows'][1][0], {})

    padding_attrs(
        conversion_context=ConversionContext(1280, 320, 14400000, 3600000, legacy=False),
        pptx_structure="flat",
    )
    assert custom_item.payload == frozen_payload

    # Real source paragraph baselines survive the projection and serializer,
    # including mixed emphasis, without changing source data or row geometry.
    from services.generation_core.drivers.svg.native_table import apply_drawn_table_style
    from svg_to_pptx.native_objects.table import _table_cell_paragraphs
    spaced_rows = [["확인 사항"], ["첫 문단\n두 번째 문단\n마지막 문단"]]
    spaced_body = ('<g id="native-table-paragraphs">'
        '<rect x="90" y="60" width="610" height="50" fill="#F1F5F9"/>'
        '<rect x="90" y="110" width="610" height="320" fill="#FFFFFF"/>'
        '<text x="114" y="95" font-family="Noto Sans KR" font-size="22" fill="#111111">확인 사항</text>'
        '<text x="114" y="160" font-family="Noto Sans KR" font-size="24" fill="#111111">'
        '<tspan x="114" y="160">첫 문단</tspan><tspan x="114" y="252">두 번째 문단</tspan>'
        '<tspan x="114" y="354" fill="#2563EB" font-weight="700">마지막 문단</tspan></text></g>')
    spaced_svg = _svg(spaced_body)
    spaced_root = ET.fromstring(spaced_svg)
    spaced_item = _entry(spaced_root, {"id": "native-table-paragraphs", "kind": "table",
                         "raw": {"rows": spaced_rows, "headerRows": 1}},
                         width=1188, height=840, pt_per_px=EMU_PER_PX / 12700,
                         svg=spaced_svg, source_preservation=None,
                         native_group_ids=frozenset({"native-table-paragraphs"}), native_group_ids_complete=True,
                         binding_digest=native_binding_digest(spaced_svg))
    frozen_item = deepcopy(spaced_item.to_wire())
    spaced = converter_payload(spaced_item, pt_per_px=EMU_PER_PX / 12700)
    apply_drawn_table_style(spaced, spaced_item, spaced_root)
    cell = spaced["rows"][1][0]
    assert [p["line_spacing"] for p in cell["paragraphs"]] == [30, 30, 30]
    assert [p["space_before"] for p in cell["paragraphs"]] == [0, 62, 72]
    assert cell["padding"] == {"top": 26, "bottom": 70}
    assert cell["valign"] == "top"
    assert [p["runs"][0]["text"] for p in cell["paragraphs"]] == spaced_rows[1][0].split("\n")
    assert cell["paragraphs"][-1]["runs"][0]["color"] == "#2563EB"
    assert cell["paragraphs"][-1]["runs"][0]["bold"] is True
    assert spaced_item.to_wire() == frozen_item
    explicit_padding = converter_payload(spaced_item, pt_per_px=EMU_PER_PX / 12700)
    explicit_padding["rows"][1][0]["padding"] = {"top": 5, "bottom": 7, "left": 12}
    apply_drawn_table_style(explicit_padding, spaced_item, spaced_root)
    explicit_cell = explicit_padding["rows"][1][0]
    assert explicit_cell["padding"] == {"top": 5, "bottom": 7, "left": 12}
    assert explicit_cell["valign"] == "middle"
    assert all("line_spacing" not in paragraph and "space_before" not in paragraph
               for paragraph in explicit_cell["paragraphs"])
    assert [p["runs"] for p in explicit_cell["paragraphs"]] == [p["runs"] for p in cell["paragraphs"]]

    # A declared paragraph that wraps over two drawn lines stays ONE hard paragraph
    # whose drawn wrap is a soft line break; the cell keeps exactly its two paragraphs
    # and every drawn baseline. The declared rows come from the source table (as on a
    # source-preserving deck), so the item reuses the drawn group with the source text.
    import copy
    wrapped_body = ('<g id="native-table-paragraphs">'
        '<rect x="90" y="60" width="610" height="50" fill="#F1F5F9"/>'
        '<rect x="90" y="110" width="610" height="320" fill="#FFFFFF"/>'
        '<text x="114" y="95" font-family="Noto Sans KR" font-size="22" fill="#111111">확인 사항</text>'
        '<text x="114" y="160" font-family="Noto Sans KR" font-size="24" fill="#111111">'
        '<tspan x="114" y="160">첫 문단</tspan><tspan x="114" y="252">두 번째</tspan>'
        '<tspan x="114" y="354" fill="#2563EB" font-weight="700">문단 길게</tspan></text></g>')
    wrapped_svg = _svg(wrapped_body)
    wrapped_root = ET.fromstring(wrapped_svg)
    wrapped_item = copy.copy(spaced_item)
    wrapped_payload = deepcopy(spaced_item.payload)
    wrapped_payload["rows"][1][0]["text"] = "첫 문단\n두 번째 문단 길게"
    object.__setattr__(wrapped_item, "payload", wrapped_payload)
    object.__setattr__(wrapped_item, "binding_digest", native_binding_digest(wrapped_svg))
    wrapped = converter_payload(wrapped_item, pt_per_px=EMU_PER_PX / 12700)
    apply_drawn_table_style(wrapped, wrapped_item, wrapped_root)
    wrapped_cell = wrapped["rows"][1][0]
    assert [[(run["text"], run.get("break_before", False)) for run in p["runs"]]
            for p in wrapped_cell["paragraphs"]] == [[("첫 문단", False)], [("두 번째", False), ("문단 길게", True)]]
    assert wrapped_cell["paragraphs"][1]["runs"][-1]["color"] == "#2563EB"
    # Baselines 160 | 252, 354: the second paragraph's own drawn pitch (102) is its
    # exact line spacing and the gap before it keeps the drawn 92 between paragraphs.
    assert [(p["line_spacing"], p["space_before"]) for p in wrapped_cell["paragraphs"]] == [
        (30, 0), (102, pytest.approx(92 - 6 - 102 * 4 / 5))]
    assert wrapped_cell["padding"] == {"top": 26, "bottom": pytest.approx(430 - 354 - 102 / 5)}
    assert len(_table_cell_paragraphs(wrapped_cell)) == 2
    wrapped_dir = tmp_path / "paragraph-wrapped-native"
    wrapped_dir.mkdir()
    wrapped_readback, wrapped_deck = _convert(wrapped_dir, [wrapped_svg], [_sidecar(wrapped_item.to_wire())])
    assert wrapped_readback["nativeObjects"] == {"offered": 1, "native": 1, "fellBack": 0, "reasons": []}
    wrapped_table = next(shape.table for shape in _shapes(wrapped_deck.slides[0].shapes) if shape.has_table)
    wrapped_native = wrapped_table.cell(1, 0)
    assert [p.text for p in wrapped_native.text_frame.paragraphs] == ["첫 문단", "두 번째\v문단 길게"]
    assert [len(p._p.xpath("a:br")) for p in wrapped_native.text_frame.paragraphs] == [0, 1]
    assert [int(p._p.xpath("a:pPr/a:lnSpc/a:spcPts")[0].get("val"))
            for p in wrapped_native.text_frame.paragraphs] == [round(v * EMU_PER_PX / 127) for v in (30, 102)]
    assert str(wrapped_native.text_frame.paragraphs[1].runs[-1].font.color.rgb) == "2563EB"
    for field in ("line_spacing", "space_before"):
        invalid_values = [-1, float("nan"), float("inf"), True, None, 1e10]
        if field == "line_spacing":
            invalid_values.append(0)
        for invalid in invalid_values:
            with pytest.raises(RuntimeError):
                _table_cell_paragraphs({"paragraphs": [{"text": "원문", field: invalid}]})
    assert _table_cell_paragraphs({"paragraphs": ["원문"]})[0].line_spacing is None
    paragraph_dir = tmp_path / "paragraph-native"
    paragraph_dir.mkdir()
    spaced_readback, spaced_deck = _convert(paragraph_dir, [spaced_svg], [_sidecar(spaced_item.to_wire())])
    assert spaced_readback["nativeObjects"] == {"offered": 1, "native": 1, "fellBack": 0, "reasons": []}
    spaced_table = next(shape.table for shape in _shapes(spaced_deck.slides[0].shapes) if shape.has_table)
    assert [[c.text for c in row.cells] for row in spaced_table.rows] == spaced_rows
    assert [row.height for row in spaced_table.rows] == pytest.approx([50 * EMU_PER_PX, 320 * EMU_PER_PX], abs=1)
    assert sum(row.height for row in spaced_table.rows) == 370 * EMU_PER_PX
    paragraph_cell = spaced_table.cell(1, 0)
    assert paragraph_cell.margin_top == 26 * EMU_PER_PX
    assert paragraph_cell.margin_bottom == 70 * EMU_PER_PX
    assert [int(p._p.xpath("a:pPr/a:lnSpc/a:spcPts")[0].get("val"))
            for p in paragraph_cell.text_frame.paragraphs] == [round(30 * EMU_PER_PX / 127)] * 3
    assert [int(p._p.xpath("a:pPr/a:spcBef/a:spcPts")[0].get("val"))
            for p in paragraph_cell.text_frame.paragraphs] == [round(gap * EMU_PER_PX / 127) for gap in (0, 62, 72)]
    assert paragraph_cell.text_frame.paragraphs[-1].runs[0].font.bold is True
    assert str(paragraph_cell.text_frame.paragraphs[-1].runs[0].font.color.rgb) == "2563EB"

    from workers import public_svg_pptx_convert as runtime
    import hashlib

    unchanged_manifest = (runtime.VENDOR_ROOT / "MANIFEST.json").read_bytes()
    original_digest = runtime.converter_digest()
    original_read_bytes = type(runtime.VENDOR_ROOT).read_bytes
    target_adapter = runtime._PROJECTION_ADAPTER_FILES[0]

    def changed_adapter_bytes(path):
        data = original_read_bytes(path)
        return data + b"\n# adapter identity witness\n" if path == target_adapter else data

    with monkeypatch.context() as digest_patch:
        digest_patch.setattr(type(runtime.VENDOR_ROOT), "read_bytes", changed_adapter_bytes)
        assert runtime.converter_digest() != original_digest
    assert runtime.converter_digest() == original_digest
    assert (runtime.VENDOR_ROOT / "MANIFEST.json").read_bytes() == unchanged_manifest
    assert hashlib.sha256(unchanged_manifest).hexdigest() != original_digest
