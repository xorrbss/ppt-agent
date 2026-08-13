"""Typed deck mutations that preserve the authored master contract."""

from __future__ import annotations

from models.authored_vnext.deck import DeckMutationResult, ReorderSlidesCommand
from services.authored_vnext.generation.core_bridge import (
    AuthoringCoreBridgePort,
    CoreBridgeCheckpoint,
)
from services.authored_vnext.generation.errors import GenerationConflictError
from services.authored_vnext.generation.pipeline import _seal_deck, _visual_plan
from services.authored_vnext.generation.quality_wire import build_deck_quality_report
from services.authored_vnext.generation.repository import GenerationRepository


def reorder_deck_slides(
    repository: GenerationRepository,
    core_bridge: AuthoringCoreBridgePort,
    command: ReorderSlidesCommand,
) -> DeckMutationResult:
    current = repository.get_deck(command.deck_id)
    fence = command.publication_fence
    if (
        current.revision != fence.expected_deck_revision
        or current.digest != fence.expected_active_revision_digest
        or current.master.master_digest != command.master_digest
    ):
        raise GenerationConflictError("reorder publication fence failed")
    by_id = {item.slide.slide_id: item for item in current.slides}
    if set(command.ordered_slide_ids) != set(by_id):
        raise GenerationConflictError("reorder must contain every slide exactly once")
    slides = [by_id[slide_id] for slide_id in command.ordered_slide_ids]
    master = repository.get_master(current.master.master_id, current.master.master_revision)
    if master.digest != current.master.master_digest:
        raise GenerationConflictError("reorder master changed")
    published = _seal_deck(
        deck_id=current.deck_id,
        revision=current.revision + 1,
        parent=current.digest,
        pin=current.master,
        source_digest=current.source_digest,
        slides=slides,
    )
    plans = []
    for item in slides:
        bounds = next(
            slot.bounds
            for layout in (
                master.contract.layouts.cover,
                master.contract.layouts.section,
                master.contract.layouts.standard,
                master.contract.layouts.long_title,
            )
            for slot in layout.slots
            if slot.id == item.checkpoint.body_slot_id
        )
        plans.append(_visual_plan(
            slide_id=item.slide.slide_id,
            item_id=f"outline:{item.slide.slide_id.removeprefix('slide:')}",
            role="longTitle" if item.slide.layout_id == master.contract.layouts.long_title.id else "standard",
            layout_id=item.slide.layout_id,
            slot_id=item.checkpoint.body_slot_id,
            source_digest=item.checkpoint.source_digest,
            bounds=bounds,
            body=item.slide.body,
        ))
    quality = build_deck_quality_report(master.contract, published, plans)
    if not quality.passed:
        raise GenerationConflictError("reordered deck failed quality gate")
    core_bridge.export(
        master=master.contract,
        master_revision=master.revision,
        slides=[item.slide for item in slides],
        checkpoint=CoreBridgeCheckpoint(
            master_digest=current.master.master_digest,
            layout_registry_digest=current.master.layout_registry_digest,
            font_manifest_digest=current.master.font_manifest_digest,
            source_digest=current.source_digest,
        ),
    )
    result = DeckMutationResult(
        operation_id=command.operation_id,
        mutation="slides.reorder",
        deck=published,
        quality=quality,
    )
    repository.publish_result(
        published,
        result,
        expected_revision=current.revision,
        expected_digest=current.digest,
    )
    return result


__all__ = ["reorder_deck_slides"]
