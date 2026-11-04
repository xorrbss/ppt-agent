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


# --- fixed text the real deck needs ------------------------------------------------

from copy import deepcopy  # noqa: E402

from lxml import etree  # noqa: E402

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"


class _LayoutShapes:
    """Adds shapes on the slide (python-pptx cannot) and moves them to the layout."""

    def __init__(self, slide, layout):
        self.slide, self.layout = slide, layout

    def _move(self, shape):
        element = shape._element
        element.getparent().remove(element)
        self.layout.shapes._spTree.append(element)
        return shape

    def add_textbox(self, *args):
        return self._move(self.slide.shapes.add_textbox(*args))

    def add_shape(self, *args):
        return self._move(self.slide.shapes.add_shape(*args))

    def add_group_shape(self):
        return self._move(self.slide.shapes.add_group_shape())


def _layout_deck(path, mutate):
    """One-slide deck whose layout carries a header text box the test mutates."""
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    layout = deck.slide_layouts[6]
    slide = deck.slides.add_slide(layout)
    for placeholder in list(slide.placeholders):
        placeholder._element.getparent().remove(placeholder._element)
    shapes = _LayoutShapes(slide, layout)
    box = shapes.add_textbox(Emu(457200), Emu(100000), Emu(6000000), Emu(500000))
    box.text_frame.auto_size = MSO_AUTO_SIZE.NONE
    run = box.text_frame.paragraphs[0].add_run()
    run.text, run.font.size, run.font.name = "Header text", Pt(12), "Pretendard"
    run.font.color.rgb = RGBColor(20, 20, 20)
    mutate(shapes, box)
    deck.save(path)
    return path


def _texts(package):
    return [element for element in package.frames[0].elements if element.kind == "text"]


def test_shrink_on_overflow_text_is_drawn_at_its_stored_scale(tmp_path):
    def mutate(layout, box):
        body = box.text_frame._txBody.bodyPr
        for child in list(body):
            body.remove(child)
        etree.SubElement(body, f"{{{_A}}}normAutofit", fontScale="50000")
    package = extract_template_package(_layout_deck(tmp_path / "deck.pptx", mutate))
    text = _texts(package)[0]
    size = float(etree.fromstring(text.svg.encode()).find(".//{http://www.w3.org/2000/svg}text").get("font-size"))
    assert abs(size - 12 * 0.5 * 12700 / package.source_canvas.emu_per_unit) < 1e-6


def test_empty_bullet_formatting_and_tab_list_do_not_block(tmp_path):
    def mutate(layout, box):
        paragraph = box.text_frame.paragraphs[0]._p
        ppr = paragraph.get_or_add_pPr()
        for name in ("buClrTx", "buSzTx", "buFontTx", "buNone", "tabLst"):
            etree.SubElement(ppr, f"{{{_A}}}{name}")
    package = extract_template_package(_layout_deck(tmp_path / "deck.pptx", mutate))
    assert _texts(package) and not any(issue.severity == "blocked" and "text" in issue.reason_code
                                       for issue in package.frames[0].issues)


def test_a_slide_number_field_is_the_page_number_and_spacing_is_kept(tmp_path):
    def mutate(layout, box):
        paragraph = box.text_frame.paragraphs[0]._p
        run = paragraph.find(f"{{{_A}}}r")
        field = etree.SubElement(paragraph, f"{{{_A}}}fld", id="{25011FFF-28D7-4E6D-888C-DFA47697978A}", type="slidenum")
        field.append(deepcopy(run.find(f"{{{_A}}}rPr")))
        etree.SubElement(field, f"{{{_A}}}t").text = "‹#›"
        paragraph.remove(run)
        field.find(f"{{{_A}}}rPr").set("spc", "-150")
        body = box.text_frame._txBody.bodyPr
        for child in list(body):
            body.remove(child)
        etree.SubElement(body, f"{{{_A}}}spAutoFit")
    package = extract_template_package(_layout_deck(tmp_path / "deck.pptx", mutate))
    assert package.frames[0].dynamic_fields == ("page_number",)
    node = etree.fromstring(_texts(package)[0].svg.encode()).find(".//{http://www.w3.org/2000/svg}text")
    assert float(node.get("letter-spacing")) == pytest.approx(-1.5 * 12700 / package.source_canvas.emu_per_unit, abs=1e-3)


def test_text_inside_a_group_stays_text_at_its_slide_position(tmp_path):
    def mutate(layout, box):
        group = layout.add_group_shape()
        moved = box._element
        moved.getparent().remove(moved)
        group._element.append(moved)
        xfrm = group._element.find(f"{{{_P}}}grpSpPr/{{{_A}}}xfrm")
        for child in list(xfrm):
            xfrm.remove(child)
        # Child space 0..1000 maps to slide EMU 457200.. at scale x2.
        etree.SubElement(xfrm, f"{{{_A}}}off", x="457200", y="100000")
        etree.SubElement(xfrm, f"{{{_A}}}ext", cx="12000000", cy="1000000")
        etree.SubElement(xfrm, f"{{{_A}}}chOff", x="457200", y="100000")
        etree.SubElement(xfrm, f"{{{_A}}}chExt", cx="6000000", cy="500000")
        box_xfrm = moved.find(f"{{{_P}}}spPr/{{{_A}}}xfrm")
        box_xfrm.find(f"{{{_A}}}off").set("x", "457200")
        box_xfrm.find(f"{{{_A}}}off").set("y", "100000")
    package = extract_template_package(_layout_deck(tmp_path / "deck.pptx", mutate))
    text = _texts(package)[0]
    scale = package.source_canvas.emu_per_unit
    assert text.bounds.x == pytest.approx(457200 / scale) and text.bounds.width == pytest.approx(12000000 / scale)


def test_preset_colours_and_rounded_rectangles_are_native(tmp_path):
    def mutate(layout, box):
        shape = layout.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(0), Emu(6300000), Emu(2000000), Emu(400000))
        fill = shape._element.find(f"{{{_P}}}spPr")
        for child in list(fill):
            if etree.QName(child).localname in {"solidFill", "noFill"}:
                fill.remove(child)
        solid = etree.Element(f"{{{_A}}}solidFill")
        etree.SubElement(solid, f"{{{_A}}}prstClr", val="dkBlue")
        fill.insert(2, solid)
        shape.line.fill.background()
        style = shape._element.find(f"{{{_P}}}style")
        if style is not None:
            shape._element.remove(style)
    package = extract_template_package(_layout_deck(tmp_path / "deck.pptx", mutate))
    rects = [element for element in package.frames[0].elements if element.kind == "rect" and 'rx="' in element.svg]
    assert rects and 'fill="#00008B"' in rects[0].svg


def test_the_slide_contract_admits_bounded_letter_spacing():
    from utils.svg_validation import validate_svg
    page = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
            '<text x="1" y="20" font-family="Pretendard" font-size="12" fill="#111111" letter-spacing="{}">a</text></svg>')
    validate_svg(page.format("-1.5"), width=100, height=100)
    for value in ("-21", "1em", "normal"):
        with pytest.raises(ValueError, match="svg_contract_invalid_letter_spacing"):
            validate_svg(page.format(value), width=100, height=100)


# --- explicit font substitution: suggestion and revision --------------------------

def test_unavailable_fonts_are_listed_with_a_suggested_substitute(tmp_path):
    from services.svg_templates.frame import suggested_substitute_weight
    assert [suggested_substitute_weight(name, 400) for name in (
        "Paperlogy 3 Light", "Paperlogy 5 Medium", "Paperlogy 6 SemiBold", "Paperlogy 8 ExtraBold")] == [400, 400, 700, 700]
    assert suggested_substitute_weight("Acme Sans", 700) == 700 and suggested_substitute_weight("Acme 12", 400) == 400

    path = _deck(tmp_path / "deck.pptx", pages=3, font="Paperlogy 7 Bold")
    blocked = extract_template_package(path)
    assert [(item.source_family, item.suggested_family, item.suggested_weight) for item in blocked.unavailable_fonts] == [
        ("Paperlogy 7 Bold", "Pretendard", 700)]
    substituted = extract_template_package(path, font_substitutions=[
        {"source_family": "Paperlogy 7 Bold", "target_family": "Pretendard", "target_weight": 700}])
    assert "unavailable_fonts" not in substituted.model_dump(mode="json")


def test_a_revision_replaces_or_keeps_the_substitution_map():
    from api.v1.ppt.endpoints.svg_templates import DraftRevisionRequest
    from services.svg_templates.registration import revised_registration_payload
    old = {"frame_reviews": {"frame-1": {"role": "cover"}}, "representative_frame_id": "frame-1",
           "font_substitutions": [{"source_family": "A", "target_family": "Pretendard", "target_weight": 400}]}
    swap = [{"source_family": "B", "target_family": "Pretendard", "target_weight": 700}]
    replaced = revised_registration_payload(old, frame_reviews={}, expected_head_revision=0, font_substitutions=swap)
    assert replaced["font_substitutions"] == swap and replaced["frame_reviews"] == old["frame_reviews"]
    kept = revised_registration_payload(old, frame_reviews={"frame-2": {"role": "body"}}, expected_head_revision=0)
    assert kept["font_substitutions"] == old["font_substitutions"] and set(kept["frame_reviews"]) == {"frame-1", "frame-2"}
    assert "font_substitutions" not in revised_registration_payload(old, frame_reviews={}, expected_head_revision=0,
                                                                    font_substitutions=[])
    request = DraftRevisionRequest.model_validate({"operationId": "00000000-0000-0000-0000-000000000001",
        "expectedHeadRevision": 0, "fontSubstitutions": [{"sourceFamily": "B", "targetFamily": "Pretendard",
                                                          "targetWeight": 700}]})
    assert request.frame_reviews == [] and request.font_substitutions[0].source_family == "B"


# --- canvas lock (D-4) ---------------------------------------------------------------

def test_template_redesign_is_locked_to_the_exact_source_canvas():
    from fastapi import HTTPException

    from services.document_canvas import document_canvas_from_source_slide_size
    from services.svg_canvas import custom_svg_canvas, svg_canvas_for_aspect_ratio
    from services.svg_templates.catalog import normalized_canvas, require_source_canvas

    source = document_canvas_from_source_slide_size(width_emu=10907713, height_emu=7775575)
    package = {"source_canvas": source.model_dump(mode="json", by_alias=True)}
    # 302.992 x 215.988 mm: only the EMU form names it exactly.
    exact = normalized_canvas({"svg_canvas": {"widthEmu": 10907713, "heightEmu": 7775575}})
    require_source_canvas(package, exact)
    assert normalized_canvas({"widthEmu": 10907713, "heightEmu": 7775575}) == exact
    for other in (custom_svg_canvas({"widthMm": "302.99", "heightMm": "215.99"}),
                  custom_svg_canvas({"widthMm": "297", "heightMm": "210"}), svg_canvas_for_aspect_ratio("16:9")):
        with pytest.raises(HTTPException) as error:
            require_source_canvas(package, other.model_dump(mode="json", by_alias=True))
        assert error.value.detail == "svg_template_canvas_locked"
    # A preset-sized template is requested through its preset.
    wide = document_canvas_from_source_slide_size(width_emu=12192000, height_emu=6858000)
    require_source_canvas({"source_canvas": wide.model_dump(mode="json", by_alias=True)},
                          svg_canvas_for_aspect_ratio("16:9").model_dump(mode="json", by_alias=True))


def test_the_exact_canvas_form_is_a_closed_alternative_to_millimetres():
    from pydantic import ValidationError

    from models.svg_canvas import SvgCanvasInput
    assert SvgCanvasInput.model_validate({"widthMm": "302.99", "heightMm": "215.99"}).model_dump(
        mode="json", by_alias=True) == {"widthMm": "302.99", "heightMm": "215.99"}
    for bad in ({"widthMm": "300", "heightMm": "200", "widthEmu": 10800000, "heightEmu": 7200000},
                {"widthEmu": 10907713}, {"widthEmu": "10907713", "heightEmu": 7775575},
                {"widthEmu": 100, "heightEmu": 7775575}, {"widthEmu": 36000000, "heightEmu": 1800000}):
        with pytest.raises(ValidationError):
            SvgCanvasInput.model_validate(bad)


def test_a_generation_request_carries_the_exact_canvas_through_durable_replay():
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.authored_canvas import request_canvas_metadata
    from services.document_canvas import document_canvas_from_source_slide_size
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload

    request = GeneratePresentationRequest(content="proposal", strategy="svg", n_slides=1, common_header_mode="free",
        svgCanvas={"widthEmu": 10907713, "heightEmu": 7775575},
        svgTemplatePin={"templateId": "canvas-test", "revision": 1, "digest": "sha256:" + "a" * 64},
        svgTemplateMode="template-redesign")
    import copy

    from utils.hashing import canonical_json_digest
    expected = document_canvas_from_source_slide_size(width_emu=10907713, height_emu=7775575)
    canvas = request_canvas_metadata(request)
    assert canvas == expected.model_dump(mode="json", by_alias=True)
    selection = {"schema_version": "svg-template-selection/v1",
                 "template_pin": request.svg_template_pin.model_dump(mode="json", by_alias=True),
                 "mode": request.svg_template_mode, "frame_map": {}, "canvas": canvas}
    selection["digest"] = canonical_json_digest(selection)
    request._svg_template_selection = copy.deepcopy(selection)
    request._svg_style_snapshot = {"svg_template": copy.deepcopy(selection)}
    restored = _request_from_durable_payload(_durable_request_payload(request))
    assert request_canvas_metadata(restored) == request_canvas_metadata(request)


def test_a_multi_run_fixed_line_exports_as_one_text_box(tmp_path):
    """A slogan with differently coloured runs is one line: one SVG text with
    tspans, one PPTX text box with several runs (no per-run boxes that a
    viewer's own metrics can make overlap)."""
    from zipfile import ZipFile

    from services.svg_templates.frame import adapt_template_frame
    from services.svg_templates.frame_composition import render_frame_svg
    from workers.public_svg_pptx_convert import convert_pptx

    def mutate(layout, box):
        paragraph = box.text_frame.paragraphs[0]
        second = paragraph.add_run()
        second.text, second.font.size, second.font.name = " 에스넷시스템이 운영합니다.", Pt(12), "Pretendard"
        second.font.color.rgb = RGBColor(200, 30, 30)
        for run in paragraph.runs:
            run.font._rPr.set("spc", "-50")

    package = extract_template_package(_layout_deck(tmp_path / "deck.pptx", mutate))
    frame = package.frames[0]
    node = etree.fromstring(_texts(package)[0].svg.encode())
    texts = node.findall(".//{http://www.w3.org/2000/svg}text")
    assert len(texts) == 1 and len(texts[0].findall("{http://www.w3.org/2000/svg}tspan")) == 1
    adapt_template_frame(frame, frame.source_canvas)

    svg = render_frame_svg(frame)
    source, target = tmp_path / "page.svg", tmp_path / "page.pptx"
    source.write_text(svg, encoding="utf-8")
    convert_pptx([source], target, [""], frame.source_canvas)
    with ZipFile(target) as deck:
        slide = deck.read("ppt/slides/slide1.xml").decode()
    boxes = [box for box in slide.split("<p:sp>") if "Header text" in box or "에스넷" in box]
    assert len(boxes) == 1 and "Header text" in boxes[0] and "에스넷시스템이" in boxes[0]


def test_a_template_body_region_failure_gets_a_move_only_repair_hint():
    import json as _json

    from services.generation_core.drivers.svg.render import template_region_hint
    assert template_region_hint({"failures": [{"kind": "text_overlap"}]}) is None
    failure = {"kind": "svg_template_body_outside_allowed_region",
               "body_region": {"x": 64, "y": 229, "width": 1152, "height": 619},
               "elements": [{"qid": "svg-40", "id": "body-node-37", "box": {"x": 63, "y": 689, "width": 1154, "height": 2}}]}
    hint = _json.loads(template_region_hint({"failures": [failure, {"kind": "text_overlap"}]}))
    assert hint["kind"] == "svg_template_body_region_repair" and hint["failures"] == [failure]
    assert "stroke-width" in hint["instruction"] and "never delete" in hint["instruction"]


def test_template_body_rules_name_the_stroke_extent():
    from services.svg_templates.generation import body_author_rules
    from utils.hashing import canonical_json_digest
    selection = {"schema_version": "svg-template-selection/v1", "mode": "template-redesign",
                 "template_pin": {"templateId": "t", "revision": 1, "digest": "sha256:" + "a" * 64}}
    selection["digest"] = canonical_json_digest(selection)
    rules = body_author_rules("rules", {"svg_template": selection, "composition_guidance": []})
    assert "half its stroke-width plus 2px inside body_region" in rules
