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
    """Records the requests and answers each with a small translucent image."""

    def __init__(self, *, error=None):
        self.requests, self.error = [], error

    def __call__(self, path, canvas, requests):
        self.requests.extend(requests)
        if self.error:
            raise ValueError(self.error)
        return {request.key: RasterResult(data=_png(), media_type="image/png", x=10, y=12, width=40, height=20,
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
