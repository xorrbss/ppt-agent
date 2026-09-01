"""Structured Narrative IR and whole-operation source-native orchestration."""

from .contracts import (
    ContentGraphV1,
    ContentIntegrityReceiptV1,
    ContentUnitV1,
    FrameSearchReceiptV1,
    FrameSelectionReceiptV1,
    NarrativeIRV1,
    PageContentSnapshotV1,
    PreserveSourceInputV1,
    RouteResolutionReceiptV1,
)
from .content_graph import (
    seal_content_graph,
    seal_content_integrity_receipt,
    seal_preserve_source_input,
    validate_content_graph,
    validate_content_integrity_receipt,
)
from .orchestration import run_narrative_ir_operation

__all__ = [
    "ContentGraphV1",
    "ContentIntegrityReceiptV1",
    "ContentUnitV1",
    "FrameSearchReceiptV1",
    "FrameSelectionReceiptV1",
    "NarrativeIRV1",
    "PageContentSnapshotV1",
    "PreserveSourceInputV1",
    "RouteResolutionReceiptV1",
    "seal_content_graph",
    "seal_content_integrity_receipt",
    "seal_preserve_source_input",
    "run_narrative_ir_operation",
    "validate_content_graph",
    "validate_content_integrity_receipt",
]
