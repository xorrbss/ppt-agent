"""Public quality evidence from persisted SVG checks and export readback.

Source content readback is not complete OOXML editability or a visual review.
"""
from __future__ import annotations

from hashlib import sha256
import json

from api.v2.contracts import QualityReceiptV3
from services.authored_vnext.capability_registry import CAPABILITY_REGISTRY_DIGEST

TEMPLATE_CHECKS = ("identity", "slots", "typography", "background", "table_style", "shape_style", "decorations")
SOURCE_CHECKS = ("source_ids_exact", "text_exact", "table_structure_exact", "image_occurrences_exact")


def _digest(value):
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def source_svg_public_quality_receipt(*, slides, source_preservation, worker_metrics,
        quality_receipt_id, operation_id, presentation_id, artifact_digest,
        source_revision, scene_digest, evaluated_at, exporter_version):
    qualities = [(slide.content or {}).get("svgReceipt", {}).get("quality", {}) for slide in slides]
    templates = [r.get("template_application", {}) for r in source_preservation]
    authoring = bool(source_preservation) and all(
        t.get("passed") is True and t.get("constraints_verified") is True
        and t.get("template_digest") == r.get("template_digest")
        and all(t.get("checks", {}).get(c) is True for c in TEMPLATE_CHECKS)
        and all(r.get("checks", {}).get(c) is True for c in SOURCE_CHECKS)
        for t, r in zip(templates, source_preservation))
    geometry = bool(qualities) and len(qualities) == len(source_preservation) and all(
        q.get("passed") is True and q.get("geometry_passed") is True
        and q.get("measurement_complete") is True and r.get("rendering_verified") is True
        and q.get("source_digest") == r["svg_digest"]
        and q.get("trusted_font_materialization", {}).get("worker_materialized") is True
        for q, r in zip(qualities, source_preservation))
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
            and p.get("text", {}).get("matchedBindingCount") == sum(b["kind"] in {"text", "table_cell"} and bool(b.get("text")) for b in r["bindings"])
            and p.get("tables", {}).get("matchedCellValueCount") == sum(b["kind"] == "table_cell" for b in r["bindings"])
            and p.get("images", {}).get("matchedOccurrenceCount") == sum(b["kind"] == "image" for b in r["bindings"])
            for i, (p, r) in enumerate(zip(pages, source_preservation)))

    def domain(passed, checker, inputs, outputs, measurements, reason):
        if not passed:
            return dict(required=True, status="failed", reasonCode=reason)
        return dict(required=True, status="passed", evidence=dict(checkerVersion=checker,
            inputDigest=_digest(inputs), outputDigest=_digest(outputs), measurements=measurements,
            thresholds={"allPagesRequired": True, "pageCount": len(source_preservation)}))

    return QualityReceiptV3(quality_receipt_id=quality_receipt_id, operation_id=operation_id,
        presentation_id=presentation_id, artifact_digest=artifact_digest, source_revision=source_revision,
        scene_digest=scene_digest, capability_registry_digest=CAPABILITY_REGISTRY_DIGEST,
        exporter_version=exporter_version, evaluated_at=evaluated_at,
        content_brief=dict(required=False, status="not_required"),
        authoring=domain(authoring, "svg-source-template-authoring/v2", source_preservation,
            templates, {"verifiedPages": len(templates), "sourcePreserved": True, "templateRulesPassed": True},
            "svg_template_authoring_evidence_incomplete"),
        geometry_font=domain(geometry, "svg-chromium/v1", [r["svg_digest"] for r in source_preservation],
            qualities, {"verifiedPages": len(qualities), "browserMeasured": True, "fontBytesMaterialized": True},
            "svg_geometry_font_evidence_incomplete"),
        image_content_integrity=domain(integrity, "svg-source-preservation-export-readback/v1",
            source_preservation, {"artifactDigest": artifact_digest, "readback": readback},
            {"verifiedPages": len(pages), "textAndCellValuesMatched": True, "imageOccurrencesMatched": True},
            "svg_source_export_readback_evidence_incomplete"),
        visual_review=dict(required=True, status="failed", reasonCode="svg_template_visual_review_required"),
        performance=dict(required=False, status="not_assessed", reasonCode="svg_performance_not_measured"),
        native_coverage_ooxml=dict(required=False, status="pending", reasonCode="svg_full_native_ooxml_coverage_not_verified"),
        release_readiness="blocked").model_dump(mode="json", by_alias=True)
