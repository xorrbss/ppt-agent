"""Vision-structured redesign with a stubbed model: grounding, fallback, ledger."""

from __future__ import annotations

from PIL import Image, ImageDraw

from services.source_content.raster_page_structuring import plan_is_lossless, structured_plan
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_text_correction import accept_correction


def test_correction_grounding_rules():
    assert accept_correction(["시가 도와주는"], "AI가 도와주는") == "AI가 도와주는"  # script swap
    assert accept_correction(["기술 미딩을"], "기술 미팅을") == "기술 미팅을"  # small edit
    assert accept_correction(["50%", "509%"], "50%") == "50%"
    assert accept_correction(["509%"], "50%") is None  # digits must equal a reading
    assert accept_correction(["Eo"], "물품") is None  # not grounded: invented text
    assert accept_correction(["—"], "↻") is None  # pure symbols are icon guesses


def _word(text, box, confidence=0.95):
    return RedesignWord(text, box, confidence, "test", True)


def _page():
    image = Image.new("RGB", (1672, 941), (246, 248, 252))
    ImageDraw.Draw(image).rectangle((900, 300, 1500, 800), fill="white", outline=(120, 120, 120), width=2)
    words = (
        _word("매출", (60, 60, 120, 60)),
        _word("현황", (200, 60, 120, 60)),
        _word("핵심", (60, 200, 60, 24)),
        _word("요약", (130, 200, 60, 24)),
        _word("영업이익은", (60, 240, 110, 20)),
        _word("40%", (180, 240, 50, 20)),
        _word("시가", (60, 280, 50, 20), 0.5),
        _word("지원", (120, 280, 50, 20)),
        _word("메뉴", (950, 350, 60, 9)),
    )
    lines = (
        RedesignLine((0, 1), (60, 60, 260, 60), 60.0, 1.0),
        RedesignLine((2, 3), (60, 200, 130, 24), 24.0, 1.0),
        RedesignLine((4, 5), (60, 240, 170, 20), 20.0, 1.0),
        RedesignLine((6, 7), (60, 280, 110, 20), 20.0, 0.0, alternates=("시가 지원",)),
        RedesignLine((8,), (950, 350, 60, 9), 9.0, 1.0),
    )
    return image, words, lines


class _Stub:
    model = "stub-vision"

    def __init__(self, answer, transcripts=None):
        self.answer = answer
        self.transcripts = transcripts
        self.calls = 0

    def __call__(self, system, parts, schema, name):
        if name == "line_transcripts":  # None: the reader failed, no line is confirmed
            assert all(isinstance(part, bytes) for part in parts[:-1]) and "L1" in parts[-1]
            return None if self.transcripts is None else {"lines": self.transcripts}
        self.calls += 1
        assert isinstance(parts[0], bytes) and "L5" in parts[1]
        return self.answer


def test_line_is_text_only_when_model_transcription_and_ocr_agree():
    image, words, lines = _page()
    stub = _Stub(
        {"title": ["L1"], "groups": [{"heading": ["L2"], "body": ["L3", "L4"]}], "screenshots": []},
        [
            {"id": "L1", "text": "매출 현황"},
            {"id": "L2", "text": "완전히 다른 제목"},
            {"id": "L3", "text": "영업이익은 40%"},
            {"id": "L4", "text": "AI가 지원"},
        ],
    )
    plan = structured_plan(image, words, lines, stub)
    assert plan_is_lossless(plan, plan.words)
    texts = [r.text for r in plan.regions if r.role in ("title", "body") and not r.editable_fallback]
    assert "매출 현황" in texts  # agreement: the model's text is published
    assert not any("핵심" in (t or "") for t in texts)  # readers disagree: pixels
    # "40%" has no independent confirmation, so its whole paragraph ("AI가 지원" too) is one crop.
    assert not any("40%" in (t or "") or "지원" in (t or "") for t in texts)
    paragraph = [r for r in plan.regions if r.editable_fallback and 4 in r.word_indices]
    assert len(paragraph) == 1 and {4, 5} <= set(paragraph[0].word_indices)


def test_structured_plan_is_grounded_and_lossless():
    image, words, lines = _page()
    stub = _Stub(
        {
            "title": ["L1"],
            "groups": [{"heading": ["L2"], "body": ["L3", "L4"], "captures": [], "icons": []}],
            "screenshots": [{"captures": [], "lines": ["L5"]}],
            "corrections": [{"line": "L4", "text": "AI가 지원"}, {"line": "L3", "text": "영업이익은 99%"}],
        },
        [
            {"id": "L1", "text": "매출 현황"},
            {"id": "L2", "text": "핵심 요약"},
            {"id": "L3", "text": "영업이익은 99%"},
            {"id": "L4", "text": "AI가 지원"},
        ],
    )
    plan = structured_plan(image, words, lines, stub)
    assert stub.calls == 1 and plan.structured_by == "stub-vision"
    assert plan_is_lossless(plan, plan.words)
    title = next(r for r in plan.regions if r.role == "title")
    texts = [r.text for r in plan.regions if r.group_id == "g1" and r.role == "body"]
    assert title.text == "매출 현황"
    assert texts == ["핵심 요약"]  # the heading is its own paragraph
    assert next(r for r in plan.regions if r.group_id == "g1" and r.role == "body").heading_line_count == 1
    # The model called L3 wrong but its fix changed a digit: the body paragraph (L3 + L4)
    # is one pixel crop, never text mixed with a pixel line.
    body = [r for r in plan.regions if r.editable_fallback and {4, 5} <= set(r.word_indices)]
    assert len(body) == 1 and len(body[0].word_indices) > 2
    assert any(r.role == "screenshot" and 8 in r.word_indices for r in plan.regions)
    assert plan.corrections == ("시가 지원 => AI가 지원",)  # the digit change was refused


def test_ungrounded_doubtful_line_stays_pixels():
    image, words, lines = _page()
    stub = _Stub(
        {
            "title": ["L1"],
            "groups": [{"heading": [], "body": ["L3", "L4"]}],
            "screenshots": [],
            "corrections": [{"line": "L4", "text": "완전히 다른 문장"}],
        }
    )
    plan = structured_plan(image, words, lines, stub)
    pixels = [r for r in plan.regions if r.editable_fallback]
    assert any({6, 7} <= set(r.word_indices) for r in pixels)
    assert plan_is_lossless(plan, plan.words)


def test_provider_failure_or_bad_answer_keeps_heuristic_plan():
    image, words, lines = _page()

    def failing(*_args):
        raise RuntimeError("provider down")

    assert structured_plan(image, words, lines, failing).structured_by is None
    untitled = structured_plan(image, words, lines, _Stub({"groups": []}, [{"id": "L1", "text": "매출 현황"}]))
    assert next(r for r in untitled.regions if r.role == "title").text == "매출 현황"  # top-band fallback title
    assert structured_plan(image, words, lines, None).structured_by is None


def test_crop_text_boundary_collapses_the_card():
    from services.source_content.raster_boundary import enforce_boundaries
    from services.source_content.raster_page_plan import PagePlan, PlannedRegion

    words = (_word("1", (100, 100, 20, 40)), _word("0,300시간", (125, 100, 150, 40)), _word("요약", (100, 200, 60, 20)))
    plan = PagePlan(
        (
            PlannedRegion("screenshot", (95, 95, 30, 50), (0,), "1", editable_fallback=True, group_id="g1"),
            PlannedRegion("body", (125, 100, 150, 40), (1,), "0,300시간", group_id="g1"),
            PlannedRegion("body", (100, 200, 60, 20), (2,), "요약", group_id="g2"),
        ),
        (),
    )
    fixed = enforce_boundaries(plan, words)
    assert [r.role for r in fixed.regions] == ["screenshot", "body"]
    assert fixed.regions[0].word_indices == (0, 1) and fixed.regions[0].bbox == (95, 95, 180, 50)


def test_ledger_counts_renderer_pixel_replacements():
    from tests.unit.test_raster_redesign_analysis import _redesign_payload
    from services.source_content.raster_contracts import seal_analysis
    from services.source_content.raster_structure import redesign_content_ledger

    payload = _redesign_payload()
    payload["pages"][0]["regions"][0]["text"] = "garbled"  # text no longer matches its tokens
    analysis = seal_analysis(payload)
    assert not redesign_content_ledger(analysis, 1).lossless
    rendered = redesign_content_ledger(analysis, 1, pixel_region_ids=("p1-r1",))
    assert rendered.lossless and rendered.entries[0].carrier == "pixels"


def test_boundary_takes_the_whole_paragraph():
    from services.source_content.raster_boundary import enforce_boundaries
    from services.source_content.raster_page_plan import PagePlan, PlannedRegion

    words = (
        _word("라벨", (100, 100, 60, 20)),
        _word("설명", (100, 130, 60, 20)),
        _word("추가", (100, 160, 60, 20)),
        _word("화면", (90, 60, 200, 60)),
    )
    plan = PagePlan(
        (
            PlannedRegion("screenshot", (90, 60, 200, 70), (3,), group_id="g1"),
            PlannedRegion("body", (100, 100, 60, 80), (0, 1, 2), "라벨\n설명\n추가", group_id="g1"),
        ),
        (),
    )
    fixed = enforce_boundaries(plan, words)
    texts = [r.text for r in fixed.regions if r.role == "body"]
    assert texts == []  # paragraph atomicity: the crop takes the whole block, never one line
    capture = next(r for r in fixed.regions if r.role == "screenshot")
    assert capture.word_indices == (3, 0, 1, 2) and not capture.editable_fallback


def test_uncovered_ink_is_reported_and_covered_ink_is_not():
    from services.source_content.raster_ink_coverage import uncovered_ink

    image = Image.new("RGB", (800, 450), (246, 248, 252))
    draw = ImageDraw.Draw(image)
    for n in range(4):  # a four-glyph mark, like a numeral OCR missed
        draw.rectangle((300 + n * 24, 200, 318 + n * 24, 240), fill=(20, 40, 200))
    assert len(uncovered_ink(image, [], [], [])) == 1
    assert uncovered_ink(image, [(290, 190, 140, 60)], [], []) == []


def test_text_guards_block_misread_numbers_and_glyph_fragments():
    from services.source_content.raster_text_guards import (
        corner_fragment,
        glued_symbols,
        number_patched,
        numbers_supported,
        symbol_only,
    )

    # Both Korean passes read "83%" as "8396"; the targeted English read says "83%".
    assert not numbers_supported("8396", ("8396", "8396"), ("83%",))
    assert numbers_supported("83%", ("8396", "8396"), ("83%",))
    assert numbers_supported("약 9,860시간", ("약 9,860시간", "9,860시간"), None)  # two reads agree
    assert not numbers_supported("약 9,860시간", ("약 9,860시간",), None)
    assert numbers_supported("40,000원/시간", ("40,000원/시간",), ("40,0008",))  # eng invents a digit for 원
    assert number_patched("보수적 5096", ("50%",)) == "보수적 50%"
    assert glued_symbols(r"\S-IS") and glued_symbols("505]") and not glued_symbols("AS-IS (처리시간)")
    assert symbol_only("-()-") and not symbol_only("•") and not symbol_only("2)")
    assert corner_fragment((1600, 900, 20, 12), "05", (1672, 941)) and not corner_fragment(
        (800, 400, 20, 12), "3", (1672, 941)
    )
