"""Generated from ooxml-package.schema.json; do not edit."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_SCHEMA_SHA256 = 'sha256:41573e339b620dcba47bb1589f3d527001de4e23d298696b3a0f10f21742ab0a'


class OoxmlPackageContractModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class PackageInspection(OoxmlPackageContractModel):
    classification: Literal["PRESERVE_EXACT", "PATCH_SLICE", "GENERATED", "QUARANTINED"] = Field(alias='classification')
    part_count: int = Field(alias='partCount', ge=1)
    slide_count: int = Field(alias='slideCount', ge=0)
    master_count: int = Field(alias='masterCount', ge=0)
    layout_count: int = Field(alias='layoutCount', ge=0)
    external_relationship_count: int = Field(alias='externalRelationshipCount', ge=0)
    external_relationship_warnings: list[str] = Field(alias='externalRelationshipWarnings', max_length=4096)
    package_digest: str = Field(alias='packageDigest', pattern='^sha256:[0-9a-f]{64}$')
    relationship_digest: str = Field(alias='relationshipDigest', pattern='^sha256:[0-9a-f]{64}$')
    closure_digest: str = Field(alias='closureDigest', pattern='^sha256:[0-9a-f]{64}$')
    macro_detected: bool = Field(alias='macroDetected')
    quarantine_reasons: list[str] = Field(alias='quarantineReasons', max_length=4096)


class PackageVersionReceipt(OoxmlPackageContractModel):
    schema_version: Literal["authored-vnext-ooxml-package/v1"] = Field(alias='schemaVersion')
    package_id: str = Field(alias='packageId', pattern='^package:[a-f0-9]{32}$')
    source_package_id: str | None = Field(alias='sourcePackageId')
    owner_id: str = Field(alias='ownerId', min_length=1, max_length=200)
    state: Literal["imported", "quarantined", "editable", "archived"] = Field(alias='state')
    mode: Literal["PRESERVE_EXACT", "CORPORATE_NORMALIZE"] = Field(alias='mode')
    origin: Literal["IMPORTED", "GENERATED"] = Field(alias='origin')
    revision: int = Field(alias='revision', ge=1)
    artifact_digest: str = Field(alias='artifactDigest', pattern='^sha256:[0-9a-f]{64}$')
    archive_byte_length: int = Field(alias='archiveByteLength', ge=1)
    inspection: PackageInspection = Field(alias='inspection')
    created_at: str = Field(alias='createdAt')


class ImportPackageReceipt(OoxmlPackageContractModel):
    schema_version: Literal["authored-vnext-ooxml-import/v1"] = Field(alias='schemaVersion')
    replayed: bool = Field(alias='replayed')
    editable: bool = Field(alias='editable')
    source_archived: Literal[True] = Field(alias='sourceArchived')
    package: PackageVersionReceipt = Field(alias='package')


class EditableForkRequest(OoxmlPackageContractModel):
    schema_version: Literal["authored-vnext-ooxml-fork-command/v1"] = Field(alias='schemaVersion')
    mode: Literal["PRESERVE_EXACT", "CORPORATE_NORMALIZE"] = Field(alias='mode')
    expected_revision: int = Field(alias='expectedRevision', ge=1)
    expected_artifact_digest: str = Field(alias='expectedArtifactDigest', pattern='^sha256:[0-9a-f]{64}$')
    idempotency_key: str = Field(alias='idempotencyKey', min_length=1, max_length=200)
    expected_account_generation: int = Field(alias='expectedAccountGeneration', ge=0)
    master_contract: dict[str, Any] | None = Field(default=None, alias='masterContract')
    slides: list[dict[str, Any]] | None = Field(default=None, alias='slides', max_length=500)


class TextSelector(OoxmlPackageContractModel):
    kind: Literal["placeholder", "element"] = Field(alias='kind')
    idx: int | None = Field(default=None, alias='idx', ge=0)
    role: str | None = Field(default=None, alias='role', min_length=1, max_length=128)
    id: int | str | None = Field(default=None, alias='id')
    occurrence: int | None = Field(default=None, alias='occurrence', ge=0)


class PackageOperationCommand(OoxmlPackageContractModel):
    schema_version: Literal["authored-vnext-ooxml-operation-command/v1"] = Field(alias='schemaVersion')
    operation: Literal["NOOP_SAVE", "PATCH_TEXT_SLICE", "CREATE_MASTER", "UPDATE_MASTER", "DELETE_MASTER", "CREATE_LAYOUT", "UPDATE_LAYOUT", "DELETE_LAYOUT", "SAVE_EXPORT", "ARCHIVE"] = Field(alias='operation')
    expected_revision: int = Field(alias='expectedRevision', ge=1)
    expected_artifact_digest: str = Field(alias='expectedArtifactDigest', pattern='^sha256:[0-9a-f]{64}$')
    idempotency_key: str = Field(alias='idempotencyKey', min_length=1, max_length=200)
    expected_account_generation: int = Field(alias='expectedAccountGeneration', ge=0)
    part_name: str | None = Field(default=None, alias='partName', max_length=512, pattern='^ppt/(slides|slideLayouts|slideMasters)/[A-Za-z0-9._/-]+\\.xml$')
    selector: TextSelector | None = Field(default=None, alias='selector')
    expected_text: str | None = Field(default=None, alias='expectedText', max_length=100000)
    replacement_text: str | None = Field(default=None, alias='replacementText', max_length=100000)
    master_contract: dict[str, Any] | None = Field(default=None, alias='masterContract')
    slides: list[dict[str, Any]] | None = Field(default=None, alias='slides', max_length=500)


class PackageOperationReceipt(OoxmlPackageContractModel):
    schema_version: Literal["authored-vnext-ooxml-operation/v1"] = Field(alias='schemaVersion')
    operation_id: str = Field(alias='operationId', pattern='^operation:[a-f0-9]{32}$')
    package_id: str = Field(alias='packageId', pattern='^package:[a-f0-9]{32}$')
    operation: Literal["NOOP_SAVE", "PATCH_TEXT_SLICE", "CREATE_MASTER", "UPDATE_MASTER", "DELETE_MASTER", "CREATE_LAYOUT", "UPDATE_LAYOUT", "DELETE_LAYOUT", "SAVE_EXPORT", "ARCHIVE"] = Field(alias='operation')
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"] = Field(alias='state')
    attempt_count: int = Field(alias='attemptCount', ge=0)
    fencing_token: int = Field(alias='fencingToken', ge=1)
    resumable: bool = Field(alias='resumable')
    replayed: bool = Field(alias='replayed')
    expected_revision: int = Field(alias='expectedRevision', ge=1)
    expected_artifact_digest: str = Field(alias='expectedArtifactDigest', pattern='^sha256:[0-9a-f]{64}$')
    result_revision: int | None = Field(alias='resultRevision')
    result_artifact_digest: str | None = Field(alias='resultArtifactDigest')
    changed_parts: list[str] = Field(alias='changedParts', max_length=8192)
    preserved_exact_part_count: int = Field(alias='preservedExactPartCount', ge=0)
    silent_loss_count: int = Field(alias='silentLossCount', ge=0)
    error_code: str | None = Field(alias='errorCode')
    created_at: str = Field(alias='createdAt')
    updated_at: str = Field(alias='updatedAt')


class PackageReceiptReadBack(OoxmlPackageContractModel):
    schema_version: Literal["authored-vnext-ooxml-readback/v1"] = Field(alias='schemaVersion')
    package: PackageVersionReceipt = Field(alias='package')
    source_artifact_digest: str = Field(alias='sourceArtifactDigest', pattern='^sha256:[0-9a-f]{64}$')
    source_archive_retained: Literal[True] = Field(alias='sourceArchiveRetained')
    unsupported_parts_passthrough: bool = Field(alias='unsupportedPartsPassthrough')
    untouched_parts_verified: bool = Field(alias='untouchedPartsVerified')
    preserved_exact_part_count: int = Field(alias='preservedExactPartCount', ge=0)
    patch_slice_part_count: int = Field(alias='patchSlicePartCount', ge=0)
    generated_part_count: int = Field(alias='generatedPartCount', ge=0)
    silent_loss_count: int = Field(alias='silentLossCount', ge=0)
    changed_parts: list[str] = Field(alias='changedParts', max_length=8192)


class PackageRollbackRequest(OoxmlPackageContractModel):
    schema_version: Literal["authored-vnext-ooxml-rollback-command/v1"] = Field(alias='schemaVersion')
    target_revision: int = Field(alias='targetRevision', ge=1)
    expected_revision: int = Field(alias='expectedRevision', ge=1)
    expected_artifact_digest: str = Field(alias='expectedArtifactDigest', pattern='^sha256:[0-9a-f]{64}$')
    idempotency_key: str = Field(alias='idempotencyKey', min_length=1, max_length=200)
    expected_account_generation: int = Field(alias='expectedAccountGeneration', ge=0)


__all__ = ['PackageInspection', 'PackageVersionReceipt', 'ImportPackageReceipt', 'EditableForkRequest', 'TextSelector', 'PackageOperationCommand', 'PackageOperationReceipt', 'PackageReceiptReadBack', 'PackageRollbackRequest']
