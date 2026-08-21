"""Compatibility contract for the authored-vNext creation boundary.

This contract is intentionally about admission, not migration.  Existing
presentations keep their persisted driver and remain readable/exportable by
the compatibility runtime; only new presentation creation is constrained.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from fastapi import HTTPException


AUTHORED_VNEXT_CONTRACT_VERSION: Final = 1
AUTHORED_VNEXT_DRIVER: Final = "authored"
TEMPLATE_V2_NEW_GENERATION_DISABLED: Final = "template_v2_new_generation_disabled"
# Preserve the previously published legacy admission code for callers that
# already classify this 409 as a non-retryable creation boundary.
LEGACY_NEW_GENERATION_DISABLED: Final = "legacy_generation_admission_closed"


@dataclass(frozen=True, slots=True)
class AuthoredVNextPromotionReadiness:
    """Server-owned compatibility state for the frozen authored-vNext path.

    Authored is the only user-facing generation/design engine.  The vNext name
    remains in this compatibility DTO because existing clients already consume
    it, but independent vNext generation is not a promotion target.
    """

    contract_version: int
    product_name: str
    canonical_driver: str
    authored_only_user_engine: bool
    canonical_authored_document_required: bool
    vnext_independent_generation: bool
    fidelity_default_export: bool
    hybrid_partial_edit_only: bool
    scene_ir_required: bool
    compatibility_freeze: bool
    legacy_open_preview_export: bool
    template_v2_new_generation: bool
    fail_closed: bool
    census_complete: bool
    archive_ready: bool
    active_jobs_drained: bool
    checkpoint_replay_verified: bool
    owner_isolation_verified: bool
    checksum_verified: bool
    relative_visual_quality_verified: bool = False

    @property
    def ready(self) -> bool:
        return (
            self.contract_version == AUTHORED_VNEXT_CONTRACT_VERSION
            and self.canonical_driver == AUTHORED_VNEXT_DRIVER
            and self.authored_only_user_engine
            and self.canonical_authored_document_required
            and not self.vnext_independent_generation
            and self.fidelity_default_export
            and self.hybrid_partial_edit_only
            and self.compatibility_freeze
            and self.legacy_open_preview_export
            and not self.template_v2_new_generation
            and self.fail_closed
            and self.census_complete
            and self.archive_ready
            and self.active_jobs_drained
            and self.checkpoint_replay_verified
            and self.owner_isolation_verified
            and self.checksum_verified
            and self.relative_visual_quality_verified
        )

    @property
    def creation_ready(self) -> bool:
        """New authored-vNext creation is independent of final name promotion."""

        return (
            self.contract_version == AUTHORED_VNEXT_CONTRACT_VERSION
            and self.canonical_driver == AUTHORED_VNEXT_DRIVER
            and self.authored_only_user_engine
            and self.canonical_authored_document_required
            and not self.vnext_independent_generation
            and self.fidelity_default_export
            and self.hybrid_partial_edit_only
            and self.compatibility_freeze
            and self.legacy_open_preview_export
            and not self.template_v2_new_generation
            and self.fail_closed
        )

    @property
    def physical_deletion(self) -> bool:
        """Physical removal stays disabled until a separately authorized gate."""

        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "product_name": self.product_name,
            "canonical_driver": self.canonical_driver,
            "authored_only_user_engine": self.authored_only_user_engine,
            "canonical_authored_document_required": self.canonical_authored_document_required,
            "vnext_independent_generation": self.vnext_independent_generation,
            "fidelity_default_export": self.fidelity_default_export,
            "hybrid_partial_edit_only": self.hybrid_partial_edit_only,
            "scene_ir_required": self.scene_ir_required,
            "compatibility_freeze": self.compatibility_freeze,
            "legacy_open_preview_export": self.legacy_open_preview_export,
            "template_v2_new_generation": self.template_v2_new_generation,
            "fail_closed": self.fail_closed,
            "census_complete": self.census_complete,
            "archive_ready": self.archive_ready,
            "active_jobs_drained": self.active_jobs_drained,
            "checkpoint_replay_verified": self.checkpoint_replay_verified,
            "owner_isolation_verified": self.owner_isolation_verified,
            "checksum_verified": self.checksum_verified,
            "relative_visual_quality_verified": self.relative_visual_quality_verified,
            "physical_deletion": self.physical_deletion,
            "creation_ready": self.creation_ready,
            "ready": self.ready,
        }


def authored_vnext_promotion_readiness() -> AuthoredVNextPromotionReadiness:
    """Return the immutable, server-issued authored-vNext readiness contract."""

    return AuthoredVNextPromotionReadiness(
        contract_version=AUTHORED_VNEXT_CONTRACT_VERSION,
        product_name="Authored",
        canonical_driver=AUTHORED_VNEXT_DRIVER,
        authored_only_user_engine=True,
        canonical_authored_document_required=True,
        vnext_independent_generation=False,
        fidelity_default_export=True,
        hybrid_partial_edit_only=True,
        # Scene documents remain readable through the compatibility runtime,
        # but new Authored creation is no longer admitted on the condition that
        # an independent heuristic Scene compiler must keep expanding.
        scene_ir_required=False,
        compatibility_freeze=True,
        legacy_open_preview_export=True,
        template_v2_new_generation=False,
        fail_closed=True,
        # Compatibility documents remain frozen in place.  Promotion cannot be
        # inferred from code landing: these gates require separate durable
        # census/archive/job/checkpoint/isolation/checksum receipts.
        census_complete=False,
        archive_ready=False,
        active_jobs_drained=False,
        checkpoint_replay_verified=False,
        owner_isolation_verified=False,
        checksum_verified=False,
        relative_visual_quality_verified=False,
    )


def _requested_driver(request: object) -> str:
    strategy = getattr(request, "strategy", None)
    template = str(getattr(request, "template", "") or "").strip().lower()
    if strategy is not None:
        if strategy == "template_v2":
            return "template_v2"
        if strategy == AUTHORED_VNEXT_DRIVER:
            return AUTHORED_VNEXT_DRIVER
        return "legacy"
    if template == AUTHORED_VNEXT_DRIVER:
        return AUTHORED_VNEXT_DRIVER
    return "legacy"


def require_authored_vnext_creation_admission(request: object) -> None:
    """Reject legacy/template-v2 creation without touching durable state."""

    readiness = authored_vnext_promotion_readiness()
    driver = _requested_driver(request)
    if driver == readiness.canonical_driver and readiness.creation_ready:
        return

    code = (
        TEMPLATE_V2_NEW_GENERATION_DISABLED
        if driver == "template_v2"
        else LEGACY_NEW_GENERATION_DISABLED
    )
    raise HTTPException(
        status_code=409,
        detail={
            "code": code,
            "requested_driver": driver,
            "required_driver": readiness.canonical_driver,
            "compatibility": {
                "contract_version": readiness.contract_version,
                "legacy_open_preview_export": readiness.legacy_open_preview_export,
                "creation_ready": readiness.creation_ready,
                "promotion_ready": readiness.ready,
                "physical_deletion": readiness.physical_deletion,
            },
            "upgrade_path": "compatibility.upgrade-fork",
        },
    )


__all__ = [
    "AUTHORED_VNEXT_CONTRACT_VERSION",
    "AUTHORED_VNEXT_DRIVER",
    "LEGACY_NEW_GENERATION_DISABLED",
    "TEMPLATE_V2_NEW_GENERATION_DISABLED",
    "AuthoredVNextPromotionReadiness",
    "authored_vnext_promotion_readiness",
    "require_authored_vnext_creation_admission",
]
