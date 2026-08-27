from __future__ import annotations

from typing import Any

import pytest

from models.authored_vnext.master.contract import Rect
from services.source_content.allocator import (
    ContentAllocator,
    PageLockedTemplateCompositionPlanner,
    PlacementCompositionPin,
    WritableLayout,
    WritableSlot,
    canonical_digest,
)
from services.source_content.contracts import (
    SourceContentManifest,
    SourceContentManifestPin,
)
from services.source_content.outline import SourceDocumentOutlineBuilder
from services.source_content.pipeline import (
    _compatibility_body_slot_id,
    _compatibility_text_for_slide,
)


def _manifest(*, unit_count: int = 36) -> SourceContentManifest:
    units: list[dict[str, Any]] = []
    text_blocks = 0
    for index in range(unit_count):
        blocks: list[dict[str, Any]] = []
        # Empty source pages are intentionally included to lock cardinality.
        if index % 2 == 0:
            blocks.append(
                {
                    "sourceBlockId": f"source-slide:{index + 1}:text:1",
                    "kind": "text",
                    "semanticRole": "body",
                    "order": 0,
                    "payload": {"text": f"Page {index + 1}"},
                    "contentDigest": "sha256:" + "a" * 64,
                }
            )
            text_blocks += 1
        units.append(
            {
                "sourceUnitId": f"source-slide:{index + 1}",
                "sourceIndex": index,
                "blocks": blocks,
            }
        )
    payload: dict[str, Any] = {
        "contractVersion": "source-content-manifest/v1",
        "manifestId": "scm_allocator_test",
        "ownerId": "owner-test",
        "referenceSnapshotId": "snapshot-test",
        "sourceType": "pptx",
        "sourceDigest": "sha256:" + "b" * 64,
        "objectInventory": {
            "discoveredObjectCount": text_blocks,
            "classifiedObjectCount": text_blocks,
            "contentBearingObjectCount": text_blocks,
            "unsupportedContentObjectIds": [],
            "unclassifiedObjectIds": [],
            "externalRelationshipCount": 0,
        },
        "inventoryObjects": [],
        "units": units,
        "counts": {
            "sourceUnits": unit_count,
            "textBlocks": text_blocks,
            "tableCells": 0,
            "chartValues": 0,
            "uniqueImages": 0,
            "imageOccurrences": 0,
            "rasterFallbackBlocks": 0,
        },
    }
    payload["manifestDigest"] = canonical_digest(payload)
    return SourceContentManifest.model_validate(payload)


def _context() -> tuple[SourceContentManifest, SourceContentManifestPin, Any, list[WritableLayout]]:
    manifest = _manifest()
    pin = SourceContentManifestPin(
        manifest_id=manifest.manifest_id,
        digest=manifest.manifest_digest,
    )
    outline = SourceDocumentOutlineBuilder().build(
        manifest=manifest,
        manifest_pin=pin,
    )
    layout = WritableLayout(
        layout_role="title-content",
        layout_id="layout:allocator-test",
        slots=(
            WritableSlot(
                slot_id="slot:body",
                bounds=Rect(x=0, y=0, width=100, height=100),
                accepted_kinds=("text",),
                capacity_units=1,
            ),
        ),
    )
    composition_pin = PlacementCompositionPin(
        authored_style_id="style:test",
        authored_style_digest="sha256:" + "c" * 64,
        authored_style_pin_digest="sha256:" + "d" * 64,
        master_geometry_digest="sha256:" + "e" * 64,
    )
    return manifest, pin, (outline, composition_pin), [layout]


@pytest.mark.parametrize("planner_type", [ContentAllocator, PageLockedTemplateCompositionPlanner])
def test_page_locked_planner_preserves_36_source_units(planner_type: type[ContentAllocator]) -> None:
    manifest, pin, (outline, composition_pin), layouts = _context()
    plan = planner_type().allocate(
        manifest=manifest,
        manifest_pin=pin,
        composition_pin=composition_pin,
        layouts=layouts,
        outline=outline,
    )

    assert len(plan.slides) == 36
    assert [slide.output_index for slide in plan.slides] == list(range(36))
    assert [slide.source_unit_id for slide in plan.slides] == [
        f"source-slide:{index}" for index in range(1, 37)
    ]
    assert sum(len(slide.placements) for slide in plan.slides) == 18


def test_multiple_blocks_and_capacity_recompose_one_source_page() -> None:
    manifest = _manifest(unit_count=1)
    # Replace the single block with several blocks that each exceed the slot's
    # capacity; all fragments must remain on the one locked output slide.
    blocks = tuple(
        manifest.units[0].blocks[0].model_copy(
            update={
                "source_block_id": f"source-slide:1:text:{index}",
                "order": index,
                "payload": {"runs": [{"text": "a"}, {"text": "b"}]},
            }
        )
        for index in range(1, 4)
    )
    manifest = manifest.model_copy(
        update={
            "units": (manifest.units[0].model_copy(update={"blocks": blocks}),),
            "counts": manifest.counts.model_copy(update={"text_blocks": 3}),
        }
    )
    # The test intentionally uses the existing manifest pin contract after
    # rebuilding its digest, as model_copy does not mutate sealed content.
    raw = manifest.model_dump(mode="json", by_alias=True, exclude={"manifest_digest"})
    manifest = manifest.model_copy(update={"manifest_digest": canonical_digest(raw)})
    pin = SourceContentManifestPin(manifest_id=manifest.manifest_id, digest=manifest.manifest_digest)
    outline = SourceDocumentOutlineBuilder().build(manifest=manifest, manifest_pin=pin)
    layout = WritableLayout(
        layout_role="body",
        layout_id="layout:capacity-test",
        slots=(
            WritableSlot(
                slot_id="slot:body",
                bounds=Rect(x=0, y=0, width=100, height=100),
                accepted_kinds=("text",),
                capacity_units=1,
            ),
        ),
    )
    composition_pin = PlacementCompositionPin(
        authored_style_id="style:test",
        authored_style_digest="sha256:" + "c" * 64,
        authored_style_pin_digest="sha256:" + "d" * 64,
        master_geometry_digest="sha256:" + "e" * 64,
    )
    plan = ContentAllocator().allocate(
        manifest=manifest,
        manifest_pin=pin,
        composition_pin=composition_pin,
        layouts=[layout],
        outline=outline,
    )

    assert len(plan.slides) == 1
    assert len(plan.slides[0].placements) == 6
    assert {placement.slot_id for placement in plan.slides[0].placements} == {"slot:body"}


def test_outline_reading_order_mismatch_fails_closed() -> None:
    manifest, pin, (outline, composition_pin), layouts = _context()
    invalid_slide = outline.slides[0].model_copy(
        update={"reading_order": ("missing:block",)}
    )
    invalid_outline = outline.model_copy(update={"slides": (invalid_slide,) + outline.slides[1:]})

    with pytest.raises(ValueError, match="source_content_outline_invalid"):
        ContentAllocator().allocate(
            manifest=manifest,
            manifest_pin=pin,
            composition_pin=composition_pin,
            layouts=layouts,
            outline=invalid_outline,
        )


def test_duplicate_source_unit_in_outline_fails_closed() -> None:
    manifest, pin, (outline, composition_pin), layouts = _context()
    duplicate = outline.slides[0].model_copy()
    invalid_outline = outline.model_copy(
        update={"slides": (outline.slides[0], duplicate) + outline.slides[2:]}
    )

    with pytest.raises(ValueError, match="source_content_source_unit_cardinality_mismatch"):
        ContentAllocator().allocate(
            manifest=manifest,
            manifest_pin=pin,
            composition_pin=composition_pin,
            layouts=layouts,
            outline=invalid_outline,
        )


def test_blank_source_page_uses_compatibility_marker_without_scene_content() -> None:
    manifest, pin, (outline, composition_pin), layouts = _context()
    plan = ContentAllocator().allocate(
        manifest=manifest,
        manifest_pin=pin,
        composition_pin=composition_pin,
        layouts=layouts,
        outline=outline,
    )
    blank_slide = plan.slides[1]

    assert blank_slide.placements == ()
    assert _compatibility_text_for_slide(
        manifest=manifest,
        source_unit_id=blank_slide.source_unit_id,
    ) == "[source-content-empty:source-slide:2]"
    assert _compatibility_body_slot_id(blank_slide, layouts) == "slot:body"
