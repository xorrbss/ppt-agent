"""Structured Narrative IR contracts: content graphs and content-integrity receipts."""

from models.authored_vnext.narrative_ir_contracts import (
    ContentGraphV1,
    ContentIntegrityReceiptV2,
    ContentUnitV1,
    NarrativeIRV1,
    PageContentSnapshotV1,
    PreserveSourceInputV1,
)
from .content_graph import (
    seal_content_graph,
    seal_content_integrity_receipt,
    seal_preserve_source_input,
    validate_content_graph,
    validate_content_integrity_receipt,
    validate_content_integrity_receipt_binding,
)

__all__ = [
    "ContentGraphV1",
    "ContentIntegrityReceiptV2",
    "ContentUnitV1",
    "NarrativeIRV1",
    "PageContentSnapshotV1",
    "PreserveSourceInputV1",
    "seal_content_graph",
    "seal_content_integrity_receipt",
    "seal_preserve_source_input",
    "validate_content_graph",
    "validate_content_integrity_receipt",
    "validate_content_integrity_receipt_binding",
]
