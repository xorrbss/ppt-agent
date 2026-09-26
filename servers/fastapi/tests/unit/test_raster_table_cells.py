"""A structured table publishes only cell lines that passed the text gates; an
unverified cell keeps its source pixels (raster_table_overlay), never its OCR reading."""

from __future__ import annotations

from PIL import Image

from services.source_content.raster_captures import PageContext
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_structure_emit import Build, _table

CELLS = [
    ["부서", "처리량"], ["총무팀", "8,200건"], ["운영팀", "2,900건"],
    ["재무팀", "3,100건"], ["인사팀", "5,600건"], ["영업팀", "12,400건"],
]


def _build(texts: dict[int, str | None], readings: dict[int, str]) -> Build:
    words, lines = [], []
    for r, row in enumerate(CELLS):
        for c, value in enumerate(row):
            box = (40 + 200 * c, 40 + 50 * r, 120, 30)
            words.append(RedesignWord(value, box, 0.95, "tesseract", False))
            lines.append(RedesignLine((len(words) - 1,), box, 30.0))
    page = PageContext(Image.new("RGB", (600, 400), "white"), tuple(words), tuple(lines))
    build = Build(page, list(words), "model", set())
    for i in range(len(lines)):
        build.tokens[i] = (i,)
        build.readings[i] = readings.get(i, words[i].text)
        build.texts[i] = texts.get(i, words[i].text)
    return build


def _rows() -> list[list[list[int]]]:
    return [[[2 * r + c] for c in range(2)] for r in range(len(CELLS))]


def test_fully_verified_table_is_native() -> None:
    build = _build({}, {})
    _table(build, _rows(), None)
    region = build.regions[-1]
    assert region.table_cells == tuple(tuple(row) for row in CELLS)
    assert not region.editable_fallback and region.review_reason is None


def test_one_unverified_text_cell_never_publishes_its_ocr_reading() -> None:
    # An icon at the cell start read as "[" failed the text gate: that cell alone
    # stays source pixels over the native table, published as "".
    build = _build({4: None}, {4: "[운영팀"})
    _table(build, _rows(), None)
    region = build.regions[-1]
    assert not region.editable_fallback and region.table_cells[2] == ("", "2,900건")
    assert [(cell.row, cell.col, cell.word_indices) for cell in region.pixel_cells] == [(2, 0, (4,))]
    assert "[운영팀" not in "".join(value for row in region.table_cells for value in row)


def test_unverified_number_cell_is_never_published() -> None:
    build = _build({5: None}, {5: "2,960건"})
    _table(build, _rows(), None)
    region = build.regions[-1]
    assert region.table_cells[2] == ("운영팀", "") and len(region.pixel_cells) == 1
    assert "2,960" not in "".join(value for row in region.table_cells for value in row)


def test_mostly_unverified_table_keeps_its_pixels() -> None:
    build = _build({i: None for i in range(7)}, {})
    _table(build, _rows(), None)
    region = build.regions[-1]
    assert region.table_cells is None and region.editable_fallback and not region.pixel_cells
    assert region.review_reason == "table_cell_ocr_low_confidence"
