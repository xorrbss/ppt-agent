"""Table cell pixel overlay: a native table whose few unverified cells stay source pixels."""

from __future__ import annotations

import base64
import hashlib
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest
from PIL import Image
from pydantic import ValidationError

from services.raster_source_assets import raster_slot_id
from services.source_content import raster_redesign
from services.source_content.raster_analysis import EmbeddedRasterPage
from services.source_content.raster_captures import PageContext
from services.source_content.raster_contracts import (
    RasterAnalysis,
    raster_digest,
    require_analysis_digest,
    seal_analysis,
)
from services.source_content.raster_page_plan import PagePlan, PlannedRegion
from services.source_content.raster_redesign_fallback import region_fallbacks
from services.source_content.raster_redesign_layout import render_redesign_page
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_redesign_pptx import redesign_native_objects, table_cell_overlays
from services.source_content.raster_structure import redesign_content_ledger
from services.source_content.raster_structure_emit import Build, _table
from services.source_content.raster_table_overlay import settle_pixel_cells

TITLE = "지역별 처리 현황"
CELLS = [
    ["부서", "처리량", "비중"],
    ["총무팀", "8,200건", "31%"],
    ["운영팀", "2,900건", "11%"],
    ["재무팀", "3,100건", "12%"],
    ["인사팀", "5,600건", "21%"],
]
SIZE = (1200, 900)


def _page():
    words = [RedesignWord(TITLE, (80, 40, 500, 60), 0.97, "tesseract", False)]
    lines = [RedesignLine((0,), (80, 40, 500, 60), 60.0)]
    for r, row in enumerate(CELLS):
        for c, value in enumerate(row):
            box = (100 + 300 * c, 200 + 80 * r, 160, 40)
            words.append(RedesignWord(value, box, 0.95, "tesseract", False))
            lines.append(RedesignLine((len(words) - 1,), box, 40.0))
    image = Image.new("RGB", SIZE, "white")
    return image, tuple(words), tuple(lines)


def _rows() -> list[list[list[int]]]:
    return [[[1 + 3 * r + c] for c in range(3)] for r in range(len(CELLS))]


def _table_region(unverified: set[int], rows=None) -> tuple[PlannedRegion, tuple[RedesignWord, ...]]:
    image, words, lines = _page()
    build = Build(PageContext(image, words, lines), list(words), "model", set())
    for i in range(1, len(lines)):
        build.tokens[i] = (i,)
        build.readings[i] = words[i].text
        build.texts[i] = None if i in unverified else words[i].text
    _table(build, rows or _rows(), None)
    return build.regions[-1], words


def _plan(unverified: set[int]) -> tuple[PagePlan, tuple[RedesignWord, ...]]:
    table, words = _table_region(unverified)
    title = PlannedRegion("title", (80, 40, 500, 60), (0,), TITLE, confidence=0.9, group_id="title")
    return PagePlan((title, table), (), words), words


def test_one_unverified_cell_keeps_the_table_native_with_one_pixel_cell() -> None:
    region, words = _table_region({8})  # "2,900건"
    assert not region.editable_fallback and region.review_reason is None
    assert region.table_cells[2] == ("운영팀", "", "11%")
    assert len(region.pixel_cells) == 1
    cell = region.pixel_cells[0]
    assert (cell.row, cell.col, cell.word_indices) == (2, 1, (8,))
    x, y, w, h = cell.bbox
    assert region.bbox[0] <= x and x + w <= region.bbox[0] + region.bbox[2]
    # Inside its own cell band: it never reaches a neighbour cell's words.
    for t, word in enumerate(words[1:], 1):
        if t != 8:
            wx, wy, ww, wh = word.bbox
            assert min(x + w, wx + ww) - max(x, wx) <= 0 or min(y + h, wy + wh) - max(y, wy) <= 0
    assert "2,900" not in "".join(v for row in region.table_cells for v in row)


def test_more_than_half_unverified_cells_keep_the_whole_table_pixels() -> None:
    region, _ = _table_region(set(range(1, 10)))  # 9 of 15 filled cells
    assert region.table_cells is None and region.editable_fallback and not region.pixel_cells
    assert region.review_reason == "table_cell_ocr_low_confidence"


def test_unconfirmed_grid_keeps_the_whole_table_pixels() -> None:
    rows = _rows()
    rows[1][0], rows[1][1] = rows[1][1], rows[1][0]  # the model swapped two columns
    region, _ = _table_region({8}, rows)
    assert region.table_cells is None and region.editable_fallback and not region.pixel_cells


def test_settle_rejects_a_crop_showing_another_carriers_word() -> None:
    plan, words = _plan({8})
    table = plan.regions[1]
    grown = replace(table.pixel_cells[0], bbox=(100, 360, 760, 40))  # reaches "운영팀" and "11%"
    broken = replace(plan, regions=(plan.regions[0], replace(table, pixel_cells=(grown,))))
    settled = settle_pixel_cells(broken, words).regions[1]
    assert settled.table_cells is None and settled.editable_fallback and not settled.pixel_cells
    assert settle_pixel_cells(plan, words) is plan


def _analysis(monkeypatch, tmp_path, plan: PagePlan, words) -> tuple[RasterAnalysis, dict]:
    image, _, lines = _page()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    media = buffer.getvalue()
    embedded = EmbeddedRasterPage(1, "ppt/media/image1.png", media, image, "occ-1")
    sha = hashlib.sha256(media).hexdigest()
    monkeypatch.setattr(raster_redesign, "extract_embedded_raster_pages", lambda _p: (sha, 12_192_000, 9_144_000,
                                                                                     (embedded,)))
    monkeypatch.setattr(raster_redesign, "redesign_ocr", lambda *_a, **_k: (words, lines))
    monkeypatch.setattr(raster_redesign, "robust_plan", lambda *_a: (plan, []))
    tesseract = tmp_path / "tesseract.exe"
    tesseract.write_bytes(b"")
    draft = raster_redesign.analyze_redesign(tmp_path / "deck.pptx", orientation="landscape",
                                             tesseract_path=tesseract, tessdata_path=tmp_path)
    return draft.analysis, draft.crop_png_by_region


def test_sealed_overlay_is_lossless_and_rendered_after_its_native_table(monkeypatch, tmp_path) -> None:
    plan, words = _plan({8})
    analysis, crops = _analysis(monkeypatch, tmp_path, plan, words)
    page = analysis.pages[0]
    table = next(r for r in page.regions if r.role == "table")
    ((row, col, crop_id),) = table.table_pixel_cells
    crop = next(r for r in page.regions if r.region_id == crop_id)
    assert (row, col) == (2, 1) and table.table_cells[2][1] == ""
    assert crop.role == "screenshot" and crop.editable_fallback and crop.ocr_token_indices == (8,)
    assert 8 not in table.ocr_token_indices and crop_id in crops
    ledger = redesign_content_ledger(analysis, 1)
    assert ledger.lossless, ledger.violations

    svg = render_redesign_page(analysis, 1).svg
    group = re.search(r'<g id="(rd-[0-9a-f]{10}-table-5x3)">.*?</g>', svg)
    assert group is not None and "<image" not in group.group(0)
    image = re.search(r'<image id="[^"]+"[^>]*href="' + raster_slot_id(1, crop_id) + '"', svg)
    assert image is not None and image.start() >= group.end()  # a sibling drawn after the table group
    root = ET.fromstring(svg)
    (native,), skipped = redesign_native_objects(root)
    assert skipped == 0 and native.payload["rows"][2][1]["text"] == ""
    assert table_cell_overlays(root) == {group.group(1): 1}


def test_contract_rejects_a_pixel_cell_outside_its_table(monkeypatch, tmp_path) -> None:
    plan, words = _plan({8})
    analysis, _ = _analysis(monkeypatch, tmp_path, plan, words)
    payload = analysis.model_dump(mode="json")
    crop = next(r for r in payload["pages"][0]["regions"] if r["region_id"].endswith("-cell-r2c1"))
    crop["bbox"]["y"] = 0.0
    with pytest.raises(ValidationError, match="raster_table_pixel_cell_invalid"):
        seal_analysis(payload)
    payload = analysis.model_dump(mode="json")
    table = next(r for r in payload["pages"][0]["regions"] if r["role"] == "table")
    table["table_cells"][2][1] = "2,900건"
    with pytest.raises(ValidationError, match="raster_table_pixel_cells_invalid"):
        seal_analysis(payload)


def test_pixel_cells_are_dropped_with_their_table(monkeypatch, tmp_path) -> None:
    plan, words = _plan({8})
    analysis, _ = _analysis(monkeypatch, tmp_path, plan, words)
    page = analysis.pages[0]
    table = next(r for r in page.regions if r.role == "table")
    fallback = region_fallbacks(page)[table.region_id]
    assert table.table_pixel_cells[0][2] in fallback.member_ids


def test_digest_of_a_page_without_overlays_is_unchanged(monkeypatch, tmp_path) -> None:
    plan, words = _plan(set())
    analysis, _ = _analysis(monkeypatch, tmp_path, plan, words)
    payload = analysis.model_dump(mode="json", exclude={"digest", "stage_timings"})
    for page in payload["pages"]:
        assert all("table_pixel_cells" not in region for region in (*page["regions"], page["reference_region"]))
    assert analysis.digest == raster_digest(payload)
    overlaid, _ = _analysis(monkeypatch, tmp_path, *_plan({8}))
    table = next(r for r in overlaid.model_dump(mode="json")["pages"][0]["regions"] if r["role"] == "table")
    assert table["table_pixel_cells"] == [[2, 1, "p1-r2-cell-r2c1"]]
    require_analysis_digest(RasterAnalysis.model_validate_json(overlaid.model_dump_json()))


def test_overlay_reaches_pptx_as_a_picture_over_the_native_table(monkeypatch, tmp_path) -> None:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    from services.document_canvas import document_canvas_for_orientation
    from workers.public_svg_export_worker import convert_pptx

    plan, words = _plan({8})
    analysis, _ = _analysis(monkeypatch, tmp_path, plan, words)
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (40, 90, 200)).save(buffer, format="PNG")
    data = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    path = tmp_path / "slide-001.svg"
    path.write_text(re.sub(r'href="source-slot-[^"]+"', f'href="{data}"', render_redesign_page(analysis, 1).svg),
                    encoding="utf-8")
    readback = convert_pptx([path], tmp_path / "deck.pptx", [""], document_canvas_for_orientation("landscape"))
    evidence = readback["rasterRedesign"]
    assert evidence["nativeTablesDelivered"] == 1 and evidence["tableCellOverlays"] == 1
    slide = Presentation(str(tmp_path / "deck.pptx")).slides[0]
    table = next(shape for shape in slide.shapes if shape.has_table).table
    assert table.cell(2, 1).text == "" and table.cell(2, 0).text == "운영팀"
    assert sum(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in slide.shapes) == 1
