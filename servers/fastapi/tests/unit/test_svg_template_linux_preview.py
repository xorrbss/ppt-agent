"""SVG user templates on a server without PowerPoint (Linux, Docker).

The framed-template source preview is rendered with LibreOffice there, marked
approximate in the review evidence, and installed fonts are discovered in the
fontconfig directories with the unchanged embedding policy.
"""

import shutil

import pytest

from services.svg_templates import frame_fonts
from services.svg_templates.frame_fonts import artifact_digest
from services.svg_templates.frame_review import (
    frame_region_review_digest, review_image_pair_digest, validate_frame_review,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _deck(path):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Emu, Pt

    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    for title in ("표지", "본문"):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Emu(12192000), Emu(640080))
        bar.fill.solid()
        bar.fill.fore_color.rgb = RGBColor(14, 61, 122)
        bar.line.fill.background()
        run = slide.shapes.add_textbox(Emu(457200), Emu(800000), Emu(9000000), Emu(700000)) \
            .text_frame.paragraphs[0].add_run()
        run.text, run.font.size, run.font.name = title, Pt(28), "Pretendard"
    deck.save(path)
    return path


def _approved_evidence(package, renderer):
    frame = package.frames[0]
    item = {"frame_id": frame.frame_id, "frame_digest": frame.digest,
            "font_manifest_digest": frame.font_manifest.digest,
            "source_image_digest": DIGEST_A, "frame_image_digest": DIGEST_B,
            "region_review_digest": frame_region_review_digest(frame, source_image_digest=DIGEST_A,
                                                               frame_image_digest=DIGEST_B)}
    evidence = {"schema_version": "svg-template-review/v1", "source_digest": package.source_digest,
                "package_digest": package.digest, "renderer": renderer, "frames": [item]}
    item["review"] = {"method": "human-source-frame-comparison", "classification_approved": True,
                      "preservation_approved": True, "decision_id": "decision-1",
                      "reviewed_image_pair_digest": review_image_pair_digest(evidence, item)}
    evidence["digest"] = artifact_digest(evidence)
    return evidence


def _renderer(kind, package):
    return {"kind": kind, "version": "LibreOffice 24.2", "approximate": True, "environment_digest": DIGEST_A,
            "font_manifest_digest": package.font_manifest.digest}


def test_an_approximate_libreoffice_review_can_be_approved(tmp_path):
    from services.svg_templates.frame import extract_template_package

    package = extract_template_package(_deck(tmp_path / "deck.pptx"))
    frame_id = package.frames[0].frame_id
    approved = validate_frame_review(package, _approved_evidence(package, _renderer("libreoffice", package)),
                                     selected_frame_ids=(frame_id,))
    assert approved["selected_frame_ids"] == [frame_id]
    with pytest.raises(ValueError, match="svg_template_source_render_review_required"):
        validate_frame_review(package, _approved_evidence(package, _renderer("unknown", package)),
                              selected_frame_ids=(frame_id,))


@pytest.mark.skipif(not (shutil.which("soffice") or shutil.which("libreoffice")), reason="LibreOffice not installed")
def test_libreoffice_renders_each_frame_page_at_the_source_canvas(tmp_path):
    from PIL import Image
    from io import BytesIO
    from services.svg_templates.frame import extract_template_package
    from services.svg_templates.source_review import _render_source_libreoffice, source_preview_renderer

    path = _deck(tmp_path / "deck.pptx")
    package = extract_template_package(path)
    assert source_preview_renderer() == "libreoffice"
    version, images = _render_source_libreoffice(path, package)
    assert version.startswith("LibreOffice ")
    assert sorted(images) == sorted({frame.source_page for frame in package.frames})
    for png in images.values():
        with Image.open(BytesIO(png)) as image:
            assert image.size == (package.source_canvas.width_px, package.source_canvas.height_px)
            # The header bar is drawn: the top-left pixel is the bar's navy, not the white canvas.
            assert image.convert("RGB").getpixel((5, 5)) == (14, 61, 122)
    with pytest.raises(ValueError, match="svg_template_source_digest_mismatch"):
        _render_source_libreoffice(_deck(tmp_path / "other.pptx"), package.model_copy(
            update={"source_digest": DIGEST_A}))


def test_posix_font_inventory_reads_only_the_font_directories(tmp_path, monkeypatch):
    system, outside = tmp_path / "system", tmp_path / "outside"
    (system / "nested").mkdir(parents=True)
    outside.mkdir()
    (system / "nested" / "a.ttf").write_bytes(b"x")
    (system / "notes.txt").write_bytes(b"x")
    (outside / "b.ttf").write_bytes(b"x")
    (system / "escape.ttf").symlink_to(outside / "b.ttf")
    monkeypatch.setattr(frame_fonts, "_POSIX_FONT_ROOTS", (str(system),))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    paths = frame_fonts._installed_posix_font_paths()
    assert paths == ((system / "nested" / "a.ttf").resolve(),)
    monkeypatch.setattr(frame_fonts, "MAX_INSTALLED_FONT_CANDIDATES", 0)
    with pytest.raises(ValueError, match="installed_font_inventory_limit"):
        frame_fonts._installed_posix_font_paths()
