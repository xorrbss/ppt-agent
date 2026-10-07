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
from services.svg_export_page_evidence import svg_authoring, svg_geometry_font
from services.svg_export_visual_review import release_readiness, svg_visual_review, page_measurement_current
from utils.hashing import sha256_prefixed

TEMPLATE_CHECKS = ("identity", "slots", "typography", "background", "table_style", "shape_style", "decorations")
SOURCE_CHECKS = ("source_ids_exact", "text_exact", "table_structure_exact", "image_occurrences_exact")


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return sha256_prefixed(encoded)


def _native_ooxml_domain(*, required, worker_metrics, artifact_digest, operation_id,
                         presentation_id, source_revision, source_preservation):
    if not required:
        return dict(required=False, status="not_required")
    from services.svg_export_whole_page_verifier import whole_page_verifier_digest
    from workers.public_svg_pptx_convert import converter_digest
    from utils.hashing import canonical_json_sha256
    worker = worker_metrics.get("public_export_worker_evidence", {})
    structural = worker.get("structuralReadback", {})
    proof = structural.get("nativeCoverageOoxml")
    source_readback = structural.get("sourcePreservation")
    style_readback = structural.get("sourceStyleReadback")
    failed = dict(required=True, status="failed", reasonCode="svg_native_ooxml_coverage_evidence_invalid")
    if not isinstance(proof, dict) or not isinstance(source_readback, dict) or not isinstance(style_readback, dict) or not source_preservation:
        return failed
    unsigned = {key: value for key, value in proof.items() if key != "coverageDigest"}
    measurements = proof.get("measurements")
    if (proof.get("schemaVersion") != "svg-native-ooxml-coverage/v1" or proof.get("passed") is not True
            or proof.get("artifactDigest") != artifact_digest or proof.get("operationId") != str(operation_id)
            or proof.get("presentationId") != str(presentation_id) or proof.get("sourceRevision") != source_revision
            or proof.get("verifierCodeDigest") != whole_page_verifier_digest()
            or proof.get("converterDigest") != converter_digest()
            or proof.get("coverageDigest") != "sha256:" + canonical_json_sha256(unsigned)
            or proof.get("sourceReadbackDigest") != "sha256:" + canonical_json_sha256(source_readback)
            or proof.get("sourceStyleReadbackDigest") != "sha256:" + canonical_json_sha256(style_readback)
            or style_readback.get("schemaVersion") != "svg-source-export-style-readback/v1"
            or style_readback.get("passed") is not True
            or source_readback.get("passed") is not True
            or proof.get("sourceDigests") != [receipt["svg_digest"] for receipt in source_preservation]
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


def source_svg_public_quality_receipt(*, slides, source_preservation, worker_metrics,
        quality_receipt_id, operation_id, presentation_id, artifact_digest,
        source_revision, scene_digest, evaluated_at, exporter_version, advisory=None, source_preservation_parents=None,
        require_native_coverage=False, review_binding=None):
    qualities = [(slide.content or {}).get("svgReceipt", {}).get("quality", {}) for slide in slides]
    templates = [r.get("template_application", {}) for r in source_preservation]
    whole_page = bool(source_preservation) and all(
        r.get("source_authoring_contract") == {"version": "svg-source-whole-page/v1"}
        for r in source_preservation)
    authoring = (all(r.get("rendering_verified") is True
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
        q.get("passed") is True and q.get("geometry_passed") is True
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
    integrity = (bool(source_preservation) and readback.get("passed") is True
        and readback.get("schemaVersion") == "svg-source-preservation-export-readback/v1"
        and readback.get("manifestDigest") == source_preservation[0]["manifest_digest"]
        and readback.get("sourceDigest") == source_preservation[0]["source_digest"]
        and len(pages) == len(source_preservation))
    if integrity:
        integrity = all(p.get("sourcePageIndex") == i
            and p.get("sourcePageId") == r["source_page_id"] and p.get("svgDigest") == r["svg_digest"]
            and p.get("text", {}).get("matchedBindingCount") == sum(
                b["kind"] in {"text", "table_cell"} and bool(b.get("text")) for b in r["bindings"])
            and p.get("tables", {}).get("matchedCellValueCount") == sum(
                b["kind"] == "table_cell" for b in r["bindings"])
            and p.get("images", {}).get("matchedOccurrenceCount") == sum(
                b["kind"] == "image" for b in r["bindings"])
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
            templates if not whole_page else [r["source_authoring_contract"] for r in source_preservation],
            {"verifiedPages": len(templates), "sourcePreserved": True,
             **({"wholePageContractPassed": True} if whole_page else {"templateRulesPassed": True})},
            "svg_template_authoring_evidence_incomplete"),
        geometry_font=domain(geometry, "svg-chromium/v1", [r["svg_digest"] for r in measured_preservation],
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
            source_preservation=source_preservation))
    if require_native_coverage:
        from services.svg_final_pptx_review import final_pptx_visual_domain
        domains["visual_review"] = final_pptx_visual_domain(domains["visual_review"],
            worker_metrics=worker_metrics, artifact_digest=artifact_digest, operation_id=operation_id,
            presentation_id=presentation_id, source_revision=source_revision,
            source_digests=[sha256_prefixed((slide.svg_content or "").encode())[7:] for slide in slides],
            expected_binding=review_binding)
    return QualityReceiptV3(quality_receipt_id=quality_receipt_id, operation_id=operation_id,
        presentation_id=presentation_id, artifact_digest=artifact_digest, source_revision=source_revision,
        scene_digest=scene_digest, capability_registry_digest=CAPABILITY_REGISTRY_DIGEST,
        exporter_version=exporter_version, evaluated_at=evaluated_at, **domains,
        release_readiness=release_readiness(domains.values())).model_dump(mode="json", by_alias=True)


def svg_public_quality_receipt(*, slides, advisory, source_preservation, worker_metrics,
                               exporter_version, source_preservation_parents=None, require_native_coverage=False,
                               review_binding=None, **receipt_args):
    """The export quality receipt for any SVG deck, source-preserving or not.

    A generated deck's ``authoring`` and ``geometry_font`` come from the page
    evidence its generation persisted (``svg_export_page_evidence``).
    """

    if source_preservation and (all(
            r.get("layout_schema_version") == "svg-source-layout/v2" for r in source_preservation)
            or all(r.get("source_authoring_contract") == {"version": "svg-source-whole-page/v1"}
                   for r in source_preservation)):
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
        native_coverage_ooxml=_native_ooxml_domain(required=require_native_coverage,
            worker_metrics=worker_metrics, artifact_digest=receipt_args["artifact_digest"],
            operation_id=receipt_args["operation_id"], presentation_id=receipt_args["presentation_id"],
            source_revision=receipt_args["source_revision"], source_preservation=source_preservation))
    if require_native_coverage:
        from services.svg_final_pptx_review import final_pptx_visual_domain
        domains["visual_review"] = final_pptx_visual_domain(domains["visual_review"],
            worker_metrics=worker_metrics, artifact_digest=receipt_args["artifact_digest"],
            operation_id=receipt_args["operation_id"], presentation_id=receipt_args["presentation_id"],
            source_revision=receipt_args["source_revision"],
            source_digests=[sha256_prefixed((slide.svg_content or "").encode())[7:] for slide in slides],
            expected_binding=review_binding)
    return QualityReceiptV3(**receipt_args, capability_registry_digest=CAPABILITY_REGISTRY_DIGEST,
        exporter_version=exporter_version, **domains,
        release_readiness=release_readiness(domains.values())).model_dump(mode="json", by_alias=True)
