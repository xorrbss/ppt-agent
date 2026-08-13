"""Read-only authored-only compatibility migration tooling."""

from services.compatibility_migration.census import (
    CensusClassification,
    PresentationCensusEntry,
    classify_presentation,
)
from services.compatibility_migration.inventory import (
    PresentationInventory,
    build_presentation_inventory,
)
from services.compatibility_migration.archive import (
    ArchiveManifestError,
    build_signed_archive_manifest,
    verify_signed_archive_manifest,
)
from services.compatibility_migration.runtime import (
    FROZEN_ALLOWED_OPERATIONS,
    FrozenCompatibilityError,
    authorize_frozen_operation,
    get_frozen_runtime_descriptor,
    require_authored_mutation_evidence,
)
from services.compatibility_migration.upgrade import (
    CompatibilityUpgradeError,
    upgrade_presentation_fork,
)

__all__ = [
    "CensusClassification",
    "ArchiveManifestError",
    "CompatibilityUpgradeError",
    "FROZEN_ALLOWED_OPERATIONS",
    "FrozenCompatibilityError",
    "PresentationCensusEntry",
    "PresentationInventory",
    "build_presentation_inventory",
    "authorize_frozen_operation",
    "build_signed_archive_manifest",
    "classify_presentation",
    "get_frozen_runtime_descriptor",
    "require_authored_mutation_evidence",
    "upgrade_presentation_fork",
    "verify_signed_archive_manifest",
]
