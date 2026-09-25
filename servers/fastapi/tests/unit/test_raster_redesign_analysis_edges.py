"""Crop-edge invariant and short-reading grounding for raster redesign."""

from __future__ import annotations

import numpy as np

from services.source_content.raster_crop_edges import EdgeItem, crop_edge_conflicts, enforce_crop_edges
from services.source_content.raster_page_plan import PagePlan, PlannedRegion
from services.source_content.raster_redesign_ocr import RedesignWord
from services.source_content.raster_text_correction import accept_correction


def _word(text: str, box) -> RedesignWord:
    return RedesignWord(text, box, 0.95, "e", True)


def _plan_with_cut_line() -> tuple[PagePlan, tuple[RedesignWord, ...]]:
    words = (
        _word("우측", (100, 100, 40, 20)),
        _word("하단", (150, 100, 40, 20)),
        _word("아이콘을", (100, 130, 80, 20)),
        _word("통해", (190, 130, 40, 20)),
        _word("원할", (100, 160, 40, 20)),
    )
    # The crop's bottom edge crosses into the editable line below.
    crop = PlannedRegion("screenshot", (95, 95, 100, 40), (0, 1), "우측 하단", editable_fallback=True)
    body = PlannedRegion("body", (100, 130, 130, 50), (2, 3, 4), "아이콘을 통해\n원할")
    return PagePlan((crop, body), ()), words


def _items(plan: PagePlan) -> list[EdgeItem]:
    return [
        EdgeItem(r.role == "screenshot" or r.editable_fallback, r.bbox, tuple(r.word_indices)) for r in plan.regions
    ]


def test_crop_edge_crossing_an_editable_line_is_detected():
    plan, words = _plan_with_cut_line()
    kinds = {c.kind for c in crop_edge_conflicts(_items(plan), [w.bbox for w in words])}
    assert kinds == {"text_word"}


def test_crop_is_shrunk_or_takes_the_whole_line_never_both():
    plan, words = _plan_with_cut_line()
    fixed = enforce_crop_edges(plan, words)
    assert not crop_edge_conflicts(_items(fixed), [w.bbox for w in words])
    carried = sorted(k for r in fixed.regions for k in r.word_indices)
    assert carried == [0, 1, 2, 3, 4]  # every token carried exactly once
    for region in fixed.regions:
        if region.role == "body":
            assert "아이콘을" not in (region.text or "") or all(k not in region.word_indices for k in (0, 1))


def test_crop_edge_through_an_ink_cluster_grows_or_shrinks_off_it():
    gray = np.full((300, 400), 255, dtype=np.int16)
    for x in range(150, 210, 8):  # glyph strokes of one word straddling the crop's right edge
        gray[200:230, x : x + 3] = 0
    crop = PlannedRegion("screenshot", (100, 190, 80, 50), (), None, editable_fallback=True)
    fixed = enforce_crop_edges(PagePlan((crop,), ()), (), gray)
    box = fixed.regions[0].bbox
    inside = box[0] <= 150 and box[0] + box[2] >= 210
    outside = box[0] + box[2] <= 150
    assert inside or outside


def test_replacing_every_character_of_a_short_reading_is_not_a_correction():
    assert accept_correction(("5",), "리") is None
    assert accept_correction(("시",), "AI") == "AI"  # script swap stays allowed
    assert accept_correction(("우측 하단 2452 아이콘", "우측 하단 챗봇 아이콘"), "우측 하단 챗봇 아이콘") is not None


def test_two_readers_must_agree_before_a_line_is_text():
    from services.source_content.raster_text_correction import transcription_agrees

    assert transcription_agrees(("어떤 항목을 조회",), "어떤 항목을 조회")
    assert transcription_agrees(("신청ㆍ조회",), "신청·조회")  # punctuation variant
    assert transcription_agrees(("시가 신청 상태를 요약 안내하고",), "AI가 신청 상태를 요약 안내하고")  # script swap
    assert not transcription_agrees(("이떤 항옥을 초회",), "어떤 항목을 조회")  # three syllables differ
    assert not transcription_agrees(("연간 83%",), "연간 8396")  # numbers must be identical
    assert not transcription_agrees(("5",), "리")


def test_no_source_pixel_is_shown_twice():
    from services.source_content.raster_exactly_once import enforce_exactly_once, exactly_once_conflicts

    words = (
        _word("유사", (100, 100, 40, 20)),
        _word("사례", (150, 100, 40, 20)),
        _word("유사", (100, 100, 40, 20)),  # a second OCR reading of the same glyphs
        _word("화면", (400, 100, 60, 20)),
    )
    pixel_line = {"editable_fallback": True, "reason": "line_pixels"}
    line = PlannedRegion("screenshot", (97, 97, 96, 26), (0, 1), "유사 사례", **pixel_line)
    duplicate = PlannedRegion("screenshot", (97, 97, 46, 26), (2,), "유사", **pixel_line)
    capture = PlannedRegion("screenshot", (380, 80, 200, 200), (3,))
    fixed = enforce_exactly_once(PagePlan((line, duplicate, capture), ()), words)
    items = [(True, r.bbox, tuple(k for k in r.word_indices if k not in fixed.unpromoted)) for r in fixed.regions]
    assert not exactly_once_conflicts(items, [w.bbox for w in words])
    carried = sorted(k for r in fixed.regions for k in r.word_indices)
    assert sorted([*carried, *fixed.unpromoted]) == [0, 1, 2, 3]


def test_model_text_keeps_ocr_word_boundaries():
    from services.source_content.raster_text_correction import respace

    assert respace("AIFORASAFERTODAY", "AI FOR A SAFER TODAY") == "AI FOR A SAFER TODAY"
    assert respace("AI가 결재 문안까지", "시가 걸재 문인까지") == "AI가 결재 문안까지"
    assert respace("AIFORASAFERTODAY", "Al FORA SAFER") is not None
    assert respace("SECURITYPORTALFORYOU", "SECURITYPORTAL") is None  # long Latin run the OCR never saw


def test_a_text_block_without_tokens_does_not_crash_the_boundary_repair():
    from services.source_content.raster_boundary import enforce_boundaries

    words = (_word("화면", (100, 100, 60, 20)),)
    crop = PlannedRegion("screenshot", (90, 90, 200, 60), (0,))
    empty_block = PlannedRegion("body", (120, 100, 80, 40), (), "")
    fixed = enforce_boundaries(PagePlan((crop, empty_block), ()), words)
    assert fixed.regions  # repaired, not raised


def test_page_structuring_failure_falls_back_losslessly_with_an_advisory(monkeypatch):
    from PIL import Image

    from services.source_content import raster_page_structuring
    from services.source_content.raster_page_fallback import robust_plan
    from services.source_content.raster_page_structuring import plan_is_lossless
    from services.source_content.raster_redesign_ocr import RedesignLine

    def broken(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(raster_page_structuring, "structured_plan", broken)
    words = (_word("Title", (40, 30, 200, 60)), _word("body", (40, 400, 80, 20)))
    lines = (RedesignLine((0,), (40, 30, 200, 60), 60.0), RedesignLine((1,), (40, 400, 80, 20), 20.0))
    image = Image.new("RGB", (1000, 560), "white")
    monkeypatch.setenv("RASTER_STRICT_PAGE_ERRORS", "1")
    try:
        robust_plan(image, words, lines, None, None, None)
        raised = False
    except ValueError:
        raised = True
    assert raised  # tests see the bug
    monkeypatch.setenv("RASTER_STRICT_PAGE_ERRORS", "0")
    plan, advisories = robust_plan(image, words, lines, None, None, None)
    assert advisories == ["page_structuring_failed:ValueError"]
    assert plan_is_lossless(plan, words)
    assert [r.group_id for r in plan.regions] == ["title", None]
