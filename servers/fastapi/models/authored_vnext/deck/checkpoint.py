"""Digest-pinned canonical slide and resumable generation checkpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..master.base import DeckId, Digest, LayoutId, MasterId, SlideId, StrictCamelModel
from ..master.slide import SlideSpecV2
from .composition import AuthoredCompositionPin


OperationId = Annotated[str, Field(pattern=r"^operation:[a-z0-9][a-z0-9._-]*$")]
UtcTimestamp = Annotated[
    str,
    Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"),
]


class MasterDigestPin(StrictCamelModel):
    master_id: MasterId
    master_revision: int = Field(ge=1)
    master_digest: Digest
    layout_registry_digest: Digest
    font_manifest_digest: Digest


class SlideValidationCheckpoint(StrictCamelModel):
    schema_version: Literal["slide-validation-checkpoint/v1"] = (
        "slide-validation-checkpoint/v1"
    )
    master_digest: Digest
    layout_registry_digest: Digest
    font_manifest_digest: Digest
    source_digest: Digest
    composition: AuthoredCompositionPin | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    visual_plan_digest: Digest
    quality_policy_version: str = Field(min_length=1, max_length=100)
    layout_id: LayoutId
    body_slot_id: str = Field(pattern=r"^slot:[a-z0-9][a-z0-9._-]*$")
    validated_at: UtcTimestamp


class ValidatedSlideSpecV2(StrictCamelModel):
    schema_version: Literal["validated-slidespec/v2"] = "validated-slidespec/v2"
    slide: SlideSpecV2
    checkpoint: SlideValidationCheckpoint

    @model_validator(mode="after")
    def _digests_and_layout_are_sealed(self) -> "ValidatedSlideSpecV2":
        if self.slide.master_digest != self.checkpoint.master_digest:
            raise ValueError("slide and checkpoint master digests differ")
        if self.slide.layout_id != self.checkpoint.layout_id:
            raise ValueError("slide and checkpoint layout IDs differ")
        if (
            self.slide.title_fit.font_manifest_digest
            != self.checkpoint.font_manifest_digest
        ):
            raise ValueError("slide and checkpoint font manifest digests differ")
        if self.slide.title_fit.clipped:
            raise ValueError("validated slide title cannot be clipped")
        if (
            self.checkpoint.composition is not None
            and self.checkpoint.composition.master_digest != self.slide.master_digest
        ):
            raise ValueError("slide uses a different composition master digest")
        if self.slide.ownership.locked or not self.slide.ownership.model_writable:
            raise ValueError("validated slide must own only the writable body layer")
        return self


class CompletedSlideCheckpoint(StrictCamelModel):
    slide_id: SlideId
    slide_index: int = Field(ge=0, le=499)
    spec_digest: Digest
    validation_checkpoint_digest: Digest


class GenerationCheckpoint(StrictCamelModel):
    schema_version: Literal["deck-generation-checkpoint/v1"] = (
        "deck-generation-checkpoint/v1"
    )
    operation_id: OperationId
    deck_id: DeckId
    stage: Literal[
        "outlineAssigned",
        "slidesCompiling",
        "slidesValidated",
        "deckValidated",
        "published",
        "rolledBack",
    ]
    master: MasterDigestPin
    composition: AuthoredCompositionPin | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_digest: Digest
    quality_policy_version: str = Field(min_length=1, max_length=100)
    total_slides: int = Field(ge=1, le=500)
    completed_slides: list[CompletedSlideCheckpoint] = Field(
        default_factory=list, max_length=500
    )
    attempt: int = Field(default=1, ge=1, le=100)
    checkpoint_digest: Digest
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def _completed_slides_are_consistent(self) -> "GenerationCheckpoint":
        if (
            self.composition is not None
            and self.composition.master_digest != self.master.master_digest
        ):
            raise ValueError("generation checkpoint uses a different composition master")
        indexes = [slide.slide_index for slide in self.completed_slides]
        ids = [slide.slide_id for slide in self.completed_slides]
        if len(set(indexes)) != len(indexes) or len(set(ids)) != len(ids):
            raise ValueError("completed slide IDs and indexes must be unique")
        if any(index >= self.total_slides for index in indexes):
            raise ValueError("completed slide index exceeds totalSlides")
        if self.stage in {"slidesValidated", "deckValidated", "published"} and len(
            self.completed_slides
        ) != self.total_slides:
            raise ValueError("completed stage requires every slide checkpoint")
        return self
