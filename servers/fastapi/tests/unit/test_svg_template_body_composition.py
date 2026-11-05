"""A template's page composition grammar and body exemplars: measured at registration, no source text.

Synthetic decks only: a key-message bar under the header, numbered sub-headings, a step sequence
with arrows, cards and a table on each body page, all with fictional words.
"""

import json
import re

import pytest
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from models.svg_template_frame import SvgTemplateBodyComposition, SvgTemplatePackage
from services.svg_templates import body_composition as composition
from services.svg_templates import generation
from services.svg_templates.frame import extract_template_package

NAVY, BLUE, GREY, MESSAGE = (0x1F, 0x2A, 0x5C), (0x00, 0x5E, 0xD0), (0xD5, 0xDB, 0xE1), (0xE4, 0xEC, 0xF8)
#: Words drawn on the synthetic pages; none may reach the stored composition or a prompt.
SECRET_WORDS = ("Quokkafield", "Zephyrine", "Marmalade", "Obsidian", "Halvorsen")


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


def _arrow(slide, x1, y1, x2, y2):
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Emu(x1), Emu(y1), Emu(x2), Emu(y2))
    line.line.color.rgb = RGBColor(*NAVY)
    ln = line.line._get_or_add_ln()
    tail = ln.makeelement(qn("a:tailEnd"), {"type": "triangle"})
    ln.append(tail)


def _deck(path, *, pages=3):
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    for page in range(pages):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        for placeholder in list(slide.placeholders):
            placeholder._element.getparent().remove(placeholder._element)
        band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(12192000), Emu(700000))
        _fill(band, NAVY)
        # Key-message bar right under the header.
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(600000), Emu(1000000), Emu(10800000), Emu(380000))
        _fill(bar, MESSAGE)
        _text(bar, f"{SECRET_WORDS[0]} key message {page}", 14, NAVY, bold=True)
        # Section 01: numbered sub-heading, then four steps joined by arrows.
        for number, top in (("01", 1500000), ("02", 3300000)):
            chip = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(600000), Emu(top), Emu(380000), Emu(260000))
            _fill(chip, BLUE)
            _text(chip, number, 11, (255, 255, 255), bold=True)
            heading = slide.shapes.add_textbox(Emu(1050000), Emu(top - 20000), Emu(6000000), Emu(300000))
            _text(heading, f"{SECRET_WORDS[1]} section heading", 15, NAVY, bold=True)
        for index in range(4):
            left = 600000 + index * 2750000
            step = slide.shapes.add_shape(MSO_SHAPE.CHEVRON, Emu(left), Emu(1900000), Emu(2500000), Emu(400000))
            _fill(step, BLUE)
            _text(step, f"{SECRET_WORDS[2]} {index}", 11, (255, 255, 255), bold=True)
            body = slide.shapes.add_textbox(Emu(left), Emu(2350000), Emu(2500000), Emu(700000))
            _text(body, f"{SECRET_WORDS[3]} detail for step {index} on this page", 11, (0x4E, 0x5B, 0x6E))
            if index:
                _arrow(slide, left - 240000, 2100000, left - 20000, 2100000)
        # Section 02: three outlined cards beside a table.
        for index in range(3):
            card = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(600000 + index * 1900000), Emu(3700000),
                                          Emu(1800000), Emu(1700000))
            _fill(card, (255, 255, 255), line=GREY)
            _text(card, f"{SECRET_WORDS[4]} card text {index}", 11, NAVY)
        table = slide.shapes.add_table(4, 3, Emu(6400000), Emu(3700000), Emu(5000000), Emu(1700000)).table
        for row in range(4):
            for column in range(3):
                cell = table.cell(row, column)
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(*(NAVY if row == 0 else (255, 255, 255)))
                cell.text = f"{SECRET_WORDS[row % 5]} {column}"
                run = cell.text_frame.paragraphs[0].runs[0]
                run.font.size, run.font.name = Pt(10), "Pretendard"
                run.font.color.rgb = RGBColor(*((255, 255, 255) if row == 0 else NAVY))
    deck.save(path)
    return path


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    return extract_template_package(_deck(tmp_path_factory.mktemp("deck") / "deck.pptx"))


def test_grammar_measures_how_the_body_pages_are_composed(package):
    stored = package.body_composition
    assert stored is not None
    grammar = stored.grammar
    assert grammar.pages_sampled == 3
    assert grammar.key_message_bar == 1.0 and grammar.numbered_headings == 1.0
    assert grammar.sections[1] == 2
    assert grammar.content_kinds_per_page[1] >= 2
    shares = dict(grammar.kind_shares)
    assert shares["table"] == 1.0 and shares["flow"] == 1.0 and shares["cards"] == 1.0
    # 1 bar + 2 chips + 2 headings + 4 steps + 4 details + 3 cards + 12 table cells.
    assert grammar.text_items[1] == 28
    assert 0 < grammar.ink_coverage <= 1 and 0.5 < grammar.bottom_reach <= 1.0
    big, usual, small = grammar.text_sizes
    assert big >= usual >= small > 0


def test_exemplars_outline_a_page_without_any_source_text(package):
    stored = package.body_composition
    assert len(stored.exemplars) == 1  # three identical pages count once
    exemplar = stored.exemplars[0]
    assert set(exemplar.kinds) >= {"table", "flow", "cards"} and exemplar.sections == 2
    kinds = [item.kind for item in exemplar.items]
    assert kinds.count("step") == 4 and kinds.count("arrow") == 3 and kinds.count("table") == 1
    assert kinds.count("chip") == 2 and "bar" in kinds
    # Boxes are relative to the body region and inside it.
    assert all(item.x >= 0 and item.y >= 0 and item.x + item.w <= exemplar.region_width + 1
               and item.y + item.h <= exemplar.region_height + 1 for item in exemplar.items)
    table = next(item for item in exemplar.items if item.kind == "table")
    assert (table.rows, table.cols, table.header_fill) == (4, 3, "#1F2A5C")
    dumped = json.dumps(package.model_dump(mode="json")["body_composition"], ensure_ascii=False)
    for word in SECRET_WORDS + ("key message", "detail for", "section heading", "card text"):
        assert word not in dumped
    # Every stored string is a kind, a literal or a colour: no field can carry words.
    strings = re.findall(r'"([^"]*)"', dumped)
    values = {value for value in strings if not re.fullmatch(r"[a-z_]+", value)}
    assert all(re.fullmatch(r"#[0-9A-F]{6}|svg-template-body-composition/v1", value) for value in values)


def test_rendered_exemplar_and_rules_carry_no_source_text(package, monkeypatch):
    stored = package.body_composition.model_dump(mode="json")
    rendered = composition.page_exemplars(stored, "## Plan\n- step one -> step two -> step three\n| a | b |\n| 1 | 2 |",
                                          floor=14.0)
    rules = composition.composition_rules(stored, body_floor=14.0)
    for text in (*rendered, rules):
        for word in SECRET_WORDS:
            assert word not in text
    assert rendered and "step " in rendered[0] and "table " in rendered[0] and "number" in rendered[0]


def test_a_package_without_body_composition_serializes_as_before(package):
    plain = package.model_copy(update={"body_composition": None})
    assert "body_composition" not in plain.model_dump(mode="json")
    assert SvgTemplatePackage.model_validate(plain.model_dump(mode="json")).body_composition is None
    stored = package.model_dump(mode="json")
    assert SvgTemplatePackage.model_validate(stored).body_composition == package.body_composition


@pytest.mark.parametrize("content, expected", [
    ("## T\n| a | b |\n| --- | --- |\n| 1 | 2 |", {"table"}),
    ("## T\n- 매출: 1월 120억 원, 2월 135억 원, 3월 150억 원, 4월 171억 원", {"chart"}),
    ("## T\n- 재고 오차율 현재 4.2%에서 목표 1.0%로\n- 장애 건수 목표 4건, 달성 시 30% 감소", {"kpi"}),
    ("## T\n1. 분석\n2. 설계\n3. 구축\n4. 시험", {"flow"}),
    ("## T\n- PMO 아래 PM과 팀장의 역할을 나눈 조직", {"hierarchy"}),
    ("## T\n- one point\n- another point\n- a third point", {"cards"}),
    ("## T\nA short paragraph of plain prose from 2027.", {"list"}),
])
def test_outline_pages_are_classified_by_cheap_signals(content, expected):
    kinds, sections = composition.content_kinds(content)
    assert expected <= set(kinds)
    assert sections >= 1
    if expected == {"list"}:
        assert kinds == ("list",)


def _exemplar(page, kinds, sections=2, text_items=40):
    return {"source_page": page, "region_width": 1000, "region_height": 600, "kinds": list(kinds),
            "sections": sections, "text_items": text_items,
            "items": [{"kind": "text", "x": 0, "y": 0, "w": 400, "h": 20,
                       "text": {"size_px": 12, "weight": 400, "color": "#222222", "paragraphs": 1, "chars": 30}}]}


def test_exemplars_are_matched_to_the_page_content_deterministically():
    stored = {"exemplars": [_exemplar(1, ["table", "chart", "hierarchy", "kpi", "cards"]),
                            _exemplar(2, ["table", "cards"]), _exemplar(3, ["flow", "cards"]),
                            _exemplar(4, ["cards"])]}
    table_page = "## T\n| a | b |\n| --- | --- |\n| 1 | 2 |\n- x\n- y\n- z"
    chosen = composition.choose_exemplars(stored, table_page)
    assert [item["source_page"] for item in chosen] == [2]  # no unneeded chart/hierarchy page
    both = composition.choose_exemplars(stored, table_page + "\n1. a\n2. b\n3. c")
    assert [item["source_page"] for item in both] == [2, 3]  # the second shows the kind the first lacks
    assert composition.choose_exemplars(stored, table_page) == chosen
    assert composition.choose_exemplars(None, table_page) == []
    assert composition.choose_exemplars({"exemplars": []}, table_page) == []


def test_render_raises_small_text_to_the_floor_and_keeps_to_the_budget():
    exemplar = _exemplar(5, ["cards"])
    line = composition.render_exemplar(exemplar, floor=18.2)
    assert "18.5/400 #222222 12ch" in line  # 30 characters at 12px fit as 12 at 18.5px
    many = dict(exemplar, items=[dict(exemplar["items"][0], y=index) for index in range(120)]
                + [{"kind": "divider", "x": 0, "y": 5, "w": 100, "h": 0, "stroke": "#CCCCCC"}])
    cut = composition.render_exemplar(many, floor=0, budget=400)
    assert composition.estimate_tokens(cut) <= 400 and re.search(r"\(\+\d+ more items\)$", cut)
    assert "divider" not in cut


def _stub_selection(monkeypatch, package, role="body"):
    monkeypatch.setattr(generation, "template_selection", lambda snapshot: {"mode": "template-redesign"})
    monkeypatch.setattr(generation, "template_package", lambda value: package.model_dump(mode="json"))

    class Frame:
        pass
    frame = Frame()
    frame.role = role
    monkeypatch.setattr(generation, "page_frame", lambda snapshot, index, count, role=None: frame)


@pytest.mark.parametrize("knob", [None, "off", "grammar", "on"])
def test_the_knob_decides_what_the_author_sees(package, monkeypatch, knob):
    _stub_selection(monkeypatch, package)
    monkeypatch.delenv("SVG_TEMPLATE_COMPONENT_STYLE", raising=False)
    if knob is None:
        monkeypatch.delenv("SVG_TEMPLATE_BODY_EXEMPLARS", raising=False)
    else:
        monkeypatch.setenv("SVG_TEMPLATE_BODY_EXEMPLARS", knob)
    rules = generation.body_author_rules("BASE", {"composition_guidance": []}, body_floor=14.0)
    shown = "on" if knob is None else knob  # unset means on (the measured default)
    assert ("TEMPLATE PAGE COMPOSITION" in rules) is (shown in {"grammar", "on"})
    assert ("template_exemplars, when given" in rules) is (shown == "on")
    context = {"page_index": 1, "page_count": 3}
    facts = generation.template_exemplar_facts({}, context, "## T\n1. a\n2. b\n3. c", body_floor=14.0)
    assert bool(facts.get("template_exemplars")) is (shown == "on")


def test_no_exemplars_for_a_cover_a_source_preserving_page_or_an_old_template(package, monkeypatch):
    monkeypatch.setenv("SVG_TEMPLATE_BODY_EXEMPLARS", "on")
    context = {"page_index": 0, "page_count": 3}
    _stub_selection(monkeypatch, package, role="cover")
    assert generation.template_exemplar_facts({}, context, "## T\n- a") == {}
    _stub_selection(monkeypatch, package)
    assert generation.template_exemplar_facts({}, context, "## T\n- a", source_preserved=True) == {}
    assert generation.template_exemplar_facts({}, None, "## T\n- a") == {}
    _stub_selection(monkeypatch, package.model_copy(update={"body_composition": None}))
    assert generation.template_exemplar_facts({}, context, "## T\n- a") == {}
    assert "TEMPLATE PAGE COMPOSITION" not in generation.body_author_rules(
        "BASE", {"composition_guidance": []}, body_floor=14.0)


def test_rules_keep_the_layout_free_and_raise_sizes(package):
    rules = composition.composition_rules(package.body_composition.model_dump(mode="json"), body_floor=18.2)
    assert "not a layout to copy" in rules and "never copy their positions" in rules
    assert "Never invent facts" in rules
    sizes = [float(value) for value in re.findall(r"([0-9.]+)px", rules)]
    assert sizes and min(sizes) >= 18.5
    assert composition.composition_rules(None) == ""
    assert SvgTemplateBodyComposition.model_validate(package.body_composition.model_dump(mode="json"))
