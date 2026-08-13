"""Published-master-authoritative authored vNext generation pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from models.authored_vnext.deck import (
    BodyTextElement,
    CandidateBundle,
    CompletedSlideCheckpoint,
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
from models.authored_vnext.master.slide import SlideDraft
from services.authored_vnext.master.digests import canonical_digest
from services.authored_vnext.master.service import _seal_slide

from .core_bridge import AuthoringCoreBridgePort, CoreBridgeCheckpoint
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _plan_body_element(slide_id: str, bounds, body: str) -> BodyTextElement:
    return BodyTextElement(
        kind="text",
        element_id=f"body:{slide_id.removeprefix('slide:')}",
        bounds=bounds,
        semantic_role="body",
        text=body,
    )


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
        "elements": [_plan_body_element(slide_id, bounds, body).model_dump(
            mode="json", by_alias=True
        )],
    }
    return SlideVisualPlan(**payload, planDigest=canonical_digest(payload))


def _validated_slide(
    master: MasterRevisionResponse,
    planned,
    item_id: str,
    source_digest: str,
    pin: MasterDigestPin,
    policy: str,
    revision: int = 1,
) -> tuple[SlideVisualPlan, ValidatedSlideSpecV2]:
    body_slot = next(
        slot
        for layout in (
            master.contract.layouts.cover,
            master.contract.layouts.section,
            master.contract.layouts.standard,
            master.contract.layouts.long_title,
        )
        if layout.id == planned.layout_id
        for slot in layout.slots
        if slot.id == planned.model_input.slot_id
    )
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
    slide = _seal_slide(
        master.contract,
        SlideDraft(
            slide_id=planned.slide_id,
            layout_id=planned.layout_id,
            title=planned.title,
            body=planned.model_input.body,
        ),
        revision,
    )
    checkpoint = SlideValidationCheckpoint(
        master_digest=pin.master_digest,
        layout_registry_digest=pin.layout_registry_digest,
        font_manifest_digest=pin.font_manifest_digest,
        source_digest=source_digest,
        visual_plan_digest=plan.plan_digest,
        quality_policy_version=policy,
        layout_id=slide.layout_id,
        body_slot_id=body_slot.id,
        validated_at=_now(),
    )
    return plan, ValidatedSlideSpecV2(slide=slide, checkpoint=checkpoint)


def _seal_deck(
    *, deck_id: str, revision: int, parent: str | None, pin: MasterDigestPin,
    source_digest: str, slides: list[ValidatedSlideSpecV2],
) -> DeckGenerationSnapshot:
    payload = {
        "schemaVersion": "deck-generation-snapshot/v1",
        "deckId": deck_id,
        "revision": revision,
        "parentRevisionDigest": parent,
        "master": pin.model_dump(mode="json", by_alias=True),
        "sourceDigest": source_digest,
        "slides": [item.model_dump(mode="json", by_alias=True) for item in slides],
        "createdAt": _now(),
    }
    return DeckGenerationSnapshot(**payload, digest=canonical_digest(payload))


def _completed(slides: list[ValidatedSlideSpecV2]) -> list[CompletedSlideCheckpoint]:
    return [
        CompletedSlideCheckpoint(
            slide_id=item.slide.slide_id,
            slide_index=index,
            spec_digest=item.slide.spec_digest,
            validation_checkpoint_digest=canonical_digest(item.checkpoint),
        )
        for index, item in enumerate(slides)
    ]


def _published_checkpoint(
    request: GenerateDeckRequest,
    slides: list[ValidatedSlideSpecV2],
    *, attempt: int = 1,
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
        planned = plan_outline(master, planning_items)
        assignments: list[OutlineAssignment] = []
        validated: list[ValidatedSlideSpecV2] = []
        plans: list[SlideVisualPlan] = []
        for item, slide in zip(request.outline.items, planned.slides, strict=True):
            if item.role_hint == "longTitle" and slide.layout_id != master.contract.layouts.long_title.id:
                forced_title = fit_and_promote_title(
                    master.contract,
                    item.title,
                    master.contract.layouts.long_title.id,
                )
                long_body_slot = next(
                    slot
                    for slot in master.contract.layouts.long_title.slots
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
                master, slide, item.item_id, request.outline.source_digest,
                request.master, request.quality_policy_version,
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
            source_digest=request.outline.source_digest, slides=validated,
        )
        quality = build_deck_quality_report(master.contract, deck, plans)
        if not quality.passed:
            raise CandidateRejectedError("generated deck failed quality gate")
        montage = build_generation_montage_manifest(deck)
        artifact = self.core_bridge.export(
            master=master.contract, master_revision=master.revision,
            slides=[item.slide for item in validated],
            checkpoint=CoreBridgeCheckpoint(
                master_digest=request.master.master_digest,
                layout_registry_digest=request.master.layout_registry_digest,
                font_manifest_digest=request.master.font_manifest_digest,
                source_digest=request.outline.source_digest,
            ),
        )
        checkpoint = _published_checkpoint(request, validated)
        result = GenerationResult(
            operation_id=request.operation_id,
            checkpoint=checkpoint,
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
        return prior
    def regenerate_body(self, request: RegenerateBodyCommand) -> RegenerationResult:
        candidate = self.prepare_regeneration(request)
        return self.publish_regeneration(candidate.candidate_digest)
    def prepare_regeneration(self, request: RegenerateBodyCommand) -> CandidateBundle:
        """Build and persist a detached candidate without advancing the deck head."""
        master = _validate_master_pin(self.repository, request.master)
        deck = self.repository.get_deck(request.deck_id)
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
            "role": "long-title" if current.slide.layout_id == master.contract.layouts.long_title.id else "standard",
            "layout_id": current.slide.layout_id,
            "title": current.slide.title,
            "model_input": type("Input", (), {"slot_id": current.checkpoint.body_slot_id, "body": request.body})(),
        })()
        plan, candidate_slide = _validated_slide(
            master, planned, f"outline:{request.slide_id.removeprefix('slide:')}",
            request.source_digest, request.master, current.checkpoint.quality_policy_version,
            current.slide.revision + 1,
        )
        slide_gate = validate_pipeline_slide_candidate(
            master.contract, candidate_slide, plan
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
            pin=deck.master, source_digest=deck.source_digest, slides=slides,
        )
        plans = [candidate.visual_plan if i == index else _visual_plan(
            slide_id=item.slide.slide_id,
            item_id=f"outline:{item.slide.slide_id.removeprefix('slide:')}",
            role="longTitle" if item.slide.layout_id == master.contract.layouts.long_title.id else "standard",
            layout_id=item.slide.layout_id, slot_id=item.checkpoint.body_slot_id,
            source_digest=item.checkpoint.source_digest,
            bounds=next(
                slot.bounds
                for layout in (
                    master.contract.layouts.cover,
                    master.contract.layouts.section,
                    master.contract.layouts.standard,
                    master.contract.layouts.long_title,
                )
                for slot in layout.slots
                if slot.id == item.checkpoint.body_slot_id
            ),
            body=item.slide.body,
        ) for i, item in enumerate(slides)]
        quality = build_deck_quality_report(master.contract, published, plans)
        if not quality.passed:
            raise CandidateRejectedError("regenerated deck failed aggregate gate")
        self.core_bridge.export(
            master=master.contract, master_revision=master.revision,
            slides=[item.slide for item in slides],
            checkpoint=CoreBridgeCheckpoint(
                master_digest=request.master.master_digest,
                layout_registry_digest=request.master.layout_registry_digest,
                font_manifest_digest=request.master.font_manifest_digest,
                source_digest=request.source_digest,
            ),
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
            pin=target.master, source_digest=target.source_digest, slides=list(target.slides),
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
