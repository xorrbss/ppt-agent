"""Generation, regeneration, resume, publication, and rollback wire models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ..master.base import DeckId, Digest, SlideId, StrictCamelModel
from .checkpoint import (
    GenerationCheckpoint,
    MasterDigestPin,
    OperationId,
    UtcTimestamp,
    ValidatedSlideSpecV2,
)
from .composition import AuthoredCompositionPin
from .outline import OutlineAssignmentSet, OutlineInput
from .quality import DeckQualityReport, MontageManifest, SlideGateReport
from .scene import (
    DeckSceneIR,
    ElementId,
    SceneAssetManifestEntry,
    SceneDataManifestEntry,
)
from .visual_plan import SlideVisualPlan


class PublicationFence(StrictCamelModel):
    expected_deck_revision: int = Field(ge=0)
    expected_active_revision_digest: Digest | None = None


class SlideSceneInput(StrictCamelModel):
    """Digest-pinned structured source for one authored Scene slide."""

    outline_item_id: str = Field(pattern=r"^outline:[a-z0-9][a-z0-9._-]*$")
    role_hint: Literal[
        "card", "process", "timeline", "comparison", "metric", "infographic",
        "image", "table", "chart",
    ]
    source_image: dict[str, Any] | None = None
    source_data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _source_matches_role(self) -> "SlideSceneInput":
        if self.role_hint == "image":
            if self.source_image is None or self.source_data is not None:
                raise ValueError("image Scene input requires only sourceImage")
        elif self.role_hint in {"table", "chart"}:
            if self.source_data is None or self.source_image is not None:
                raise ValueError(f"{self.role_hint} Scene input requires only sourceData")
        elif self.source_image is not None or self.source_data is not None:
            raise ValueError("decorative Scene roles do not accept structured sources")
        return self


class GenerateDeckRequest(StrictCamelModel):
    schema_version: Literal["generate-deck-request/v1"] = "generate-deck-request/v1"
    operation_id: OperationId
    deck_id: DeckId
    master: MasterDigestPin
    composition: AuthoredCompositionPin | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    outline: OutlineInput
    slide_scene_inputs: list[SlideSceneInput] = Field(default_factory=list, max_length=500)
    asset_manifest: list[SceneAssetManifestEntry] = Field(default_factory=list, max_length=500)
    data_manifest: list[SceneDataManifestEntry] = Field(default_factory=list, max_length=500)
    quality_policy_version: str = Field(min_length=1, max_length=100)
    publication_fence: PublicationFence

    @model_validator(mode="after")
    def _composition_uses_requested_master(self) -> "GenerateDeckRequest":
        if (
            self.composition is not None
            and self.composition.master_digest != self.master.master_digest
        ):
            raise ValueError("composition uses a different requested master")
        item_ids = {item.item_id for item in self.outline.items}
        input_ids = [item.outline_item_id for item in self.slide_scene_inputs]
        if len(set(input_ids)) != len(input_ids):
            raise ValueError("slideSceneInputs outlineItemIds must be unique")
        if not set(input_ids).issubset(item_ids):
            raise ValueError("slideSceneInputs must reference outline items")
        return self


class RegenerateBodyCommand(StrictCamelModel):
    schema_version: Literal["regenerate-body-command/v1"] = (
        "regenerate-body-command/v1"
    )
    scope: Literal["bodyOnly"] = "bodyOnly"
    operation_id: OperationId
    deck_id: DeckId
    slide_id: SlideId
    expected_slide_revision: int = Field(ge=1)
    expected_slide_spec_digest: Digest
    master: MasterDigestPin
    source_digest: Digest
    body: str = Field(min_length=1, max_length=50000)
    publication_fence: PublicationFence


class SceneMutationCommand(StrictCamelModel):
    schema_version: Literal["scene-mutation-command/v1"] = (
        "scene-mutation-command/v1"
    )
    operation_id: OperationId
    deck_id: DeckId
    slide_id: SlideId
    element_id: ElementId
    kind: Literal["text.replace", "geometry.update", "asset.set", "data.update"]
    payload: dict[str, Any]
    expected_slide_revision: int = Field(ge=1)
    expected_slide_digest: Digest
    publication_fence: PublicationFence

    @model_validator(mode="after")
    def _payload_is_bounded_and_matches_mutation(self) -> "SceneMutationCommand":
        if not self.payload:
            raise ValueError("scene mutation payload must not be empty")
        if len(self.payload) > 12:
            raise ValueError("scene mutation payload is too large")
        required = {
            "text.replace": {"text"},
            "geometry.update": set(),
            "asset.set": {"assetId", "assetDigest", "altText", "sizeBytes"},
            "data.update": {"dataId", "dataDigest", "dataPayload"},
        }[self.kind]
        if not required.issubset(self.payload):
            raise ValueError(f"{self.kind} payload is missing required fields")
        return self


class ResumeGenerationRequest(StrictCamelModel):
    schema_version: Literal["resume-generation-request/v1"] = (
        "resume-generation-request/v1"
    )
    operation_id: OperationId
    expected_checkpoint_digest: Digest
    expected_master_digest: Digest
    checkpoint: GenerationCheckpoint

    @model_validator(mode="after")
    def _fail_closed_on_changed_checkpoint_or_master(self) -> "ResumeGenerationRequest":
        if self.operation_id != self.checkpoint.operation_id:
            raise ValueError("operationId does not match checkpoint")
        if self.expected_checkpoint_digest != self.checkpoint.checkpoint_digest:
            raise ValueError("stale generation checkpoint")
        if self.expected_master_digest != self.checkpoint.master.master_digest:
            raise ValueError("stale published master")
        return self


class RollbackDeckRequest(StrictCamelModel):
    schema_version: Literal["rollback-deck-request/v1"] = (
        "rollback-deck-request/v1"
    )
    operation_id: OperationId
    deck_id: DeckId
    target_revision: int = Field(ge=1)
    target_revision_digest: Digest
    master_digest: Digest
    publication_fence: PublicationFence
    reason: str = Field(min_length=1, max_length=2000)


class DeckGenerationSnapshot(StrictCamelModel):
    schema_version: Literal["deck-generation-snapshot/v1"] = (
        "deck-generation-snapshot/v1"
    )
    deck_id: DeckId
    revision: int = Field(ge=1)
    digest: Digest
    parent_revision_digest: Digest | None = None
    master: MasterDigestPin
    composition: AuthoredCompositionPin | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_digest: Digest
    scene: DeckSceneIR | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    slides: list[ValidatedSlideSpecV2] = Field(min_length=1, max_length=500)
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def _all_slides_use_pinned_inputs(self) -> "DeckGenerationSnapshot":
        if (
            self.composition is not None
            and self.composition.master_digest != self.master.master_digest
        ):
            raise ValueError("deck uses a different composition master")
        ids = [item.slide.slide_id for item in self.slides]
        if len(set(ids)) != len(ids):
            raise ValueError("slide IDs must be unique")
        for item in self.slides:
            checkpoint = item.checkpoint
            if checkpoint.master_digest != self.master.master_digest:
                raise ValueError("slide uses a different master digest")
            if checkpoint.layout_registry_digest != self.master.layout_registry_digest:
                raise ValueError("slide uses a different layout registry digest")
            if checkpoint.font_manifest_digest != self.master.font_manifest_digest:
                raise ValueError("slide uses a different font manifest digest")
            if checkpoint.source_digest != self.source_digest:
                raise ValueError("slide uses a different source digest")
            if checkpoint.composition != self.composition:
                raise ValueError("slide uses a different authored composition")
        if self.scene is not None:
            if (
                self.scene.deck_id != self.deck_id
                or self.scene.revision != self.revision
                or self.scene.source_digest != self.source_digest
            ):
                raise ValueError("deck scene identity differs from generation snapshot")
            if (
                self.composition is not None
                and self.scene.composition_digest != self.composition.digest
            ):
                raise ValueError("deck scene uses a different authored composition")
            scene_by_id = {slide.slide_id: slide for slide in self.scene.slides}
            if set(scene_by_id) != set(ids):
                raise ValueError("deck scene slide IDs differ from compatibility slides")
            for item in self.slides:
                if item.scene is None or item.scene != scene_by_id[item.slide.slide_id]:
                    raise ValueError("deck scene differs from validated slide scene")
        return self


class CandidateBundle(StrictCamelModel):
    lifecycle: Literal["detached"] = "detached"
    parent_deck_digest: Digest
    visual_plan: SlideVisualPlan
    slide: ValidatedSlideSpecV2
    slide_gate: SlideGateReport
    candidate_digest: Digest

    @model_validator(mode="after")
    def _candidate_is_one_slide(self) -> "CandidateBundle":
        if self.visual_plan.slide_id != self.slide.slide.slide_id:
            raise ValueError("visual plan and validated slide IDs differ")
        if self.slide_gate.slide_id != self.slide.slide.slide_id:
            raise ValueError("slide gate and validated slide IDs differ")
        if self.visual_plan.plan_digest != self.slide.checkpoint.visual_plan_digest:
            raise ValueError("visual plan digest was not sealed in checkpoint")
        return self


class GenerationResult(StrictCamelModel):
    schema_version: Literal["generation-result/v1"] = "generation-result/v1"
    operation_id: OperationId
    checkpoint: GenerationCheckpoint
    composition: AuthoredCompositionPin | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    outline_assignments: OutlineAssignmentSet
    deck: DeckGenerationSnapshot
    quality: DeckQualityReport
    montage: MontageManifest
    export_artifact_digest: Digest

    @model_validator(mode="after")
    def _publishable_result_is_consistent(self) -> "GenerationResult":
        if self.checkpoint.stage != "published":
            raise ValueError("generation result checkpoint must be published")
        if self.operation_id != self.checkpoint.operation_id:
            raise ValueError("operationId does not match checkpoint")
        if (
            self.composition != self.checkpoint.composition
            or self.composition != self.deck.composition
        ):
            raise ValueError("generation result uses a different authored composition")
        if not self.quality.passed:
            raise ValueError("failed deck quality cannot be published")
        if self.deck.digest != self.quality.deck_digest:
            raise ValueError("quality report does not identify published deck")
        if self.deck.master.master_digest != self.quality.master_digest:
            raise ValueError("quality report does not identify published master")
        if self.deck.digest != self.montage.deck_digest:
            raise ValueError("montage does not identify published deck")
        if self.deck.scene is not None:
            if self.checkpoint.scene_digest != self.deck.scene.scene_digest:
                raise ValueError("generation checkpoint uses a different deck scene")
            if self.composition is None:
                # Explicit compatibility generation retains a scene but has no
                # authored composition pin. New authored requests always take
                # the stronger branch above.
                pass
            elif self.deck.scene.composition_digest != self.composition.digest:
                raise ValueError("generation result scene composition changed")
        return self


class RegenerationResult(StrictCamelModel):
    schema_version: Literal["regeneration-result/v1"] = "regeneration-result/v1"
    operation_id: OperationId
    candidate: CandidateBundle
    deck: DeckGenerationSnapshot
    quality: DeckQualityReport
    published_by_cas: Literal[True] = True

    @model_validator(mode="after")
    def _published_candidate_passed_all_gates(self) -> "RegenerationResult":
        if not self.candidate.slide_gate.passed or not self.quality.passed:
            raise ValueError("regeneration publication requires slide and deck gates")
        if self.candidate.parent_deck_digest != self.deck.parent_revision_digest:
            raise ValueError("candidate parent does not match published deck parent")
        return self


class RollbackResult(StrictCamelModel):
    schema_version: Literal["rollback-result/v1"] = "rollback-result/v1"
    operation_id: OperationId
    restored_from_revision: int = Field(ge=1)
    deck: DeckGenerationSnapshot
    published_by_cas: Literal[True] = True


class ReorderSlidesCommand(StrictCamelModel):
    schema_version: Literal["reorder-slides-command/v1"] = "reorder-slides-command/v1"
    operation_id: OperationId
    deck_id: DeckId
    ordered_slide_ids: list[SlideId] = Field(min_length=1, max_length=500)
    master_digest: Digest
    publication_fence: PublicationFence

    @model_validator(mode="after")
    def _unique_order(self) -> "ReorderSlidesCommand":
        if len(set(self.ordered_slide_ids)) != len(self.ordered_slide_ids):
            raise ValueError("orderedSlideIds must be unique")
        return self


class DeckMutationResult(StrictCamelModel):
    schema_version: Literal["deck-mutation-result/v1"] = "deck-mutation-result/v1"
    operation_id: OperationId
    mutation: Literal["slides.reorder"]
    deck: DeckGenerationSnapshot
    quality: DeckQualityReport
    published_by_cas: Literal[True] = True


class SceneMutationResult(StrictCamelModel):
    schema_version: Literal["scene-mutation-result/v1"] = (
        "scene-mutation-result/v1"
    )
    operation_id: OperationId
    mutation: Literal["text.replace", "geometry.update", "asset.set", "data.update"]
    slide_id: SlideId
    element_id: ElementId
    slide_digest: Digest
    scene_digest: Digest
    deck: DeckGenerationSnapshot
    quality: DeckQualityReport
    published_by_cas: Literal[True] = True

    @model_validator(mode="after")
    def _scene_identity_matches_published_deck(self) -> "SceneMutationResult":
        if self.deck.scene is None or self.deck.scene.scene_digest != self.scene_digest:
            raise ValueError("scene mutation result uses a different deck scene")
        slide = next(
            (
                item
                for item in self.deck.scene.slides
                if item.slide_id == self.slide_id
            ),
            None,
        )
        if slide is None or slide.slide_digest != self.slide_digest:
            raise ValueError("scene mutation result uses a different slide scene")
        if not any(element.element_id == self.element_id for element in slide.elements):
            raise ValueError("scene mutation result element is missing")
        if not self.quality.passed or self.quality.deck_digest != self.deck.digest:
            raise ValueError("scene mutation result requires passed deck quality")
        return self
