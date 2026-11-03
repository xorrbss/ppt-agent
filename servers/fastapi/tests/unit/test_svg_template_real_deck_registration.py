"""Registering a real deck: representative pages and explicit font substitution.

Design: docs/svg-template-real-deck-registration-design-20261103.md.
"""

import pytest
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Emu, Pt

from services.svg_templates.frame import extract_template_package


def _deck(path, *, pages=14, font="Pretendard"):
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    for number in range(1, pages + 1):
        layout = deck.slide_layouts[6 if number % 2 else 5]
        slide = deck.slides.add_slide(layout)
        for placeholder in list(slide.placeholders):
            placeholder._element.getparent().remove(placeholder._element)
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(12192000), Emu(640080))
        bar.fill.solid()
        bar.fill.fore_color.rgb = RGBColor(14, 61, 122)
        bar.line.fill.background()
        footer = slide.shapes.add_textbox(Emu(457200), Emu(6300000), Emu(4000000), Emu(400000)).text_frame
        footer.auto_size = MSO_AUTO_SIZE.NONE
        run = footer.paragraphs[0].add_run()
        run.text, run.font.size, run.font.name = "ACME Proposal", Pt(11), font
        run.font.color.rgb = RGBColor(80, 80, 80)
    deck.save(path)
    return path


def test_a_long_deck_is_analysed_through_its_representative_pages(tmp_path):
    package = extract_template_package(_deck(tmp_path / "deck.pptx", pages=14))
    # Page 1, then the first page of the other layout; original page numbers kept.
    assert [(frame.frame_id, frame.source_page) for frame in package.frames] == [("frame-1", 0), ("frame-2", 1)]


def test_explicit_pages_are_analysed_and_validated(tmp_path):
    path = _deck(tmp_path / "deck.pptx", pages=14)
    package = extract_template_package(path, source_pages=[9, 3])
    assert [frame.frame_id for frame in package.frames] == ["frame-3", "frame-9"]
    for pages in ([], [0], [15], [3, 3]):
        with pytest.raises(ValueError, match="svg_template_source_pages_invalid"):
            extract_template_package(path, source_pages=pages)


def test_a_short_deck_is_analysed_whole(tmp_path):
    package = extract_template_package(_deck(tmp_path / "deck.pptx", pages=4))
    assert [frame.source_page for frame in package.frames] == [0, 1, 2, 3]


def test_unavailable_fonts_block_until_explicitly_substituted(tmp_path):
    path = _deck(tmp_path / "deck.pptx", pages=3, font="Paperlogy 7 Bold")
    blocked = extract_template_package(path)
    assert any(issue.reason_code == "svg_template_font_unavailable"
               for frame in blocked.frames for issue in frame.issues)
    assert "font_substitutions" not in blocked.model_dump(mode="json")

    package = extract_template_package(path, font_substitutions=[
        {"source_family": "Paperlogy 7 Bold", "target_family": "Pretendard", "target_weight": 700},
        {"source_family": "Unused Font", "target_family": "Pretendard", "target_weight": 400}])
    assert not any(issue.reason_code == "svg_template_font_unavailable"
                   for frame in package.frames for issue in frame.issues)
    # Only substitutions that were applied are recorded.
    assert [(item.source_family, item.target_weight) for item in package.font_substitutions] == [
        ("Paperlogy 7 Bold", 700)]
    texts = [element for element in package.frames[0].elements if element.kind == "text"]
    assert texts and 'font-family="Pretendard"' in texts[0].svg and 'font-weight="700"' in texts[0].svg
    with pytest.raises(ValueError, match="svg_template_font_substitution_invalid"):
        extract_template_package(path, font_substitutions=[
            {"source_family": "A", "target_family": "Pretendard", "target_weight": 400},
            {"source_family": "a", "target_family": "Pretendard", "target_weight": 700}])
