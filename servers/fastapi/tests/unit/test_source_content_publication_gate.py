from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from services.authored_vnext.master.digests import canonical_digest
from services.source_content.coverage import (
    SceneCoverageResult,
    SourceContentCoverageError,
    SourceContentPreservationInventory,
)
from services.source_content.export_readback import ExportReadbackResult
from services.source_content.publication import (
    _seal_receipt,
    publish_verified_source_content,
    require_publication_ready,
)


def test_source_block_omission_never_reaches_atomic_publication() -> None:
    published = False

    async def atomic_publish(_receipt: object) -> None:
        nonlocal published
        published = True

    manifest = {
        "manifestDigest": f"sha256:{'1' * 64}",
        "objectInventory": {
            "discoveredObjectCount": 1,
            "classifiedObjectCount": 1,
            "unclassifiedObjectIds": [],
            "unsupportedContentObjectIds": [],
        },
        "units": [
            {
                "blocks": [
                    {
                        "sourceBlockId": "source-slide:1:text:1",
                        "kind": "text",
                        "order": 0,
                        "contentDigest": f"sha256:{'2' * 64}",
                        "payload": {"text": "원본", "runs": ["원본"]},
                    }
                ]
            }
        ],
    }
    plan = {
        "planDigest": f"sha256:{'3' * 64}",
        "slides": [
            {
                "outputIndex": 0,
                "placements": [
                    {
                        "sourceBlockId": "source-slide:1:text:1",
                        "sourceRange": {
                            "kind": "atomic",
                            "start": 0,
                            "endExclusive": 1,
                        },
                    }
                ],
            }
        ],
    }
    envelope_with_omitted_binding = {
        "scene": {"sceneDigest": f"sha256:{'4' * 64}", "slides": []},
        "sourceBindings": [],
    }

    with pytest.raises(SourceContentCoverageError) as captured:
        asyncio.run(
            publish_verified_source_content(
                presentation_id="presentation-1",
                presentation_revision=1,
                manifest=manifest,
                placement_plan=plan,
                materialized_scene=envelope_with_omitted_binding,
                candidate_pptx=b"not-inspected-because-scene-gate-fails-first",
                render_evidence={},
                atomic_publish=atomic_publish,
            )
        )

    assert captured.value.code == "source_content_coverage_failed"
    assert "bindings" in captured.value.detail
    assert published is False


def test_text_image_receipt_binds_bounded_source_preservation_inventory() -> None:
    receipt = _seal_receipt(
        presentation_id="presentation-1",
        presentation_revision=1,
        scene=SceneCoverageResult(
            manifest_digest="manifest-digest",
            plan_digest="plan-digest",
            scene_digest="scene-digest",
            discovered_object_coverage="2/2",
            text_block_coverage="1/1",
            table_cell_coverage="4/4",
            chart_value_coverage="2/2",
            image_occurrence_coverage="1/1",
            unclassified_object_count=0,
            unsupported_content_object_count=0,
            order_violations=0,
            fragment_gap_count=0,
            fragment_overlap_count=0,
            content_digest_mismatches=0,
            render_clipping_count=0,
            style_token_digest_matched=True,
            master_geometry_digest_matched=True,
            unplaced_block_ids=(),
            preservation_inventory=SourceContentPreservationInventory(
                source_mode="text-image",
                source_complete_claim=False,
                preserved=("text", "image-occurrences"),
                excluded=("tables", "charts", "original-layout"),
                excluded_source_object_counts={
                    "tables": 1,
                    "charts": 1,
                    "originalLayoutShapes": 2,
                },
                text_occurrences=(("source-text-1", "text-digest"),),
                image_occurrences=(("source-image-1", "image-digest"),),
            ),
        ),
        export=ExportReadbackResult(
            candidate_artifact_digest="artifact-digest",
            export_readback_digest="readback-digest",
            export_relationship_mismatches=0,
            render_clipping_count=0,
            render_overflow_count=0,
            style_token_digest_matched=True,
            master_geometry_digest_matched=True,
        ),
    )

    assert receipt.as_json()["sourceContent"] == {
        "sourceMode": "text-image",
        "sourceCompleteClaim": False,
        "preserved": ["text", "image-occurrences"],
        "excluded": ["tables", "charts", "original-layout"],
        "excludedSourceObjectCounts": {
            "tables": 1,
            "charts": 1,
            "originalLayoutShapes": 2,
        },
        "textOccurrences": [
            {"sourceBlockId": "source-text-1", "contentDigest": "text-digest"}
        ],
        "imageOccurrences": [
            {"sourceBlockId": "source-image-1", "contentDigest": "image-digest"}
        ],
        "textOccurrenceCount": 1,
        "imageOccurrenceCount": 1,
        "occurrenceInventoryDigest": receipt.preservation_inventory.occurrence_inventory_digest,
    }
    require_publication_ready(
        receipt,
        presentation_id="presentation-1",
        presentation_revision=1,
        candidate_artifact_digest="artifact-digest",
    )

    tampered = replace(
        receipt,
        preservation_inventory=replace(
            receipt.preservation_inventory,
            image_occurrences=(("source-image-1", "different-image-digest"),),
        ),
    )
    with pytest.raises(SourceContentCoverageError, match="source_content_publication_blocked"):
        require_publication_ready(
            tampered,
            presentation_id="presentation-1",
            presentation_revision=1,
            candidate_artifact_digest="artifact-digest",
        )

    count_drift = replace(receipt, image_occurrence_coverage="0/1")
    count_drift_payload = count_drift.as_json()
    count_drift_payload.pop("receiptDigest")
    count_drift = replace(
        count_drift,
        receipt_digest=canonical_digest(count_drift_payload),
    )
    with pytest.raises(SourceContentCoverageError, match="source_content_coverage_failed"):
        require_publication_ready(
            count_drift,
            presentation_id="presentation-1",
            presentation_revision=1,
            candidate_artifact_digest="artifact-digest",
        )
