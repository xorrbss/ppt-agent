"""PPTX conversion of svg export pages through the vendored slide-master converter."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from pptx import Presentation

from services.authored_canvas import AuthoredCanvasMetadata
from services.document_canvas import canvas_physical_size
from workers.public_svg_pptx_readback import iter_shapes, source_preservation_readback

CONVERTER_VERSION = "slide-master/166472bd2a22de9aa9fb6c8cdf8b0cdfc6b698ef+document-canvas.2+text-advance.1+chart-furniture.16"
VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "slide_master"


_PROJECTION_ADAPTER_FILES = tuple(
    Path(__file__).resolve().parents[1] / name
    for name in (
        "services/generation_core/drivers/svg/export_projection.py",
        "services/generation_core/drivers/svg/export_scene.py",
        "services/generation_core/drivers/svg/export_chart_style.py",
        "workers/public_svg_pptx_convert.py",
    )
)


def converter_digest():
    manifest = json.loads((VENDOR_ROOT / "MANIFEST.json").read_text())
    for name, expected in manifest["files"].items():
        if hashlib.sha256((VENDOR_ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("svg_converter_integrity_failed")
    identity = {
        "schema": "svg-export-adapter-projection/v1",
        "vendorManifestSha256": hashlib.sha256((VENDOR_ROOT / "MANIFEST.json").read_bytes()).hexdigest(),
        "adapterSha256": {
            path.relative_to(Path(__file__).resolve().parents[1]).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in _PROJECTION_ADAPTER_FILES
        },
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def convert_pptx(paths, target, notes, canvas, *, source_preservation=None,
                 expected_image_digests=None, font_embedding=False, native_objects=None):
    """Convert page files to an editable PPTX and read the result back.

    ``native_objects`` is one ``PageNativeObjects`` per page (``None``: no
    sidecars). Every deck's native tables/charts -- sidecar entries and the
    tables of raster-redesign pages -- go through the one export scene.
    """
    converter_digest()
    from services.generation_core.drivers.svg.export_scene import prepare_svg_pptx_inputs
    from services.svg_text_advance import text_advance
    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from svg_to_pptx import create_pptx_with_native_svg
        from svg_to_pptx.conversion_context import ConversionContext
        width_emu, height_emu = canvas_physical_size(canvas)
        source_pt_per_px = (0.75 if isinstance(canvas, AuthoredCanvasMetadata)
                             else width_emu / 12700 / canvas.width_px)
        scene = prepare_svg_pptx_inputs(paths, native_objects, width=canvas.width_px, height=canvas.height_px, pt_per_px=source_pt_per_px)
        # Bundled-font metrics keep single-line frames at least as wide as their text.
        context = ConversionContext(
            canvas.width_px, canvas.height_px, width_emu, height_emu,
            legacy=isinstance(canvas, AuthoredCanvasMetadata), text_advance=text_advance,
        )

        def run(native):
            # Raster-redesign pages only: one text frame per paragraph block.
            return create_pptx_with_native_svg(
                paths, target, verbose=False, transition=None, use_native_shapes=True,
                notes={path.stem: note for path, note in zip(paths, notes, strict=True)},
                workers=1, merge_paragraphs=scene.redesign_active, image_optimize=False,
                pptx_structure="flat", use_compat_mode=False,
                conversion_context=context, native_objects=native,
            )

        fallback_reason = None
        try:
            success = run(scene.native_requested)
        except RuntimeError as error:
            if not scene.native_requested:
                raise
            success, fallback_reason = False, str(error)
        if not success and scene.native_requested:
            # The converter rejected the native run: convert every drawn group
            # instead and record the reason on each object (never silently).
            target.unlink(missing_ok=True)
            scene.fall_back_all(fallback_reason or "native_object_conversion_failed")
            success = run(False)
    finally:
        sys.path.remove(str(VENDOR_ROOT))
    if not success or not target.is_file():
        raise ValueError("svg_native_conversion_failed")
    deck = Presentation(str(target))
    if len(deck.slides) != len(paths) or (deck.slide_width, deck.slide_height) != canvas_physical_size(canvas):
        raise ValueError("svg_pptx_readback_mismatch")
    scene.verify_readback(deck)
    readback = {"slideCount": len(paths), "nativeTextRuns": sum(
        len(p.runs) for slide in deck.slides for shape in iter_shapes(slide.shapes)
        if shape.has_text_frame for p in shape.text_frame.paragraphs
    ), "nativeObjects": scene.receipt()}
    if scene.redesign_active:
        readback["rasterRedesign"] = scene.raster_evidence()
    if source_preservation is not None:
        if (len(source_preservation) != len(paths) or expected_image_digests is None
                or len(expected_image_digests) != len(paths)):
            raise ValueError("svg_source_pptx_readback_input_invalid")
        readback["sourcePreservation"] = source_preservation_readback(
            deck, source_preservation, expected_image_digests
        )
    if font_embedding:
        from services.svg_pptx_font_embedding import embed_trusted_fonts
        from services.svg_trusted_fonts import FONT_FAMILY
        digests = embed_trusted_fonts(target)
        readback["fontEmbedding"] = {"embedded": True, "family": FONT_FAMILY, "faceDigests": list(digests)}
    return readback


__all__ = ["CONVERTER_VERSION", "VENDOR_ROOT", "convert_pptx", "converter_digest"]
