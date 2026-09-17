"""Isolated, source-preserving SVG export; no HTML hybrid fallback."""
from __future__ import annotations
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from pptx import Presentation
from services.authored_canvas import AuthoredCanvasMetadata
from services.isolated_worker_contracts import ArtifactRef
from workers.public_worker_runtime import PublicWorkerExecutionError, WorkerOutput

SVG_EXPORT_SCHEMA = "public-svg-export-bundle/v1"
SVG_EXPORT_SCHEMA_V2 = "public-svg-export-bundle/v2"
CONVERTER_VERSION = "slide-master/166472bd2a22de9aa9fb6c8cdf8b0cdfc6b698ef"
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
        return isinstance(value, dict) and value.get("schemaVersion") in {SVG_EXPORT_SCHEMA, SVG_EXPORT_SCHEMA_V2}
    except (ValueError, UnicodeDecodeError):
        return False

def convert_pptx(paths, target, notes, canvas):
    converter_digest()
    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from svg_to_pptx import create_pptx_with_native_svg
        success = create_pptx_with_native_svg(
            paths, target, verbose=False, transition=None, use_native_shapes=True,
            notes={path.stem: note for path, note in zip(paths, notes)},
            workers=1, merge_paragraphs=False, image_optimize=False,
            pptx_structure="flat", use_compat_mode=False,
        )
    finally:
        sys.path.remove(str(VENDOR_ROOT))
    if not success or not target.is_file():
        raise ValueError("svg_native_conversion_failed")
    deck = Presentation(str(target))
    if len(deck.slides) != len(paths) or (deck.slide_width, deck.slide_height) != (canvas.width_px * 9525, canvas.height_px * 9525):
        raise ValueError("svg_pptx_readback_mismatch")
    return {"slideCount": len(paths), "nativeTextRuns": sum(len(p.runs) for slide in deck.slides for shape in slide.shapes if shape.has_text_frame for p in shape.text_frame.paragraphs)}

async def execute_svg_export(job, store):
    from services.svg_contract import SVG_CONTRACT_VERSION, validate_svg
    value = json.loads(store.read(job.input_artifact))
    v2 = value.get("schemaVersion") == SVG_EXPORT_SCHEMA_V2
    expected_keys = {"schemaVersion", "contractVersion", "converterVersion", "converterDigest", "canonicalExportIdentity", "exportFormat", "canvas", "slides"}
    if v2:
        expected_keys |= {"presentationId", "sourceRevision"}
        import uuid
        uuid.UUID(value["presentationId"])
        if type(value["sourceRevision"]) is not int or value["sourceRevision"] < 0:
            raise PublicWorkerExecutionError("svg_export_revision_invalid")
    if set(value) != expected_keys or value["schemaVersion"] not in {SVG_EXPORT_SCHEMA, SVG_EXPORT_SCHEMA_V2}:
        raise PublicWorkerExecutionError("svg_export_bundle_invalid")
    if value["contractVersion"] != ("svg-static/v2" if v2 else SVG_CONTRACT_VERSION) or value["converterVersion"] != CONVERTER_VERSION or value["converterDigest"] != converter_digest():
        raise PublicWorkerExecutionError("svg_export_version_mismatch")
    canvas = AuthoredCanvasMetadata.model_validate(value["canvas"])
    slides = value["slides"]
    fmt = value["exportFormat"]
    if fmt not in {"pptx", "pdf"} or not isinstance(slides, list) or not 1 <= len(slides) <= 100:
        raise PublicWorkerExecutionError("svg_export_slide_count_invalid")
    sources, notes, digests, admitted_assets = [], [], [], []
    for index, entry in enumerate(slides):
        if set(entry) != ({"slideIndex", "artifactRef", "notes", "assets"} if v2 else {"slideIndex", "artifactRef", "notes"}) or entry["slideIndex"] != index or not isinstance(entry["notes"], str) or len(entry["notes"]) > 100000:
            raise PublicWorkerExecutionError("svg_export_slide_invalid")
        ref = ArtifactRef.from_wire(entry["artifactRef"])
        if ref.owner_id != job.input_artifact.owner_id or ref.tenant_id != job.input_artifact.tenant_id or ref.created_by_operation_id != job.operation_id or ref.kind != "slide-svg" or ref.media_type != "image/svg+xml":
            raise PublicWorkerExecutionError("svg_export_source_binding_invalid")
        source = store.read(ref).decode("utf-8")
        assets = entry.get("assets")
        if assets is not None:
            from services.svg_export_assets import materialize_export_assets
            from utils.renderer_isolation import admit_authorized_svg_materialized
            canonical_source = source
            source, source_contract = materialize_export_assets(assets, svg=source, job=job, store=store,
                presentation_id=value["presentationId"], export_format=fmt,
                width=canvas.width_px, height=canvas.height_px)
            admitted_assets.append(admit_authorized_svg_materialized(canonical_source, source,
                source_manifest=source_contract, width_px=canvas.width_px, height_px=canvas.height_px))
        else:
            validate_svg(source, width=canvas.width_px, height=canvas.height_px)
            admitted_assets.append(None)
        sources.append(source); notes.append(entry["notes"]); digests.append(ref.sha256)
    with tempfile.TemporaryDirectory(prefix="public-svg-export-") as temporary:
        scratch = Path(temporary); target = scratch / f"result.{fmt}"
        if time.time() >= job.deadline_epoch_seconds:
            raise PublicWorkerExecutionError("svg_export_deadline_exhausted")
        if fmt == "pptx":
            paths=[]
            for index, source in enumerate(sources):
                path=scratch/f"slide-{index+1:03d}.svg";path.write_text(source, encoding="utf-8");paths.append(path)
            readback=convert_pptx(paths,target,notes,canvas)
            mode="editable-svg"
        else:
            from services.svg_renderer import svg_preview_html
            from workers.public_render_worker import _admit_html
            from utils.slide_capture import _render_admitted_htmls_over_pipe
            from workers.public_export_worker import _build_pdf
            # Asset SVGs already passed canonical admission and an authenticated
            # manifest/decoder check. Never feed expanded base64 through the
            # legacy canonical-source size limit a second time.
            wrappers=[]
            for source, admitted in zip(sources, admitted_assets):
                if admitted is not None:
                    wrappers.append(admitted)
                else:
                    wrapper = svg_preview_html(source, width=canvas.width_px, height=canvas.height_px)
                    wrappers.append(_admit_html(wrapper))
            rendered=_render_admitted_htmls_over_pipe(wrappers, timeout=max(1,min(120,int(job.deadline_epoch_seconds-time.time()))),width_px=canvas.width_px,height_px=canvas.height_px)
            if len(rendered) != len(sources):
                raise PublicWorkerExecutionError("svg_export_render_slide_count_mismatch")
            refs=[store.put(png,source=job.input_artifact,operation_id=job.operation_id,kind="slide-png",media_type="image/png") for png,_ in rendered]
            _build_pdf(refs,store,target,canvas)
            mode="fidelity-raster";readback={"slideCount":len(refs),"rasterized":True}
        artifact=store.put_path(target,source=job.input_artifact,operation_id=job.operation_id,kind=f"presentation-{fmt}",media_type="application/pdf" if fmt=="pdf" else "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        return WorkerOutput(artifact=artifact,evidence={"sourceFormat":"svg","requestedExportMode":mode,"deliveredExportMode":mode,"contractVersion":value["contractVersion"],"converterVersion":CONVERTER_VERSION,"converterDigest":value["converterDigest"],"sourceDigests":digests,"assetManifestDigests":[{"source":entry["assets"]["sourceManifestDigest"],"placement":entry["assets"]["placementManifestDigest"]} if entry.get("assets") else None for entry in slides],"canvas":value["canvas"],"structuralReadback":readback,"visualQualityVerified":False,"fallbackReason":None})
