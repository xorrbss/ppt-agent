from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api.v2.contracts import QualityReceiptV3
from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.public_export_errors import PublicExportOperationError
from services.svg_contract import source_digest
from services.svg_export_operation import _source_preservation_receipts
from services.svg_export_quality import SOURCE_CHECKS, TEMPLATE_CHECKS, source_svg_public_quality_receipt


def evidence_fixture():
    canvas = canvas_metadata_for_aspect_ratio("16:9")
    svg = '<svg xmlns="http://www.w3.org/2000/svg"/>'
    receipt = dict(
        contract_version="svg-source-preservation/v1",
        manifest_digest="sha256:" + "1" * 64,
        source_digest="sha256:" + "2" * 64,
        source_page_id="page-1",
        source_page_index=0,
        page_count=1,
        page_digest="sha256:" + "3" * 64,
        plan_digest="sha256:" + "4" * 64,
        svg_digest=source_digest(svg),
        style_digest="sha256:" + "5" * 64,
        template_digest="sha256:" + "6" * 64,
        layout_id="content-stack",
        layout_schema_version="svg-source-layout/v2",
        canvas=dict(width=canvas.width_px, height=canvas.height_px),
        rendering_verified=True,
        bindings=[dict(source_id="block-1", kind="text", element_id="text-1", text="원문")],
        checks={c: True for c in SOURCE_CHECKS},
    )
    receipt["template_application"] = dict(
        template_digest=receipt["template_digest"],
        layout_id=receipt["layout_id"],
        constraints_verified=True,
        passed=True,
        checks={c: True for c in TEMPLATE_CHECKS},
        visual_review_verified=False,
    )
    quality = dict(
        passed=True,
        geometry_passed=True,
        measurement_complete=True,
        source_digest=receipt["svg_digest"],
        trusted_font_materialization=dict(worker_materialized=True),
    )
    slide = SimpleNamespace(svg_content=svg, content={"svgReceipt": dict(source_preservation=receipt, quality=quality)})
    presentation = SimpleNamespace(
        theme={
            "svg": dict(
                sourcePreservation=dict(
                    schemaVersion=receipt["contract_version"], manifestDigest=receipt["manifest_digest"], pageCount=1
                ),
                styleSnapshot=dict(
                    digest=receipt["style_digest"],
                    source_template=dict(digest=receipt["template_digest"], layouts=[dict(id="content-stack")]),
                ),
            )
        }
    )
    readback = dict(
        schemaVersion="svg-source-preservation-export-readback/v1",
        passed=True,
        manifestDigest=receipt["manifest_digest"],
        sourceDigest=receipt["source_digest"],
        pages=[
            dict(
                sourcePageIndex=0,
                sourcePageId=receipt["source_page_id"],
                svgDigest=receipt["svg_digest"],
                text=dict(matchedBindingCount=1),
                tables=dict(matchedCellValueCount=0),
                images=dict(matchedOccurrenceCount=0),
            )
        ],
    )
    return canvas, presentation, slide, receipt, readback


def public_receipt(slide, receipt, readback):
    return source_svg_public_quality_receipt(
        slides=[slide],
        source_preservation=[receipt],
        worker_metrics=dict(public_export_worker_evidence=dict(structuralReadback=dict(sourcePreservation=readback))),
        quality_receipt_id="quality-test",
        operation_id="operation-test",
        presentation_id="presentation-test",
        artifact_digest="sha256:" + "a" * 64,
        source_revision=1,
        scene_digest="sha256:" + "b" * 64,
        evaluated_at=datetime.now(timezone.utc),
        exporter_version="test-converter/v1",
    )


def test_persisted_domains_are_connected_without_claiming_visual_or_native_acceptance():
    canvas, presentation, slide, receipt, readback = evidence_fixture()
    assert _source_preservation_receipts(presentation, [slide], canvas) == [receipt]
    result = public_receipt(slide, receipt, readback)
    validated = QualityReceiptV3.model_validate(result)
    assert validated.authoring.status == "passed"
    assert validated.geometry_font.status == "passed"
    assert validated.image_content_integrity.status == "passed"
    assert validated.visual_review.required and validated.visual_review.status == "failed"
    assert validated.performance.status == "not_assessed"
    assert validated.native_coverage_ooxml.status == "pending"
    assert validated.release_readiness == "blocked"
    assert validated.authoring.evidence.input_digest.startswith("sha256:")


@pytest.mark.parametrize("broken", ["template", "render", "readback"])
def test_missing_evidence_is_never_promoted(broken):
    _, _, slide, receipt, readback = evidence_fixture()
    if broken == "template":
        receipt["template_application"]["checks"]["slots"] = False
    elif broken == "render":
        slide.content["svgReceipt"]["quality"]["measurement_complete"] = False
    else:
        readback["pages"][0]["svgDigest"] = "sha256:" + "f" * 64
    result = public_receipt(slide, receipt, readback)
    key = dict(template="authoring", render="geometryFont", readback="imageContentIntegrity")[broken]
    assert result[key]["status"] == "failed"
    assert result[key]["evidence"] is None


def test_v2_template_mismatch_rejected_and_legacy_source_receipt_unchanged():
    canvas, presentation, slide, receipt, _ = evidence_fixture()
    presentation.theme["svg"]["styleSnapshot"]["source_template"]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(PublicExportOperationError, match="template_evidence_invalid"):
        _source_preservation_receipts(presentation, [slide], canvas)
    legacy = deepcopy(receipt)
    legacy.pop("layout_schema_version")
    slide.content["svgReceipt"] = dict(source_preservation=legacy)
    with pytest.raises(PublicExportOperationError, match="template_contract_required"):
        _source_preservation_receipts(presentation, [slide], canvas)
    presentation.theme["svg"]["styleSnapshot"].pop("source_template")
    assert _source_preservation_receipts(presentation, [slide], canvas) == [legacy]
