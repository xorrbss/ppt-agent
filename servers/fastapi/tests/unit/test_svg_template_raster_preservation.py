"""Unsupported fixed decoration preserved as an image, text kept as text.

Design: docs/svg-template-real-deck-registration-design-20261103.md, 3.2.
"""

from io import BytesIO
from zipfile import ZipFile

import numpy as np
import pytest
from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Emu, Pt

from services.svg_templates.frame import extract_template_package
from services.svg_templates.raster_preservation import (
    RasterRequest,
    RasterResult,
    RasterShape,
    build_render_deck,
    recover_alpha,
)

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_WIDTH, _HEIGHT = 12192000, 6858000


def _png(width=8, height=4):
    output = BytesIO()
    Image.new("RGBA", (width, height), (10, 20, 200, 128)).save(output, format="PNG")
    return output.getvalue()


class _FakeRenderer:
    """Records the requests and answers each with a small translucent image
    (or at ``bounds`` when given)."""

    def __init__(self, *, error=None, bounds=(10, 12, 40, 20)):
        self.requests, self.error, self.bounds = [], error, bounds

    def __call__(self, path, canvas, requests):
        self.requests.extend(requests)
        if self.error:
            raise ValueError(self.error)
        x, y, width, height = self.bounds
        return {request.key: RasterResult(data=_png(), media_type="image/png", x=x, y=y, width=width, height=height,
                                          renderer="fake", renderer_version="1", approximate=True)
                for request in requests}


def _gradient(shape):
    shape.fill.gradient()
    shape.line.fill.background()
    style = shape._element.find(f"{{{_P}}}style")
    if style is not None:
        shape._element.remove(style)


def _solid(shape, rgb=(14, 61, 122)):
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(*rgb)
    shape.line.fill.background()
    style = shape._element.find(f"{{{_P}}}style")
    if style is not None:
        shape._element.remove(style)


def _text(shape, value="Header"):
    frame = shape.text_frame
    frame.auto_size = MSO_AUTO_SIZE.NONE
    run = frame.paragraphs[0].add_run()
    run.text, run.font.size, run.font.name = value, Pt(12), "Pretendard"
    run.font.color.rgb = RGBColor(255, 255, 255)


def _deck(path, build):
    """One slide whose layout furniture ``build`` adds (moved from the slide)."""
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(_WIDTH), Emu(_HEIGHT)
    layout = deck.slide_layouts[6]
    slide = deck.slides.add_slide(layout)
    for placeholder in list(slide.placeholders):
        placeholder._element.getparent().remove(placeholder._element)

    def add(shape):
        element = shape._element
        element.getparent().remove(element)
        layout.shapes._spTree.append(element)
        return shape

    build(slide, add, deck)
    deck.save(path)
    return path


def _band_and_footer(slide, add, deck):
    _gradient(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(_WIDTH), Emu(640080))))
    _solid(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, Emu(6400000), Emu(_WIDTH), Emu(100000))))
    _gradient(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(9000000), Emu(6500000), Emu(2000000), Emu(200000))))
    _gradient(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(9000000), Emu(6700000), Emu(2000000), Emu(100000))))


def _codes(package):
    return {issue.reason_code for frame in package.frames for issue in frame.issues if issue.severity == "blocked"}


def test_without_a_renderer_unsupported_decoration_still_blocks(tmp_path):
    package = extract_template_package(_deck(tmp_path / "deck.pptx", _band_and_footer))
    assert "svg_template_paint_unsupported" in _codes(package)
    assert not any(element.raster_preservation for element in package.frames[0].elements)


def test_runs_follow_paint_order_and_become_recorded_images(tmp_path):
    renderer = _FakeRenderer()
    package = extract_template_package(_deck(tmp_path / "deck.pptx", _band_and_footer), raster_renderer=renderer)
    assert not _codes(package)
    frame = package.frames[0]
    # Band (run 0), the vector rule, then the two adjacent gradient boxes as one run.
    assert [len(request.shapes) for request in renderer.requests] == [1, 2]
    kinds = [(element.kind, element.raster_preservation is not None) for element in frame.elements
             if element.source_shape_id != "implicit-background"]
    assert kinds[-3:] == [("image", True), ("rect", False), ("image", True)]
    raster = next(element for element in frame.elements if element.raster_preservation)
    assert raster.layer == "foreground" and raster.raster_preservation.renderer == "fake"
    assert raster.raster_preservation.reasons == ("svg_template_paint_unsupported",)
    assert raster.svg.startswith("<image") and "data:image/png;base64," in raster.svg
    assert any(issue.reason_code == "svg_template_raster_preservation_review_required" for issue in frame.issues)
    # Images are fixed regions the body keeps clear of.
    assert raster.bounds.y + raster.bounds.height <= frame.body_region.y or raster.bounds.y >= 12


def test_text_in_unsupported_paint_stays_vector_text(tmp_path):
    def build(slide, add, deck):
        box = add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(_WIDTH), Emu(640080)))
        _gradient(box)
        _text(box, "Chapter header")

    renderer = _FakeRenderer()
    package = extract_template_package(_deck(tmp_path / "deck.pptx", build), raster_renderer=renderer)
    assert not _codes(package)
    (request,) = renderer.requests
    assert request.shapes[0].hide_text is True
    elements = package.frames[0].elements
    raster_index = next(index for index, element in enumerate(elements) if element.raster_preservation)
    text_index = next(index for index, element in enumerate(elements) if element.kind == "text")
    assert raster_index < text_index and "Chapter header" in elements[text_index].svg
    assert "Chapter header" not in elements[raster_index].svg


def test_a_full_slide_picture_is_a_background_layer_only_with_a_renderer(tmp_path):
    picture = tmp_path / "photo.png"
    Image.new("RGB", (64, 36), (5, 30, 90)).save(picture)

    def build(slide, add, deck):
        # On the slide itself (a cover photo), first in paint order.
        slide.shapes.add_picture(str(picture), 0, 0, Emu(_WIDTH), Emu(_HEIGHT))
        # Furniture painted above it; a layout shape would sit under the photo.
        _solid(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, Emu(6400000), Emu(_WIDTH), Emu(100000)))

    path = _deck(tmp_path / "deck.pptx", build)
    assert "svg_template_fullslide_raster_forbidden" in _codes(extract_template_package(path))
    package = extract_template_package(path, raster_renderer=_FakeRenderer())
    assert not _codes(package)
    photo = next(element for element in package.frames[0].elements if element.kind == "image")
    assert photo.layer == "background" and photo.raster_preservation is None


def test_an_unsupported_slide_background_is_a_background_run(tmp_path):
    def build(slide, add, deck):
        background = deck.slide_layouts[6].background.fill
        background.gradient()
        _solid(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(_WIDTH), Emu(640080))))

    renderer = _FakeRenderer()
    package = extract_template_package(_deck(tmp_path / "deck.pptx", build), raster_renderer=renderer)
    assert not _codes(package)
    (request,) = renderer.requests
    assert request.background_part and not request.shapes
    first = package.frames[0].elements[0]
    assert first.layer == "background" and first.raster_preservation is not None


def test_a_renderer_failure_blocks_every_candidate_with_its_reason(tmp_path):
    package = extract_template_package(_deck(tmp_path / "deck.pptx", _band_and_footer),
                                       raster_renderer=_FakeRenderer(error="svg_template_raster_render_failed"))
    blocked = [issue for issue in package.frames[0].issues if issue.reason_code == "svg_template_raster_render_failed"]
    assert len(blocked) == 3


def test_an_element_without_preservation_serialises_as_before(tmp_path):
    package = extract_template_package(_deck(tmp_path / "deck.pptx", _band_and_footer), raster_renderer=_FakeRenderer())
    dumped = package.model_dump(mode="json")
    plain = [element for frame in dumped["frames"] for element in frame["elements"]
             if element["kind"] == "rect"]
    assert plain and all("raster_preservation" not in element for element in plain)


def test_alpha_and_colour_are_recovered_from_white_and_black_renders():
    colour = np.array([200.0, 40.0, 10.0])
    alpha = np.array([[1.0, 0.5, 0.0]])
    black = (colour * alpha[..., None])
    white = black + 255.0 * (1 - alpha[..., None])
    rgba = recover_alpha(white, black)
    assert rgba[0, 0].tolist() == [200, 40, 10, 255]
    assert rgba[0, 1, 3] == 128 and abs(int(rgba[0, 1, 0]) - 200) <= 1
    assert rgba[0, 2, 3] == 0


def test_the_render_deck_isolates_each_run_and_empties_its_text(tmp_path):
    def build(slide, add, deck):
        box = add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(_WIDTH), Emu(640080)))
        _gradient(box)
        _text(box, "Secret")

    path = _deck(tmp_path / "deck.pptx", build)
    with ZipFile(path) as package:
        layout_part = "ppt/slideLayouts/slideLayout7.xml"
        layout = etree.fromstring(package.read(layout_part))
    shape = layout.find(f".//{{{_P}}}sp")
    requests = [RasterRequest(key="a", shapes=(RasterShape(part=layout_part, element=shape, hide_text=True,
                                                           emu_rect=(1, 2, 3, 4)),)),
                RasterRequest(key="b", background_part=None)]
    deck = build_render_deck(path.read_bytes(), requests, "000000")
    with ZipFile(BytesIO(deck)) as rendered:
        presentation = etree.fromstring(rendered.read("ppt/presentation.xml"))
        assert len(presentation.findall(f"{{{_P}}}sldIdLst/{{{_P}}}sldId")) == 2
        first = etree.fromstring(rendered.read("ppt/slides/raster-render-1.xml"))
        assert first.get("showMasterSp") == "0"
        assert not any(node.text for node in first.iter(f"{{{_A}}}t"))
        offset = first.find(f".//{{{_A}}}xfrm/{{{_A}}}off")
        assert (offset.get("x"), offset.get("y")) == ("1", "2")
        assert first.find(f".//{{{_P}}}bg//{{{_A}}}srgbClr").get("val") == "000000"
        assert "ppt/slides/slide1.xml" in rendered.namelist()


def test_libreoffice_preserves_a_gradient_band_on_transparency(tmp_path):
    from services.svg_templates.raster_preservation import libreoffice_raster_renderer
    renderer = libreoffice_raster_renderer()
    if renderer is None:
        pytest.skip("LibreOffice is not installed")
    try:
        package = extract_template_package(_deck(tmp_path / "deck.pptx", _band_and_footer), raster_renderer=renderer)
    except ValueError as error:  # pragma: no cover - environment specific
        pytest.skip(f"LibreOffice render unavailable here: {error}")
    if "svg_template_raster_render_failed" in _codes(package):
        pytest.skip("LibreOffice could not render in this environment")
    assert not _codes(package)
    band = next(element for element in package.frames[0].elements if element.raster_preservation)
    assert band.raster_preservation.renderer == "libreoffice" and band.raster_preservation.approximate
    assert band.bounds.y == pytest.approx(0, abs=1) and band.bounds.width == pytest.approx(1280, abs=2)


def test_a_review_that_keeps_every_listed_element_reproduces_the_frame(tmp_path):
    """Review lists raster run shapes and the background; saving it unchanged
    must not drop any furniture or fail as an unknown shape."""
    path = _deck(tmp_path / "deck.pptx", _band_and_footer)
    first = extract_template_package(path, raster_renderer=_FakeRenderer())
    frame = first.frames[0]
    listed = []
    for element in frame.elements:
        if element.raster_preservation:
            listed.extend(element.raster_preservation.shape_refs)
        else:
            listed.append(f"{element.source_part}#{element.source_shape_id}")
    again = extract_template_package(path, raster_renderer=_FakeRenderer(),
                                     frame_reviews={frame.frame_id: {"fixed_shape_ids": listed}})
    assert [element.kind for element in again.frames[0].elements] == [element.kind for element in frame.elements]


def test_a_reviewed_group_leaf_keeps_its_group(tmp_path):
    def build(slide, add, deck):
        group = add(slide.shapes.add_group_shape())
        _solid(group.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(_WIDTH), Emu(640080)))
        _solid(group.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, Emu(700000), Emu(_WIDTH), Emu(40000)), (200, 0, 0))

    path = _deck(tmp_path / "deck.pptx", build)
    frame = extract_template_package(path).frames[0]
    leaves = [element for element in frame.elements if element.kind == "rect" and element.layer == "foreground"]
    assert len(leaves) == 2
    again = extract_template_package(path, frame_reviews={frame.frame_id: {
        "fixed_shape_ids": [f"{leaves[0].source_part}#{leaves[0].source_shape_id}"]}})
    assert len([element for element in again.frames[0].elements if element.layer == "foreground"]) == 2
    with pytest.raises(ValueError, match="svg_template_fixed_shape_unknown"):
        extract_template_package(path, frame_reviews={frame.frame_id: {"fixed_shape_ids": [leaves[0].source_part + "#999"]}})


def test_raster_pixels_map_to_the_logical_canvas_per_axis():
    from types import SimpleNamespace

    from services.svg_templates.raster_preservation import _logical_bounds
    # 303 x 216 mm: logical 1280 x 912 (912.46 rounded), rendered 2560 x 1825.
    canvas = SimpleNamespace(width_px=1280, height_px=912)
    bounds = _logical_bounds((0, 0, 2560, 1825), 2560, 1825, canvas)
    assert bounds == {"x": 0.0, "y": 0.0, "width": 1280.0, "height": 912.0}


def test_a_large_early_decoration_run_is_a_background_layer(tmp_path):
    from services.svg_templates.frame import adapt_template_frame

    def build(slide, add, deck):
        # A picture-filled divider shape: not a p:pic, yet text-free and under everything.
        _gradient(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, Emu(800000), Emu(_WIDTH), Emu(_HEIGHT - 800000))))
        _solid(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(_WIDTH), Emu(300000))))

    package = extract_template_package(_deck(tmp_path / "deck.pptx", build),
                                       raster_renderer=_FakeRenderer(bounds=(0, 84, 1280, 636)))
    frame = package.frames[0]
    raster = next(element for element in frame.elements if element.raster_preservation)
    assert raster.layer == "background" and not frame.forbidden_regions
    adapt_template_frame(frame, frame.source_canvas)


def test_a_mid_page_decoration_reserves_its_image_bounds(tmp_path):
    from services.svg_templates.frame import adapt_template_frame

    def build(slide, add, deck):
        _solid(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(_WIDTH), Emu(300000))))
        _gradient(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(10000000), Emu(3000000), Emu(1500000), Emu(600000))))

    package = extract_template_package(_deck(tmp_path / "deck.pptx", build),
                                       raster_renderer=_FakeRenderer(bounds=(1050, 316, 156, 62)))
    frame = package.frames[0]
    raster = next(element for element in frame.elements if element.raster_preservation)
    # The reserved region is the image itself, not the source shape's box.
    assert frame.forbidden_regions == (raster.bounds,)
    adapt_template_frame(frame, frame.source_canvas)


def test_a_frame_with_only_a_background_picture_is_compared_on_it(tmp_path):
    from services.svg_templates.source_review import _compare_fixed_regions

    def build(slide, add, deck):
        _gradient(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(_WIDTH), Emu(_HEIGHT))))

    package = extract_template_package(_deck(tmp_path / "deck.pptx", build),
                                       raster_renderer=_FakeRenderer(bounds=(0, 0, 1280, 720)))
    frame = package.frames[0]
    assert not any(element.layer == "foreground" for element in frame.elements)
    image = BytesIO()
    Image.new("RGB", (1280, 720), (0, 0, 0)).save(image, format="PNG")
    comparison = _compare_fixed_regions(image.getvalue(), image.getvalue(), frame)
    assert [region["mean_absolute_channel_error"] for region in comparison["regions"]] == [0]


def test_distant_consecutive_decoration_becomes_separate_images(tmp_path):
    from services.svg_templates.frame import adapt_template_frame

    def build(slide, add, deck):
        # A top tab and a bottom page pill, consecutive in paint order (the real deck).
        _gradient(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(400000), 0, Emu(800000), Emu(500000))))
        _gradient(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(5800000), Emu(6500000), Emu(600000), Emu(200000))))

    renderer = _FakeRenderer()
    package = extract_template_package(_deck(tmp_path / "deck.pptx", build), raster_renderer=renderer)
    assert [len(request.shapes) for request in renderer.requests] == [1, 1]
    frame = package.frames[0]
    assert len([element for element in frame.elements if element.raster_preservation]) == 2
    adapt_template_frame(frame, frame.source_canvas)


def test_decoration_with_an_unknown_box_is_never_merged(tmp_path):
    def build(slide, add, deck):
        _gradient(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(9000000), Emu(6400000), Emu(1000000), Emu(300000))))
        tab = add(slide.shapes.add_shape(MSO_SHAPE.ROUND_2_SAME_RECTANGLE, Emu(400000), 0, Emu(800000), Emu(500000)))
        _gradient(tab)
        tab.rotation = 180

    renderer = _FakeRenderer()
    extract_template_package(_deck(tmp_path / "deck.pptx", build), raster_renderer=renderer)
    assert [len(request.shapes) for request in renderer.requests] == [1, 1]


def test_the_body_background_reports_a_dark_cover_photo(tmp_path):
    from services.generation_core.drivers.svg.fallback import template_fallback_body_svg
    from services.svg_templates.frame_composition import body_background

    class _DarkPhoto(_FakeRenderer):
        def __call__(self, path, canvas, requests):
            self.requests.extend(requests)
            output = BytesIO()
            Image.new("RGB", (64, 36), (8, 30, 70)).save(output, format="JPEG")
            return {request.key: RasterResult(data=output.getvalue(), media_type="image/jpeg", x=0, y=0,
                                              width=1280, height=720, renderer="fake", renderer_version="1",
                                              approximate=True) for request in requests}

    def build(slide, add, deck):
        deck.slide_layouts[6].background.fill.gradient()
        _solid(add(slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, Emu(6600000), Emu(_WIDTH), Emu(100000))))

    frame = extract_template_package(_deck(tmp_path / "deck.pptx", build), raster_renderer=_DarkPhoto()).frames[0]
    background = body_background(frame)
    assert background["tone"] == "dark" and background["text_color"] == "#FFFFFF"
    svg = template_fallback_body_svg(content="# 제목\n- 첫째 항목", width=1280, height=720,
        body_region=frame.body_region.model_dump(), title_in_frame=False,
        title_face={"family": "Pretendard", "weight": 700}, body_face={"family": "Pretendard", "weight": 400},
        style_snapshot={"tokens": {"background": "#FFFFFF", "palette": ["#222222"]}}, body_background=background)
    assert 'fill="#FFFFFF"' in svg and 'fill="#222222"' not in svg

    light = extract_template_package(_deck(tmp_path / "plain.pptx", _band_and_footer),
                                     raster_renderer=_FakeRenderer()).frames[0]
    assert body_background(light)["tone"] == "light"


def test_a_repair_sees_only_the_body_of_a_composed_template_page(tmp_path):
    from services.svg_templates.frame_composition import compose_frame_svg, strip_template_furniture

    package = extract_template_package(_deck(tmp_path / "deck.pptx", _band_and_footer), raster_renderer=_FakeRenderer())
    frame = package.frames[0]
    region = frame.body_region
    body = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            f'<text id="body-claim" x="{region.x + 10}" y="{region.y + 40}" font-family="Pretendard" '
            'font-weight="400" font-size="20" fill="#111111">본문 문장</text></svg>')
    page = compose_frame_svg(frame, body, dynamic_values={})
    assert "data:image/png;base64," in page and "svg-system-template-" in page
    shown = strip_template_furniture(page)
    assert "본문 문장" in shown and "body-claim" in shown
    assert "data:image" not in shown and "svg-system-" not in shown
    assert 'viewBox="0 0 1280 720"' in shown and len(shown) < len(page) / 2
    # Anything else is shown unchanged.
    assert strip_template_furniture(body) == body and strip_template_furniture("<not svg") == "<not svg"
