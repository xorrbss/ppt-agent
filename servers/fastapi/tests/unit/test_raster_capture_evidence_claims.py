"""Screenshot claims are verified against layout evidence before the plan is built.

A line the model put in a screenshot that continues a paragraph it placed as slide
text is released back to that paragraph (and then passes every text gate); lines
inside a capture frame are never released; a frame edge running through a
released line is clipped off it.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from services.source_content.raster_capture_evidence import clip_frame, verify_screenshot_claims
from services.source_content.raster_captures import PageContext
from services.source_content.raster_llm_structure import Candidate
from services.source_content.raster_number_units import NumberUnit, UnitRead
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_structure_apply import apply_structure
from services.source_content.raster_text_guards import NumberReads

H = 24
PANEL = (600, 150, 560, 400)


class _Page:
    def __init__(self):
        self.image = Image.new("RGB", (1400, 800), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.draw.rectangle((PANEL[0], PANEL[1], PANEL[0] + PANEL[2], PANEL[1] + PANEL[3]), outline="gray", width=2)
        self.words: list[RedesignWord] = []
        self.lines: list[RedesignLine] = []

    def line(self, x: int, y: int, texts: list[str], height: int = H) -> int:
        start = len(self.words)
        left = x
        for k, text in enumerate(texts):
            width = height * max(2, len(text))
            self.draw.rectangle((x, y, x + width - 1, y + height - 1), fill="black")
            self.words.append(RedesignWord(text, (x, y, width, height), 0.96, "test", k > 0))
            x += width + height // 2
        indices = tuple(range(start, len(self.words)))
        self.lines.append(RedesignLine(indices, (left, y, x - left - height // 2, height), float(height), 1.0))
        return len(self.lines) - 1

    def context(self) -> PageContext:
        return PageContext(self.image, tuple(self.words), tuple(self.lines))


def _caption_page():
    """Title, a two-line card paragraph whose second line the model put in a screenshot."""

    page = _Page()
    page.line(60, 30, ["개선", "방식"], 40)
    first = page.line(60, 200, ["자연어로", "방문", "상황을"])
    second = page.line(60, 200 + H + 8, ["시스템이", "이해합니다"])
    ui = [page.line(640, 200 + 40 * k, ["메뉴", "항목"], 10) for k in range(5)]
    return page, first, second, ui


def _raw(title: int, group: list[int], shot: list[int]) -> dict:
    ids = lambda values: [f"L{i + 1}" for i in values]  # noqa: E731
    return {
        "title": ids([title]),
        "groups": [{"heading": [], "body": ids(group)}],
        "screenshots": [{"captures": ["C1"], "lines": ids(shot)}],
    }


def test_paragraph_continuation_is_released_to_its_card():
    page, first, second, ui = _caption_page()
    context = page.context()
    raw = _raw(0, [first], [second, *ui])
    verified, candidates, reviews = verify_screenshot_claims(context, [Candidate("C1", PANEL, "panel")], raw)
    assert reviews[0].released == ((second,),)
    assert verified["groups"][0]["body"] == [f"L{first + 1}", f"L{second + 1}"]
    shots = verified["screenshots"]
    assert all(f"L{second + 1}" not in shot["lines"] for shot in shots)
    assert sorted(line for shot in shots for line in shot["lines"]) == sorted(f"L{i + 1}" for i in ui)
    assert [c.candidate_id for c in candidates] == ["C1"]  # the frame needed no clipping
    assert raw["groups"][0]["body"] == [f"L{first + 1}"]  # the model output is not mutated


def test_line_inside_a_capture_frame_is_never_released():
    page, first, _, ui = _caption_page()
    # A UI line inside the frame, aligned with and one line pitch below the card text.
    framed = page.line(PANEL[0] + 20, 300, ["항목", "보기"])
    anchor = page.line(PANEL[0] + 20, 300 - H - 8, ["자연어로", "입력"])
    context = page.context()
    raw = _raw(0, [first, anchor], [framed, *ui])
    verified, _, reviews = verify_screenshot_claims(context, [Candidate("C1", PANEL, "panel")], raw)
    assert reviews[0].released == ()
    assert verified is raw


def test_icon_glyph_next_to_a_paragraph_stays_a_picture():
    page, first, _, ui = _caption_page()
    glyph = page.line(60, 200 + H + 8, ["@"])
    raw = _raw(0, [first], [glyph, *ui])
    verified, _, reviews = verify_screenshot_claims(page.context(), [Candidate("C1", PANEL, "panel")], raw)
    assert reviews[0].released == ()
    assert verified is raw


def test_frame_edge_through_a_released_line_is_clipped():
    assert clip_frame((100, 90, 400, 300), [(100, 80, 200, 20)]) == (100, 100, 400, 290)
    assert clip_frame((100, 90, 400, 300), [(600, 80, 200, 20)]) == (100, 90, 400, 300)


def test_named_capture_cutting_a_released_line_is_clipped_off_it():
    page, first, second, ui = _caption_page()
    context = page.context()
    # A ruled grid whose top edge runs through the released line.
    grid = Candidate("C2", (40, 200 + H + 20, 500, 200), "grid")
    raw = _raw(0, [first], [second, *ui])
    raw["screenshots"][0]["captures"] = ["C1", "C2"]
    verified, candidates, _ = verify_screenshot_claims(context, [Candidate("C1", PANEL, "panel"), grid], raw)
    clipped = [c for c in candidates if c.candidate_id.startswith("C2-")]
    assert len(clipped) == 1 and clipped[0].box[1] >= 200 + 2 * H + 8
    assert {"captures": [clipped[0].candidate_id], "lines": []} in verified["screenshots"]


def _texts(plan) -> list[str]:
    return [r.text or "" for r in plan.regions if r.role in ("title", "body") and not r.editable_fallback]


def test_released_line_passes_the_text_gates():
    page, first, second, ui = _caption_page()
    context = page.context()
    raw, candidates, _ = verify_screenshot_claims(
        context, [Candidate("C1", PANEL, "panel")], _raw(0, [first], [second, *ui])
    )
    agreed = {0: "개선 방식", first: "자연어로 방문 상황을", second: "시스템이 이해합니다"}
    plan = apply_structure(context, candidates, raw, "stub", {}, {}, agreed)
    assert "자연어로 방문 상황을\n시스템이 이해합니다" in _texts(plan)
    # The second reader disagrees: the released line keeps its pixels.
    disagreed = {**agreed, second: "시스템이 이해했습니다"}
    plan = apply_structure(context, candidates, raw, "stub", {}, {}, disagreed)
    assert not any("시스템이" in text for text in _texts(plan))


def test_released_line_passes_the_number_gate():
    page = _Page()
    page.line(60, 30, ["개선", "방식"], 40)
    first = page.line(60, 200, ["예상", "일정은"])
    second = page.line(60, 200 + H + 8, ["2026", "하반기"])
    number = len(page.words) - 2
    ui = [page.line(640, 200 + 40 * k, ["메뉴", "항목"], 10) for k in range(5)]
    context = page.context()
    raw, candidates, _ = verify_screenshot_claims(
        context, [Candidate("C1", PANEL, "panel")], _raw(0, [first], [second, *ui])
    )
    agreed = {0: "개선 방식", first: "예상 일정은", second: "2026 하반기"}

    def reads(value: str) -> dict:
        unit = NumberUnit((number,), page.words[number].bbox, page.words[number].text, False)
        return {second: NumberReads([UnitRead(unit, (value,), (value,))])}

    confirmed = apply_structure(context, candidates, raw, "stub", reads("2026"), {}, agreed)
    assert any("2026 하반기" in text for text in _texts(confirmed))
    for targeted in (reads("2028"), {}):  # a conflicting read, or none: the number is unconfirmed
        plan = apply_structure(context, candidates, raw, "stub", targeted, {}, agreed)
        assert not any("2026" in text for text in _texts(plan))


def test_verification_failure_keeps_the_model_claims(monkeypatch):
    from services.source_content import raster_page_structuring

    def broken(*_args):
        raise ValueError("bug")

    monkeypatch.setattr(raster_page_structuring, "verify_screenshot_claims", broken)
    page, first, second, ui = _caption_page()
    raw = _raw(0, [first], [second, *ui])
    candidates = [Candidate("C1", PANEL, "panel")]
    assert raster_page_structuring._verified_captures(page.context(), candidates, raw) == (raw, candidates)
