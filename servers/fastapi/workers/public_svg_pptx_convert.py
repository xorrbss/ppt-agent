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

CONVERTER_VERSION = "slide-master/166472bd2a22de9aa9fb6c8cdf8b0cdfc6b698ef+document-canvas.2+text-advance.1+chart-furniture.16+native-fixed-text.2"
VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "slide_master"


_PROJECTION_ADAPTER_FILES = tuple(
    Path(__file__).resolve().parents[1] / name
    for name in (
        "services/generation_core/drivers/svg/export_projection.py",
        "services/generation_core/drivers/svg/export_scene.py",
        "services/generation_core/drivers/svg/export_chart_style.py",
        "workers/public_svg_pptx_convert.py",
        "workers/svg_common_header_export.py",
        "workers/svg_common_header_export_v2.py",
        "services/svg_common_header_export_evidence.py",
        "services/svg_pptx_font_embedding.py",
        "services/svg_templates/frame_native_text.py",
    )
)


_VENDOR_MANIFEST_KEY = "vendor/slide_master/MANIFEST.json"


def converter_digest():
    """The converter identity that export bundles, proofs and identities carry.

    It is the reviewed execution revision's identity (``services/verifier_revisions``)
    while the running source is a reviewed state of that revision, and the raw
    ``converter_source_digest()`` otherwise. The vendored bytes are verified first
    in both cases.
    """
    from services.verifier_revisions import CONVERTER_LEDGER

    return CONVERTER_LEDGER.resolve(converter_source_state())


def converter_source_digest():
    """The pre-migration identity: vendor manifest and adapter bytes as they are on disk."""
    return converter_source_state().raw


def converter_source_state():
    """Verify the vendored files, then digest the vendor manifest and adapter sources."""
    from services.verifier_revisions import CONVERTER_LEDGER

    manifest = json.loads((VENDOR_ROOT / "MANIFEST.json").read_text())
    for name, expected in manifest["files"].items():
        if hashlib.sha256((VENDOR_ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("svg_converter_integrity_failed")
    root = Path(__file__).resolve().parents[1]
    sources = {_VENDOR_MANIFEST_KEY: VENDOR_ROOT / "MANIFEST.json",
               **{path.relative_to(root).as_posix(): path for path in _PROJECTION_ADAPTER_FILES}}
    return CONVERTER_LEDGER.source_state(sources, converter_identity)


def converter_identity(hashes):
    """Identity formula ``svg-export-adapter-projection/v1`` over ``{source key: SHA-256}``."""
    identity = {
        "schema": "svg-export-adapter-projection/v1",
        "vendorManifestSha256": hashes[_VENDOR_MANIFEST_KEY],
        "adapterSha256": {key: value for key, value in hashes.items() if key != _VENDOR_MANIFEST_KEY},
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def convert_pptx(paths, target, notes, canvas, *, source_preservation=None,
                 expected_image_digests=None, font_embedding=False, native_objects=None,
                 native_coverage_sources=None, font_manifest=None, font_usage=None, system_furniture=None):
    """Convert page files to an editable PPTX and read the result back.

    ``native_objects`` is one ``PageNativeObjects`` per page (``None``: no
    sidecars). Every deck's native tables/charts -- sidecar entries and the
    tables of raster-redesign pages -- go through the one export scene.
    """
    converter_digest()
    capture = None
    fixed_text = None
    if system_furniture is not None:
        from services.svg_templates.frame_native_text import NativeFixedTextExport
        from models.svg_template_frame import SvgTemplateFontManifest
        if font_manifest is None or source_preservation is not None:
            raise ValueError("svg_template_native_layout_context_missing")
        fixed_text = NativeFixedTextExport(
            native_coverage_sources if native_coverage_sources is not None else
            [path.read_text(encoding="utf-8") for path in paths], system_furniture, canvas,
            SvgTemplateFontManifest.model_validate(font_manifest))
    from workers.svg_common_header_export import CommonHeaderExport
    width_emu, height_emu = canvas_physical_size(canvas)
    source_pt_per_px = (0.75 if isinstance(canvas, AuthoredCanvasMetadata)
                       else width_emu / 12700 / canvas.width_px)
    if native_coverage_sources is not None:
        if (source_preservation is not None or len(native_coverage_sources) != len(paths)
                or not paths or any(not isinstance(source, str) for source in native_coverage_sources)):
            raise ValueError("svg_generic_native_coverage_input_invalid")
        # Retain canonical and materialized bytes before the existing scene annotates
        # native table/chart groups. Both inputs are bound into the final proof.
        conversion_sources = [path.read_text(encoding="utf-8") for path in paths]
        trace_path = target.with_suffix(".native-ooxml-trace.json")
    header_export = CommonHeaderExport(
        native_coverage_sources if native_coverage_sources is not None
        else [path.read_text(encoding="utf-8") for path in paths], source_pt_per_px)
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
            native_fixed_text=fixed_text.convert if fixed_text is not None else None,
        )
        from svg_to_pptx.drawingml import converter as drawing_converter
        header_export.install(drawing_converter)
        if native_coverage_sources is not None:
            from workers.public_svg_whole_page_convert import NativeOoxmlCapture
            from svg_to_pptx.drawingml import converter as drawing_converter
            capture = NativeOoxmlCapture(drawing_converter)
            capture.install()

        def run(native):
            # Raster-redesign pages only: one text frame per paragraph block.
            return create_pptx_with_native_svg(
                paths, target, verbose=False, transition=None, use_native_shapes=True,
                notes={path.stem: note for path, note in zip(paths, notes, strict=True)},
                workers=1, merge_paragraphs=scene.redesign_active, image_optimize=False,
                pptx_structure="flat", use_compat_mode=False,
                conversion_context=context, native_objects=native,
                **({"conversion_trace_path": trace_path} if capture is not None else {}),
            )

        fallback_reason = None
        try:
            success = run(scene.native_requested)
        except RuntimeError as error:
            if not scene.native_requested:
                raise
            success, fallback_reason = False, str(error)
        if not success and scene.native_requested:
            if fixed_text is not None:
                raise ValueError("svg_template_native_layout_fallback_forbidden")
            # The converter rejected the native run: convert every drawn group
            # instead and record the reason on each object (never silently).
            target.unlink(missing_ok=True)
            scene.fall_back_all(fallback_reason or "native_object_conversion_failed")
            success = run(False)
    finally:
        if capture is not None:
            capture.restore()
        header_export.restore()
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
    if fixed_text is not None:
        readback["nativeFixedText"] = {"schemaVersion": "svg-template-native-fixed-text-readback/v1",
                                      "converted": len(fixed_text.used), "complete": True}
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
        from services.svg_pptx_font_embedding import embed_trusted_fonts, ooxml_trusted_font_usage
        from services.svg_trusted_fonts import FONT_FAMILY
        if font_manifest is None and font_usage is None:
            # Embed the trusted faces the slides actually use (ordinary decks
            # use Noto Sans KR), falling back to the historical Pretendard set.
            font_usage = ooxml_trusted_font_usage(target) or None
        digests = embed_trusted_fonts(target, font_manifest=font_manifest, font_usage=font_usage)
        families = (sorted(font_usage) if font_usage is not None and font_manifest is None
                    else [FONT_FAMILY] if font_manifest is None else None)
        readback["fontEmbedding"] = {"embedded": True,
                                     "family": FONT_FAMILY if font_manifest is None and font_usage is None else None,
                                     "families": families, "faceDigests": list(digests)}
    if fixed_text is not None:
        fixed_text.verify(target)
    if capture is not None:
        from services.svg_contract import source_digest
        from utils.hashing import canonical_json_sha256
        # An object that could not be projected stays a drawn shape; its reason is in
        # the receipt, which nativeObjectsDigest binds into the proof below.
        receipts = [{"source_page_id": f"slide-{index + 1}", "svg_digest": source_digest(source)}
                    for index, source in enumerate(native_coverage_sources)]
        coverage = capture.verify(target, trace_path, native_coverage_sources, receipts)
        coverage["measurements"]["unboundRelationships"] = 0
        coverage.update(schemaVersion="svg-generic-native-ooxml-coverage/v1",
            conversionSourceDigests=[source_digest(source) for source in conversion_sources],
            projectedSourceDigests=[source_digest(path.read_text(encoding="utf-8")) for path in paths],
            nativeObjectsDigest="sha256:" + canonical_json_sha256(readback["nativeObjects"]),
            officeRenderMeasured=False)
        header_readback = header_export.verify(target)
        if header_readback is not None:
            coverage["commonHeaderReadback"] = header_readback
        readback["genericNativeCoverageOoxml"] = coverage
    elif any(header_export.lines):
        readback["commonHeaderReadback"] = header_export.verify(target)
    return readback


__all__ = ["CONVERTER_VERSION", "VENDOR_ROOT", "convert_pptx", "converter_digest", "converter_identity",
           "converter_source_digest", "converter_source_state"]
