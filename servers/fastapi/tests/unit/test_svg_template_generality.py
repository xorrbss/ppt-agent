"""Template registration on decks that are not the first real deck it was built on.

Synthetic, fictional decks: titles in placeholders and under 18 pt, a header
without a coloured band, furniture saved by PowerPoint (creation ids, unwrapped
boxes measured per page), a full-slide rectangle a few EMU short, a deck whose
pages all share one layout.
"""

import re

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Emu, Pt

from services.svg_templates.frame import extract_template_package, representative_pages
from services.svg_templates.frame_composition import adapt_frame

_WIDTH, _HEIGHT = 12192000, 6858000
_TITLES = ("Company overview", "Market context", "Delivery plan", "Risk and budget")


def _text(shapes, text, left, top, width, size, color=(0, 0, 0), wrap=True):
    box = shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(500000))
    box.text_frame.auto_size = MSO_AUTO_SIZE.NONE
    box.text_frame.word_wrap = wrap
    run = box.text_frame.paragraphs[0].add_run()
    run.text, run.font.size, run.font.name = text, Pt(size), "Pretendard"
    run.font.color.rgb = RGBColor(*color)
    return box


def _blank(deck):
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    for placeholder in list(slide.placeholders):
        placeholder._element.getparent().remove(placeholder._element)
    return slide


def _band(slide, height=1300000):
    band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(_WIDTH), Emu(height))
    band.fill.solid()
    band.fill.fore_color.rgb = RGBColor(0, 64, 152)
    band.line.fill.background()


def _deck(tmp_path, build):
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(_WIDTH), Emu(_HEIGHT)
    for index in range(4):
        build(deck, index)
    path = tmp_path / "deck.pptx"
    deck.save(path)
    return path


def _texts(frame):
    return {element.dynamic_key or "fixed:" + "".join(re.findall(r">([^<>]+)<", element.svg)): element
            for element in frame.elements if element.kind == "text"}


def test_a_title_under_18_pt_is_dynamic_and_a_small_label_is_not(tmp_path):
    def build(deck, index):
        slide = _blank(deck)
        _band(slide, 1100000)
        _text(slide.shapes, _TITLES[index], 457200, 350000, 9000000, 14, (255, 255, 255))
        _text(slide.shapes, f"REQ-00{index}", 9500000, 350000, 2000000, 8, (255, 255, 255))
        _text(slide.shapes, "Fictional Corp | Confidential", 457200, 6300000, 4000000, 10, (90, 90, 90))

    for frame in extract_template_package(_deck(tmp_path, build)).frames:
        assert frame.dynamic_fields == ("title",), frame.frame_id
        assert "fixed:Fictional Corp | Confidential" in _texts(frame)
        assert not any("REQ-" in key for key in _texts(frame))  # varying label stays page content


def test_a_bare_page_number_in_the_header_is_not_the_title(tmp_path):
    def build(deck, index):
        slide = _blank(deck)
        _text(slide.shapes, str(index + 1), 457200, 300000, 600000, 20)
        _text(slide.shapes, _TITLES[index], 1200000, 330000, 9000000, 16)

    for frame in extract_template_package(_deck(tmp_path, build)).frames:
        assert [element.dynamic_key for element in frame.elements if element.dynamic_key] == ["title"]
        assert "".join(re.findall(r">([^<>]+)<", _texts(frame)["title"].svg)) in _TITLES


def test_a_title_placeholder_and_a_title_on_white_are_dynamic(tmp_path):
    def build(deck, index):
        slide = deck.slides.add_slide(deck.slide_layouts[5])
        title = slide.shapes.title
        title.text = _TITLES[index]
        title.left, title.top, title.width, title.height = Emu(457200), Emu(300000), Emu(9000000), Emu(800000)
        for run in title.text_frame.paragraphs[0].runs:
            run.font.name, run.font.size = "Pretendard", Pt(28)
        _text(slide.shapes, "Fictional Corp", 457200, 6300000, 4000000, 10, (90, 90, 90))

    for frame in extract_template_package(_deck(tmp_path, build)).frames:
        assert frame.dynamic_fields == ("title",)
        assert "fixed:Fictional Corp" in _texts(frame)


def _saved_by_powerpoint(shape, creation_id):
    """The creation id PowerPoint writes into every shape's ``cNvPr`` (different per slide)."""
    a16 = "http://schemas.microsoft.com/office/drawing/2014/main"
    properties = shape._element.xpath(".//p:cNvPr")[0]
    extension_list = etree.SubElement(properties, "{http://schemas.openxmlformats.org/drawingml/2006/main}extLst")
    extension = etree.SubElement(extension_list, "{http://schemas.openxmlformats.org/drawingml/2006/main}ext",
                                 uri="{FF2B5EF4-FFF2-40B4-BE49-F238E27FC236}")
    etree.SubElement(extension, f"{{{a16}}}creationId", nsmap={"a16": a16}, id="{%08d-0000-0000-0000-000000000000}" % creation_id)


def _powerpoint_deck(tmp_path, *, short_background=False):
    def build(deck, index):
        slide = _blank(deck)
        if short_background:
            dark = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(_WIDTH - 240), Emu(_HEIGHT - 360))
            dark.fill.solid()
            dark.fill.fore_color.rgb = RGBColor(14, 17, 22)
            dark.line.fill.background()
            _saved_by_powerpoint(dark, index * 10)
        # A label whose unwrapped box was measured for a different text on every page.
        label = _text(slide.shapes, "ACME - QUARTERLY REPORT", 560880, 444240, 700000 + index * 90000, 8,
                      (255, 255, 255) if short_background else (0, 0, 0), wrap=False)
        _saved_by_powerpoint(label, index * 10 + 1)
        _text(slide.shapes, _TITLES[index], 560880, 900000, 6000000, 24, (255, 255, 255) if short_background else (0, 0, 0))
        _text(slide.shapes, f"Body {index}", 560880, 2400000, 6000000, 14, (255, 255, 255) if short_background else (0, 0, 0))
    return _deck(tmp_path, build)


def test_furniture_saved_by_powerpoint_repeats_despite_creation_ids_and_unwrapped_widths(tmp_path):
    for frame in extract_template_package(_powerpoint_deck(tmp_path)).frames:
        texts = _texts(frame)
        label = texts["fixed:ACME - QUARTERLY REPORT"]
        # The unwrapped box grew to the text its (substituted) face needs: nothing overflows.
        assert not any(issue.severity == "blocked" for issue in frame.issues), frame.issues
        assert label.bounds.width > 0
        assert "title" in texts and not any("Body" in key for key in texts)
        adapt_frame(frame, frame.source_canvas)


def test_a_full_slide_rectangle_a_few_emu_short_is_the_page_background(tmp_path):
    for frame in extract_template_package(_powerpoint_deck(tmp_path, short_background=True)).frames:
        backgrounds = [element for element in frame.elements if element.kind == "rect" and element.layer == "background"]
        assert any('fill="#0E1116"' in element.svg for element in backgrounds), [e.svg[:120] for e in frame.elements]


def test_a_footer_box_hanging_below_the_page_still_adapts(tmp_path):
    def build(deck, index):
        slide = _blank(deck)
        _text(slide.shapes, _TITLES[index], 457200, 300000, 9000000, 24)
        _text(slide.shapes, "Fictional Corp | Confidential", 457200, _HEIGHT - 150000, 4000000, 10, (90, 90, 90))

    for frame in extract_template_package(_deck(tmp_path, build)).frames:
        footer = _texts(frame)["fixed:Fictional Corp | Confidential"]
        assert footer.bounds.y + footer.bounds.height > frame.source_canvas.logical_size.height
        adapt_frame(frame, frame.source_canvas)


def test_a_deck_with_one_layout_analyses_body_pages_too():
    class _Context:
        @staticmethod
        def related(_part, _kind):
            return "ppt/slideLayouts/slideLayout1.xml"

    slides = [f"ppt/slides/slide{index}.xml" for index in range(1, 16)]
    assert representative_pages(_Context, slides) == (0, 5, 9, 14)
