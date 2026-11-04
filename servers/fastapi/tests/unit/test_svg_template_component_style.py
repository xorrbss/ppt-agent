"""A template's body component styles are measured at registration and passed as style only.

Synthetic decks only: numbered chips, outlined cards, a heading mark, a tinted panel, a table
and plain text at two sizes, drawn on several pages so they recur.
"""

import json
import re

import pytest
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Emu, Pt

from models.svg_template_frame import SvgTemplatePackage
from services.svg_templates import generation
from services.svg_templates.component_style import component_style_rules
from services.svg_templates.frame import extract_template_package

NAVY, BLUE, GREY, TINT = (0x1F, 0x2A, 0x5C), (0x00, 0x5E, 0xD0), (0xD5, 0xDB, 0xE1), (0xEE, 0xF2, 0xFB)


def _fill(shape, color, line=None):
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(*color)
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = RGBColor(*line)
        shape.line.width = Emu(9525)


def _text(shape, text, size, color, bold=False):
    frame = shape.text_frame
    frame.auto_size = MSO_AUTO_SIZE.NONE
    run = frame.paragraphs[0].add_run()
    run.text, run.font.size, run.font.name, run.font.bold = text, Pt(size), "Pretendard", bold
    run.font.color.rgb = RGBColor(*color)


def _deck(path, *, pages=3, chips_on=None):
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    for page in range(pages):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        for placeholder in list(slide.placeholders):
            placeholder._element.getparent().remove(placeholder._element)
        band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(12192000), Emu(700000))
        _fill(band, NAVY)
        mark = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(600000), Emu(1100000), Emu(50000), Emu(300000))
        _fill(mark, NAVY)
        heading = slide.shapes.add_textbox(Emu(700000), Emu(1050000), Emu(6000000), Emu(400000))
        _text(heading, f"Fictional heading {page}", 16, NAVY, bold=True)
        for index in range(3):
            left = Emu(600000 + index * 3600000)
            card = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, Emu(1700000), Emu(3300000), Emu(1600000))
            _fill(card, (255, 255, 255), line=GREY)
            if chips_on is None or page in chips_on:
                chip = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left + Emu(100000), Emu(1800000),
                                              Emu(380000), Emu(280000))
                _fill(chip, BLUE)
                _text(chip, f"0{index + 1}", 11, (255, 255, 255), bold=True)
            body = slide.shapes.add_textbox(left + Emu(100000), Emu(2200000), Emu(3000000), Emu(900000))
            _text(body, "Fictional body text for a card on this page.", 11, (0x4E, 0x5B, 0x6E))
        panel = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(600000), Emu(3500000), Emu(10800000), Emu(900000))
        _fill(panel, TINT)
        table = slide.shapes.add_table(3, 2, Emu(600000), Emu(4600000), Emu(6000000), Emu(1000000)).table
        for row in range(3):
            for column in range(2):
                cell = table.cell(row, column)
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(*(NAVY if row == 0 else (255, 255, 255)))
                cell.text = "Header" if row == 0 else "Cell"
                run = cell.text_frame.paragraphs[0].runs[0]
                run.font.size, run.font.name = Pt(10), "Pretendard"
                run.font.color.rgb = RGBColor(*((255, 255, 255) if row == 0 else NAVY))
    deck.save(path)
    return path


def _style(tmp_path, **kwargs):
    package = extract_template_package(_deck(tmp_path / "deck.pptx", **kwargs))
    return package, package.component_style


def _components(style):
    return {item.kind: item for item in style.components}


def test_recurring_body_components_are_measured_as_style(tmp_path):
    package, style = _style(tmp_path)
    assert style is not None and style.pages_sampled == 3
    found = _components(style)
    chip = found["number_chip"]
    assert (chip.shape, chip.fill, chip.stroke) == ("rounded", "#005ED0", "none")
    assert chip.text.colors == ("#FFFFFF",) and chip.text.weight == 700
    assert chip.count == 9 and chip.pages == 3 and chip.width_px and chip.height_px
    card = found["card"]
    assert (card.shape, card.fill, card.stroke, card.stroke_width_px) == ("rect", "#FFFFFF", "#D5DBE1", 1.0)
    assert card.width_px is None and card.height_px is None  # an area's size is layout, not style
    assert found["accent_bar"].fill == "#1F2A5C"
    assert found["panel"].fill == "#EEF2FB"
    assert style.tables[0].header_fill == "#1F2A5C" and style.tables[0].header_text.colors == ("#FFFFFF",)
    assert style.tables[0].body_fill == "#FFFFFF" and style.tables[0].body_text.colors == ("#1F2A5C",)
    sizes = [level.size_px for level in style.type_ladder]
    assert sizes == sorted(sizes, reverse=True) and len(sizes) >= 2
    assert style.type_ladder[0].weight == 700 and style.type_ladder[0].colors[0] == "#1F2A5C"
    # The header band is frame furniture, not a body component.
    assert all(item.fill != "#1F2A5C" or item.kind == "accent_bar" for item in style.components)
    # Style only: nothing in the stored vocabulary says where a component sits.
    dumped = json.dumps(style.model_dump(mode="json"))
    assert not re.search(r'"(x|y|left|top|bounds|position)"', dumped)
    assert package.model_dump(mode="json")["component_style"] == style.model_dump(mode="json")


def test_a_component_on_one_page_only_is_not_vocabulary(tmp_path):
    _, style = _style(tmp_path, chips_on={1})
    assert "number_chip" not in _components(style)
    assert "card" in _components(style)


def test_a_package_without_component_style_serializes_as_before(tmp_path):
    package, _ = _style(tmp_path)
    plain = package.model_copy(update={"component_style": None})
    assert "component_style" not in plain.model_dump(mode="json")
    assert SvgTemplatePackage.model_validate(plain.model_dump(mode="json")).component_style is None


def test_rules_describe_looks_raise_small_text_and_keep_layout_free(tmp_path):
    _, style = _style(tmp_path)
    stored = style.model_dump(mode="json")
    rules = component_style_rules(stored, body_floor=18.2)
    assert "the layout stays free" in rules and "never add one to imitate" in rules
    assert "Number chip" in rules and "fill #005ED0" in rules and "Table: header row fill #1F2A5C" in rules
    sizes = [float(value) for value in re.findall(r"text ([0-9.]+)px", rules)]
    assert sizes and min(sizes) >= 18.2
    assert not re.search(r"\b(x|y)=", rules)
    assert len(rules) < 2500
    # A chip around raised text grows with it.
    chip = _components(style)["number_chip"]
    shown = re.search(r"Number chip[^\n]*about ([0-9.]+)x([0-9.]+)px", rules)
    assert float(shown.group(1)) > chip.width_px
    assert component_style_rules(None) == "" and component_style_rules({}) == ""


@pytest.mark.parametrize("knob", [None, "off", "on"])
def test_author_rules_carry_the_styles_only_when_the_knob_is_on(tmp_path, monkeypatch, knob):
    package, style = _style(tmp_path)
    selection = {"mode": "template-redesign"}
    monkeypatch.setattr(generation, "template_selection", lambda snapshot: selection)
    monkeypatch.setattr(generation, "template_package", lambda value: package.model_dump(mode="json"))
    if knob is None:
        monkeypatch.delenv("SVG_TEMPLATE_COMPONENT_STYLE", raising=False)
    else:
        monkeypatch.setenv("SVG_TEMPLATE_COMPONENT_STYLE", knob)
    rules = generation.body_author_rules("BASE", {"composition_guidance": []}, body_floor=14.0)
    assert ("TEMPLATE COMPONENT STYLE" in rules) is (knob == "on")
    if knob == "on":
        assert rules.endswith(component_style_rules(style.model_dump(mode="json"), body_floor=14.0))
