"""Deck-wide visual intent for the Authored HTML authoring path.

This is prompt-planning metadata, not a second renderer or a claim that arbitrary
HTML is natively editable.  Fidelity keeps the complete authored composition;
Hybrid may promote only the explicitly listed semantic candidates and keeps the
declared CSS remainder as a raster backplate.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


VISUAL_PLAN_SCHEMA_VERSION = "authored-visual-plan/v1"

StoryKind = Literal[
    "cover",
    "closing",
    "section",
    "agenda",
    "editorial",
    "pillars",
    "metric",
    "process",
    "architecture",
    "image",
    "dataTable",
    "dataChart",
]
HybridNativeCandidate = Literal["text", "image", "shape", "table", "chart"]


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthoredSlideVisualIntent(_Spec):
    """One slide's frozen narrative and silhouette assignment."""

    slide_index: int
    role: str
    story_kind: StoryKind
    silhouette: str
    hybrid_native_candidates: tuple[HybridNativeCandidate, ...] = ()
    raster_fallback_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_metadata(self) -> "AuthoredSlideVisualIntent":
        if self.slide_index < 0:
            raise ValueError("visual intent slide index must not be negative")
        if not self.role.strip() or not self.silhouette.strip():
            raise ValueError("visual intent role and silhouette must be non-empty")
        if len(set(self.hybrid_native_candidates)) != len(
            self.hybrid_native_candidates
        ):
            raise ValueError("hybrid native candidates must be unique")
        if len(set(self.raster_fallback_reasons)) != len(
            self.raster_fallback_reasons
        ):
            raise ValueError("raster fallback reasons must be unique")
        return self


class AuthoredVisualPlan(_Spec):
    """The visual decisions shared by independently authored slides."""

    schema_version: Literal["authored-visual-plan/v1"] = VISUAL_PLAN_SCHEMA_VERSION
    slides: tuple[AuthoredSlideVisualIntent, ...]

    @model_validator(mode="after")
    def _indices_and_silhouettes_are_stable(self) -> "AuthoredVisualPlan":
        for index, slide in enumerate(self.slides):
            if slide.slide_index != index:
                raise ValueError("visual plan slide indices must be contiguous")
            if index and slide.silhouette == self.slides[index - 1].silhouette:
                raise ValueError("adjacent authored slides must not repeat a silhouette")
        return self

    def at(self, index: int) -> AuthoredSlideVisualIntent:
        return self.slides[index]


__all__ = [
    "AuthoredSlideVisualIntent",
    "AuthoredVisualPlan",
    "HybridNativeCandidate",
    "StoryKind",
    "VISUAL_PLAN_SCHEMA_VERSION",
]
