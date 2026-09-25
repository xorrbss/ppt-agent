"""Contract: the editable-text paths relaxed for clean text never bypass the number gate.

Each relaxed path (a pixel-grounded mark the OCR dropped, a middle dot the OCR read as
another symbol, a list item split into its own paragraph) is first shown to publish a
line as text when its number is confirmed; the same line then stays pixels when the
number gate (``raster_text_guards.numbers_supported``) does not confirm its number:
an unverifiable whole-number unit, a conflicting per-token read, or no read at all.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from services.source_content.raster_captures import PageContext
from services.source_content.raster_number_units import NumberUnit, UnitRead
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_structure_apply import apply_structure
from services.source_content.raster_text_guards import NumberReads

H = 30


class _Page:
    def __init__(self):
        self.image = Image.new("RGB", (1400, 700), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.words: list[RedesignWord] = []
        self.lines: list[RedesignLine] = []

    def line(self, y: int, parts: list[tuple[str, str]]) -> int:
        """Word boxes left to right; each part is (text, what follows it: "dot" or "")."""

        start, x = len(self.words), 60
        for k, (text, after) in enumerate(parts):
            width = 24 * max(2, len(text))
            self.draw.rectangle((x, y, x + width - 1, y + H - 1), fill="black")
            self.words.append(RedesignWord(text, (x, y, width, H), 0.96, "test", k > 0))
            x += width
            if after == "dot":
                self.draw.ellipse((x + 18, y + 13, x + 22, y + 17), fill="black")
            x += 40
        indices = tuple(range(start, len(self.words)))
        self.lines.append(RedesignLine(indices, (60, y, x - 100, H), float(H), 1.0))
        return len(self.lines) - 1

    def context(self) -> PageContext:
        return PageContext(self.image, tuple(self.words), tuple(self.lines))


def _unit(page: _Page, word: int, read: str, wrapped: bool = False) -> UnitRead:
    box = page.words[word].bbox
    unit = NumberUnit((word,), box, page.words[word].text, wrapped)
    return UnitRead(unit, (read,), (read,))


def _texts(plan) -> list[str]:
    return [r.text or "" for r in plan.regions if r.role in ("title", "body") and not r.editable_fallback]


def _run(page: _Page, body: list[int], transcripts: dict[int, str], targeted: dict):
    raw = {"title": ["L1"], "groups": [{"heading": [], "body": [f"L{i + 1}" for i in body]}], "screenshots": []}
    context = page.context()
    return apply_structure(context, [], raw, "stub", targeted, {}, {0: "개요 요약", **transcripts})


def _mark_page() -> tuple[_Page, int, int]:
    page = _Page()
    page.line(40, [("개요", ""), ("요약", "")])
    # OCR "PART II 06": the dot after "II" is only in the pixels (and the model's text).
    line = page.line(200, [("PART", ""), ("II", "dot"), ("06", "")])
    return page, line, len(page.words) - 1


def test_grounded_mark_path_publishes_only_a_confirmed_number():
    page, line, number = _mark_page()
    transcript = {line: "PART II · 06"}
    confirmed = _run(page, [line], transcript, {line: NumberReads([_unit(page, number, "06")])})
    assert "PART II · 06" in _texts(confirmed)  # the relaxed path is live
    for targeted in (
        NumberReads([_unit(page, number, "06", wrapped=True)]),  # unit cannot be verified
        NumberReads([_unit(page, number, "08")]),  # the whole number reads differently
        NumberReads([]),  # no unit read at all
        ("0",),  # legacy per-token read that conflicts with "06"
    ):
        plan = _run(page, [line], transcript, {line: targeted})
        assert not any("06" in text for text in _texts(plan)), targeted
        assert any(number in r.word_indices and r.editable_fallback for r in plan.regions)


def test_symbol_substitution_path_keeps_an_unconfirmed_number_as_pixels():
    page = _Page()
    page.line(40, [("개요", ""), ("요약", "")])
    line = page.line(200, [("매출", ""), ("-", ""), ("12억", "")])
    number = len(page.words) - 1
    transcript = {line: "매출 · 12억"}
    confirmed = _run(page, [line], transcript, {line: NumberReads([_unit(page, number, "12")])})
    assert "매출 · 12억" in _texts(confirmed)
    plan = _run(page, [line], transcript, {line: NumberReads([_unit(page, number, "12", wrapped=True)])})
    assert not any("12" in text for text in _texts(plan))


def test_list_item_split_never_publishes_an_unconfirmed_item():
    page = _Page()
    page.line(40, [("개요", ""), ("요약", "")])
    first = page.line(200, [("•", ""), ("1.", ""), ("추진", ""), ("배경", "")])
    second = page.line(235, [("•", ""), ("2.", ""), ("처리", ""), ("시간", "")])
    one, two = page.lines[first].word_indices[1], page.lines[second].word_indices[1]
    transcripts = {first: "• 1. 추진 배경", second: "• 2. 처리 시간"}
    targeted = {first: NumberReads([_unit(page, one, "1.")]), second: NumberReads([_unit(page, two, "2.", True)])}
    plan = _run(page, [first, second], transcripts, targeted)
    texts = _texts(plan)
    assert any("추진 배경" in text for text in texts)  # its own paragraph: published
    assert not any("처리 시간" in text or "2." in text for text in texts)  # unconfirmed: pixels
    assert any(two in r.word_indices and r.editable_fallback for r in plan.regions)
