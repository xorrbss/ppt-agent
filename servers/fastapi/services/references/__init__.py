"""Immutable reference-source normalization and generation admission."""

from services.references.contracts import (
    NormalizedReference,
    ReferenceProviderError,
    ReferenceSnapshotView,
)
from services.references.providers import (
    CommunityReferenceProvider,
    ReferenceSourceProviderRegistry,
    TemplateReferenceProvider,
    UploadedDocumentProvider,
    default_reference_source_registry,
)
from services.references.service import (
    admit_reference_snapshot,
    admit_reference_snapshots,
    canonicalize_legacy_document_paths,
    claim_reference_snapshots,
    cleanup_expired_reference_snapshots,
    create_reference_snapshot,
)

__all__ = [
    "CommunityReferenceProvider",
    "NormalizedReference",
    "ReferenceProviderError",
    "ReferenceSnapshotView",
    "ReferenceSourceProviderRegistry",
    "TemplateReferenceProvider",
    "UploadedDocumentProvider",
    "admit_reference_snapshot",
    "admit_reference_snapshots",
    "canonicalize_legacy_document_paths",
    "claim_reference_snapshots",
    "cleanup_expired_reference_snapshots",
    "create_reference_snapshot",
    "default_reference_source_registry",
]
