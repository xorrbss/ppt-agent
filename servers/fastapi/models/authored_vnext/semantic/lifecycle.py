"""Public CAS contracts for Authored semantic remake and cutover lifecycle."""

from __future__ import annotations

from typing import Literal
import uuid

from pydantic import Field

from models.authored_vnext.master.base import Digest, StrictCamelModel


class AuthoredEmissionCasV1(StrictCamelModel):
    schema_version: Literal["authored-emission-cas/v1"] = "authored-emission-cas/v1"
    expected_presentation_revision: int = Field(ge=1)
    expected_active_generation: int = Field(ge=1)
    expected_manifest_digest: Digest
    # Strict contracts still need to accept the RFC 4122 string representation
    # carried by JSON/HTTP; the resulting model value is always a UUID.
    operation_id: uuid.UUID = Field(strict=False)


class AuthoredOrderedRemakeRequestV1(AuthoredEmissionCasV1):
    schema_version: Literal["authored-ordered-remake-request/v1"] = (
        "authored-ordered-remake-request/v1"
    )
    generation_attempt_id: uuid.UUID = Field(strict=False)
    semantic_retry_limit: Literal[2] = 2


class AuthoredManifestRollbackRequestV1(AuthoredEmissionCasV1):
    schema_version: Literal["authored-manifest-rollback-request/v1"] = (
        "authored-manifest-rollback-request/v1"
    )
    target_manifest_digest: Digest
    generation_attempt_id: uuid.UUID = Field(strict=False)


class AuthoredLegacyCutoverRequestV1(AuthoredEmissionCasV1):
    schema_version: Literal["authored-legacy-cutover-request/v1"] = (
        "authored-legacy-cutover-request/v1"
    )


class AuthoredEmissionLifecycleReceiptV1(StrictCamelModel):
    schema_version: Literal["authored-emission-lifecycle-receipt/v1"] = (
        "authored-emission-lifecycle-receipt/v1"
    )
    action: Literal["orderedRemake", "manifestRollback", "legacyCutover"]
    presentation_id: uuid.UUID
    operation_id: uuid.UUID
    generation_attempt_id: uuid.UUID | None = None
    previous_manifest_digest: Digest
    target_manifest_digest: Digest | None = None
    active_manifest_digest: Digest
    canonical_mode: Literal["legacyHtml", "bundleGraph"]
    presentation_revision: int = Field(ge=1)
    active_generation: int = Field(ge=1)
    ordered_slide_count: int = Field(ge=1, le=100)
    degraded_fidelity_only_count: int = Field(ge=0, le=100)
    bounded_semantic_attempts: int = Field(ge=0, le=200)
    receipt_digest: Digest


__all__ = [
    "AuthoredEmissionCasV1",
    "AuthoredEmissionLifecycleReceiptV1",
    "AuthoredLegacyCutoverRequestV1",
    "AuthoredManifestRollbackRequestV1",
    "AuthoredOrderedRemakeRequestV1",
]
