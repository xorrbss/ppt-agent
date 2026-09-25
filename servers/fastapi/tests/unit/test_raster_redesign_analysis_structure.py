"""Structure detection for raster redesign: OCR alignment, tables, page plans, end to end."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from services.source_content.raster_page_plan import plan_page
from services.source_content.raster_redesign_ocr import (
    RedesignLine,
    RedesignWord,
    _space_flags,
    drop_fragment_lines,
    line_text,
)
from services.source_content.raster_tables import assign_table_cells, detect_ruled_tables


def test_txt_alignment_recovers_korean_word_spacing():
    tokens = ["통", "합", "보안", "포털", "AX", "개선"]
    assert _space_flags(tokens, "통합보안포털 AX 개선\n") == [False, False, False, False, True, True]
    assert _space_flags(tokens, "different text") is None


def test_fragment_lines_inside_a_taller_line_are_dropped():
    words = (
        RedesignWord("실물사진을", (100, 100, 120, 20), 0.93, "e", True),
        RedesignWord("므", (118, 100, 12, 6), 0.93, "e", True),
    )
    lines = (RedesignLine((0,), (100, 100, 120, 20), 20.0), RedesignLine((1,), (118, 100, 12, 6), 6.0))
    kept_words, kept_lines = drop_fragment_lines(words, lines)
    assert [w.text for w in kept_words] == ["실물사진을"]
    assert [line.word_indices for line in kept_lines] == [(0,)]


def _grid_image() -> Image.Image:
    image = Image.new("RGB", (1000, 600), "white")
    draw = ImageDraw.Draw(image)
    for y in (100, 160, 220, 280, 340):
        draw.line((100, y, 800, y), fill=(200, 200, 200), width=1)
    for x in (100, 400, 800):
        draw.line((x, 100, x, 340), fill=(200, 200, 200), width=1)
    return image


def test_ruled_grid_becomes_structured_cells():
    grids = detect_ruled_tables(_grid_image(), [])
    assert len(grids) == 1 and grids[0].ruled_columns
    grid = grids[0]
    boxes = {
        0: (120, 120, 60, 20),
        1: (420, 120, 80, 20),
        2: (120, 180, 60, 20),
        3: (420, 180, 90, 20),
        4: (120, 240, 60, 20),
        5: (420, 240, 70, 20),
        6: (120, 300, 60, 20),
        7: (420, 300, 70, 20),
    }
    texts = {
        i: t for i, t in enumerate(["항목", "내용", "인건비", "40,000원", "기준", "2,200시간", "대상", "보안포털"])
    }
    cells = assign_table_cells(grid, boxes, texts, {i: True for i in boxes})
    assert cells.cells == (("항목", "내용"), ("인건비", "40,000원"), ("기준", "2,200시간"), ("대상", "보안포털"))
    crossing = dict(boxes) | {1: (350, 120, 120, 20)}
    assert assign_table_cells(grid, crossing, texts, {i: True for i in boxes}).cells is None


def _word(text: str, box: tuple[int, int, int, int], confidence: float = 0.95) -> RedesignWord:
    return RedesignWord(text, box, confidence, "test", True)


def test_page_plan_separates_title_body_capture_and_icon():
    image = Image.new("RGB", (1672, 941), (246, 248, 252))
    draw = ImageDraw.Draw(image)
    draw.rectangle((900, 300, 1500, 800), fill="white", outline=(150, 150, 150), width=2)
    words: list[RedesignWord] = [
        _word("분기", (60, 60, 120, 60)),
        _word("실적", (200, 60, 120, 60)),
        _word("매출이", (60, 200, 80, 20)),
        _word("증가했습니다", (150, 200, 150, 20)),
        _word("영업이익은", (60, 230, 110, 20)),
        _word("40%", (180, 230, 50, 20)),
        _word("Ey", (60, 400, 40, 40), 0.3),
    ]
    lines = [
        RedesignLine((0, 1), (60, 60, 260, 60), 60.0, 1.0),
        RedesignLine((2, 3), (60, 200, 240, 20), 20.0, 1.0),
        RedesignLine((4, 5), (60, 230, 170, 20), 20.0, 1.0),
        RedesignLine((6,), (60, 400, 40, 40), 40.0),
    ]
    for row in range(12):  # dense micro UI text inside the bordered panel
        index = len(words)
        words.append(_word(f"메뉴{row}", (950, 330 + row * 35, 60, 9)))
        lines.append(RedesignLine((index,), (950, 330 + row * 35, 60, 9), 9.0, 1.0))
    plan = plan_page(image, tuple(words), tuple(lines))
    roles = {region.role: region for region in plan.regions}
    assert roles["title"].text == "분기 실적"
    assert roles["body"].text == "매출이 증가했습니다\n영업이익은 40%"
    assert roles["screenshot"].bbox[0] <= 900 and roles["screenshot"].bbox[1] <= 300
    assert "decoration" in roles and roles["decoration"].word_indices == ()  # icon misread as "Ey"
    carried = [i for region in plan.regions for i in region.word_indices]
    assert sorted(carried + list(plan.unpromoted)) == list(range(len(words)))
    assert plan.regions[0].role == "title"
    assert all(region.review_reason is None for region in plan.regions)


def _font(size: int):
    for candidate in (Path(r"C:\Windows\Fonts\malgun.ttf"), Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return None


def _image_only_pptx(path: Path, image_path: Path) -> None:
    from pptx import Presentation
    from pptx.util import Emu

    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(14_630_400), Emu(8_229_600)
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_picture(str(image_path), 0, 0, deck.slide_width, deck.slide_height)
    deck.save(path)


def test_redesign_end_to_end_with_local_ocr(tmp_path: Path):
    from services.source_content.raster_analysis import DEFAULT_TESSDATA, DEFAULT_TESSERACT, analyze_redesign_pptx
    from services.source_content.raster_structure import redesign_content_ledger

    tesseract = (
        DEFAULT_TESSERACT
        if DEFAULT_TESSERACT.is_file()
        else (Path(shutil.which("tesseract")) if shutil.which("tesseract") else None)
    )
    title_font, body_font = _font(64), _font(28)
    if tesseract is None or title_font is None or not DEFAULT_TESSDATA.is_dir():
        pytest.skip("local tesseract or Korean font unavailable")
    image = _grid_image().resize((1672, 941))
    draw = ImageDraw.Draw(image)
    draw.text((80, 30), "보안 검토 효과", font=title_font, fill=(20, 20, 40))
    draw.text((80, 780), "검토 시간이 크게 단축됩니다", font=body_font, fill=(40, 40, 40))
    image_path = tmp_path / "page.png"
    image.save(image_path)
    source = tmp_path / "deck.pptx"
    _image_only_pptx(source, image_path)
    draft = analyze_redesign_pptx(source, tesseract_path=tesseract, structurer=None)
    page = draft.analysis.pages[0]
    assert page.render_mode == "redesign" and page.approval_method == "automatic_redesign"
    assert page.reference_region is not None and page.reference_region.region_id in draft.crop_png_by_region
    # Without the vision reader no reading is independently confirmed: the title keeps
    # its pixels in the title band, its OCR reading only a suggestion.
    assert not any(region.role in ("title", "body") for region in page.regions)
    title = next(region for region in page.regions if region.group_id == "title")
    assert title.role == "screenshot" and title.editable_fallback and "보안" in (title.text or "")
    assert redesign_content_ledger(draft.analysis, 1).lossless
    assert all(
        region.region_id in draft.crop_png_by_region
        for region in page.regions
        if region.role in ("screenshot", "logo", "table")
    )
    blocking = [issue for issue in draft.analysis.unresolved if not issue.startswith("advisory:")]
    assert all(issue.startswith("page_1:low_confidence_region:") for issue in blocking)
    # No structurer: one deck-level advisory, never repeated per page.
    assert [i for i in draft.analysis.unresolved if "structurer" in i] == ["advisory:vision_structurer_unavailable"]
    assert line_text(RedesignLine((0,), (0, 0, 1, 1), 1.0), (_word("x", (0, 0, 1, 1)),)) == "x"


def test_doubtful_line_is_kept_as_pixels_with_suggestion():
    image = Image.new("RGB", (1672, 941), "white")
    words = (
        _word("매출", (60, 60, 120, 60)),
        _word("현황", (200, 60, 120, 60)),
        _word("영업이익은", (60, 300, 110, 20)),
        _word("40%", (180, 300, 50, 20)),
    )
    lines = (
        RedesignLine((0, 1), (60, 60, 260, 60), 60.0, 1.0),
        RedesignLine((2, 3), (60, 300, 170, 20), 20.0, 0.1, True),
    )
    plan = plan_page(image, words, lines)
    fallback = [region for region in plan.regions if region.editable_fallback]
    assert len(fallback) == 1 and fallback[0].role == "screenshot" and fallback[0].word_indices == (2, 3)
    assert fallback[0].text == "영업이익은 40%" and fallback[0].review_reason
