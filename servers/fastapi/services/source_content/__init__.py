"""Fail-closed source-content restyle admission and immutable manifests."""

from services.source_content.contracts import (
    SourceContentAnalysisReceipt,
    SourceContentManifest,
    SourceContentManifestPin,
)
from services.source_content.manifest_store import (
    admit_source_content_manifest_pin,
    analyze_reference_snapshot,
)
from services.source_content.outline import (
    SourceDocumentOutline,
    SourceDocumentOutlineBuilder,
    SourceDocumentOutlineSlide,
    build_source_document_outline,
)
from services.source_content.allocator import PageLockedTemplateCompositionPlanner

__all__ = [
    "SourceContentAnalysisReceipt",
    "SourceContentManifest",
    "SourceContentManifestPin",
    "admit_source_content_manifest_pin",
    "analyze_reference_snapshot",
    "SourceDocumentOutline",
    "SourceDocumentOutlineBuilder",
    "SourceDocumentOutlineSlide",
    "build_source_document_outline",
    "PageLockedTemplateCompositionPlanner",
]
