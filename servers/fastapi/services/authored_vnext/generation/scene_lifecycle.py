"""Detached Scene lifecycle commands sharing the generation repository CAS boundary."""
from __future__ import annotations

from models.authored_vnext.deck import (
    CandidateBundle, RegenerateBodyCommand, RegenerationResult,
    RollbackDeckRequest, RollbackResult,
)
from models.authored_vnext.deck.scene import seal_deck_scene
from services.authored_vnext.master.digests import canonical_digest
from .core_bridge import CoreBridgeCheckpoint
from .effective_style import materialize_authored_composition
from .errors import (
    CandidateRejectedError, GenerationConflictError, GenerationNotFoundError,
    RollbackConflictError, StaleMasterError,
)
from .master_pin import validate_master_pin as _validate_master_pin
from .quality_wire import build_deck_quality_report, validate_pipeline_slide_candidate
from services.document_canvas_scene_layout import document_scene_master


class SceneLifecycle:
    def regenerate_body(self, request: RegenerateBodyCommand) -> RegenerationResult:
        candidate = self.prepare_regeneration(request)
        return self.publish_regeneration(candidate.candidate_digest)
    def prepare_regeneration(self, request: RegenerateBodyCommand) -> CandidateBundle:
        """Build and persist a detached candidate without advancing the deck head."""
        from .pipeline import _validated_slide
        master = _validate_master_pin(self.repository, request.master)
        deck = self.repository.get_deck(request.deck_id)
        effective_contract = (
            materialize_authored_composition(master.contract, deck.composition)
            if deck.composition is not None
            else master.contract
        )
        effective_contract = document_scene_master(
            effective_contract, deck.scene.document_canvas if deck.scene is not None else None
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
        from .pipeline import _seal_deck, _visual_plan
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
        export_contract = effective_contract
        effective_contract = document_scene_master(
            effective_contract, deck.scene.document_canvas if deck.scene is not None else None
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
        canonical_scene = None
        if deck.scene is not None:
            canonical_scene = seal_deck_scene(
                deck_id=deck.deck_id, revision=deck.revision + 1,
                composition_digest=deck.scene.composition_digest,
                source_digest=deck.source_digest,
                slides=[item.scene for item in slides],
                document_canvas=deck.scene.document_canvas,
                hybrid_profile_id=deck.scene.hybrid_profile_id,
                asset_manifest=list(deck.scene.asset_manifest),
                data_manifest=list(deck.scene.data_manifest),
                capabilities=list(deck.scene.capabilities),
            )
        published = _seal_deck(
            deck_id=deck.deck_id, revision=deck.revision + 1, parent=deck.digest,
            pin=deck.master, composition=deck.composition,
            source_digest=deck.source_digest, slides=slides,
            canonical_scene=canonical_scene,
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
        # This report verifies published master/layout/font pins. Target layout
        # is an ephemeral projection and must not replace that source identity.
        quality = build_deck_quality_report(export_contract, published, plans)
        if not quality.passed:
            raise CandidateRejectedError("regenerated deck failed aggregate gate")
        if published.scene is None:
            raise CandidateRejectedError("regenerated deck has no canonical scene")
        self.core_bridge.export(
            master=export_contract, master_revision=master.revision,
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
        from .pipeline import _seal_deck
        current = self.repository.get_deck(request.deck_id)
        fence = request.publication_fence
        if current.revision != fence.expected_deck_revision or current.digest != fence.expected_active_revision_digest:
            raise RollbackConflictError()
        target = self.repository.get_deck(request.deck_id, request.target_revision)
        if target.digest != request.target_revision_digest or target.master.master_digest != request.master_digest:
            raise RollbackConflictError("rollback target changed")
        canonical_scene = None
        if target.scene is not None:
            canonical_scene = seal_deck_scene(
                deck_id=current.deck_id, revision=current.revision + 1,
                composition_digest=target.scene.composition_digest,
                source_digest=target.scene.source_digest,
                slides=list(target.scene.slides),
                document_canvas=target.scene.document_canvas,
                hybrid_profile_id=target.scene.hybrid_profile_id,
                asset_manifest=list(target.scene.asset_manifest),
                data_manifest=list(target.scene.data_manifest),
                capabilities=list(target.scene.capabilities),
            )
        restored = _seal_deck(
            deck_id=current.deck_id, revision=current.revision + 1, parent=current.digest,
            pin=target.master, composition=target.composition,
            source_digest=target.source_digest, slides=list(target.slides),
            canonical_scene=canonical_scene,
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
