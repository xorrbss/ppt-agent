"""A character no reading contains is published only when its own shape is in the source."""

from __future__ import annotations

from PIL import Image, ImageDraw

from services.source_content.raster_captures import PageContext
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_structure_apply import apply_structure
from services.source_content.raster_symbol_grounding import unread_glyphs_grounded
from services.source_content.raster_text_guards import NumberReads

H = 30
Y = 60


def _glyph(draw: ImageDraw.ImageDraw, kind: str, x: int) -> tuple[int, int, int, int]:
    """Draw a symbol glyph at ``x`` on the line; return the tight box OCR would give it."""

    if kind == "dot":
        draw.ellipse((x, Y + 13, x + 4, Y + 17), fill="black")
        return (x, Y + 13, 5, 5)
    if kind == "hyphen":
        draw.rectangle((x, Y + 14, x + 11, Y + 16), fill="black")
        return (x, Y + 14, 12, 3)
    if kind == "arrow":  # shaft plus a head on the right; OCR boxes only the shaft
        draw.rectangle((x, Y + 14, x + 17, Y + 16), fill="black")
        draw.polygon([(x + 14, Y + 8), (x + 22, Y + 15), (x + 14, Y + 22)], fill="black")
        return (x, Y + 14, 14, 3)
    if kind == "up":
        draw.rectangle((x + 4, Y + 8, x + 6, Y + 26), fill="black")
        draw.polygon([(x, Y + 12), (x + 5, Y + 4), (x + 10, Y + 12)], fill="black")
        return (x, Y + 4, 11, 23)
    if kind in ("tilde", "approx"):
        for top in (Y + 10, Y + 17) if kind == "approx" else (Y + 13,):
            draw.line([(x, top + 3), (x + 4, top), (x + 8, top + 3), (x + 12, top)], fill="black", width=2)
        return (x, Y + 10, 13, 12)
    raise ValueError(kind)


def _page(parts: list[str]) -> tuple[PageContext, tuple[int, ...]]:
    """One line; a part is a word (drawn as a letter block) or ``"<kind>:<ocr text>"``
    for a symbol OCR read as ``ocr text``, or ``"<kind>"`` for a glyph OCR never read."""

    image = Image.new("RGB", (1200, 200), "white")
    draw = ImageDraw.Draw(image)
    words, x = [], 40
    for part in parts:
        kind, _, read = part.partition(":")
        if kind in ("dot", "hyphen", "arrow", "up", "tilde", "approx"):
            box = _glyph(draw, kind, x + 10)
            if read:
                words.append(RedesignWord(read, box, 0.9, "test", bool(words)))
            x += 50
            continue
        width = 30 * len(part)
        draw.rectangle((x, Y, x + width - 1, Y + H - 1), fill="black")
        words.append(RedesignWord(part, (x, Y, width, H), 0.95, "test", bool(words)))
        x += width + 20
    line = RedesignLine(tuple(range(len(words))), (40, Y, x - 40, H), float(H))
    return PageContext(image, tuple(words), (line,)), tuple(range(len(words)))


def _grounded(parts: list[str], text: str, *more: str) -> bool:
    page, carried = _page(parts)
    reading = " ".join(page.words[i].text for i in carried)
    return unread_glyphs_grounded(page, carried, (reading, *more), text)


def test_a_symbol_nobody_read_needs_pixels_of_its_own_shape():
    # The source shows a middle dot after the word; the model wrote an arrow.
    assert not _grounded(["감가상각", "dot"], "감가상각↑")
    assert _grounded(["감가상각", "dot"], "감가상각·")
    assert _grounded(["감가상각", "up"], "감가상각↑")
    # Nothing after the word at all.
    assert not _grounded(["감가상각"], "감가상각·")


def test_a_replaced_ocr_symbol_is_judged_by_its_glyph():
    assert _grounded(["자본", "arrow:-", "물리"], "자본 → 물리")
    assert not _grounded(["자본", "hyphen:-", "물리"], "자본 → 물리")  # a hyphen is not an arrow
    assert _grounded(["자본", "dot:-", "물리"], "자본 · 물리")
    assert not _grounded(["자본", "hyphen:-", "물리"], "자본 · 물리")  # nor a middle dot
    assert _grounded(["approx:~", "$54.8B"], "≈ $54.8B")
    assert not _grounded(["tilde:~", "$54.8B"], "≈ $54.8B")


def test_a_symbol_without_a_shape_rule_is_never_grounded():
    assert not _grounded(["매출", "dot", "현황"], "매출 ★ 현황")


def test_letters_need_a_reading_of_the_same_shape():
    assert _grounded(["Al", "Capital"], "AI Capital")  # l / I homoglyph
    assert _grounded(["seqment"], "segment")
    assert _grounded(["시", "투자"], "AI 투자")  # a glyph read in the wrong script
    assert not _grounded(["Executive", "summary"], "Executive Summary")  # a case change
    assert not _grounded(["공개치"], "공개처")  # another letter
    assert not _grounded(["승를"], "승패를")  # a letter nobody read
    # A character some reading holds needs nothing more.
    assert _grounded(["가이덤슈"], "가이던스", "가이던스")


def _plan(parts: list[str], transcript: str):
    page, carried = _page(parts)
    title_words = (RedesignWord("개요", (40, 150, 60, H), 0.95, "test", False),)
    words = page.words + title_words
    lines = (RedesignLine((len(page.words),), (40, 150, 60, H), float(H), 1.0), *page.lines)
    image = page.image.copy()
    ImageDraw.Draw(image).rectangle((40, 150, 99, 150 + H - 1), fill="black")
    context = PageContext(image, words, lines)
    raw = {"title": ["L1"], "groups": [{"heading": [], "body": ["L2"]}], "screenshots": []}
    plan = apply_structure(context, [], raw, "stub", {1: NumberReads([])}, {}, {0: "개요", 1: transcript})
    return [r.text for r in plan.regions if r.role in ("title", "body") and not r.editable_fallback]


def test_agreement_path_keeps_an_unread_symbol_line_as_pixels():
    # "감가상각↑" is within the agreement edit budget of "감가상각", but the arrow is not in the source.
    assert "감가상각↑" not in _plan(["감가상각", "dot"], "감가상각↑")
    assert "감가상각·" in _plan(["감가상각", "dot"], "감가상각·")
