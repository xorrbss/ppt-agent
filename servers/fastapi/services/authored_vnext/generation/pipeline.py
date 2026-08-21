"""Published-master-authoritative authored vNext generation pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from models.authored_vnext.deck import (
    AuthoredCompositionPin,
    BodyShapeElement,
    BodyTextElement,
    CandidateBundle,
    CompletedSlideCheckpoint,
    DeckSceneIR,
    DeckGenerationSnapshot,
    GenerateDeckRequest,
    GenerationCheckpoint,
    GenerationResult,
    MasterDigestPin,
    OutlineAssignment,
    OutlineAssignmentSet,
    RegenerateBodyCommand,
    RegenerationResult,
    ResumeGenerationRequest,
    RollbackDeckRequest,
    RollbackResult,
    SlideValidationCheckpoint,
    SlideVisualPlan,
    ValidatedSlideSpecV2,
)
from models.authored_vnext.master.operations import MasterRevisionResponse
from models.authored_vnext.master.slide import SlideSpecV2
from services.authored_vnext.master.digests import canonical_digest

from .core_bridge import AuthoringCoreBridgePort, CoreBridgeCheckpoint
from .effective_style import materialize_authored_composition
from .errors import (
    CandidateRejectedError,
    GenerationConflictError,
    GenerationNotFoundError,
    RollbackConflictError,
    StaleMasterError,
)
from .montage import build_generation_montage_manifest
from .master_pin import validate_master_pin as _validate_master_pin
from .outline import OutlineItem as PlanningOutlineItem
from .outline import plan_outline
from .title_fit import fit_and_promote_title
from .quality_wire import build_deck_quality_report, validate_pipeline_slide_candidate
from .repository import GenerationRepository
from .scene_compiler import (
    compatibility_composition_digest,
    compile_deck_scene,
    compile_slide_scene,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _plan_body_elements(slide_id: str, bounds, body: str):
    """Emit a real authored object composition, not one synthetic body blob."""

    suffix = slide_id.removeprefix("slide:")
    inset = min(24.0, bounds.width / 12, bounds.height / 12)
    text_bounds = bounds.model_copy(
        update={
            "x": bounds.x + inset,
            "y": bounds.y + inset,
            "width": bounds.width - (2 * inset),
            "height": bounds.height - (2 * inset),
        }
    )
    return [
        BodyShapeElement(
            kind="shape",
            element_id=f"body:{suffix}.card",
            bounds=bounds,
            z_order=0,
            semantic_role="decoration",
            shape="roundRect",
            color_token="surface",
        ),
        BodyTextElement(
            kind="text",
            element_id=f"body:{suffix}.content",
            bounds=text_bounds,
            z_order=1,
            semantic_role="body",
            text=body,
        ),
    ]


def _visual_plan(
    *, slide_id: str, item_id: str, role: str, layout_id: str,
    slot_id: str, source_digest: str, bounds, body: str,
) -> SlideVisualPlan:
    payload = {
        "schemaVersion": "slide-visual-plan/v1",
        "scope": "bodyOnly",
        "slideId": slide_id,
        "outlineItemId": item_id,
        "role": role,
        "layoutId": layout_id,
        "bodySlotId": slot_id,
        "sourceDigest": source_digest,
        "elements": [
            element.model_dump(mode="json", by_alias=True)
            for element in _plan_body_elements(slide_id, bounds, body)
        ],
    }
    return SlideVisualPlan(**payload, planDigest=canonical_digest(payload))


def _validated_slide(
    master: MasterRevisionResponse,
    planned,
    item_id: str,
    source_digest: str,
    pin: MasterDigestPin,
    composition: AuthoredCompositionPin | None,
    policy: str,
    revision: int = 1,
    structured_inputs: dict | None = None,
) -> tuple[SlideVisualPlan, ValidatedSlideSpecV2]:
    layout = next(
        layout
        for layout in (
            master.contract.layouts.cover,
            master.contract.layouts.section,
            master.contract.layouts.standard,
            master.contract.layouts.long_title,
        )
        if layout.id == planned.layout_id
    )
    body_slot = next(
        slot for slot in layout.slots if slot.id == planned.model_input.slot_id
    )
    title_slot = next((slot for slot in layout.slots if slot.role == "title"), None)
    if title_slot is None:
        raise ValueError("canonical scene requires an authored title slot")
    plan = _visual_plan(
        slide_id=planned.slide_id,
        item_id=item_id,
        role="longTitle" if planned.role == "long-title" else planned.role,
        layout_id=planned.layout_id,
        slot_id=body_slot.id,
        source_digest=source_digest,
        bounds=body_slot.bounds,
        body=planned.model_input.body,
    )
    content_digest = canonical_digest(
        {
            "title": planned.title.model_dump(mode="json", by_alias=True),
            "body": planned.model_input.body,
        }
    )
    # SlideSpec/DeckGenerationSnapshot v1 binds this compatibility field to the
    # published master pin. The composition's resolved reference snapshot is
    # the authoritative provenance for template-owned font family/size/style.
    durable_title_fit = planned.title_fit.model_copy(
        update={"font_manifest_digest": pin.font_manifest_digest}
    )
    slide_payload = {
        "schemaVersion": "slidespec/v2",
        "slideId": planned.slide_id,
        "revision": revision,
        "layoutId": planned.layout_id,
        "title": planned.title.model_dump(mode="json", by_alias=True),
        "body": planned.model_input.body,
        "ownership": {"locked": False, "modelWritable": True},
        "masterDigest": pin.master_digest,
        "titleFit": durable_title_fit.model_dump(mode="json", by_alias=True),
        "contentDigest": content_digest,
    }
    slide = SlideSpecV2(
        **slide_payload,
        specDigest=canonical_digest(slide_payload),
    )
    scene = compile_slide_scene(
        slide=slide,
        visual_plan=plan,
        title_bounds=title_slot.bounds,
        contract=master.contract,
        structured_inputs=structured_inputs,
    )
    checkpoint = SlideValidationCheckpoint(
        master_digest=pin.master_digest,
        layout_registry_digest=pin.layout_registry_digest,
        font_manifest_digest=pin.font_manifest_digest,
        source_digest=source_digest,
        composition=composition,
        visual_plan_digest=plan.plan_digest,
        scene_digest=scene.slide_digest,
        quality_policy_version=policy,
        layout_id=slide.layout_id,
        body_slot_id=body_slot.id,
        validated_at=_now(),
    )
    return plan, ValidatedSlideSpecV2(
        slide=slide,
        checkpoint=checkpoint,
        scene=scene,
    )


def _seal_deck(
    *, deck_id: str, revision: int, parent: str | None, pin: MasterDigestPin,
    composition: AuthoredCompositionPin | None,
    source_digest: str, slides: list[ValidatedSlideSpecV2],
    canonical_scene: DeckSceneIR | None = None,
    asset_manifest=None,
    data_manifest=None,
) -> DeckGenerationSnapshot:
    scene_slides = [item.scene for item in slides]
    if any(scene is None for scene in scene_slides):
        raise ValueError("new authored deck requires every canonical slide scene")
    expected_composition_digest = (
        composition.digest
        if composition is not None
        else compatibility_composition_digest(master_pin=pin)
    )
    scene = canonical_scene or compile_deck_scene(
        deck_id=deck_id,
        revision=revision,
        composition_digest=expected_composition_digest,
        source_digest=source_digest,
        slides=[item for item in scene_slides if item is not None],
        asset_manifest=asset_manifest,
        data_manifest=data_manifest,
    )
    if (
        scene.deck_id != deck_id
        or scene.revision != revision
        or scene.composition_digest != expected_composition_digest
        or scene.source_digest != source_digest
        or list(scene.slides) != [item for item in scene_slides if item is not None]
    ):
        raise ValueError("canonical scene differs from deck publication inputs")
    payload = {
        "schemaVersion": "deck-generation-snapshot/v1",
        "deckId": deck_id,
        "revision": revision,
        "parentRevisionDigest": parent,
        "master": pin.model_dump(mode="json", by_alias=True),
        "sourceDigest": source_digest,
        "scene": scene.model_dump(mode="json", by_alias=True),
        "slides": [item.model_dump(mode="json", by_alias=True) for item in slides],
        "createdAt": _now(),
    }
    if composition is not None:
        payload["composition"] = composition.model_dump(mode="json", by_alias=True)
    return DeckGenerationSnapshot(**payload, digest=canonical_digest(payload))


def _completed(slides: list[ValidatedSlideSpecV2]) -> list[CompletedSlideCheckpoint]:
    return [
        CompletedSlideCheckpoint(
            slide_id=item.slide.slide_id,
            slide_index=index,
            spec_digest=item.slide.spec_digest,
            validation_checkpoint_digest=canonical_digest(item.checkpoint),
            slide_scene_digest=(
                item.scene.slide_digest if item.scene is not None else None
            ),
        )
        for index, item in enumerate(slides)
    ]


def _published_checkpoint(
    request: GenerateDeckRequest,
    slides: list[ValidatedSlideSpecV2],
    *, scene_digest: str | None = None, attempt: int = 1,
) -> GenerationCheckpoint:
    payload = {
        "schemaVersion": "deck-generation-checkpoint/v1",
        "operationId": request.operation_id,
        "deckId": request.deck_id,
        "stage": "published",
        "master": request.master.model_dump(mode="json", by_alias=True),
        "sourceDigest": request.outline.source_digest,
        "qualityPolicyVersion": request.quality_policy_version,
        "totalSlides": len(slides),
        "completedSlides": [item.model_dump(mode="json", by_alias=True) for item in _completed(slides)],
        "attempt": attempt,
        "createdAt": _now(),
    }
    if scene_digest is not None:
        payload["sceneDigest"] = scene_digest
    if request.composition is not None:
        payload["composition"] = request.composition.model_dump(mode="json", by_alias=True)
    return GenerationCheckpoint(**payload, checkpointDigest=canonical_digest(payload))


class AuthoredVNextGenerationPipeline:
    def __init__(
        self,
        repository: GenerationRepository,
        core_bridge: AuthoringCoreBridgePort,
    ) -> None:
        self.repository = repository
        self.core_bridge = core_bridge
    def generate(self, request: GenerateDeckRequest) -> GenerationResult:
        master = _validate_master_pin(self.repository, request.master)
        effective_contract = (
            materialize_authored_composition(master.contract, request.composition)
            if request.composition is not None
            else master.contract
        )
        planning_master = master.model_copy(update={"contract": effective_contract})
        if request.publication_fence.expected_deck_revision != 0:
            raise GenerationConflictError("new generation requires revision zero fence")
        planning_items = [
            PlanningOutlineItem(
                slide_id=f"slide:{item.item_id.removeprefix('outline:')}",
                title=item.title,
                body=item.summary,
                kind="section" if item.role_hint == "section" else "content",
            )
            for item in request.outline.items
        ]
        planned = plan_outline(
            master,
            planning_items,
            effective_contract=effective_contract,
        )
        assignments: list[OutlineAssignment] = []
        validated: list[ValidatedSlideSpecV2] = []
        plans: list[SlideVisualPlan] = []
        scene_inputs = {
            item.outline_item_id: {
                "roleHint": item.role_hint,
                **(
                    {"sourceImage": item.source_image}
                    if item.source_image is not None
                    else {}
                ),
                **(
                    {"sourceData": item.source_data}
                    if item.source_data is not None
                    else {}
                ),
            }
            for item in request.slide_scene_inputs
        }
        for item, slide in zip(request.outline.items, planned.slides, strict=True):
            if item.role_hint == "longTitle" and slide.layout_id != effective_contract.layouts.long_title.id:
                forced_title = fit_and_promote_title(
                    effective_contract,
                    item.title,
                    effective_contract.layouts.long_title.id,
                )
                long_body_slot = next(
                    slot
                    for slot in effective_contract.layouts.long_title.slots
                    if slot.role == "body"
                )
                slide = replace(
                    slide,
                    role="long-title",
                    layout_id=forced_title.layout_id,
                    title=forced_title.title,
                    title_fit=forced_title.fit,
                    model_input=replace(
                        slide.model_input,
                        slot_id=long_body_slot.id,
                        bounds=(
                            long_body_slot.bounds.x,
                            long_body_slot.bounds.y,
                            long_body_slot.bounds.width,
                            long_body_slot.bounds.height,
                        ),
                        style=long_body_slot.style,
                    ),
                )
            plan, sealed = _validated_slide(
                planning_master, slide, item.item_id, request.outline.source_digest,
                request.master, request.composition, request.quality_policy_version,
                structured_inputs=scene_inputs.get(item.item_id),
            )
            plans.append(plan)
            validated.append(sealed)
            role = "longTitle" if slide.role == "long-title" else slide.role
            reason = (
                "first-slide-cover" if item.index == 0
                else "title-fit-promotion" if role == "longTitle"
                else "explicit-role-hint" if item.role_hint is not None
                else "section-boundary" if role == "section"
                else "standard-content"
            )
            assignments.append(OutlineAssignment(
                item_id=item.item_id, index=item.index, role=role,
                layout_id=slide.layout_id, title_promoted=role == "longTitle",
                reason=reason,
            ))
        deck = _seal_deck(
            deck_id=request.deck_id, revision=1, parent=None, pin=request.master,
            composition=request.composition,
            source_digest=request.outline.source_digest, slides=validated,
            asset_manifest=request.asset_manifest,
            data_manifest=request.data_manifest,
        )
        quality = build_deck_quality_report(effective_contract, deck, plans)
        if not quality.passed:
            issue_codes = sorted(
                {
                    issue.code
                    for issue in quality.issues
                }
                | {
                    issue.code
                    for gate in quality.slide_gates
                    for issue in gate.issues
                }
            )
            failed_checks = [
                name
                for name in (
                    "fonts",
                    "colors",
                    "frame",
                    "page_numbers",
                    "layouts",
                    "slots",
                    "sources",
                )
                if not getattr(quality, name).passed
            ]
            evidence = ",".join([*issue_codes, *failed_checks]) or "unknown"
            raise CandidateRejectedError(
                f"generated deck failed quality gate: {evidence}"
            )
        montage = build_generation_montage_manifest(deck)
        if deck.scene is None:
            raise CandidateRejectedError("generated deck has no canonical scene")
        artifact = self.core_bridge.export(
            master=effective_contract, master_revision=master.revision,
            slides=[item.slide for item in validated],
            checkpoint=CoreBridgeCheckpoint(
                master_digest=request.master.master_digest,
                layout_registry_digest=request.master.layout_registry_digest,
                font_manifest_digest=request.master.font_manifest_digest,
                source_digest=request.outline.source_digest,
                composition=request.composition,
                scene_digest=deck.scene.scene_digest,
            ),
            scene=deck.scene,
        )
        checkpoint = _published_checkpoint(
            request,
            validated,
            scene_digest=deck.scene.scene_digest,
        )
        result = GenerationResult(
            operation_id=request.operation_id,
            checkpoint=checkpoint,
            composition=request.composition,
            outline_assignments=OutlineAssignmentSet(
                source_digest=request.outline.source_digest,
                master_digest=request.master.master_digest,
                layout_registry_digest=request.master.layout_registry_digest,
                assignments=assignments,
            ),
            deck=deck, quality=quality, montage=montage,
            export_artifact_digest=artifact.artifact_digest,
        )
        self.repository.publish_result(
            deck,
            result,
            expected_revision=None,
            expected_digest=None,
        )
        return result
    def resume(self, request: ResumeGenerationRequest) -> GenerationResult:
        prior = self.repository.get_operation(request.operation_id)
        head = _validate_master_pin(self.repository, request.checkpoint.master)
        if request.expected_master_digest != head.digest:
            raise StaleMasterError()
        if request.expected_checkpoint_digest != prior.checkpoint.checkpoint_digest:
            raise GenerationConflictError("stale resume checkpoint")
        if request.checkpoint.composition != prior.composition:
            raise GenerationConflictError("stale resume composition")
        return prior
    def regenerate_body(self, request: RegenerateBodyCommand) -> RegenerationResult:
        candidate = self.prepare_regeneration(request)
        return self.publish_regeneration(candidate.candidate_digest)
    def prepare_regeneration(self, request: RegenerateBodyCommand) -> CandidateBundle:
        """Build and persist a detached candidate without advancing the deck head."""
        master = _validate_master_pin(self.repository, request.master)
        deck = self.repository.get_deck(request.deck_id)
        effective_contract = (
            materialize_authored_composition(master.contract, deck.composition)
            if deck.composition is not None
            else master.contract
        )
        effective_master = master.model_copy(update={"contract": effective_contract})
        fence = request.publication_fence
        if deck.revision != fence.expected_deck_revision or deck.digest != fence.expected_active_revision_digest:
            raise GenerationConflictError("regeneration publication fence failed")
        if deck.master != request.master or deck.source_digest != request.source_digest:
            raise StaleMasterError("regeneration checkpoint changed")
        index = next((i for i, item in enumerate(deck.slides) if item.slide.slide_id == request.slide_id), None)
        if index is None:
            raise GenerationConflictError("slide not found")
        current = deck.slides[index]
        if (
            current.slide.revision != request.expected_slide_revision
            or current.slide.spec_digest != request.expected_slide_spec_digest
        ):
            raise GenerationConflictError("slide CAS failed")
        planned = type("RegenerationPlan", (), {
            "slide_id": current.slide.slide_id,
            "role": "long-title" if current.slide.layout_id == effective_contract.layouts.long_title.id else "standard",
            "layout_id": current.slide.layout_id,
            "title": current.slide.title,
            "title_fit": current.slide.title_fit,
            "model_input": type("Input", (), {"slot_id": current.checkpoint.body_slot_id, "body": request.body})(),
        })()
        plan, candidate_slide = _validated_slide(
            effective_master, planned, f"outline:{request.slide_id.removeprefix('slide:')}",
            request.source_digest, request.master, deck.composition,
            current.checkpoint.quality_policy_version,
            current.slide.revision + 1,
        )
        slide_gate = validate_pipeline_slide_candidate(
            effective_contract, candidate_slide, plan
        )
        candidate_payload = {
            "lifecycle": "detached", "parentDeckDigest": deck.digest,
            "visualPlan": plan.model_dump(mode="json", by_alias=True),
            "slide": candidate_slide.model_dump(mode="json", by_alias=True),
            "slideGate": slide_gate.model_dump(mode="json", by_alias=True),
        }
        candidate = CandidateBundle(**candidate_payload, candidateDigest=canonical_digest(candidate_payload))
        if not slide_gate.passed:
            raise CandidateRejectedError()
        try:
            existing = self.repository.get_candidate(candidate.candidate_digest)
        except GenerationNotFoundError:
            existing = None
        if existing is not None:
            try:
                persisted = CandidateBundle.model_validate(existing["candidate"])
            except (KeyError, TypeError, ValueError) as error:
                raise GenerationConflictError("candidate record is invalid") from error
            if persisted != candidate:
                raise GenerationConflictError("candidate digest payload conflict")
            return persisted
        self.repository.save_candidate(
            candidate.candidate_digest,
            {
                "schemaVersion": "regeneration-candidate-record/v1",
                "command": request.model_dump(mode="json", by_alias=True),
                "candidate": candidate.model_dump(mode="json", by_alias=True),
            },
        )
        return candidate
    def publish_regeneration(
        self,
        candidate_digest: str,
        *,
        publication_operation_id: str | None = None,
    ) -> RegenerationResult:
        """Publish an accepted candidate only if its original CAS fence still holds."""
        stored = self.repository.get_candidate(candidate_digest)
        try:
            request = RegenerateBodyCommand.model_validate(stored["command"])
            candidate = CandidateBundle.model_validate(stored["candidate"])
        except (KeyError, TypeError, ValueError) as error:
            raise GenerationConflictError("candidate record is invalid") from error
        if candidate.candidate_digest != candidate_digest:
            raise GenerationConflictError("candidate digest changed")
        master = _validate_master_pin(self.repository, request.master)
        deck = self.repository.get_deck(request.deck_id)
        effective_contract = (
            materialize_authored_composition(master.contract, deck.composition)
            if deck.composition is not None
            else master.contract
        )
        fence = request.publication_fence
        if (
            deck.revision != fence.expected_deck_revision
            or deck.digest != fence.expected_active_revision_digest
            or candidate.parent_deck_digest != deck.digest
        ):
            raise GenerationConflictError("candidate publication fence failed")
        index = next(
            (
                i
                for i, item in enumerate(deck.slides)
                if item.slide.slide_id == request.slide_id
            ),
            None,
        )
        if index is None:
            raise GenerationConflictError("slide not found")
        current = deck.slides[index]
        if (
            current.slide.revision != request.expected_slide_revision
            or current.slide.spec_digest != request.expected_slide_spec_digest
            or candidate.slide.slide.revision != current.slide.revision + 1
        ):
            raise GenerationConflictError("candidate slide CAS failed")
        slides = list(deck.slides)
        slides[index] = candidate.slide
        published = _seal_deck(
            deck_id=deck.deck_id, revision=deck.revision + 1, parent=deck.digest,
            pin=deck.master, composition=deck.composition,
            source_digest=deck.source_digest, slides=slides,
        )
        plans = [candidate.visual_plan if i == index else _visual_plan(
            slide_id=item.slide.slide_id,
            item_id=f"outline:{item.slide.slide_id.removeprefix('slide:')}",
            role="longTitle" if item.slide.layout_id == effective_contract.layouts.long_title.id else "standard",
            layout_id=item.slide.layout_id, slot_id=item.checkpoint.body_slot_id,
            source_digest=item.checkpoint.source_digest,
            bounds=next(
                slot.bounds
                for layout in (
                    effective_contract.layouts.cover,
                    effective_contract.layouts.section,
                    effective_contract.layouts.standard,
                    effective_contract.layouts.long_title,
                )
                for slot in layout.slots
                if slot.id == item.checkpoint.body_slot_id
            ),
            body=item.slide.body,
        ) for i, item in enumerate(slides)]
        quality = build_deck_quality_report(effective_contract, published, plans)
        if not quality.passed:
            raise CandidateRejectedError("regenerated deck failed aggregate gate")
        if published.scene is None:
            raise CandidateRejectedError("regenerated deck has no canonical scene")
        self.core_bridge.export(
            master=effective_contract, master_revision=master.revision,
            slides=[item.slide for item in slides],
            checkpoint=CoreBridgeCheckpoint(
                master_digest=request.master.master_digest,
                layout_registry_digest=request.master.layout_registry_digest,
                font_manifest_digest=request.master.font_manifest_digest,
                source_digest=request.source_digest,
                composition=published.composition,
                scene_digest=published.scene.scene_digest,
            ),
            scene=published.scene,
        )
        result = RegenerationResult(
            operation_id=publication_operation_id or request.operation_id,
            candidate=candidate,
            deck=published, quality=quality,
        )
        self.repository.publish_result(
            published,
            result,
            expected_revision=deck.revision,
            expected_digest=deck.digest,
        )
        return result
    def rollback(self, request: RollbackDeckRequest) -> RollbackResult:
        current = self.repository.get_deck(request.deck_id)
        fence = request.publication_fence
        if current.revision != fence.expected_deck_revision or current.digest != fence.expected_active_revision_digest:
            raise RollbackConflictError()
        target = self.repository.get_deck(request.deck_id, request.target_revision)
        if target.digest != request.target_revision_digest or target.master.master_digest != request.master_digest:
            raise RollbackConflictError("rollback target changed")
        restored = _seal_deck(
            deck_id=current.deck_id, revision=current.revision + 1, parent=current.digest,
            pin=target.master, composition=target.composition,
            source_digest=target.source_digest, slides=list(target.slides),
        )
        result = RollbackResult(
            operation_id=request.operation_id,
            restored_from_revision=target.revision,
            deck=restored,
        )
        self.repository.publish_result(
            restored,
            result,
            expected_revision=current.revision,
            expected_digest=current.digest,
        )
        return result
