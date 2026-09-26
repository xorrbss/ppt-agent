"""Redesign SVG pages through the real SVG export worker conversion boundary."""

from __future__ import annotations

import base64
import io
import re
import xml.etree.ElementTree as ET

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

import test_raster_redesign_layout_fixtures as fx
from services.document_canvas import document_canvas_for_orientation
from services.generation_core.drivers.svg import export_scene
from services.source_content.raster_redesign_layout import render_redesign_page_svg
from services.svg_contract import validate_svg
from workers.public_svg_export_worker import convert_pptx

NS = "{http://www.w3.org/2000/svg}"
CANVAS = document_canvas_for_orientation("landscape")
DECK = fx.deck()


def _png_data_url():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (40, 90, 200)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _materialize(svg):
    return re.sub(r'href="source-slot-[^"]+"', f'href="{_png_data_url()}"', svg)


def _export(svgs, tmp_path):
    paths = []
    for index, svg in enumerate(svgs, 1):
        path = tmp_path / f"slide-{index:03d}.svg"
        path.write_text(_materialize(svg), encoding="utf-8")
        paths.append(path)
    target = tmp_path / "deck.pptx"
    readback = convert_pptx(paths, target, [""] * len(paths), CANVAS)
    return readback, Presentation(str(target))


def _dense(value):
    return re.sub(r"\s+", "", value or "")


def _shapes(slide):
    def walk(shapes):
        for shape in shapes:
            yield shape
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from walk(shape.shapes)
    return list(walk(slide.shapes))


def test_redesign_blocks_become_single_text_frames_and_tables_native(tmp_path):
    svgs = [render_redesign_page_svg(DECK, number) for number in range(1, len(DECK.pages) + 1)]
    readback, deck = _export(svgs, tmp_path)
    assert readback["rasterRedesign"] == {
        "rasterRedesign": True, "nativeTablesRequested": 1, "nativeTablesDelivered": 1,
        "tablesKeptAsGrid": 0, "nativeTableFallbackReason": None,
    }
    assert (deck.slide_width, deck.slide_height) == (1188 * 9000, 840 * 9000)
    for svg, slide in zip(svgs, deck.slides, strict=True):
        root = ET.fromstring(svg)
        grids = {node.get("id") for node in root.iter(f"{NS}g")}
        table_texts = {text for g in root.iter(f"{NS}g") for text in g.iter(f"{NS}text")}
        blocks = [node for node in root.iter(f"{NS}text") if node not in table_texts]
        frames = [s for s in _shapes(slide) if s.has_text_frame and s.text_frame.text.strip()]
        # One editable frame per <text> block (hanging-indent blocks keep per-line boxes).
        assert len(frames) >= len(blocks)
        assert "".join(sorted(_dense(f.text_frame.text) for f in frames)) == "".join(
            sorted(_dense("".join(b.itertext())) for b in blocks))
        tables = [s for s in _shapes(slide) if s.has_table]
        assert len(tables) == len(grids)
        for shape, group in zip(tables, root.iter(f"{NS}g"), strict=True):
            actual = [_dense(cell.text) for row in shape.table.rows for cell in row.cells]
            expected = _dense("".join(t for node in group.iter(f"{NS}text") for t in node.itertext()))
            assert "".join(actual) == expected
        pictures = sum(s.shape_type == MSO_SHAPE_TYPE.PICTURE for s in _shapes(slide))
        assert pictures == len(list(root.iter(f"{NS}image")))
        sizes = [run.font.size.pt for s in _shapes(slide) if s.has_text_frame
                 for paragraph in s.text_frame.paragraphs for run in paragraph.runs if run.font.size]
        assert min(sizes) >= 9.5
    page5 = deck.slides[4]
    assert sum(len(s.text_frame.paragraphs) > 1 for s in _shapes(page5) if s.has_text_frame) >= 6


def test_structured_table_cells_are_verbatim_in_the_native_table(tmp_path):
    region = next(r for r in DECK.pages[3].regions if r.region_id == "p4-table")
    _, deck = _export([render_redesign_page_svg(DECK, 4)], tmp_path)
    table = next(s for s in _shapes(deck.slides[0]) if s.has_table).table
    assert [[cell.text for cell in row.cells] for row in table.rows] == [list(row) for row in region.table_cells]


def test_native_table_failure_falls_back_to_grid_with_evidence(tmp_path, monkeypatch):
    original = export_scene.converter_payload

    def broken(item):
        payload = original(item)
        payload["header_rows"] = 99  # rejected by the converter's table schema
        return payload

    monkeypatch.setattr(export_scene, "converter_payload", broken)
    svg = render_redesign_page_svg(DECK, 4)
    readback, deck = _export([svg], tmp_path)
    evidence = readback["rasterRedesign"]
    assert evidence["nativeTablesDelivered"] == 0 and evidence["tablesKeptAsGrid"] == 1
    assert evidence["nativeTableFallbackReason"].startswith("converter_rejected:")
    assert readback["nativeObjects"]["fellBack"] == 1 and readback["nativeObjects"]["native"] == 0
    texts = "".join(sorted(_dense(s.text_frame.text) for s in _shapes(deck.slides[0])
                           if s.has_text_frame and s.text_frame.text.strip()))
    expected = "".join(sorted(_dense("".join(n.itertext())) for n in ET.fromstring(svg).iter(f"{NS}text")))
    assert texts == expected


def test_other_svg_decks_convert_unchanged(tmp_path):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1188" height="840" viewBox="0 0 1188 840">'
           '<text x="60" y="100" font-family="Noto Sans KR" font-size="20" fill="#000000">'
           '<tspan x="60" y="100">first</tspan><tspan x="60" y="130">second</tspan></text></svg>')
    readback, deck = _export([svg], tmp_path)
    assert "rasterRedesign" not in readback
    frames = [s for s in _shapes(deck.slides[0]) if s.has_text_frame and s.text_frame.text.strip()]
    assert len(frames) == 2


def test_contract_admits_tspan_dy_only_on_redesign_pages():
    body = ('<text x="10" y="40" font-family="Noto Sans KR" font-size="20" fill="#000000">'
            '<tspan x="10">a</tspan><tspan x="10" dy="{dy}">b</tspan></text>')
    plain = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">{}</svg>'
    marked = ('<svg xmlns="http://www.w3.org/2000/svg" id="svg-raster-redesign" width="100" height="100" '
              'viewBox="0 0 100 100">{}</svg>')
    validate_svg(marked.format(body.format(dy="24")), width=100, height=100)
    with pytest.raises(ValueError, match="unsupported_attribute:dy"):
        validate_svg(plain.format(body.format(dy="24")), width=100, height=100)
    for bad in ("0", "-3", "5000", "1 2"):
        with pytest.raises(ValueError):
            validate_svg(marked.format(body.format(dy=bad)), width=100, height=100)
    with pytest.raises(ValueError, match="unsupported_attribute:dy"):
        validate_svg(marked.format(body.replace("<tspan x=\"10\" dy", "<rect x=\"1\" y=\"1\" width=\"2\" "
                                                "height=\"2\"/><tspan x=\"10\" dy").format(dy="24")
                                   .replace('<rect x="1" y="1" width="2" height="2"/>', "")
                                   .replace("<text ", '<text dy="3" ')), width=100, height=100)
