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
MESSAGE = (0xE4, 0xEC, 0xF8)


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
        panel = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(600000), Emu(3400000), Emu(10800000), Emu(900000))
        _fill(panel, TINT)
        # A key-message bar whose words sit in a separate text box drawn over it.
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(600000), Emu(4450000), Emu(10800000), Emu(400000))
        _fill(bar, MESSAGE)
        words = slide.shapes.add_textbox(Emu(700000), Emu(4470000), Emu(9000000), Emu(360000))
        _text(words, "Fictional key message", 13, NAVY, bold=True)
        table = slide.shapes.add_table(3, 2, Emu(600000), Emu(4950000), Emu(6000000), Emu(600000)).table
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
    bar = found["bar"]
    assert (bar.fill, bar.text.colors, bar.text.weight) == ("#E4ECF8", ("#1F2A5C",), 700)
    # The bar's words describe the bar, not the plain body text ladder.
    assert all(abs(level.size_px - bar.text.size_px) > .1 for level in style.type_ladder)
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
    assert "Number chip" in rules and "fill #005ED0" in rules and "Table (every cell its own opaque filled rect): header row fill #1F2A5C" in rules
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
    monkeypatch.setenv("SVG_TEMPLATE_BODY_EXEMPLARS", "off")  # this test is about component style only
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


def _level(size, weight, color, share):
    return {"size_px": size, "weight": weight, "colors": [color], "share": share}


def _component(kind, shape, fill, **extra):
    return {"kind": kind, "shape": shape, "fill": fill, "stroke": "none", "stroke_width_px": 0.0, "radius_px": 0.0,
            "count": 5, "pages": 3, **extra}


def _stored(**extra):
    """A synthetic stored component style: a source whose body text is set below the floor."""
    return {"pages_sampled": 6, "type_ladder": [
        _level(24, 700, "#102030", .08), _level(17, 700, "#102030", .14), _level(14, 700, "#102030", .05),
        _level(12.5, 400, "#405060", .2), _level(11, 400, "#405060", .4), _level(9, 400, "#708090", .13)],
        "components": [
            _component("label", "rect", "#102030", height_px=24.0,
                       text={"size_px": 11, "weight": 700, "colors": ["#FFFFFF"]}, pages=5),
            _component("label", "pill", "#ABCDEF", height_px=20.0,
                       text={"size_px": 10, "weight": 400, "colors": ["#102030"]}),
            _component("step", "chevron", "#0055CC", height_px=30.0,
                       text={"size_px": 12, "weight": 700, "colors": ["#FFFFFF"]}),
            _component("panel", "rect", "#F0F2F4"), _component("panel", "rect", "#E0ECF8")],
        "tables": [], **extra}


def _ladder_line(rules):
    return next(line for line in rules.splitlines() if line.startswith("- Body text levels"))


def test_ladder_gives_heading_levels_and_body_and_drops_captions_below_the_floor():
    rules = component_style_rules(_stored(), body_floor=14.2)
    ladder = _ladder_line(rules)
    # Largest first: the title level, the sub-heading, then the body (most characters) at the floor.
    assert re.findall(r"(title|sub-heading|body|caption) ([0-9.]+)px", ladder) == [
        ("title", "24"), ("sub-heading", "17"), ("body", "14.5")]
    assert "body 14.5px weight 400 #405060" in ladder
    # The 9, 11, 12.5 and 14 px source sizes are neither inflated into extra levels nor listed.
    assert "caption" not in ladder and "No text smaller than the body" in ladder


def test_ladder_keeps_a_caption_the_floor_allows():
    rules = component_style_rules(_stored(), body_floor=8.0)
    levels = re.findall(r"(title|sub-heading|body|caption) ([0-9.]+)px", _ladder_line(rules))
    # A lighter level between body and sub-heading (12.5 px) is a body variant, not a heading.
    assert levels == [("title", "24"), ("title", "17"), ("sub-heading", "14"), ("body", "11"), ("caption", "9")]
    assert "No text smaller than the caption" in rules


def test_one_look_per_component_kind_the_most_used_first():
    rules = component_style_rules(_stored(), body_floor=14.2)
    assert rules.count("- Label or tag:") == 1 and "fill #102030" in rules and "#ABCDEF" not in rules
    assert rules.count("- Tinted panel") == 1 and "#F0F2F4" in rules and "#E0ECF8" not in rules


def test_a_sequence_is_asked_for_as_separate_gapped_steps():
    step = next(line for line in component_style_rules(_stored(), body_floor=14.2).splitlines()
                if line.startswith("- Step shape"))
    assert "one chevron per step" in step and "small gap" in step and "never one long bar" in step
    # The step grows with its raised text (12 -> 14.5 px).
    assert "text 14.5px" in step and "about 36px high" in step


def test_rules_stay_within_the_budget_and_keep_the_layout_free(monkeypatch):
    from services.svg_templates import component_style
    full = component_style_rules(_stored(), body_floor=14.2)
    assert len(full) <= component_style.RULES_BUDGET_CHARS
    monkeypatch.setattr(component_style, "RULES_BUDGET_CHARS", len(full) - 1)
    cut = component_style_rules(_stored(), body_floor=14.2)
    assert len(cut) < len(full)
    # The least telling line goes first; the ladder and the free-layout sentence always stay.
    assert "Tinted panel" not in cut and "Step shape" in cut
    assert "the layout stays free" in cut and "use a component only when the content calls for it" in cut
    assert "- Body text levels" in cut


def test_a_source_table_cell_without_fill_is_asked_for_filled():
    table = {"header_fill": "#102030", "body_fill": "none", "row_header_fill": "#E0ECF8", "rule_color": "#C0C8D0",
             "body_text": {"size_px": 11, "weight": 400, "colors": ["#202020"]}, "count": 4, "pages": 3}
    line = next(item for item in component_style_rules(_stored(tables=[table]), body_floor=14.2).splitlines()
                if item.startswith("- Table"))
    # A native table needs an opaque fill under every cell; "without fill" over a raster frame was rejected.
    assert "without fill" not in line and "every cell its own opaque filled rect" in line
    assert "body cells fill the body background colour (template_page.body_background.mean_color)" in line
    assert "first column fill #E0ECF8" in line and "cell text 14.5px" in line
