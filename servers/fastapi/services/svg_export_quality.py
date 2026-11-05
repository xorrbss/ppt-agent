"""Public quality evidence from persisted SVG checks and export readback.

Source content readback is not complete OOXML editability or a visual review;
``visual_review`` comes from the deck's contact-sheet vision advisory and the
per-page classification publication recorded (``svg_visual_review``), and
``release_readiness`` is derived from the domains, never a constant.
"""
from __future__ import annotations

import json

from models.public_v2_contracts import QualityReceiptV3
from models.authored_vnext.capability_registry import CAPABILITY_REGISTRY_DIGEST
from services.generation_core.drivers.svg.export_scene import native_fallbacks_recorded
from services.svg_export_page_evidence import svg_authoring, svg_geometry_font
from services.svg_export_source_preservation import page_svg_digest, source_receipts_present
from services.svg_export_visual_review import release_readiness, svg_visual_review, page_measurement_current
from utils.hashing import sha256_prefixed

TEMPLATE_CHECKS = ("identity", "slots", "typography", "background", "table_style", "shape_style", "decorations")
SOURCE_CHECKS = ("source_ids_exact", "text_exact", "table_structure_exact", "image_occurrences_exact")


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return sha256_prefixed(encoded)


def _native_ooxml_domain(*, required, worker_metrics, artifact_digest, operation_id,
                         presentation_id, source_revision, source_preservation, slides=()):
    if not required:
        return dict(required=False, status="not_required")
    from services.svg_export_whole_page_verifier import whole_page_verifier_digest
    from workers.public_svg_converter_aliases import converter_digest_accepted
    from utils.hashing import canonical_json_sha256
    worker = worker_metrics.get("public_export_worker_evidence", {})
    structural = worker.get("structuralReadback", {})
    proof = structural.get("nativeCoverageOoxml")
    source_readback = structural.get("sourcePreservation")
    style_readback = structural.get("sourceStyleReadback")
    failed = dict(required=True, status="failed", reasonCode="svg_native_ooxml_coverage_evidence_invalid")
    if slides and not _common_header_ooxml_current(slides, structural):
        return failed
    header_readback = structural.get("commonHeaderReadback") or {}
    if header_readback.get("schemaVersion") == "svg-common-header-ooxml/v2" and (
            header_readback.get("finalPackageReverified") is not True
            or not header_readback.get("canonicalProjectionBinding")):
        return failed
    if not isinstance(proof, dict) or not isinstance(source_readback, dict) or not isinstance(style_readback, dict) or not source_preservation:
        return failed
    unsigned = {key: value for key, value in proof.items() if key != "coverageDigest"}
    measurements = proof.get("measurements")
    if (proof.get("schemaVersion") != "svg-native-ooxml-coverage/v1" or proof.get("passed") is not True
            or proof.get("artifactDigest") != artifact_digest or proof.get("operationId") != str(operation_id)
            or proof.get("presentationId") != str(presentation_id) or proof.get("sourceRevision") != source_revision
            or proof.get("verifierCodeDigest") != whole_page_verifier_digest()
            or not converter_digest_accepted(proof.get("converterDigest"))
            or proof.get("coverageDigest") != "sha256:" + canonical_json_sha256(unsigned)
            or proof.get("sourceReadbackDigest") != "sha256:" + canonical_json_sha256(source_readback)
            or proof.get("sourceStyleReadbackDigest") != "sha256:" + canonical_json_sha256(style_readback)
            or style_readback.get("schemaVersion") != "svg-source-export-style-readback/v1"
            or style_readback.get("passed") is not True
            or source_readback.get("passed") is not True
            or len(slides) != len(source_preservation)
            or proof.get("sourceDigests") != [page_svg_digest(receipt, slide.svg_content or "")
                                              for receipt, slide in zip(source_preservation, slides, strict=True)]
            or not isinstance(measurements, dict) or measurements.get("verifiedPages") != len(source_preservation)
            or any(measurements.get(key) != 0 for key in ("unboundShapes", "unboundPrimitives", "geometryMismatches"))
            or type(measurements.get("verifiedShapes")) is not int or measurements["verifiedShapes"] < 1
            or not isinstance(style_readback.get("rightAlignedCells"), list)
            or not isinstance(style_readback.get("uniformGridCharts"), list)
            or measurements.get("verifiedRightAlignedCells") != len(style_readback["rightAlignedCells"])
            or measurements.get("verifiedSourceGridColorCharts") != len(style_readback["uniformGridCharts"])
            or proof.get("officeRenderMeasured") is not False):
        return failed
    return dict(required=True, status="passed", evidence=dict(
        checkerVersion="svg-native-ooxml-coverage/v1", inputDigest=proof["coverageDigest"],
        outputDigest=artifact_digest, measurements=measurements,
        thresholds=dict(allPagesRequired=True, unboundShapesAllowed=0, unboundPrimitivesAllowed=0,
                        geometryMismatchesAllowed=0, officeRenderMeasured=False)))


def _common_header_ooxml_current(slides, proof):
    """Require the title-specific readback only for pages with a frozen header."""
    from workers.svg_common_header_export import header_title_lines
    from services.svg_export_page_evidence import _common_header_current
    from services.svg_contract import source_digest
    from utils.hashing import canonical_json_sha256
    import math

    try:
        from services.svg_common_header_export_evidence import header_scene, common_header_v2_ooxml_current
        if any(header_scene(slide.svg_content or "") is not None for slide in slides):
            v2_readback = proof.get("commonHeaderReadback") or {}
            if not common_header_v2_ooxml_current(slides, v2_readback):
                return False
            proof = {**proof, "commonHeaderReadback": v2_readback.get("legacyReadback")}
        expected_pages = [header_title_lines(slide.svg_content or "") for slide in slides]
        if not any(expected_pages):
            return True
        value = proof.get("commonHeaderReadback") or {}
        unsigned = {key: item for key, item in value.items() if key != "digest"}
        pages = value.get("pages")
        scale = value.get("pointPerPx")
        if (value.get("schemaVersion") != "svg-common-header-ooxml/v1" or value.get("passed") is not True
                or value.get("origin") != "generated-common-header" or value.get("officeRenderMeasured") is not False
                or value.get("digest") != "sha256:" + canonical_json_sha256(unsigned)
                or not isinstance(scale, (int, float)) or isinstance(scale, bool) or not math.isfinite(scale) or scale <= 0
                or not isinstance(pages, list) or len(pages) != len(slides)):
            return False
        for index, (slide, expected, page) in enumerate(zip(slides, expected_pages, pages, strict=True)):
            receipt = (slide.content or {}).get("svgReceipt") or {}
            if not _common_header_current(slide, receipt.get("quality") or {}):
                return False
            lines = page.get("lines")
            if (page.get("pageIndex") != index or page.get("svgDigest") != source_digest(slide.svg_content or "")
                    or not isinstance(lines, list) or len(lines) != len(expected)):
                return False
            shape_ids = set()
            for line, (identity, source) in zip(lines, expected.items(), strict=True):
                attrs = source["attributes"]
                bounds = line.get("boundsEmu")
                shape_id = line.get("shapeId")
                if (line.get("elementId") != identity or line.get("text") != source["text"]
                        or line.get("fontFamily") != attrs["font-family"]
                        or line.get("fontSizePx") != float(attrs["font-size"])
                        or not isinstance(line.get("fontSizePt"), (int, float))
                        or not math.isfinite(line["fontSizePt"])
                        or abs(line["fontSizePt"] - float(attrs["font-size"]) * scale) > .051
                        or line.get("fontWeight") != attrs.get("font-weight", "400")
                        or line.get("color") != attrs["fill"]
                        or line.get("alignment") != attrs.get("text-anchor", "start")
                        or line.get("svgAnchor") != {"x": float(attrs["x"]), "baselineY": float(attrs["y"])}
                        or line.get("autoFit") is not False or type(shape_id) is not int or shape_id < 1
                        or shape_id in shape_ids or not isinstance(bounds, list) or len(bounds) != 4
                        or any(type(number) is not int for number in bounds)
                        or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]):
                    return False
                shape_ids.add(shape_id)
        return True
    except (KeyError, ValueError, TypeError, AttributeError, SyntaxError):
        return False


def _generic_native_ooxml_domain(*, required, slides, worker_metrics, artifact_digest,
                                 operation_id, presentation_id, source_revision, review_binding,
                                 export_identity_digest=None, export_tenant_id=None):
    """Validate the existing final-package verifier's generic, owner-bound proof."""
    if not required:
        return dict(required=False, status="not_required")
    from services.isolated_worker_contracts import ArtifactRef
    from services.svg_contract import source_digest
    from services.svg_export_whole_page_verifier import whole_page_verifier_digest
    from workers.public_svg_converter_aliases import converter_digest_accepted
    from utils.hashing import canonical_json_sha256
    failed = dict(required=True, status="failed", reasonCode="svg_generic_native_ooxml_coverage_evidence_invalid")
    worker = worker_metrics.get("public_export_worker_evidence", {})
    structural = worker.get("structuralReadback", {})
    proof = structural.get("genericNativeCoverageOoxml")
    native = structural.get("nativeObjects")
    if (not isinstance(proof, dict) or not isinstance(native, dict)
            or not isinstance(review_binding, dict) or not review_binding.get("ownerId")):
        return failed
    if not _common_header_ooxml_current(slides, proof):
        return failed
    sources = [source_digest(slide.svg_content or "") for slide in slides]
    producer_operation_id = str(operation_id)
    if worker.get("reusedCompletedIdentity") is True:
        binding = worker.get("reuseProducerBinding")
        if not isinstance(binding, dict):
            return failed
        binding_unsigned = {key: value for key, value in binding.items() if key != "digest"}
        producer_operation_id = binding.get("producerOperationId")
        # The original worker envelope is retained independently of join telemetry.
        original = worker.get("reuseProducerWorkerEvidence")
        if not isinstance(original, dict):
            return failed
        if (binding.get("schemaVersion") != "svg-export-reuse-producer-binding/v1"
                or binding.get("digest") != "sha256:" + canonical_json_sha256(binding_unsigned)
                or binding.get("canonicalIdentityDigest") != export_identity_digest or not export_identity_digest
                or binding.get("consumerOperationId") != str(operation_id)
                or not isinstance(producer_operation_id, str) or producer_operation_id == str(operation_id)
                or binding.get("ownerId") != review_binding["ownerId"]
                or binding.get("ownerScope") != review_binding.get("ownerScope")
                or binding.get("tenantId") != export_tenant_id or not export_tenant_id
                or binding.get("presentationId") != str(presentation_id)
                or binding.get("sourceRevision") != source_revision
                or binding.get("artifactDigest") != artifact_digest
                or binding.get("producerWorkerEvidenceDigest") != "sha256:" + canonical_json_sha256(original)
                or original.get("sourceDigests") != [value[7:] for value in sources]
                or worker.get("sourceDigests") != original.get("sourceDigests")
                or structural != original.get("structuralReadback")):
            return failed
        try:
            producer_ref = ArtifactRef.from_wire(binding.get("producerArtifactRef", {}))
            consumer_ref = ArtifactRef.from_wire(binding.get("consumerArtifactRef", {}))
        except (ValueError, TypeError, KeyError, AttributeError):
            return failed
        if (any("sha256:" + ref.sha256 != artifact_digest or str(ref.owner_id) != review_binding["ownerId"]
                or ref.tenant_id != export_tenant_id or ref.kind != "presentation-pptx"
                or ref.media_type != "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                for ref in (producer_ref, consumer_ref))
                or str(producer_ref.created_by_operation_id) != producer_operation_id
                or str(consumer_ref.created_by_operation_id) != str(operation_id)
                or producer_ref.storage_key != consumer_ref.storage_key
                or producer_ref.size_bytes != consumer_ref.size_bytes):
            return failed
    unsigned = {key: value for key, value in proof.items() if key != "coverageDigest"}
    pages, measurements = proof.get("pages"), proof.get("measurements")
    def digest_valid(value):
        return (isinstance(value, str) and len(value) == 71 and value.startswith("sha256:")
                and all(char in "0123456789abcdef" for char in value[7:]))
    if (not sources or proof.get("schemaVersion") != "svg-generic-native-ooxml-coverage/v1"
            or proof.get("passed") is not True or proof.get("artifactDigest") != artifact_digest
            or proof.get("operationId") != producer_operation_id
            or proof.get("presentationId") != str(presentation_id)
            or proof.get("sourceRevision") != source_revision
            or proof.get("ownerId") != review_binding["ownerId"]
            or not isinstance(proof.get("tenantId"), str) or not proof["tenantId"]
            or ("tenantId" in review_binding and proof["tenantId"] != review_binding["tenantId"])
            or proof.get("sourceDigests") != sources
            or worker.get("sourceDigests") != [digest[7:] for digest in sources]
            or structural.get("slideCount") != len(sources)
            or proof.get("verifierCodeDigest") != whole_page_verifier_digest()
            or not converter_digest_accepted(proof.get("converterDigest"))
            or proof.get("coverageDigest") != "sha256:" + canonical_json_sha256(unsigned)
            or proof.get("nativeObjectsDigest") != "sha256:" + canonical_json_sha256(native)
            or not native_fallbacks_recorded(native) or worker.get("fallbackReason") is not None
            or proof.get("officeRenderMeasured") is not False
            or not digest_valid(proof.get("packagePartsDigest"))
            or not isinstance(pages, list) or len(pages) != len(sources)
            or not isinstance(measurements, dict) or type(measurements.get("verifiedPages")) is not int
            or measurements["verifiedPages"] != len(sources)
            or any(type(measurements.get(key)) is not int or measurements[key] != 0
                   for key in ("unboundShapes", "unboundPrimitives", "geometryMismatches", "unboundRelationships"))
            or any(type(measurements.get(key)) is not int or measurements[key] < 1
                   for key in ("verifiedShapes", "nativePrimitives", "verifiedRelationships"))):
        return failed
    for key in ("conversionSourceDigests", "projectedSourceDigests"):
        values = proof.get(key)
        if not isinstance(values, list) or len(values) != len(sources) or not all(map(digest_valid, values)):
            return failed
    for index, (page, digest) in enumerate(zip(pages, sources, strict=True)):
        if (not isinstance(page, dict) or type(page.get("pageIndex")) is not int or page["pageIndex"] != index
                or page.get("sourcePageId") != f"slide-{index + 1}" or page.get("svgDigest") != digest
                or any(type(page.get(key)) is not int or page[key] < 1
                       for key in ("shapeCount", "nativePrimitiveCount"))
                or type(page.get("promotedBackgroundCount")) is not int
                or page["promotedBackgroundCount"] not in (0, 1)
                or not digest_valid(page.get("shapeInventoryDigest")) or not digest_valid(page.get("traceDigest"))):
            return failed
    if (sum(page["shapeCount"] for page in pages) != measurements["verifiedShapes"]
            or sum(page["nativePrimitiveCount"] for page in pages) != measurements["nativePrimitives"]):
        return failed
    try:
        artifact = ArtifactRef.from_wire(proof.get("artifactRef", {}))
        refs = proof.get("sourceArtifactRefs")
        if not isinstance(refs, list) or len(refs) != len(sources):
            return failed
        source_refs = [ArtifactRef.from_wire(ref) for ref in refs]
    except (ValueError, TypeError, KeyError, AttributeError):
        return failed
    if worker.get("reusedCompletedIdentity") is True and artifact != producer_ref:
        # The worker's ephemeral registration and the server's published
        # canonical registration may identify the same immutable CAS bytes.
        # Keep every identity field exact except that proven promotion pair.
        if (artifact.artifact_id == producer_ref.artifact_id
                or artifact.retention_class != "ephemeral"
                or producer_ref.retention_class != "published"
                or any(getattr(artifact, field) != getattr(producer_ref, field) for field in (
                    "owner_id", "tenant_id", "kind", "sha256", "size_bytes", "media_type",
                    "storage_key", "created_by_operation_id"))):
            return failed
    if ("sha256:" + artifact.sha256 != artifact_digest or artifact.kind != "presentation-pptx"
            or artifact.media_type != "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            or any(str(ref.owner_id) != proof["ownerId"] or ref.tenant_id != proof["tenantId"]
                   or str(ref.created_by_operation_id) != proof["operationId"] for ref in [artifact, *source_refs])
            or any(ref.kind != "slide-svg" or ref.media_type != "image/svg+xml" or "sha256:" + ref.sha256 != digest
                   for ref, digest in zip(source_refs, sources, strict=True))):
        return failed
    render = structural.get("finalPptxRender")
    if isinstance(render, dict) and render.get("status") == "measured" and (
            render.get("ownerId") != proof["ownerId"] or render.get("tenantId") != proof["tenantId"]):
        return failed
    return dict(required=True, status="passed", evidence=dict(
        checkerVersion="svg-generic-native-ooxml-coverage/v1", inputDigest=proof["coverageDigest"],
        outputDigest=artifact_digest, measurements=measurements,
        thresholds=dict(allPagesRequired=True, pageCount=len(sources), unboundShapesAllowed=0,
                        unboundPrimitivesAllowed=0, geometryMismatchesAllowed=0, unboundRelationshipsAllowed=0, fallbackAllowed=0,
                        officeRenderMeasured=False)))


def source_svg_public_quality_receipt(*, slides, source_preservation, worker_metrics,
        quality_receipt_id, operation_id, presentation_id, artifact_digest,
        source_revision, scene_digest, evaluated_at, exporter_version, advisory=None, source_preservation_parents=None,
        require_native_coverage=False, review_binding=None):
    qualities = [(slide.content or {}).get("svgReceipt", {}).get("quality", {}) for slide in slides]
    # A failure placeholder (``None`` receipt) exports as drawn: it is read back
    # as a placeholder, and the authoring and geometry domains fail for it.
    present = source_receipts_present(source_preservation)
    placeholder = len(present) != len(source_preservation)
    templates = [(r or {}).get("template_application", {}) for r in source_preservation]
    whole_page = bool(present) and all(
        r.get("source_authoring_contract") == {"version": "svg-source-whole-page/v1"}
        for r in present)
    authoring = not placeholder and (all(r.get("rendering_verified") is True
                     and isinstance(r.get("bindings"), list) and bool(r["bindings"])
                     for r in source_preservation) if whole_page else
        bool(source_preservation) and all(
            t.get("passed") is True and t.get("constraints_verified") is True
            and t.get("template_digest") == r.get("template_digest")
            and all(t.get("checks", {}).get(c) is True for c in TEMPLATE_CHECKS)
            and all(r.get("checks", {}).get(c) is True for c in SOURCE_CHECKS)
            for t, r in zip(templates, source_preservation, strict=True)))
    measured_preservation = source_preservation_parents or source_preservation
    geometry = bool(qualities) and len(qualities) == len(measured_preservation) and all(
        r is not None and q.get("passed") is True and q.get("geometry_passed") is True
        and q.get("measurement_complete") is True and r.get("rendering_verified") is True
        and q.get("source_digest") == r["svg_digest"]
        and q.get("trusted_font_materialization", {}).get("worker_materialized") is True
        and page_measurement_current(slide)
        for slide, q, r in zip(slides, qualities, measured_preservation, strict=True))
    # The caller proves unchanged ordered paint before providing a derivative.
    # Keep measured/vision inputs bound to their original canonical bytes.
    if geometry and source_preservation_parents is not None:
        geometry = all(parent == child or (
            child.get("export_projection", {}).get("version") == "svg-source-native-table-projection/v1"
            and child["export_projection"].get("parent_svg_digest") == parent["svg_digest"]
            and child["export_projection"].get("unchanged_paint") is True
            and all(child.get(key) == value for key, value in parent.items() if key != "svg_digest")
        ) for parent, child in zip(measured_preservation, source_preservation, strict=True))
    # Source-template receipts do not override the canonical measurement or a
    # terminal substitute's failed author-loop outcome.
    authoring = authoring and svg_authoring(slides).get("status") == "passed"
    geometry = geometry and svg_geometry_font(slides).get("status") == "passed"
    worker = worker_metrics.get("public_export_worker_evidence", {})
    readback = worker.get("structuralReadback", {}).get("sourcePreservation", {})
    pages = readback.get("pages", [])
    integrity = (bool(present) and readback.get("passed") is True
        and readback.get("schemaVersion") == "svg-source-preservation-export-readback/v1"
        and readback.get("manifestDigest") == present[0]["manifest_digest"]
        and readback.get("sourceDigest") == present[0]["source_digest"]
        and len(pages) == len(source_preservation))
    if integrity:
        # A placeholder page is read back as one: it claims no source page.
        integrity = all(p.get("sourcePageIndex") == i and (
            p.get("failurePlaceholder") is True and p.get("sourcePageId") is None
            and p.get("svgDigest", page_svg_digest(None, slides[i].svg_content or ""))
            == page_svg_digest(None, slides[i].svg_content or "")
            if r is None else
            p.get("sourcePageId") == r["source_page_id"] and p.get("svgDigest") == r["svg_digest"]
            and p.get("text", {}).get("matchedBindingCount") == sum(
                b["kind"] in {"text", "table_cell"} and bool(b.get("text")) for b in r["bindings"])
            and p.get("tables", {}).get("matchedCellValueCount") == sum(
                b["kind"] == "table_cell" for b in r["bindings"])
            and p.get("images", {}).get("matchedOccurrenceCount") == sum(
                b["kind"] == "image" for b in r["bindings"]))
            for i, (p, r) in enumerate(zip(pages, source_preservation, strict=True)))

    def domain(passed, checker, inputs, outputs, measurements, reason):
        if not passed:
            return dict(required=True, status="failed", reasonCode=reason)
        return dict(required=True, status="passed", evidence=dict(checkerVersion=checker,
            inputDigest=_digest(inputs), outputDigest=_digest(outputs), measurements=measurements,
            thresholds={"allPagesRequired": True, "pageCount": len(source_preservation)}))

    domains = dict(
        content_brief=dict(required=False, status="not_required"),
        authoring=domain(authoring, "svg-source-whole-page-authoring/v1" if whole_page
            else "svg-source-template-authoring/v2", source_preservation,
            templates if not whole_page else [(r or {}).get("source_authoring_contract")
                                              for r in source_preservation],
            {"verifiedPages": len(templates), "sourcePreserved": True,
             **({"wholePageContractPassed": True} if whole_page else {"templateRulesPassed": True})},
            "svg_template_authoring_evidence_incomplete"),
        geometry_font=domain(geometry, "svg-chromium/v1", [page_svg_digest(r, slide.svg_content or "")
            for r, slide in zip(measured_preservation, slides, strict=False)],
            qualities, {"verifiedPages": len(qualities), "browserMeasured": True, "fontBytesMaterialized": True},
            "svg_geometry_font_evidence_incomplete"),
        image_content_integrity=domain(integrity, "svg-source-preservation-export-readback/v1",
            source_preservation, {"artifactDigest": artifact_digest, "readback": readback},
            {"verifiedPages": len(pages), "textAndCellValuesMatched": True, "imageOccurrencesMatched": True},
            "svg_source_export_readback_evidence_incomplete"),
        visual_review=svg_visual_review(slides=slides, advisory=advisory, expected_binding=review_binding),
        performance=dict(required=False, status="not_assessed", reasonCode="svg_performance_not_measured"),
        native_coverage_ooxml=_native_ooxml_domain(required=require_native_coverage,
            worker_metrics=worker_metrics, artifact_digest=artifact_digest, operation_id=operation_id,
            presentation_id=presentation_id, source_revision=source_revision,
            source_preservation=source_preservation, slides=slides))
    if require_native_coverage:
        from services.svg_final_pptx_review import final_pptx_visual_domain
        domains["visual_review"] = final_pptx_visual_domain(domains["visual_review"],
            worker_metrics=worker_metrics, artifact_digest=artifact_digest, operation_id=operation_id,
            presentation_id=presentation_id, source_revision=source_revision,
            source_digests=[sha256_prefixed((slide.svg_content or "").encode())[7:] for slide in slides],
            expected_binding={key: value for key, value in (review_binding or {}).items()
                              if key in {"presentationId", "ownerId", "ownerScope", "sourceRevision", "tenantId"}})
    return QualityReceiptV3(quality_receipt_id=quality_receipt_id, operation_id=operation_id,
        presentation_id=presentation_id, artifact_digest=artifact_digest, source_revision=source_revision,
        scene_digest=scene_digest, capability_registry_digest=CAPABILITY_REGISTRY_DIGEST,
        exporter_version=exporter_version, evaluated_at=evaluated_at, **domains,
        release_readiness=release_readiness(domains.values())).model_dump(mode="json", by_alias=True)


def svg_public_quality_receipt(*, slides, advisory, source_preservation, worker_metrics,
                               exporter_version, source_preservation_parents=None, require_native_coverage=False,
                               review_binding=None, export_identity_digest=None, export_tenant_id=None, **receipt_args):
    """The export quality receipt for any SVG deck, source-preserving or not.

    A generated deck's ``authoring`` and ``geometry_font`` come from the page
    evidence its generation persisted (``svg_export_page_evidence``).
    """

    present = source_receipts_present(source_preservation)
    if present and (all(
            r.get("layout_schema_version") == "svg-source-layout/v2" for r in present)
            or all(r.get("source_authoring_contract") == {"version": "svg-source-whole-page/v1"}
                   for r in present)):
        return source_svg_public_quality_receipt(slides=slides, source_preservation=source_preservation,
            source_preservation_parents=source_preservation_parents,
            worker_metrics=worker_metrics, exporter_version=exporter_version, advisory=advisory,
            require_native_coverage=require_native_coverage,
            review_binding=review_binding,
            **receipt_args)
    domains = dict(
        content_brief=dict(required=False, status="not_required"),
        authoring=svg_authoring(slides),
        geometry_font=svg_geometry_font(slides),
        image_content_integrity=dict(required=False, status="not_required"),
        visual_review=svg_visual_review(slides=slides, advisory=advisory, expected_binding=review_binding),
        performance=dict(required=False, status="not_assessed", reasonCode="svg_performance_evidence_missing"),
        native_coverage_ooxml=(_native_ooxml_domain(required=require_native_coverage,
            worker_metrics=worker_metrics, artifact_digest=receipt_args["artifact_digest"],
            operation_id=receipt_args["operation_id"], presentation_id=receipt_args["presentation_id"],
            source_revision=receipt_args["source_revision"], source_preservation=source_preservation, slides=slides)
            if source_preservation else _generic_native_ooxml_domain(required=require_native_coverage,
                slides=slides, worker_metrics=worker_metrics, artifact_digest=receipt_args["artifact_digest"],
                operation_id=receipt_args["operation_id"], presentation_id=receipt_args["presentation_id"],
                source_revision=receipt_args["source_revision"], review_binding=review_binding,
                export_identity_digest=export_identity_digest, export_tenant_id=export_tenant_id)))
    if require_native_coverage:
        from services.svg_final_pptx_review import final_pptx_visual_domain
        domains["visual_review"] = final_pptx_visual_domain(domains["visual_review"],
            worker_metrics=worker_metrics, artifact_digest=receipt_args["artifact_digest"],
            operation_id=receipt_args["operation_id"], presentation_id=receipt_args["presentation_id"],
            source_revision=receipt_args["source_revision"],
            source_digests=[sha256_prefixed((slide.svg_content or "").encode())[7:] for slide in slides],
            expected_binding={key: value for key, value in (review_binding or {}).items()
                              if key in {"presentationId", "ownerId", "ownerScope", "sourceRevision", "tenantId"}})
    return QualityReceiptV3(**receipt_args, capability_registry_digest=CAPABILITY_REGISTRY_DIGEST,
        exporter_version=exporter_version, **domains,
        release_readiness=release_readiness(domains.values())).model_dump(mode="json", by_alias=True)
