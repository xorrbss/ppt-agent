"""Timing receipt models for a raster reconstruction analysis run."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from services.source_content.raster_contracts_base import RasterContract

TimingStage = Literal[
    "ocr", "rereads", "number_units", "structure_calls", "tables", "transcription", "vision_other", "plan", "other",
]


class RasterVisionCallTiming(RasterContract):
    name: str | None = None
    wait_seconds: float = Field(ge=0)  # waiting for the client's concurrency gate
    latency_seconds: float = Field(ge=0)


class RasterPageTimings(RasterContract):
    page_number: int = Field(ge=1)
    wall_seconds: float = Field(ge=0)
    # Exclusive seconds per stage; "other" is the page wall time outside every stage.
    stages: dict[TimingStage, float]
    vision_calls: tuple[RasterVisionCallTiming, ...] = ()


class RasterStageTimings(RasterContract):
    """Measurement of the run that produced the analysis; never part of its digest."""

    clock: Literal["monotonic"] = "monotonic"  # time.perf_counter seconds
    wall_seconds: float = Field(ge=0)
    pages: tuple[RasterPageTimings, ...] = ()
    stage_totals: dict[TimingStage, float] = Field(default_factory=dict)
    stage_page_max: dict[TimingStage, float] = Field(default_factory=dict)
    vision_call_count: int = Field(default=0, ge=0)
    vision_wait_seconds: float = Field(default=0, ge=0)
    vision_latency_seconds: float = Field(default=0, ge=0)
    unpaged_vision_calls: tuple[RasterVisionCallTiming, ...] = ()
