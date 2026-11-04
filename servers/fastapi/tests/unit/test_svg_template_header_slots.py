"""Header title and path of a template page are filled per page, in the source's style.

The real proposal deck draws each page's title, a ``number | chapter › page``
path and a requirement ID in its header band. The title and the path are
dynamic text the generated deck fills (the requirement ID stays page content).
"""

import re

import pytest
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Emu, Pt

from services.svg_templates import chapters
from services.svg_templates.frame import extract_template_package
from services.svg_templates.frame_composition import render_frame_svg

_SVG = "{http://www.w3.org/2000/svg}"
_TITLES = ("회사 소개", "사업 이해", "수행 방안")
_PATHS = ("회사 › 1.1 일반 현황", "사업 › 2.1 배경", "수행 › 3.1 일정")


def _text(shapes, text, left, top, width, size, color=(255, 255, 255), runs=None):
    box = shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(600000))
    box.text_frame.auto_size = MSO_AUTO_SIZE.NONE
    paragraph = box.text_frame.paragraphs[0]
    for part in runs or [text]:
        run = paragraph.add_run()
        run.text, run.font.size, run.font.name = part, Pt(size), "Pretendard"
        run.font.color.rgb = RGBColor(*color)
    return box


def _deck(path, *, title_runs=False):
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    for index in range(3):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        for placeholder in list(slide.placeholders):
            placeholder._element.getparent().remove(placeholder._element)
        band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(12192000), Emu(1500000))
        band.fill.solid()
        band.fill.fore_color.rgb = RGBColor(0, 64, 152)
        band.line.fill.background()
        _text(slide.shapes, _TITLES[index], 457200, 900000, 9000000, 24,
              runs=[_TITLES[index][:2], _TITLES[index][2:]] if title_runs else None)
        group = slide.shapes.add_group_shape()
        _text(group.shapes, str(index + 1), 457200, 560000, 300000, 12)
        _text(group.shapes, _PATHS[index], 900000, 560000, 7000000, 12)
        _text(slide.shapes, f"요구사항 ID  REQ-00{index}", 8000000, 300000, 3000000, 8)
        _text(slide.shapes, "ACME Proposal", 457200, 6300000, 4000000, 11, color=(80, 80, 80))
    deck.save(path)
    return path


def _path_deck(path, paths):
    """One page per ``(number, path runs)``: a title and a number + path group in the header."""
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    for index, (number, runs) in enumerate(paths):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        for placeholder in list(slide.placeholders):
            placeholder._element.getparent().remove(placeholder._element)
        _text(slide.shapes, f"Page title {index}", 457200, 900000, 9000000, 24, color=(0, 0, 0))
        group = slide.shapes.add_group_shape()
        _text(group.shapes, number, 457200, 560000, 300000, 12, color=(0, 0, 0))
        _text(group.shapes, "".join(runs), 900000, 560000, 7000000, 12, color=(0, 0, 0), runs=runs)
    deck.save(path)
    return path


def test_a_number_and_text_group_alike_on_every_page_is_not_a_path(tmp_path):
    """A constant section label drawn alike on every page is not a path to overwrite.

    It is classified as it was before header paths were recognised: a
    slide-level group the frame does not hold, left as the page's own source.
    """
    package = extract_template_package(_path_deck(tmp_path / "deck.pptx", [("7", ["Section label"])] * 3))
    for frame, evidence in zip(package.frames, package.body_evidence, strict=True):
        assert not {"breadcrumb", "breadcrumb_number"} & set(frame.dynamic_fields), frame.frame_id
        assert "title" in frame.dynamic_fields
        assert not any(element.dynamic_key and _drawn(element) in {"7", "Section label"} for element in frame.elements)
        assert evidence.body_kinds == ("grpSp",)


def test_a_path_shared_by_pages_with_different_runs_is_still_dynamic(tmp_path):
    """Pages of one section share a path, and PowerPoint may split it into different runs.

    Neither the text-free slot key (page 2's runs give it a slot of its own)
    nor the repeat key (pages 1 and 3 repeat each other) can see that.
    """
    paths = [("1", ["Plan › Scope"]), ("1", ["Plan ", "› ", "Scope"]), ("1", ["Plan › Scope"]),
             ("2", ["Build › Schedule"])]
    package = extract_template_package(_path_deck(tmp_path / "deck.pptx", paths))
    assert len(package.frames) == 4
    for frame, evidence in zip(package.frames, package.body_evidence, strict=True):
        assert {"breadcrumb", "breadcrumb_number"} <= set(frame.dynamic_fields), frame.frame_id
        assert "grpSp" not in evidence.body_kinds  # the frame holds the path group


def _drawn(element):
    return "".join(re.findall(r">([^<>]+)<", element.svg))


def _texts(frame):
    return {element.dynamic_key or "fixed": "".join(re.findall(r">([^<>]+)<", element.svg))
            for element in frame.elements if element.kind == "text"}


def test_the_title_and_the_path_are_dynamic_and_the_requirement_id_is_page_content(tmp_path):
    package = extract_template_package(_deck(tmp_path / "deck.pptx"))
    assert len(package.frames) == 3
    for frame in package.frames:
        assert {"title", "breadcrumb", "breadcrumb_number"} <= set(frame.dynamic_fields), frame.frame_id
        texts = [element for element in frame.elements if element.kind == "text"]
        assert not any("요구사항" in "".join(re.findall(r">([^<>]+)<", element.svg)) for element in texts)
        assert sorted(element.dynamic_key for element in texts if element.dynamic_key) == [
            "breadcrumb", "breadcrumb_number", "title"]
        assert any("ACME Proposal" in "".join(re.findall(r">([^<>]+)<", element.svg)) for element in texts)
        # The title keeps the source face and size (24 pt on a 12192000 EMU slide).
        title = next(element for element in texts if element.dynamic_key == "title")
        size = float(etree.fromstring(title.svg.encode()).find(f".//{_SVG}text").get("font-size"))
        path = next(element for element in texts if element.dynamic_key == "breadcrumb")
        path_size = float(etree.fromstring(path.svg.encode()).find(f".//{_SVG}text").get("font-size"))
        assert size == pytest.approx(path_size * 2, rel=.02)


def test_a_multi_run_title_is_replaced_as_a_whole_in_its_first_run_style(tmp_path):
    package = extract_template_package(_deck(tmp_path / "deck.pptx", title_runs=True))
    for frame in package.frames:
        assert "title" in frame.dynamic_fields
        title = next(e for e in frame.elements if e.dynamic_key == "title")
        assert len(etree.fromstring(title.svg.encode()).findall(f".//{_SVG}text")) == 1


def test_a_page_fills_its_title_and_path(tmp_path):
    frame = extract_template_package(_deck(tmp_path / "deck.pptx")).frames[0]
    chapter = {"number": "Ⅲ", "title": "수행 방안"}
    values = {"title": "추진 일정", "page_number": "7", **chapters.header_values(frame, "추진 일정", chapter)}
    assert values["breadcrumb"] == "수행 방안 › 추진 일정" and values["breadcrumb_number"] == "3"
    svg = render_frame_svg(frame, dynamic_values=values)
    drawn = ["".join(node.itertext()) for node in etree.fromstring(svg.encode()).iter(f"{_SVG}text")]
    assert {"추진 일정", "수행 방안 › 추진 일정", "3"} <= set(drawn)
    assert not any("회사" in text for text in drawn)  # the source page's own title is gone


def test_a_page_without_a_chapter_draws_no_path_and_a_long_title_still_fits(tmp_path):
    frame = extract_template_package(_deck(tmp_path / "deck.pptx")).frames[0]
    values = {"title": "제목", "page_number": "1", **chapters.header_values(frame, "제목", None)}
    assert values["breadcrumb"] == "" and values["breadcrumb_number"] == ""
    drawn = ["".join(node.itertext()) for node in etree.fromstring(
        render_frame_svg(frame, dynamic_values=values).encode()).iter(f"{_SVG}text")]
    assert "제목" in drawn and not any("›" in text for text in drawn)

    long_title = "사업 추진 배경과 목적 그리고 기대 효과를 한 번에 설명하는 아주 긴 제목입니다 " * 3
    values = {"title": long_title, "page_number": "1", **chapters.header_values(frame, long_title, {"number": "Ⅰ", "title": "개요"})}
    root = etree.fromstring(render_frame_svg(frame, dynamic_values=values).encode())
    source_size = float(next(e for e in frame.elements if e.dynamic_key == "title" and e.kind == "text").svg
                        .split('font-size="')[1].split('"')[0])
    title = next(node for node in root.iter(f"{_SVG}text") if "사업" in "".join(node.itertext()))
    # Shrunk to at most 60 % of the source size and cut with an ellipsis; the page never fails.
    assert float(title.get("font-size")) == pytest.approx(source_size * .6, rel=.01)
    assert "".join(title.itertext()).endswith("…")


def test_chapter_numbers_map_to_their_position():
    assert [chapters.chapter_ordinal(value) for value in ("Ⅰ", "Ⅲ", "IV", "03", "5", "별첨")] == [1, 3, 4, 3, 5, None]


def test_a_picture_below_the_header_band_is_body_decoration_not_furniture(tmp_path):
    """The key-message bar's icon must not stay behind as a lone ghost when its bar is body."""
    from PIL import Image

    for name, colour in (("logo.png", (255, 255, 255)), ("icon.png", (0, 64, 152))):
        Image.new("RGB", (40, 40), colour).save(tmp_path / name)
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    for _ in range(3):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        for placeholder in list(slide.placeholders):
            placeholder._element.getparent().remove(placeholder._element)
        band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(12192000), Emu(1300000))
        band.fill.solid()
        band.fill.fore_color.rgb = RGBColor(0, 64, 152)
        band.line.fill.background()
        slide.shapes.add_picture(str(tmp_path / "logo.png"), Emu(500000), Emu(300000), Emu(300000), Emu(300000))
        slide.shapes.add_picture(str(tmp_path / "icon.png"), Emu(500000), Emu(1350000), Emu(300000), Emu(300000))
    path = tmp_path / "deck.pptx"
    deck.save(path)
    for frame in extract_template_package(path).frames:
        images = [element for element in frame.elements if element.kind == "image"]
        assert len(images) == 1 and images[0].bounds.y < 100, [e.bounds for e in images]
