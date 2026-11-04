"""Chapter tabs: detected per layout, planned per deck, filled per page."""

import asyncio
from types import SimpleNamespace

import pytest
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Emu, Pt

from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from services.svg_templates import chapters
from services.svg_templates.frame import extract_template_package
from utils.llm_calls.plan_template_chapters import parse_template_chapters


def _chapter_deck(path, labels=(("Ⅰ", "회사소개"), ("Ⅱ", "인력 투입"), ("Ⅲ", "수행전략"))):
    """One layout per chapter; each layout's tab names its chapter."""
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    layouts = list(deck.slide_layouts)[:len(labels)]
    for layout, (number, title) in zip(layouts, labels, strict=True):
        for placeholder in list(layout.placeholders):
            placeholder._element.getparent().remove(placeholder._element)
        slide = deck.slides.add_slide(layout)
        for placeholder in list(slide.placeholders):
            placeholder._element.getparent().remove(placeholder._element)
        for text, top in ((number, 50000), (title, 500000)):
            box = slide.shapes.add_textbox(Emu(457200), Emu(top), Emu(1400000), Emu(420000))
            box.text_frame.auto_size = MSO_AUTO_SIZE.NONE
            run = box.text_frame.paragraphs[0].add_run()
            run.text, run.font.size, run.font.name = text, Pt(11), "Pretendard"
            run.font.color.rgb = RGBColor(20, 20, 20)
            element = box._element
            element.getparent().remove(element)
            layout.shapes._spTree.append(element)
    deck.save(path)
    return path


def test_chapter_tabs_are_detected_across_chapter_layouts(tmp_path):
    package = extract_template_package(_chapter_deck(tmp_path / "deck.pptx"))
    for frame in package.frames:
        assert {"chapter_number", "chapter_title"} <= set(frame.dynamic_fields), frame.frame_id
    assert chapters.number_style(package.frames) == "roman-unicode"


def test_a_chapter_plan_must_cover_the_deck_in_order():
    content = {"chapters": [{"start": 1, "title": "제안 개요"}, {"start": 4, "title": "수행 방안"}]}
    assert parse_template_chapters(content, 8, max_title_chars=8, max_chapters=9) == [(0, "제안 개요"), (3, "수행 방안")]
    for bad in ({"chapters": []}, {"chapters": [{"start": 2, "title": "가"}]},
                {"chapters": [{"start": 1, "title": "가"}, {"start": 1, "title": "나"}]},
                {"chapters": [{"start": 1, "title": "가"}, {"start": 9, "title": "나"}]},
                {"chapters": [{"start": 1, "title": "아주 긴 장 이름입니다"}]}):
        with pytest.raises(ValueError, match="svg_template_chapter_plan_invalid"):
            parse_template_chapters(bad, 8, max_title_chars=8, max_chapters=9)


def test_numbers_follow_the_source_style():
    assert [chapters.format_number(i, "roman-unicode") for i in range(3)] == ["Ⅰ", "Ⅱ", "Ⅲ"]
    assert chapters.format_number(1, "zero-padded") == "02" and chapters.format_number(1, "arabic") == "2"
    assert chapters.format_number(3, "roman-ascii") == "IV"


def test_every_page_gets_its_chapter_and_a_saved_page_keeps_it(tmp_path):
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content=f"# 쪽 {i}") for i in range(5)])
    titles = chapters.assign_chapters(outline, [(0, "제안 개요"), (2, "수행 방안")], "roman-unicode")
    assert titles == {"Ⅰ": "제안 개요", "Ⅱ": "수행 방안"}
    assert [slide.chapter["number"] for slide in outline.slides] == ["Ⅰ", "Ⅰ", "Ⅱ", "Ⅱ", "Ⅱ"]
    # Outlines without chapters keep their old JSON (and digests).
    assert "chapter" not in SlideOutlineModel(content="x").model_dump()

    package = extract_template_package(_chapter_deck(tmp_path / "deck.pptx"))
    frame = package.frames[0]
    values = chapters.chapter_values(frame, outline.slides[2].chapter)
    assert values == {"chapter_number": "Ⅱ", "chapter_title": "수행 방안"}
    with pytest.raises(ValueError, match="svg_template_chapter_missing"):
        chapters.chapter_values(frame, None)
    assert chapters.chapter_from_context({"dynamic_values": values}) == {"number": "Ⅱ", "title": "수행 방안"}
    assert chapters.chapter_from_context({"dynamic_values": {"title": "x"}}) is None


def test_a_chapter_name_that_does_not_fit_the_tab_is_reported(tmp_path):
    package = extract_template_package(_chapter_deck(tmp_path / "deck.pptx"))
    frames = [frame for frame in package.frames]
    assert not chapters.overlong_titles(frames, {"Ⅰ": "개요"})
    assert chapters.overlong_titles(frames, {"Ⅰ": "아주아주 긴 장 이름이 탭을 넘칩니다 정말로"}) == {"Ⅰ"}


def test_planning_retries_once_with_a_smaller_budget_then_fails(monkeypatch, tmp_path):
    package = extract_template_package(_chapter_deck(tmp_path / "deck.pptx"))
    monkeypatch.setattr(chapters, "chapter_frames", lambda selection: list(package.frames))
    calls = []

    async def plan(titles, *, max_title_chars, language):
        calls.append(max_title_chars)
        return [(0, "아주아주 긴 장 이름이 탭을 넘칩니다 정말로")]

    import utils.llm_calls.plan_template_chapters as planner
    monkeypatch.setattr(planner, "plan_template_chapters", plan)
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content="# 하나"), SlideOutlineModel(content="# 둘")])
    request = SimpleNamespace(_svg_template_selection={"mode": "template-redesign"}, language="Korean")
    with pytest.raises(ValueError, match="svg_template_chapter_title_too_long"):
        asyncio.run(chapters.plan_outline_chapters(request, outline))
    assert len(calls) == 2 and calls[1] < calls[0]

    async def short(titles, *, max_title_chars, language):
        return [(0, "개요"), (1, "방안")]

    monkeypatch.setattr(planner, "plan_template_chapters", short)
    asyncio.run(chapters.plan_outline_chapters(request, outline))
    assert [slide.chapter for slide in outline.slides] == [{"number": "Ⅰ", "title": "개요"},
                                                          {"number": "Ⅱ", "title": "방안"}]
