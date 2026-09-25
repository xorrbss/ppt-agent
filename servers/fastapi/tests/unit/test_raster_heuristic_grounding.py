"""Without the vision reader no OCR reading is independently confirmed: text stays pixels."""

from __future__ import annotations

from PIL import Image

from services.source_content.raster_heuristic_grounding import unconfirmed_text_as_pixels
from services.source_content.raster_page_plan import PagePlan, PlannedRegion
from services.source_content.raster_page_structuring import plan_is_lossless, structured_plan
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord


def _word(text, box, confidence=0.95):
    return RedesignWord(text, box, confidence, "test", True)


def _page():
    image = Image.new("RGB", (1672, 941), (246, 248, 252))
    words = (
        _word("매출", (60, 60, 120, 60)),
        _word("현황", (200, 60, 120, 60)),
        _word("생성형", (60, 240, 80, 20)),
        _word("시가", (150, 240, 50, 20)),  # both OCR passes read "AI" as "시"
    )
    lines = (
        RedesignLine((0, 1), (60, 60, 260, 60), 60.0, 1.0, alternates=("매출 현황", "매출 현황")),
        RedesignLine((2, 3), (60, 240, 140, 20), 20.0, 1.0, alternates=("생성형 시가", "생성형 시가")),
    )
    return image, words, lines


def test_every_editable_region_becomes_source_pixels():
    table = PlannedRegion("table", (0, 300, 400, 100), (4, 5), table_cells=(("항목", "40%"),))
    plan = PagePlan(
        (
            PlannedRegion("title", (0, 0, 400, 60), (0, 1), "매출 현황"),
            PlannedRegion("body", (0, 100, 400, 40), (2, 3), "생성형 시가", group_id="g1"),
            table,
            PlannedRegion("screenshot", (500, 0, 200, 200), (6,), reason="micro_text_capture"),
        ),
        (),
    )
    title, body, pixel_table, capture = unconfirmed_text_as_pixels(plan).regions
    assert (title.role, title.editable_fallback, title.group_id) == ("screenshot", True, "title")
    assert title.text == "매출 현황"  # the reading stays only as a suggestion
    assert (body.role, body.editable_fallback, body.group_id) == ("screenshot", True, "g1")
    assert pixel_table.role == "table" and pixel_table.table_cells is None and pixel_table.text == "항목\t40%"
    assert capture == plan.regions[3]
    # The page-level advisory says why; regions add none of their own.
    assert all(region.review_reason is None for region in (title, body, pixel_table))
    assert [r.word_indices for r in (title, body, pixel_table)] == [(0, 1), (2, 3), (4, 5)]


def test_a_plan_without_editable_text_is_returned_unchanged():
    plan = PagePlan((PlannedRegion("screenshot", (0, 0, 10, 10), (0,), "x", editable_fallback=True),), ())
    assert unconfirmed_text_as_pixels(plan) is plan


def test_heuristic_page_publishes_no_editable_text_even_when_ocr_passes_agree():
    image, words, lines = _page()
    plan = structured_plan(image, words, lines, None)
    assert not [r for r in plan.regions if r.role in ("title", "body") and not r.editable_fallback]
    assert not [r for r in plan.regions if r.role == "table" and r.table_cells is not None]
    assert plan_is_lossless(plan, plan.words)
    assert any(r.group_id == "title" and 0 in r.word_indices for r in plan.regions)


def test_provider_failure_keeps_the_page_as_pixels_and_says_so():
    image, words, lines = _page()

    def failing(*_args):
        raise RuntimeError("provider down")

    plan = structured_plan(image, words, lines, failing)
    assert not [r for r in plan.regions if r.role in ("title", "body") and not r.editable_fallback]
    assert plan.advisories == ("heuristic_plan:structuring_failed:RuntimeError",)


class _Stub:
    model = "stub-vision"

    def __call__(self, system, parts, schema, name):
        if name == "line_transcripts":
            return {"lines": [{"id": "L1", "text": "매출 현황"}, {"id": "L2", "text": "생성형 AI가"}]}
        return {"title": ["L1"], "groups": [{"heading": [], "body": ["L2"]}], "screenshots": []}


def test_structured_path_still_publishes_agreed_text():
    image, words, lines = _page()
    plan = structured_plan(image, words, lines, _Stub())
    assert plan.structured_by == "stub-vision" and plan.advisories == ()
    title = [r for r in plan.regions if r.role == "title" and not r.editable_fallback]
    assert title and title[0].text == "매출 현황"
