"""Admission-side validation of svg source-preservation receipts for export."""
from __future__ import annotations

from services.public_export_errors import PublicExportOperationError
from services.svg_contract import source_digest as svg_source_digest


def source_preservation_receipts(presentation, slides, canvas):
    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    svg_theme = theme.get("svg") if isinstance(theme.get("svg"), dict) else {}
    marker = svg_theme.get("sourcePreservation")
    receipts = [
        (slide.content or {}).get("svgReceipt", {}).get("source_preservation")
        if isinstance((slide.content or {}).get("svgReceipt"), dict) else None
        for slide in slides
    ]
    if marker is None and not any(receipt is not None for receipt in receipts):
        return None
    if (not isinstance(marker, dict)
            or set(marker) != {"schemaVersion", "manifestDigest", "pageCount"}
            or marker.get("schemaVersion") != "svg-source-preservation/v1"
            or marker.get("pageCount") != len(slides)):
        raise PublicExportOperationError("svg_source_export_marker_invalid")
    manifest_digest = marker.get("manifestDigest")
    source_digest = None
    for index, (slide, receipt) in enumerate(zip(slides, receipts, strict=True)):
        if not isinstance(receipt, dict):
            raise PublicExportOperationError("svg_source_export_receipt_required")
        expected = {
            "contract_version": "svg-source-preservation/v1",
            "manifest_digest": manifest_digest,
            "source_page_index": index,
            "page_count": len(slides),
            "svg_digest": svg_source_digest(slide.svg_content),
            "canvas": {"width": canvas.width_px, "height": canvas.height_px},
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise PublicExportOperationError("svg_source_export_receipt_input_mismatch")
        if receipt.get("rendering_verified") is not True:
            raise PublicExportOperationError("svg_source_export_rendering_unverified")
        pinned_template = svg_theme.get("styleSnapshot", {}).get("source_template")
        if pinned_template and receipt.get("layout_schema_version") != "svg-source-layout/v2":
            raise PublicExportOperationError("svg_source_export_template_contract_required")
        if receipt.get("layout_schema_version") == "svg-source-layout/v2":
            from services.svg_export_quality import TEMPLATE_CHECKS
            snapshot = svg_theme.get("styleSnapshot", {})
            template = snapshot.get("source_template", {})
            application = receipt.get("template_application", {})
            quality = (slide.content or {}).get("svgReceipt", {}).get("quality", {})
            if (not template.get("digest") or receipt.get("template_digest") != template["digest"]
                    or receipt.get("style_digest") != snapshot.get("digest")
                    or application.get("template_digest") != template["digest"]
                    or application.get("layout_id") != receipt.get("layout_id")
                    or receipt.get("layout_id") not in {layout["id"] for layout in template.get("layouts", [])}
                    or application.get("passed") is not True
                    or any(application.get("checks", {}).get(c) is not True for c in TEMPLATE_CHECKS)):
                raise PublicExportOperationError("svg_source_export_template_evidence_invalid")
            if (quality.get("source_digest") != receipt["svg_digest"]
                    or quality.get("passed") is not True or quality.get("measurement_complete") is not True):
                raise PublicExportOperationError("svg_source_export_geometry_evidence_invalid")
        checks = receipt.get("checks")
        if (not isinstance(checks, dict)
                or any(checks.get(key) is not True for key in (
                    "source_ids_exact", "text_exact", "table_structure_exact", "image_occurrences_exact"
                ))):
            raise PublicExportOperationError("svg_source_export_preservation_failed")
        if not all(isinstance(receipt.get(key), str) and receipt[key] for key in (
            "manifest_digest", "source_digest", "source_page_id", "page_digest", "plan_digest", "style_digest"
        )):
            raise PublicExportOperationError("svg_source_export_receipt_invalid")
        if source_digest is None:
            source_digest = receipt["source_digest"]
        elif receipt["source_digest"] != source_digest:
            raise PublicExportOperationError("svg_source_export_source_digest_mismatch")
        bindings = receipt.get("bindings")
        if not isinstance(bindings, list) or not bindings:
            raise PublicExportOperationError("svg_source_export_bindings_invalid")
        element_ids = [binding.get("element_id") for binding in bindings if isinstance(binding, dict)]
        if (len(element_ids) != len(bindings) or any(not value for value in element_ids)
                or len(set(element_ids)) != len(element_ids)):
            raise PublicExportOperationError("svg_source_export_bindings_invalid")
        for binding in bindings:
            kind = binding.get("kind")
            if kind not in {"text", "table_cell", "image", "shape"} or not binding.get("source_id"):
                raise PublicExportOperationError("svg_source_export_bindings_invalid")
            if kind in {"text", "table_cell"} and not isinstance(binding.get("text"), str):
                raise PublicExportOperationError("svg_source_export_text_binding_invalid")
            if kind == "image" and (not binding.get("slot_id") or not binding.get("asset_digest")):
                raise PublicExportOperationError("svg_source_export_image_binding_invalid")
    return receipts
