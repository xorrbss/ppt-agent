"""Structured Narrative IR and whole-operation source-native orchestration."""

from .contracts import (
    FrameSearchReceiptV1,
    FrameSelectionReceiptV1,
    NarrativeIRV1,
    RouteResolutionReceiptV1,
)
from .orchestration import run_narrative_ir_operation

__all__ = [
    "FrameSearchReceiptV1",
    "FrameSelectionReceiptV1",
    "NarrativeIRV1",
    "RouteResolutionReceiptV1",
    "run_narrative_ir_operation",
]
