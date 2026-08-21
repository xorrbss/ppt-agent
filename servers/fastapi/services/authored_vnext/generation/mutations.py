"""Typed deck mutations that preserve the authored master contract."""

from __future__ import annotations

from models.authored_vnext.deck import (
    DeckMutationResult,
    ReorderSlidesCommand,
    SceneMutationCommand,
    SceneMutationResult,
    ValidatedSlideSpecV2,
)
from services.authored_vnext.generation.core_bridge import (
    AuthoringCoreBridgePort,
    CoreBridgeCheckpoint,
)
from services.authored_vnext.generation.errors import GenerationConflictError
from services.authored_vnext.generation.effective_style import (
    materialize_authored_composition,
)
from services.authored_vnext.generation.pipeline import _seal_deck, _visual_plan
from services.authored_vnext.generation.quality_wire import build_deck_quality_report
from services.authored_vnext.generation.repository import GenerationRepository
from services.authored_vnext.generation.scene_compiler import (
    apply_scene_element_mutation,
)
from services.authored_vnext.master.digests import canonical_digest


def _effective_contract(master, composition):
    return (
        materialize_authored_composition(master.contract, composition)
        if composition is not None
        else master.contract
    )


def _plans(contract, slides):
    plans = []
    for item in slides:
        bounds = next(
            slot.bounds
            for layout in (
                contract.layouts.cover,
                contract.layouts.section,
                contract.layouts.standard,
                contract.layouts.long_title,
            )
            for slot in layout.slots
            if slot.id == item.checkpoint.body_slot_id
        )
        plans.append(
            _visual_plan(
                slide_id=item.slide.slide_id,
                item_id=f"outline:{item.slide.slide_id.removeprefix('slide:')}",
                role=(
                    "longTitle"
                    if item.slide.layout_id == contract.layouts.long_title.id
                    else "standard"
                ),
                layout_id=item.slide.layout_id,
                slot_id=item.checkpoint.body_slot_id,
                source_digest=item.checkpoint.source_digest,
                bounds=bounds,
                body=item.slide.body,
            )
        )
    return plans


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
    contract = _effective_contract(master, current.composition)
    published = _seal_deck(
        deck_id=current.deck_id,
        revision=current.revision + 1,
        parent=current.digest,
        pin=current.master,
        composition=current.composition,
        source_digest=current.source_digest,
        slides=slides,
    )
    plans = _plans(contract, slides)
    quality = build_deck_quality_report(contract, published, plans)
    if not quality.passed:
        raise GenerationConflictError("reordered deck failed quality gate")
    if published.scene is None:
        raise GenerationConflictError("reordered deck lost canonical scene")
    core_bridge.export(
        master=contract,
        master_revision=master.revision,
        slides=[item.slide for item in slides],
        checkpoint=CoreBridgeCheckpoint(
            master_digest=current.master.master_digest,
            layout_registry_digest=current.master.layout_registry_digest,
            font_manifest_digest=current.master.font_manifest_digest,
            source_digest=current.source_digest,
            composition=current.composition,
            scene_digest=published.scene.scene_digest,
        ),
        scene=published.scene,
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


def mutate_scene_element(
    repository: GenerationRepository,
    core_bridge: AuthoringCoreBridgePort,
    command: SceneMutationCommand,
) -> SceneMutationResult:
    """Publish one canonical scene edit with slide and deck CAS fences."""

    current = repository.get_deck(command.deck_id)
    fence = command.publication_fence
    if (
        current.revision != fence.expected_deck_revision
        or current.digest != fence.expected_active_revision_digest
    ):
        raise GenerationConflictError("scene mutation publication fence failed")
    if current.scene is None:
        raise GenerationConflictError(
            "compatibility deck has no canonical scene mutation capability"
        )
    try:
        updated_scene = apply_scene_element_mutation(
            current.scene,
            slide_id=command.slide_id,
            element_id=command.element_id,
            mutation=command.kind,
            payload=command.payload,
            expected_slide_revision=command.expected_slide_revision,
            expected_slide_digest=command.expected_slide_digest,
        )
    except ValueError as error:
        raise GenerationConflictError(str(error)) from error
    slide_index = next(
        (
            index
            for index, item in enumerate(current.slides)
            if item.slide.slide_id == command.slide_id
        ),
        None,
    )
    if slide_index is None:
        raise GenerationConflictError("scene mutation compatibility slide not found")
    current_item = current.slides[slide_index]
    updated_slide_scene = updated_scene.slides[slide_index]
    slide_values = current_item.slide.model_dump(mode="json", by_alias=False)
    slide_values["revision"] = updated_slide_scene.revision
    if command.kind == "text.replace":
        old_element = next(
            element
            for element in current.scene.slides[slide_index].elements
            if element.element_id == command.element_id
        )
        if old_element.semantic_role == "title":
            raise GenerationConflictError(
                "title scene mutation requires title-fit recomputation"
            )
        if old_element.semantic_role == "body":
            slide_values["body"] = command.payload["text"]
    content_payload = {
        "title": current_item.slide.title.model_dump(mode="json", by_alias=True),
        "body": slide_values["body"],
    }
    slide_values["content_digest"] = canonical_digest(content_payload)
    slide_values.pop("spec_digest", None)
    # specDigest is excluded from its own canonical input.
    digest_payload = current_item.slide.model_dump(mode="json", by_alias=True)
    digest_payload.update(
        revision=updated_slide_scene.revision,
        body=slide_values["body"],
        contentDigest=slide_values["content_digest"],
    )
    digest_payload.pop("specDigest", None)
    slide_values["spec_digest"] = canonical_digest(digest_payload)
    updated_slide = type(current_item.slide).model_validate(slide_values)
    plan = _visual_plan(
        slide_id=updated_slide.slide_id,
        item_id=f"outline:{updated_slide.slide_id.removeprefix('slide:')}",
        role="standard",
        layout_id=updated_slide.layout_id,
        slot_id=current_item.checkpoint.body_slot_id,
        source_digest=current_item.checkpoint.source_digest,
        bounds=next(
            element.bounds
            for element in updated_slide_scene.elements
            if element.semantic_role == "body"
        ),
        body=updated_slide.body,
    )
    checkpoint = current_item.checkpoint.model_copy(
        update={
            "visual_plan_digest": plan.plan_digest,
            "scene_digest": updated_slide_scene.slide_digest,
        }
    )
    updated_item = ValidatedSlideSpecV2(
        slide=updated_slide,
        checkpoint=checkpoint,
        scene=updated_slide_scene,
    )
    slides = list(current.slides)
    slides[slide_index] = updated_item
    master = repository.get_master(
        current.master.master_id, current.master.master_revision
    )
    if master.digest != current.master.master_digest:
        raise GenerationConflictError("scene mutation master changed")
    contract = _effective_contract(master, current.composition)
    published = _seal_deck(
        deck_id=current.deck_id,
        revision=current.revision + 1,
        parent=current.digest,
        pin=current.master,
        composition=current.composition,
        source_digest=current.source_digest,
        slides=slides,
        canonical_scene=updated_scene,
    )
    plans = _plans(contract, slides)
    quality = build_deck_quality_report(contract, published, plans)
    if not quality.passed:
        raise GenerationConflictError("scene mutation failed quality gate")
    published.scene.require_capability(consumer="nativePptxExport")
    core_bridge.export(
        master=contract,
        master_revision=master.revision,
        slides=[item.slide for item in slides],
        checkpoint=CoreBridgeCheckpoint(
            master_digest=current.master.master_digest,
            layout_registry_digest=current.master.layout_registry_digest,
            font_manifest_digest=current.master.font_manifest_digest,
            source_digest=current.source_digest,
            composition=current.composition,
            scene_digest=published.scene.scene_digest,
        ),
        scene=published.scene,
    )
    result = SceneMutationResult(
        operation_id=command.operation_id,
        mutation=command.kind,
        slide_id=command.slide_id,
        element_id=command.element_id,
        slide_digest=updated_slide_scene.slide_digest,
        scene_digest=published.scene.scene_digest,
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


__all__ = ["mutate_scene_element", "reorder_deck_slides"]
