"""Isolated, source-preserving SVG export; no HTML hybrid fallback."""
from __future__ import annotations
import hashlib
import json
import re
import sys
import tempfile
import time
import unicodedata
from collections import Counter
from pathlib import Path
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from services.authored_canvas import AuthoredCanvasMetadata
from services.document_canvas import parse_canvas_metadata, canvas_physical_size
from services.isolated_worker_contracts import ArtifactRef
from workers.public_worker_runtime import PublicWorkerExecutionError, WorkerOutput

SVG_EXPORT_SCHEMA = "public-svg-export-bundle/v1"
SVG_EXPORT_SCHEMA_V2 = "public-svg-export-bundle/v2"
SVG_EXPORT_SCHEMA_V3 = "public-svg-export-bundle/v3"
CONVERTER_VERSION = "slide-master/166472bd2a22de9aa9fb6c8cdf8b0cdfc6b698ef+document-canvas.2"
VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "slide_master"

def converter_digest():
    manifest = json.loads((VENDOR_ROOT / "MANIFEST.json").read_text())
    for name, expected in manifest["files"].items():
        if hashlib.sha256((VENDOR_ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("svg_converter_integrity_failed")
    return hashlib.sha256((VENDOR_ROOT / "MANIFEST.json").read_bytes()).hexdigest()

def is_svg_export_bundle(job, store):
    try:
        value = json.loads(store.read(job.input_artifact))
        return isinstance(value, dict) and value.get("schemaVersion") in {
            SVG_EXPORT_SCHEMA, SVG_EXPORT_SCHEMA_V2, SVG_EXPORT_SCHEMA_V3
        }
    except (ValueError, UnicodeDecodeError):
        return False

def _iter_shapes(shapes):
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)


def _readback_text(value):
    value = unicodedata.normalize("NFC", value or "")
    return re.sub(r"\s+", " ", value).strip()


def _binding_digest(value):
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_preservation_readback(deck, receipts, expected_image_digests):
    pages = []
    for index, (slide, receipt, image_digests) in enumerate(
        zip(deck.slides, receipts, expected_image_digests)
    ):
        shapes = list(_iter_shapes(slide.shapes))
        text_bindings = [
            binding for binding in receipt["bindings"]
            if binding["kind"] in {"text", "table_cell"} and binding["text"]
        ]
        expected_text = Counter(_readback_text(binding["text"]) for binding in text_bindings)
        actual_text = Counter(
            value for shape in shapes if shape.has_text_frame
            if (value := _readback_text(shape.text_frame.text))
        )
        if actual_text != expected_text:
            raise ValueError("svg_source_pptx_text_readback_mismatch")

        image_bindings = [binding for binding in receipt["bindings"] if binding["kind"] == "image"]
        if set(image_digests) != {binding["slot_id"] for binding in image_bindings}:
            raise ValueError("svg_source_pptx_image_expectation_mismatch")
        expected_images = Counter(image_digests[binding["slot_id"]] for binding in image_bindings)
        actual_images = Counter(
            hashlib.sha256(shape.image.blob).hexdigest()
            for shape in shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
        )
        if actual_images != expected_images:
            raise ValueError("svg_source_pptx_image_readback_mismatch")

        table_bindings = [binding for binding in receipt["bindings"] if binding["kind"] == "table_cell"]
        table_shapes = [shape for shape in shapes if shape.has_table]
        pages.append({
            "sourcePageId": receipt["source_page_id"],
            "sourcePageIndex": index,
            "svgDigest": receipt["svg_digest"],
            "text": {
                "representation": "native-text-shapes",
                "expected": [{"sourceId": binding["source_id"],
                    "valueDigest": _binding_digest(binding["text"])} for binding in text_bindings],
                "matchedBindingCount": len(text_bindings),
                "blankBindingCount": sum(
                    binding["kind"] in {"text", "table_cell"} and not binding["text"]
                    for binding in receipt["bindings"]
                ),
            },
            "tables": {
                "representation": "native-text-and-shape-cells",
                "nativeTableShapeCount": len(table_shapes),
                "nativeTableEditabilityVerified": False,
                "expectedCells": [{
                    "sourceId": binding["source_id"],
                    "row": binding["row"],
                    "column": binding["column"],
                    "rowSpan": binding["row_span"],
                    "columnSpan": binding["column_span"],
                    "valueDigest": _binding_digest(binding["text"]),
                } for binding in table_bindings],
                "matchedCellValueCount": len(table_bindings),
            },
            "images": {
                "representation": "native-picture-shapes",
                "expectedOccurrences": [{
                    "sourceId": binding["source_id"],
                    "slotId": binding["slot_id"],
                    "sourceAssetDigest": binding["asset_digest"],
                    "exportAssetDigest": "sha256:" + image_digests[binding["slot_id"]],
                } for binding in image_bindings],
                "matchedOccurrenceCount": len(image_bindings),
            },
        })
    return {
        "schemaVersion": "svg-source-preservation-export-readback/v1",
        "passed": True,
        "sourceDigest": receipts[0]["source_digest"],
        "manifestDigest": receipts[0]["manifest_digest"],
        "pages": pages,
    }


def convert_pptx(paths, target, notes, canvas, *, source_preservation=None,
                 expected_image_digests=None):
    converter_digest()
    from services.source_content.raster_redesign_pptx import prepare_redesign_pptx_inputs
    # Raster-redesign pages only: one text frame per paragraph block and
    # native tables derived from their validated grids. Other decks convert
    # exactly as before (merge_paragraphs/native_objects stay off).
    redesign = prepare_redesign_pptx_inputs(paths)
    fallback_reason = None
    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from svg_to_pptx import create_pptx_with_native_svg
        from svg_to_pptx.conversion_context import ConversionContext
        width_emu, height_emu = canvas_physical_size(canvas)
        context = ConversionContext(
            canvas.width_px, canvas.height_px, width_emu, height_emu,
            legacy=isinstance(canvas, AuthoredCanvasMetadata),
        )

        def run(native_objects):
            return create_pptx_with_native_svg(
                paths, target, verbose=False, transition=None, use_native_shapes=True,
                notes={path.stem: note for path, note in zip(paths, notes)},
                workers=1, merge_paragraphs=redesign.active, image_optimize=False,
                pptx_structure="flat", use_compat_mode=False,
                conversion_context=context, native_objects=native_objects,
            )

        try:
            success = run(redesign.native_tables > 0)
        except RuntimeError as error:
            if not redesign.native_tables:
                raise
            success = False
            fallback_reason = str(error)[:200]
        if not success and redesign.native_tables:
            # Native table rejected: convert the validated grids instead and
            # record it in the readback evidence (never silently).
            fallback_reason = fallback_reason or "native_table_conversion_failed"
            target.unlink(missing_ok=True)
            redesign.restore_grid()
            success = run(False)
    finally:
        sys.path.remove(str(VENDOR_ROOT))
    if not success or not target.is_file():
        raise ValueError("svg_native_conversion_failed")
    deck = Presentation(str(target))
    if len(deck.slides) != len(paths) or (deck.slide_width, deck.slide_height) != canvas_physical_size(canvas):
        raise ValueError("svg_pptx_readback_mismatch")
    readback = {"slideCount": len(paths), "nativeTextRuns": sum(
        len(p.runs) for slide in deck.slides for shape in _iter_shapes(slide.shapes)
        if shape.has_text_frame for p in shape.text_frame.paragraphs
    )}
    if redesign.active:
        readback["rasterRedesign"] = redesign.evidence(
            delivered_native=fallback_reason is None, fallback_reason=fallback_reason,
        )
    if source_preservation is not None:
        if (len(source_preservation) != len(paths) or expected_image_digests is None
                or len(expected_image_digests) != len(paths)):
            raise ValueError("svg_source_pptx_readback_input_invalid")
        readback["sourcePreservation"] = _source_preservation_readback(
            deck, source_preservation, expected_image_digests
        )
    return readback


def _validate_source_preservation_entry(source, receipt, *, index, page_count, canvas):
    from services.svg_contract import SVG_NS, source_digest
    from xml.etree import ElementTree as ET
    expected = {
        "contract_version": "svg-source-preservation/v1",
        "source_page_index": index,
        "page_count": page_count,
        "svg_digest": source_digest(source),
        "canvas": {"width": canvas.width_px, "height": canvas.height_px},
    }
    if not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in expected.items()):
        raise PublicWorkerExecutionError("svg_source_export_receipt_input_mismatch")
    if receipt.get("rendering_verified") is not True:
        raise PublicWorkerExecutionError("svg_source_export_rendering_unverified")
    if not all(isinstance(receipt.get(key), str) and receipt[key] for key in (
        "manifest_digest", "source_digest", "source_page_id", "page_digest", "plan_digest", "style_digest"
    )):
        raise PublicWorkerExecutionError("svg_source_export_receipt_invalid")
    checks = receipt.get("checks")
    if (not isinstance(checks, dict) or any(checks.get(key) is not True for key in (
        "source_ids_exact", "text_exact", "table_structure_exact", "image_occurrences_exact"
    ))):
        raise PublicWorkerExecutionError("svg_source_export_preservation_failed")
    bindings = receipt.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        raise PublicWorkerExecutionError("svg_source_export_bindings_invalid")
    root = ET.fromstring(source)
    elements = {element.get("id"): element for element in root.iter() if element.get("id")}
    element_ids = [binding.get("element_id") for binding in bindings if isinstance(binding, dict)]
    if len(element_ids) != len(bindings) or len(set(element_ids)) != len(element_ids):
        raise PublicWorkerExecutionError("svg_source_export_bindings_invalid")
    for binding in bindings:
        kind = binding.get("kind")
        element = elements.get(binding.get("element_id"))
        if element is None or kind not in {"text", "table_cell", "image", "shape"}:
            raise PublicWorkerExecutionError("svg_source_export_binding_mismatch")
        if kind in {"text", "table_cell"}:
            text = binding.get("text")
            if (not isinstance(text, str) or element.tag != f"{{{SVG_NS}}}text"
                    or "".join(element.itertext()) != text):
                raise PublicWorkerExecutionError("svg_source_export_text_binding_mismatch")
            if kind == "table_cell" and any(type(binding.get(key)) is not int for key in (
                "row", "column", "row_span", "column_span"
            )):
                raise PublicWorkerExecutionError("svg_source_export_table_binding_mismatch")
        elif kind == "image":
            if (element.tag != f"{{{SVG_NS}}}image"
                    or element.get("href") != binding.get("slot_id")
                    or not binding.get("asset_digest")):
                raise PublicWorkerExecutionError("svg_source_export_image_binding_mismatch")
    visible = {element.get("id") for element in root.iter()
               if element.tag in {f"{{{SVG_NS}}}text", f"{{{SVG_NS}}}image"}}
    expected_visible = {binding["element_id"] for binding in bindings
                        if binding["kind"] in {"text", "table_cell", "image"}}
    if visible != expected_visible:
        raise PublicWorkerExecutionError("svg_source_export_unbound_content")
    return receipt

async def execute_svg_export(job, store):
    from services.svg_contract import SVG_CONTRACT_VERSION, validate_svg
    value = json.loads(store.read(job.input_artifact))
    v2 = value.get("schemaVersion") == SVG_EXPORT_SCHEMA_V2
    v3 = value.get("schemaVersion") == SVG_EXPORT_SCHEMA_V3
    expected_keys = {"schemaVersion", "contractVersion", "converterVersion", "converterDigest", "canonicalExportIdentity", "exportFormat", "canvas", "slides"}
    if v2 or v3:
        expected_keys |= {"presentationId", "sourceRevision"}
        import uuid
        uuid.UUID(value["presentationId"])
        if type(value["sourceRevision"]) is not int or value["sourceRevision"] < 0:
            raise PublicWorkerExecutionError("svg_export_revision_invalid")
    if set(value) != expected_keys or value["schemaVersion"] not in {
        SVG_EXPORT_SCHEMA, SVG_EXPORT_SCHEMA_V2, SVG_EXPORT_SCHEMA_V3
    }:
        raise PublicWorkerExecutionError("svg_export_bundle_invalid")
    if value["contractVersion"] != ("svg-static/v2" if (v2 or v3) else SVG_CONTRACT_VERSION) or value["converterVersion"] != CONVERTER_VERSION or value["converterDigest"] != converter_digest():
        raise PublicWorkerExecutionError("svg_export_version_mismatch")
    canvas = parse_canvas_metadata(value["canvas"])
    slides = value["slides"]
    fmt = value["exportFormat"]
    if fmt not in {"pptx", "pdf"} or (v3 and fmt != "pptx") or not isinstance(slides, list) or not 1 <= len(slides) <= 100:
        raise PublicWorkerExecutionError("svg_export_slide_count_invalid")
    sources, notes, digests, admitted_assets = [], [], [], []
    source_preservation, expected_image_digests = [], []
    for index, entry in enumerate(slides):
        expected_slide_keys = ({"slideIndex", "artifactRef", "notes", "assets", "sourcePreservation"}
                               if v3 else {"slideIndex", "artifactRef", "notes", "assets"}
                               if v2 else {"slideIndex", "artifactRef", "notes"})
        if set(entry) != expected_slide_keys or entry["slideIndex"] != index or not isinstance(entry["notes"], str) or len(entry["notes"]) > 100000:
            raise PublicWorkerExecutionError("svg_export_slide_invalid")
        ref = ArtifactRef.from_wire(entry["artifactRef"])
        if ref.owner_id != job.input_artifact.owner_id or ref.tenant_id != job.input_artifact.tenant_id or ref.created_by_operation_id != job.operation_id or ref.kind != "slide-svg" or ref.media_type != "image/svg+xml":
            raise PublicWorkerExecutionError("svg_export_source_binding_invalid")
        source = store.read(ref).decode("utf-8")
        if v3:
            source_preservation.append(_validate_source_preservation_entry(
                source, entry["sourcePreservation"], index=index,
                page_count=len(slides), canvas=canvas,
            ))
        assets = entry.get("assets")
        if assets is not None:
            from services.svg_export_assets import materialize_export_assets, verified_full_page_preserve_export
            from utils.renderer_isolation import admit_authorized_svg_materialized
            canonical_source = source
            source, source_contract = materialize_export_assets(assets, svg=source, job=job, store=store,
                presentation_id=value["presentationId"], export_format=fmt,
                width=canvas.width_px, height=canvas.height_px)
            admitted_assets.append(admit_authorized_svg_materialized(canonical_source, source,
                source_manifest=source_contract, width_px=canvas.width_px, height_px=canvas.height_px, allow_image_only=verified_full_page_preserve_export(assets, canonical_svg=canonical_source, source_contract=source_contract)))
        else:
            validate_svg(source, width=canvas.width_px, height=canvas.height_px)
            admitted_assets.append(None)
        if v3:
            image_bindings = [binding for binding in source_preservation[-1]["bindings"]
                              if binding["kind"] == "image"]
            derivatives = assets.get("derivatives", {}) if isinstance(assets, dict) else {}
            if set(derivatives) != {binding["slot_id"] for binding in image_bindings}:
                raise PublicWorkerExecutionError("svg_source_export_image_transport_mismatch")
            expected_image_digests.append({
                slot: ArtifactRef.from_wire(wire).sha256 for slot, wire in derivatives.items()
            })
        sources.append(source); notes.append(entry["notes"]); digests.append(ref.sha256)
    if v3 and (len({receipt["manifest_digest"] for receipt in source_preservation}) != 1
               or len({receipt["source_digest"] for receipt in source_preservation}) != 1):
        raise PublicWorkerExecutionError("svg_source_export_deck_receipt_mismatch")
    with tempfile.TemporaryDirectory(prefix="public-svg-export-") as temporary:
        scratch = Path(temporary); target = scratch / f"result.{fmt}"
        if time.time() >= job.deadline_epoch_seconds:
            raise PublicWorkerExecutionError("svg_export_deadline_exhausted")
        if fmt == "pptx":
            paths=[]
            for index, source in enumerate(sources):
                path=scratch/f"slide-{index+1:03d}.svg";path.write_text(source, encoding="utf-8");paths.append(path)
            readback=convert_pptx(paths,target,notes,canvas,
                source_preservation=source_preservation if v3 else None,
                expected_image_digests=expected_image_digests if v3 else None)
            mode="editable-svg"
        else:
            from workers.public_svg_pdf_export import export_svg_pdf
            export_svg_pdf(sources, admitted_assets, canvas, target,
                timeout=max(1,min(120,int(job.deadline_epoch_seconds-time.time()))))
            mode = "svg-vector-pdf"
            readback = {"slideCount": len(sources), "rasterized": False, "vectorText": True}
        artifact=store.put_path(target,source=job.input_artifact,operation_id=job.operation_id,kind=f"presentation-{fmt}",media_type="application/pdf" if fmt=="pdf" else "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        evidence={"sourceFormat":"svg","requestedExportMode":mode,"deliveredExportMode":mode,"contractVersion":value["contractVersion"],"converterVersion":CONVERTER_VERSION,"converterDigest":value["converterDigest"],"sourceDigests":digests,"assetManifestDigests":[{"source":entry["assets"]["sourceManifestDigest"],"placement":entry["assets"]["placementManifestDigest"]} if entry.get("assets") else None for entry in slides],"canvas":value["canvas"],"structuralReadback":readback,"visualQualityVerified":False,"fallbackReason":None}
        if fmt == "pdf":
            evidence["pdfTextLayer"] = "vector"
        return WorkerOutput(artifact=artifact,evidence=evidence)
