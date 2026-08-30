from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.authored_quality_receipt_v2 import (
    QualityReceiptLedger,
    create_quality_receipt_v2,
)
from services.authored_quality_receipt_v3_pipeline import (
    create_generation_quality_receipt_v3,
    project_persisted_authored_quality_receipt_v3,
)
from services.authored_vnext.capability_registry import capability_metadata
from models.authored_vnext.deck import (
    SceneEditability,
    SceneOwnerDecision,
    SceneTextElement,
    SlideHybridExportPlan,
    seal_deck_scene,
    seal_slide_scene,
)
from models.authored_vnext.master.contract import Rect
from services.authored_vnext.capability_registry import (
    EDITABLE_HYBRID_PROFILE_ID,
    scene_capabilities,
)
from services.authored_vnext.integration.export_service import _assert_worker_readback
from services.authored_vnext.ooxml.native_scene import materialize_scene_pptx


def _receipt_v2():
    return create_quality_receipt_v2(
        QualityReceiptLedger(),
        attempt_id="live-generation-v3",
        revision=2,
        ledger_digest="sha256:" + "1" * 64,
        deck_html_digest="sha256:" + "2" * 64,
        evidence={
            "safety": {"status": "passed", "residuals": []},
            "capacity": {
                "browserObserved": True,
                "checks": {
                    name: True
                    for name in ("text", "title", "body", "font", "qid", "image")
                },
            },
            "sourceReadback": {"required": False, "status": "not_applicable"},
        },
    )


@pytest.mark.anyio
async def test_live_generation_binds_v2_to_evidence_bound_v3() -> None:
    receipt_v2 = _receipt_v2()
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=receipt_v2,
        job_id=None,
        slide_roles=("cover", "content"),
        provider_attempt_counts=(1, 2),
        repaired_slide_indices=(1,),
        capacity_evidence={
            "browserObserved": True,
            "checks": {"font": True},
            "violations": [],
        },
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
        ),
        visual_review_passed=True,
        source_manifest=None,
        source_readback=None,
    )

    assert receipt_v3.gate_passed is False
    assert receipt_v3.previous_schema_version == "quality-receipt/v2"
    assert receipt_v3.previous_version_receipt_digest == receipt_v2.receipt_digest
    assert receipt_v3.capability_consumer_statuses == (
        ("prompt", "unavailable"),
        ("validator", "unavailable"),
        ("font", "unavailable"),
        ("chromium", "passed"),
        ("scene", "unavailable"),
        ("ooxml", "not_required"),
        ("readback", "not_required"),
    )
    assert "deterministic.geometry.failed" in receipt_v3.blockers
    assert "visualReview.required_failed" not in receipt_v3.blockers
    assert "provider:slide:1:recovery" in receipt_v3.t1_triggers

    generation_path = (
        Path(__file__).parents[2] / "services" / "authored_presentation_generation.py"
    )
    tree = ast.parse(generation_path.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_generation_quality_receipt_v3"
        for node in ast.walk(tree)
    )
    source = generation_path.read_text(encoding="utf-8")
    assert '"promotionReady": False' in source
    assert '"powerPointReadback": "unavailable"' in source
    assert '"libreOfficeReadback": "unavailable"' in source
    assert '"performance_checker_evidence_not_persisted"' in source
    assert '"visual_checker_evidence_not_persisted"' in source


@pytest.mark.anyio
async def test_live_source_generation_seals_occurrences_and_readback_registry() -> None:
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=_receipt_v2(),
        job_id=None,
        slide_roles=("content",),
        provider_attempt_counts=(1,),
        repaired_slide_indices=(),
        capacity_evidence={
            "browserObserved": True,
            "checks": {"font": True},
            "violations": [],
        },
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
        ),
        visual_review_passed=False,
        source_manifest={
            "units": [
                {
                    "blocks": [
                        {
                            "sourceBlockId": "text-1",
                            "kind": "text",
                            "contentDigest": "sha256:" + "3" * 64,
                        },
                        {
                            "sourceBlockId": "image-1",
                            "kind": "image",
                            "contentDigest": "sha256:" + "4" * 64,
                        },
                    ]
                }
            ]
        },
        source_readback={
            "verified": True,
            "capabilityRegistry": capability_metadata(consumer="readback"),
            "evidence": [
                {"sourceBlockId": "text-1"},
                {"sourceBlockId": "image-1"},
            ],
        },
    )

    assert receipt_v3.gate_passed is False
    assert dict(receipt_v3.capability_consumer_statuses)["readback"] == "passed"


@pytest.mark.anyio
async def test_t1_without_visual_review_is_unavailable_and_blocks_quality() -> None:
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=_receipt_v2(),
        job_id=None,
        slide_roles=("cover",),
        provider_attempt_counts=(1,),
        repaired_slide_indices=(),
        capacity_evidence={
            "browserObserved": True,
            "checks": {"font": True},
            "violations": [],
        },
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
        ),
        visual_review_passed=False,
        source_manifest=None,
        source_readback=None,
    )

    assert receipt_v3.gate_passed is False
    assert "visualReview.required_failed" in receipt_v3.blockers


@pytest.mark.anyio
async def test_generation_seals_observed_prompt_scene_and_capacity_evidence_to_port() -> None:
    appended: list[tuple[object, object]] = []

    class Port:
        async def append_quality_receipt_v3(self, *, receipt, evidence) -> None:
            appended.append((receipt, evidence))

    evidence = {
        "browserObserved": True,
        "checks": {
            name: True
            for name in ("text", "title", "body", "font", "qid", "image")
        },
        "violations": [],
    }
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=_receipt_v2(),
        job_id=None,
        slide_roles=("content",),
        provider_attempt_counts=(1,),
        repaired_slide_indices=(),
        capacity_evidence=evidence,
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
        ),
        visual_review_passed=False,
        source_manifest=None,
        source_readback=None,
        design_system_prompt=(
            "Capability contract: "
            + capability_metadata(consumer="prompt")["registryDigest"]
        ),
        scene_envelope={
            "schemaVersion": "authored-deck-scene-envelope/v1",
            "sceneDigest": "sha256:" + "1" * 64,
            "sourceDigest": "sha256:" + "2" * 64,
            "qidDigest": "sha256:" + "3" * 64,
        },
        persistence_port=Port(),
        durability_profile="public",
    )

    statuses = dict(receipt_v3.capability_consumer_statuses)
    assert statuses["prompt"] == "passed"
    assert statuses["validator"] == "passed"
    assert statuses["font"] == "passed"
    assert statuses["scene"] == "passed"
    assert "deterministic.geometry.failed" not in receipt_v3.blockers
    assert "deterministic.font.failed" not in receipt_v3.blockers
    assert "deterministic.content.failed" not in receipt_v3.blockers
    assert "persistence.durable_authority_required" not in receipt_v3.blockers
    assert receipt_v3.gate_passed is True
    assert appended and appended[0][0] == receipt_v3


@pytest.mark.anyio
async def test_public_generation_without_persistence_port_is_blocked() -> None:
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=_receipt_v2(),
        job_id=None,
        slide_roles=("content",),
        provider_attempt_counts=(1,),
        repaired_slide_indices=(),
        capacity_evidence={"browserObserved": False, "checks": {}},
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(),
        visual_review_passed=False,
        source_manifest=None,
        source_readback=None,
        durability_profile="public",
    )
    assert receipt_v3.gate_passed is False
    assert "persistence.durable_authority_required" in receipt_v3.blockers


@pytest.mark.anyio
async def test_persisted_quality_gate_projects_only_verified_internal_receipts() -> None:
    receipt_v2 = _receipt_v2()
    receipt_v3 = await create_generation_quality_receipt_v3(
        receipt_v2=receipt_v2,
        job_id=None,
        slide_roles=("content",),
        provider_attempt_counts=(1,),
        repaired_slide_indices=(),
        capacity_evidence={"browserObserved": True, "checks": {"font": True}},
        safety_evidence={"status": "passed", "residuals": []},
        browser_metrics=(
            {"capabilityRegistry": capability_metadata(consumer="chromium")},
        ),
        visual_review_passed=False,
        source_manifest=None,
        source_readback=None,
    )
    persisted_gate = {
        **receipt_v2.to_dict(),
        "qualityReceiptV3": receipt_v3.to_dict(),
    }

    projected = project_persisted_authored_quality_receipt_v3(
        authored_quality_gate=persisted_gate,
        quality_receipt_id="qrcpt_1",
        operation_id="op_generation_1",
        presentation_id="prs_1",
        artifact_digest="sha256:" + "3" * 64,
        source_revision=2,
        scene_digest="sha256:" + "4" * 64,
        exporter_version="authored-ooxml/1",
        evaluated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    assert projected.release_readiness == "blocked"
    assert projected.authoring.status == "failed"
    assert projected.geometry_font.status == "failed"
    assert projected.visual_review.status == "unavailable"
    assert projected.performance.status == "not_assessed"
    assert projected.native_coverage_ooxml.status == "pending"

    tampered = {
        **persisted_gate,
        "qualityReceiptV3": {
            **receipt_v3.to_dict(),
            "receiptDigest": "sha256:" + "0" * 64,
        },
    }
    with pytest.raises(ValueError, match="quality_receipt_v3_payload_digest_mismatch"):
        project_persisted_authored_quality_receipt_v3(
            authored_quality_gate=tampered,
            quality_receipt_id="qrcpt_1",
            operation_id="op_generation_1",
            presentation_id="prs_1",
            artifact_digest="sha256:" + "3" * 64,
            source_revision=2,
            scene_digest="sha256:" + "4" * 64,
            exporter_version="authored-ooxml/1",
            evaluated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        )


def test_export_adapter_accepts_registry_bound_hybrid_readback() -> None:
    element = SceneTextElement(
        kind="text",
        element_id="title.main",
        z_order=1,
        bounds=Rect(x=40, y=40, width=600, height=80),
        semantic_role="title",
        editability=SceneEditability(text=True, geometry=True, style=True),
        text="Registry-bound export",
    )
    scene = seal_deck_scene(
        deck_id="deck:registry-export",
        revision=1,
        composition_digest="sha256:" + "5" * 64,
        source_digest="sha256:" + "6" * 64,
        hybrid_profile_id=EDITABLE_HYBRID_PROFILE_ID,
        slides=[
            seal_slide_scene(
                slide_id="slide:registry-export",
                revision=1,
                elements=[element],
                hybrid_export=SlideHybridExportPlan(
                    owner_decisions=[
                        SceneOwnerDecision(
                            owner_id=element.element_id,
                            decision="nativeEditable",
                            capability_id="editable.text.ko-en.v1",
                        )
                    ]
                ),
            )
        ],
        capabilities=scene_capabilities(),
    )
    scene_payload = scene.model_dump(mode="json", by_alias=True)
    master_payload = {
        "slideSizePx": {"width": 960, "height": 540},
        "tokens": {"color.background": "#F7F9FC"},
        "furniture": [],
        "fontManifest": {"files": []},
    }
    _content, native_receipt = materialize_scene_pptx(
        scene_payload,
        [],
        master_payload,
    )
    # Keep this adapter test scoped to the registry/readback seam.  Native text
    # style/auto-fit parity has its own OOXML tests and is intentionally removed
    # from the synthetic worker receipt below.
    actual_element = native_receipt["scene"]["slides"][0]["elements"][0]
    native_receipt["scene"]["slides"][0]["elements"][0] = {
        key: actual_element[key]
        for key in ("elementId", "kind", "textDigest", "renderedBounds")
    }
    receipt = {
        **native_receipt,
        "validation": "candidate-reopened",
        "candidateDigestVerified": True,
        "package": {
            "macroDetected": False,
            "quarantineReasons": [],
            "slideCount": 1,
        },
        "preservation": {
            "silentLossCount": 0,
            "missingParts": [],
            "unexpectedChangedParts": [],
        },
        "master": {
            "furnitureCount": 0,
            "logoCount": 0,
            "pageNumberCount": 0,
            "fontFileCount": 0,
            "duplicateFurnitureCount": 0,
            "fontFilesMode": "REFERENCE_METADATA_VERIFIED",
        },
        "slides": [
            {
                "slideId": scene_payload["slides"][0]["slideId"],
                "slideDigest": scene_payload["slides"][0]["slideDigest"],
                "elementIds": ["title.main"],
                "rasterIslandIds": [],
                "rasterOwnerIds": [],
            }
        ],
    }
    _assert_worker_readback(
        receipt,
        {"master": master_payload, "slides": [{}], "scene": scene_payload},
    )
