"""Isolated, source-preserving SVG export; no HTML hybrid fallback."""
from __future__ import annotations

import json
import logging
import tempfile
import time
from pathlib import Path

from services.document_canvas import parse_canvas_metadata
from services.isolated_worker_contracts import ArtifactRef
from services.svg_export_source_preservation import page_svg_digest
from workers.public_svg_pptx_convert import CONVERTER_VERSION, VENDOR_ROOT, convert_pptx, converter_digest
from workers.public_svg_pptx_readback import source_preservation_readback as _source_preservation_readback
from workers.public_worker_runtime import PublicWorkerExecutionError, WorkerOutput

SVG_EXPORT_SCHEMA = "public-svg-export-bundle/v1"
SVG_EXPORT_SCHEMA_V2 = "public-svg-export-bundle/v2"
SVG_EXPORT_SCHEMA_V3 = "public-svg-export-bundle/v3"
SVG_EXPORT_SCHEMA_V4 = "public-svg-export-bundle/v4"
LOGGER = logging.getLogger(__name__)
# Re-exported: callers and tests import the converter surface from this module.
__all__ = ["CONVERTER_VERSION", "SVG_EXPORT_SCHEMA", "SVG_EXPORT_SCHEMA_V2", "SVG_EXPORT_SCHEMA_V3",
           "SVG_EXPORT_SCHEMA_V4",
           "VENDOR_ROOT", "_source_preservation_readback", "convert_pptx", "converter_digest",
           "execute_svg_export", "is_svg_export_bundle"]


def load_svg_export_bundle(job, store):
    """Parse the input bundle once; the parsed value when it is an svg export bundle, else None."""
    try:
        value = json.loads(store.read(job.input_artifact))
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(value, dict) and value.get("schemaVersion") in {
        SVG_EXPORT_SCHEMA, SVG_EXPORT_SCHEMA_V2, SVG_EXPORT_SCHEMA_V3, SVG_EXPORT_SCHEMA_V4
    }:
        return value
    return None


def is_svg_export_bundle(job, store):
    return load_svg_export_bundle(job, store) is not None


def _first_receipt(slides):
    """The first page receipt of a source bundle; a placeholder carries none."""
    return next((entry["sourcePreservation"] for entry in slides
                 if isinstance(entry, dict) and isinstance(entry.get("sourcePreservation"), dict)), {})


def _validate_source_preservation_entry(source, receipt, *, index, page_count, canvas,
                                        source_manifest=None, pages_resolver=None):
    """The receipt that binds this page's bytes to the source, or ``None``.

    ``None`` is a failure placeholder: admission
    (``services/svg_export_source_preservation.py``) lets a page publish without
    a receipt only when it is the terminal ladder's placeholder, so the page is
    exported as drawn and claims no source content. A receipt is verified
    exactly; a mismatch is fatal. ``rendering_verified`` is a measured page
    outcome, not part of the binding: admission requires the page's
    ``source_visibility`` flag when it is not true, and the export receipt
    reports it (``svg_export_quality``).
    """
    from services.svg_contract import SVG_NS, source_digest
    from xml.etree import ElementTree as ET
    if receipt is None:
        return None
    expected = {
        "contract_version": "svg-source-preservation/v1",
        "source_page_index": index,
        "page_count": page_count,
        "svg_digest": source_digest(source),
        "canvas": {"width": canvas.width_px, "height": canvas.height_px},
    }
    if not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in expected.items()):
        raise PublicWorkerExecutionError("svg_source_export_receipt_input_mismatch")
    if type(receipt.get("rendering_verified")) is not bool:
        raise PublicWorkerExecutionError("svg_source_export_receipt_invalid")
    whole_page = source_manifest is not None
    if not all(isinstance(receipt.get(key), str) and receipt[key] for key in (
        "manifest_digest", "source_digest", "source_page_id", "page_digest", "style_digest"
    ) + (() if whole_page else ("plan_digest",))):
        raise PublicWorkerExecutionError("svg_source_export_receipt_invalid")
    checks = receipt.get("checks")
    if not whole_page and (not isinstance(checks, dict) or any(checks.get(key) is not True for key in (
        "source_ids_exact", "text_exact", "table_structure_exact", "image_occurrences_exact"
    ))):
        raise PublicWorkerExecutionError("svg_source_export_preservation_failed")
    if whole_page:
        from services.svg_source_layout import ValidatedSourcePages
        if receipt.get("source_authoring_contract") != {"version": "svg-source-whole-page/v1"}:
            raise PublicWorkerExecutionError("svg_source_export_whole_page_contract_invalid")
        try:
            (pages_resolver or ValidatedSourcePages(source_manifest)).verify_receipt(source, receipt, index)
        except (ValueError, TypeError, KeyError) as error:
            raise PublicWorkerExecutionError("svg_source_export_whole_page_proof_invalid") from error
    bindings = receipt.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        raise PublicWorkerExecutionError("svg_source_export_bindings_invalid")
    root = ET.fromstring(source)
    from services.svg_master_furniture import attested_source_furniture
    try:
        furniture = attested_source_furniture(source, receipt)
    except ValueError as error:
        raise PublicWorkerExecutionError(str(error)) from error
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
    if not whole_page and visible - set(furniture) != expected_visible:
        raise PublicWorkerExecutionError("svg_source_export_unbound_content")
    if not furniture:
        return receipt
    import base64
    from utils.hashing import sha256_hex

    # Derived only after receipt, SVG and furniture-image attestation. The
    # body still has its complete original set of exact source bindings.
    export_furniture = {
        "texts": ["".join(node.itertext()) for node in furniture.values()
                  if node.tag == f"{{{SVG_NS}}}text"],
        "image_digests": [sha256_hex(base64.b64decode(node.get("href").split(",", 1)[1], validate=True))
                          for node in furniture.values() if node.tag == f"{{{SVG_NS}}}image"],
        "digest": receipt["system_furniture"]["digest"],
    }
    return {**receipt, "_export_furniture": export_furniture}


_BUNDLE_KEYS = {"schemaVersion", "contractVersion", "converterVersion", "converterDigest",
                "canonicalExportIdentity", "exportFormat", "canvas", "fontEmbedding", "slides"}
_PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _native_objects(value, fmt, slide_count):
    """Per-page sidecars for the PPTX scene; the vector PDF never carries them."""
    from services.generation_core.drivers.svg.export_native_wire import BUNDLE_KEY, parse_bundle_native_objects
    if BUNDLE_KEY not in value:
        return None
    if fmt != "pptx":
        raise PublicWorkerExecutionError("svg_export_native_objects_pptx_only")
    try:
        return parse_bundle_native_objects(value[BUNDLE_KEY], slide_count)
    except ValueError as error:
        raise PublicWorkerExecutionError(str(error)) from error


def _evidence(value, slides, digests, mode, readback, stage_timings):
    return {
        "sourceFormat": "svg", "requestedExportMode": mode, "deliveredExportMode": mode,
        "contractVersion": value["contractVersion"], "converterVersion": CONVERTER_VERSION,
        "converterDigest": value["converterDigest"], "sourceDigests": digests,
        "assetManifestDigests": [{"source": entry["assets"]["sourceManifestDigest"],
                                  "placement": entry["assets"]["placementManifestDigest"]}
                                 if entry.get("assets") else None for entry in slides],
        "canvas": value["canvas"], "structuralReadback": readback, "visualQualityVerified": False,
        "fallbackReason": None,
        # Diagnostic spans only; never included in structural or identity digests.
        "stageTimingsSeconds": dict(stage_timings),
    }


async def execute_svg_export(job, store, *, bundle=None):
    stage_started = time.monotonic()
    stage_timings = {}
    from services.svg_contract import SVG_CONTRACT_VERSION, validate_svg
    # ``bundle`` is this job's input already parsed by the caller (same process, same bytes).
    value = bundle if bundle is not None else json.loads(store.read(job.input_artifact))
    v2 = value.get("schemaVersion") == SVG_EXPORT_SCHEMA_V2
    v3 = value.get("schemaVersion") == SVG_EXPORT_SCHEMA_V3
    v4 = value.get("schemaVersion") == SVG_EXPORT_SCHEMA_V4
    expected_keys = _BUNDLE_KEYS | ({"nativeObjects", "fontManifest"} & set(value))
    if v2 or v3 or v4:
        expected_keys |= {"presentationId", "sourceRevision"}
        if v4:
            expected_keys |= {"sourceManifest", "wholePageVerifierDigest"}
        import uuid
        uuid.UUID(value["presentationId"])
        if type(value["sourceRevision"]) is not int or value["sourceRevision"] < 0:
            raise PublicWorkerExecutionError("svg_export_revision_invalid")
    if set(value) != expected_keys or value["schemaVersion"] not in {
        SVG_EXPORT_SCHEMA, SVG_EXPORT_SCHEMA_V2, SVG_EXPORT_SCHEMA_V3, SVG_EXPORT_SCHEMA_V4
    }:
        raise PublicWorkerExecutionError("svg_export_bundle_invalid")
    if (value["contractVersion"] != ("svg-static/v2" if (v2 or v3 or v4) else SVG_CONTRACT_VERSION)
            or value["converterVersion"] != CONVERTER_VERSION or value["converterDigest"] != converter_digest()):
        raise PublicWorkerExecutionError("svg_export_version_mismatch")
    canvas = parse_canvas_metadata(value["canvas"])
    slides = value["slides"]
    fmt = value["exportFormat"]
    font_embedding = value["fontEmbedding"]
    if type(font_embedding) is not bool or (font_embedding and fmt != "pptx"):
        raise PublicWorkerExecutionError("svg_export_font_embedding_invalid")
    font_manifest = value.get("fontManifest")
    if font_manifest is not None:
        if (fmt == "pptx" and not font_embedding) or v3 or v4:
            raise PublicWorkerExecutionError("svg_template_font_export_unsupported")
        from services.svg_templates.frame_fonts import resolve_font_manifest
        resolve_font_manifest(font_manifest)
    if (fmt not in {"pptx", "pdf"} or ((v3 or v4) and fmt != "pptx") or not isinstance(slides, list)
            or not 1 <= len(slides) <= 100):
        raise PublicWorkerExecutionError("svg_export_slide_count_invalid")
    source_manifest = None
    pages_resolver = None
    if v4:
        from services.svg_export_whole_page_verifier import whole_page_verifier_digest
        from services.source_content.contracts import SourceContentManifest
        from services.source_content.digests import digest_json
        from services.svg_source_layout import ValidatedSourcePages
        if value.get("wholePageVerifierDigest") != whole_page_verifier_digest():
            raise PublicWorkerExecutionError("svg_source_export_verifier_code_mismatch")
        bound = value["sourceManifest"]
        owner_scope = str(job.input_artifact.owner_id) if job.input_artifact.owner_id else "anonymous"
        if (not isinstance(bound, dict)
                or set(bound) != {"ownerScope", "manifestDigest", "sourceDigest", "manifest"}
                or bound["ownerScope"] != owner_scope
                or not isinstance(bound["manifest"], dict)):
            raise PublicWorkerExecutionError("svg_source_export_manifest_binding_invalid")
        try:
            source_manifest = SourceContentManifest.model_validate({
                **bound["manifest"], "ownerId": owner_scope})
            pages_resolver = ValidatedSourcePages(source_manifest)
            sealed = source_manifest.model_dump(mode="json", by_alias=True)
            sealed.pop("manifestDigest", None)
            if (source_manifest.manifest_digest != bound["manifestDigest"]
                    or source_manifest.source_digest != bound["sourceDigest"]
                    or digest_json(sealed) != bound["manifestDigest"]
                    or len(pages_resolver.pages_for(_first_receipt(slides).get(
                        "source_chart_contract"))) != len(slides)):
                raise ValueError("source_manifest_mismatch")
        except (ValueError, TypeError, KeyError) as error:
            raise PublicWorkerExecutionError("svg_source_export_manifest_proof_invalid") from error
    native_objects = _native_objects(value, fmt, len(slides))
    sources, notes, digests, admitted_assets, canonical_sources = [], [], [], [], []
    source_preservation, expected_image_digests = [], []
    source_manifest_cache = {}
    for index, entry in enumerate(slides):
        expected_slide_keys = ({"slideIndex", "artifactRef", "notes", "assets", "sourcePreservation"}
                               if v3 or v4 else {"slideIndex", "artifactRef", "notes", "assets"}
                               if v2 else {"slideIndex", "artifactRef", "notes"})
        if "systemFurniture" in entry:
            expected_slide_keys.add("systemFurniture")
        if (set(entry) != expected_slide_keys or entry["slideIndex"] != index
                or not isinstance(entry["notes"], str) or len(entry["notes"]) > 100000):
            raise PublicWorkerExecutionError("svg_export_slide_invalid")
        ref = ArtifactRef.from_wire(entry["artifactRef"])
        if (ref.owner_id != job.input_artifact.owner_id or ref.tenant_id != job.input_artifact.tenant_id
                or ref.created_by_operation_id != job.operation_id or ref.kind != "slide-svg"
                or ref.media_type != "image/svg+xml"):
            raise PublicWorkerExecutionError("svg_export_source_binding_invalid")
        source = store.read(ref).decode("utf-8")
        canonical_sources.append(source)
        if v3 or v4:
            source_preservation.append(_validate_source_preservation_entry(
                source, entry["sourcePreservation"], index=index,
                page_count=len(slides), canvas=canvas, source_manifest=source_manifest,
                pages_resolver=pages_resolver,
            ))
        # A placeholder (no receipt) admits its assets as an ordinary page does.
        page_receipt = entry["sourcePreservation"] if v3 or v4 else None
        assets = entry.get("assets")
        if assets is not None:
            from services.svg_export_assets import materialize_export_assets, verified_full_page_preserve_export
            from utils.renderer_isolation import admit_authorized_svg_materialized
            canonical_source = source
            source, source_contract = materialize_export_assets(
                assets, svg=source, job=job, store=store, presentation_id=value["presentationId"],
                export_format=fmt, width=canvas.width_px, height=canvas.height_px,
                source_cache=source_manifest_cache,
                **({"source_preservation": page_receipt} if page_receipt is not None else {}))
            admitted_assets.append(admit_authorized_svg_materialized(
                canonical_source, source, source_manifest=source_contract, width_px=canvas.width_px,
                height_px=canvas.height_px, allow_image_only=verified_full_page_preserve_export(
                    assets, canonical_svg=canonical_source, source_contract=source_contract),
                **({"source_preservation": page_receipt} if page_receipt is not None else
                   {"system_furniture": assets["systemFurniture"]} if assets.get("systemFurniture") else {})))
        else:
            validate_svg(source, width=canvas.width_px, height=canvas.height_px)
            source_contract = None
            admitted_assets.append(None)
        if "systemFurniture" in entry:
            from services.svg_master_furniture import attested_svg_furniture
            if font_manifest is None:
                raise PublicWorkerExecutionError("svg_template_font_manifest_missing")
            attested_svg_furniture(canonical_sources[-1], entry["systemFurniture"],
                source_manifest=source_contract, width=canvas.width_px, height=canvas.height_px)
        elif font_manifest is not None:
            raise PublicWorkerExecutionError("svg_template_furniture_binding_missing")
        if v3 or v4:
            # A placeholder binds no source image, so it may carry no derivative.
            image_bindings = [binding for binding in (source_preservation[-1] or {}).get("bindings", [])
                              if binding["kind"] == "image"]
            derivatives = assets.get("derivatives", {}) if isinstance(assets, dict) else {}
            if set(derivatives) != {binding["slot_id"] for binding in image_bindings}:
                raise PublicWorkerExecutionError("svg_source_export_image_transport_mismatch")
            expected_image_digests.append({
                slot: ArtifactRef.from_wire(wire).sha256 for slot, wire in derivatives.items()
            })
        sources.append(source)
        notes.append(entry["notes"])
        digests.append(ref.sha256)
    present = [receipt for receipt in source_preservation if receipt is not None]
    if (v3 or v4) and (len({receipt["manifest_digest"] for receipt in present}) > 1
               or len({receipt["source_digest"] for receipt in present}) > 1):
        # Every page that claims the source claims the same one.
        raise PublicWorkerExecutionError("svg_source_export_deck_receipt_mismatch")
    if v4:
        from services.svg_export_bundle import digest as bundle_digest
        identity = bundle_digest({
            "revision": value["sourceRevision"], "sources": digests, "notes": notes,
            "canvas": value["canvas"], "format": fmt, "converter": value["converterDigest"],
            "contract": SVG_CONTRACT_VERSION, "fontEmbedding": font_embedding})
        identity = bundle_digest({"legacyIdentity": identity,
            "sourcePreservation": [bundle_digest(entry["sourcePreservation"]) for entry in slides],
            "manifestDigests": [(entry["assets"]["sourceManifestDigest"],
                                 entry["assets"]["placementManifestDigest"])
                                if entry["assets"] else None for entry in slides]})
        identity = bundle_digest({"legacyIdentity": identity,
            "sourceManifest": value["sourceManifest"],
            "wholePageVerifierDigest": value["wholePageVerifierDigest"]})
        if "nativeObjects" in value:
            identity = bundle_digest({"legacyIdentity": identity,
                                      "nativeObjects": value["nativeObjects"]})
        if identity != value["canonicalExportIdentity"]:
            raise PublicWorkerExecutionError("svg_source_export_bundle_identity_mismatch")
    template_font_usage = None
    if font_manifest is not None:
        from services.svg_renderer import _template_manifest_materials
        template_font_usage = {}
        for source in canonical_sources:
            _manifest, _entries, used = _template_manifest_materials(font_manifest, source)
            for family, weight, _asset in used:
                template_font_usage.setdefault(family, set()).add(weight)
    with tempfile.TemporaryDirectory(prefix="public-svg-export-") as temporary:
        stage_timings["bundleRead"] = time.monotonic() - stage_started
        stage_started = time.monotonic()
        scratch = Path(temporary)
        target = scratch / f"result.{fmt}"
        if time.time() >= job.deadline_epoch_seconds:
            raise PublicWorkerExecutionError("svg_export_deadline_exhausted")
        if fmt == "pptx":
            paths = []
            for index, source in enumerate(sources):
                path = scratch / f"slide-{index + 1:03d}.svg"
                path.write_text(source, encoding="utf-8")
                paths.append(path)
            if v4:
                from pptx import Presentation
                from services.generation_core.drivers.svg.export_native_wire import PageNativeObjects
                from workers.public_svg_whole_page_convert import convert_whole_page_pptx
                try:
                    readback, ownership_claims, fragment_claims = convert_whole_page_pptx(
                        paths, target, notes, canvas, receipts=source_preservation,
                        native_objects=native_objects or [PageNativeObjects() for _ in sources],
                        source_originals=canonical_sources, font_embedding=font_embedding)
                except (ValueError, TypeError, KeyError, IndexError) as error:
                    from workers.public_svg_projection_error import SvgExportProjectionError

                    # Deck-level failures have no page; page projections attach
                    # their actual index before crossing this boundary.
                    raise SvgExportProjectionError(None, error) from error
                stage_timings["conversion"] = time.monotonic() - stage_started
                stage_started = time.monotonic()
                try:
                    readback["sourcePreservation"] = _source_preservation_readback(
                        Presentation(str(target)), source_preservation, expected_image_digests,
                        native_objects=native_objects or [PageNativeObjects() for _ in sources],
                        source_svgs=canonical_sources, ownership_claims=ownership_claims,
                        fragment_claims=fragment_claims)
                except (ValueError, TypeError, KeyError, IndexError) as error:
                    raise PublicWorkerExecutionError("svg_source_export_readback_invalid") from error
            else:
                readback = convert_pptx(
                    paths, target, notes, canvas,
                    source_preservation=source_preservation if v3 else None,
                    expected_image_digests=expected_image_digests if v3 else None,
                    font_embedding=font_embedding, native_objects=native_objects,
                    **({"font_manifest": font_manifest, "font_usage": template_font_usage}
                       if font_manifest is not None else {}),
                    **({"native_coverage_sources": canonical_sources} if v2 else {}))
                # The converter call includes its own internal OOXML readback.
                stage_timings["conversion"] = time.monotonic() - stage_started
                stage_started = time.monotonic()
            if v4:
                from services.svg_export_whole_page_verifier import whole_page_verifier_digest
                from utils.hashing import canonical_json_sha256
                import hashlib
                coverage = readback.get("nativeCoverageOoxml")
                source_readback = readback.get("sourcePreservation")
                if (not isinstance(coverage, dict) or coverage.get("passed") is not True
                        or not isinstance(source_readback, dict) or source_readback.get("passed") is not True
                        or coverage.get("artifactDigest") != "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
                        or coverage.get("sourceDigests") != [
                            page_svg_digest(receipt, source)
                            for receipt, source in zip(source_preservation, canonical_sources, strict=True)]
                        or len(coverage.get("pages", [])) != len(source_readback.get("pages", []))
                        or any((proof["sourcePageId"], proof["svgDigest"])
                               != (bound["sourcePageId"], bound["svgDigest"])
                               for proof, bound in zip(coverage["pages"], source_readback["pages"], strict=True))):
                    raise PublicWorkerExecutionError("svg_native_ooxml_source_coverage_mismatch")
                coverage.update(operationId=str(job.operation_id), presentationId=value["presentationId"],
                                sourceRevision=value["sourceRevision"], verifierCodeDigest=whole_page_verifier_digest(),
                                converterDigest=converter_digest(),
                                sourceReadbackDigest="sha256:" + canonical_json_sha256(source_readback),
                                officeRenderMeasured=False)
                coverage["coverageDigest"] = "sha256:" + canonical_json_sha256(coverage)
            if v2:
                import hashlib
                coverage = readback.get("genericNativeCoverageOoxml")
                if (not isinstance(coverage, dict) or coverage.get("passed") is not True
                        or coverage.get("artifactDigest") != "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
                        or coverage.get("sourceDigests") != ["sha256:" + digest for digest in digests]
                        or len(coverage.get("pages", [])) != len(slides)
                        or any(page.get("pageIndex") != index or page.get("svgDigest") != "sha256:" + digests[index]
                               for index, page in enumerate(coverage["pages"]))
                        or readback.get("nativeObjects", {}).get("fellBack") != 0):
                    raise PublicWorkerExecutionError("svg_generic_native_ooxml_coverage_mismatch")
            mode = "editable-svg"
        else:
            # Vector PDF: native objects are a PPTX concept; drawn groups print as-is.
            from workers.public_svg_pdf_export import export_svg_pdf
            export_svg_pdf(sources, admitted_assets, canvas, target,
                           timeout=max(1, min(120, int(job.deadline_epoch_seconds - time.time()))),
                           **({"font_manifest": font_manifest, "canonical_sources": canonical_sources}
                              if font_manifest is not None else {}))
            stage_timings["conversion"] = time.monotonic() - stage_started
            stage_started = time.monotonic()
            mode = "svg-vector-pdf"
            readback = {"slideCount": len(sources), "rasterized": False, "vectorText": True}
        if font_manifest is not None:
            from services.svg_export_bundle import digest as bundle_digest
            readback["templateFurnitureDigests"] = [bundle_digest(entry["systemFurniture"]) for entry in slides]
        stage_timings["readback"] = time.monotonic() - stage_started
        stage_started = time.monotonic()
        artifact = store.put_path(
            target, source=job.input_artifact, operation_id=job.operation_id, kind=f"presentation-{fmt}",
            media_type="application/pdf" if fmt == "pdf" else _PPTX_MEDIA_TYPE)
        stage_timings["artifactPut"] = time.monotonic() - stage_started
        if fmt == "pptx" and v2:
            from services.svg_export_whole_page_verifier import whole_page_verifier_digest
            from utils.hashing import canonical_json_sha256
            coverage = readback.get("genericNativeCoverageOoxml")
            if coverage["artifactDigest"] != "sha256:" + artifact.sha256:
                raise PublicWorkerExecutionError("svg_generic_native_ooxml_coverage_mismatch")
            coverage.update(operationId=str(job.operation_id), presentationId=value["presentationId"],
                sourceRevision=value["sourceRevision"], ownerId=str(job.input_artifact.owner_id),
                tenantId=job.input_artifact.tenant_id, artifactRef=artifact.to_wire(),
                sourceArtifactRefs=[entry["artifactRef"] for entry in slides],
                verifierCodeDigest=whole_page_verifier_digest(), converterDigest=converter_digest(),
                officeRenderMeasured=False)
            coverage["coverageDigest"] = "sha256:" + canonical_json_sha256(coverage)
        evidence = _evidence(value, slides, digests, mode, readback, stage_timings)
        if fmt == "pdf":
            evidence["pdfTextLayer"] = "vector"
        LOGGER.info("svg_export_io phase=worker operation=%s cas_reads=%s cas_bytes_hashed=%s",
                    job.operation_id, getattr(store, "cas_reads", 0), getattr(store, "cas_bytes_hashed", 0))
        return WorkerOutput(artifact=artifact, evidence=evidence)
