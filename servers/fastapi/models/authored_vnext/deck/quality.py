"""Slide/deck quality evidence and deterministic montage contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..master.base import DeckId, Digest, SlideId, StrictCamelModel


class GateIssue(StrictCamelModel):
    code: str = Field(pattern=r"^authored_vnext_[a-z0-9_]+$")
    slide_id: SlideId | None = None
    path: str = Field(min_length=1, max_length=1000)
    message: str = Field(min_length=1, max_length=4000)


class SlideGateReport(StrictCamelModel):
    slide_id: SlideId
    candidate_digest: Digest
    dom_passed: bool
    ocr_passed: bool
    title_fit_passed: bool
    furniture_body_duplicates: int = Field(ge=0)
    issues: list[GateIssue] = Field(default_factory=list, max_length=100)
    passed: bool

    @model_validator(mode="after")
    def _verdict_is_derived(self) -> "SlideGateReport":
        expected = (
            self.dom_passed
            and self.ocr_passed
            and self.title_fit_passed
            and self.furniture_body_duplicates == 0
            and not self.issues
        )
        if self.passed != expected:
            raise ValueError("slide gate passed must equal its evidence verdict")
        return self


class ConsistencyCheck(StrictCamelModel):
    passed: bool
    expected_digest: Digest | None = None
    observed_digests: list[Digest] = Field(default_factory=list, max_length=500)
    mismatched_slide_ids: list[SlideId] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _verdict_matches_mismatches(self) -> "ConsistencyCheck":
        if self.passed == bool(self.mismatched_slide_ids):
            raise ValueError("consistency verdict must match mismatchedSlideIds")
        return self


class DeckQualityReport(StrictCamelModel):
    schema_version: Literal["deck-quality-report/v1"] = "deck-quality-report/v1"
    deck_id: DeckId
    deck_digest: Digest
    master_digest: Digest
    fonts: ConsistencyCheck
    colors: ConsistencyCheck
    frame: ConsistencyCheck
    page_numbers: ConsistencyCheck
    layouts: ConsistencyCheck
    slots: ConsistencyCheck
    sources: ConsistencyCheck
    slide_gates: list[SlideGateReport] = Field(min_length=1, max_length=500)
    passed: bool
    issues: list[GateIssue] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def _deck_verdict_is_derived(self) -> "DeckQualityReport":
        checks = (
            self.fonts,
            self.colors,
            self.frame,
            self.page_numbers,
            self.layouts,
            self.slots,
            self.sources,
        )
        expected = all(check.passed for check in checks) and all(
            gate.passed for gate in self.slide_gates
        ) and not self.issues
        if self.passed != expected:
            raise ValueError("deck gate passed must equal its evidence verdict")
        return self


class MontageTile(StrictCamelModel):
    slide_id: SlideId
    slide_index: int = Field(ge=0, le=89)
    png_digest: Digest


class MontageManifest(StrictCamelModel):
    schema_version: Literal["deck-montage-manifest/v1"] = (
        "deck-montage-manifest/v1"
    )
    deck_id: DeckId
    deck_digest: Digest
    deck_size: Literal[10, 90]
    columns: int = Field(ge=1, le=10)
    tiles: list[MontageTile] = Field(min_length=10, max_length=90)
    montage_digest: Digest

    @model_validator(mode="after")
    def _all_slides_are_in_order(self) -> "MontageManifest":
        if len(self.tiles) != self.deck_size:
            raise ValueError("10/90 montage must contain every slide")
        if [tile.slide_index for tile in self.tiles] != list(range(self.deck_size)):
            raise ValueError("montage tiles must be ordered and contiguous")
        if len({tile.slide_id for tile in self.tiles}) != self.deck_size:
            raise ValueError("montage slide IDs must be unique")
        return self
