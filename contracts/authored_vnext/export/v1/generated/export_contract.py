"""Generated from export-contract.schema.json; do not edit."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_SCHEMA_SHA256 = 'sha256:fb34dcea968cfea4485e470eb482601d3016a4b43a9118cd8affc2249828bf4e'


class ExportContractModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class OwnerScope(ExportContractModel):
    tenant_id: str = Field(alias='tenantId', min_length=1, max_length=200)
    owner_id: str = Field(alias='ownerId', min_length=1, max_length=200)


class SlideSpecDigest(ExportContractModel):
    slide_id: str = Field(alias='slideId', pattern='^slide:[a-z0-9][a-z0-9._-]*$')
    revision: int = Field(alias='revision', ge=1)
    digest: str = Field(alias='digest', pattern='^sha256:[0-9a-f]{64}$')


class AssetDigest(ExportContractModel):
    asset_id: str = Field(alias='assetId', pattern='^asset:[a-z0-9][a-z0-9._-]*$')
    digest: str = Field(alias='digest', pattern='^sha256:[0-9a-f]{64}$')


class QualityIssue(ExportContractModel):
    code: str = Field(alias='code', min_length=1, max_length=120)
    path: str = Field(alias='path', min_length=1, max_length=500)
    message: str = Field(alias='message', min_length=1, max_length=2000)


class QualityDecision(ExportContractModel):
    decision: Literal["approved", "rejected"] = Field(alias='decision')
    decision_digest: str = Field(alias='decisionDigest', pattern='^sha256:[0-9a-f]{64}$')
    checked_at: str = Field(alias='checkedAt')
    issues: list[QualityIssue] = Field(alias='issues', max_length=1000)


class ArtifactReceipt(ExportContractModel):
    schema_version: Literal["authored-vnext-artifact-receipt/v1"] = Field(alias='schemaVersion')
    artifact_id: str = Field(alias='artifactId', pattern='^artifact:[a-z0-9][a-z0-9._-]*$')
    artifact_revision: int = Field(alias='artifactRevision', ge=1)
    sha256: str = Field(alias='sha256', pattern='^sha256:[0-9a-f]{64}$')
    byte_length: int = Field(alias='byteLength', ge=1)
    media_type: Literal["application/vnd.openxmlformats-officedocument.presentationml.presentation"] = Field(alias='mediaType')
    created_at: str = Field(alias='createdAt')
    download_url: str = Field(alias='downloadUrl', pattern='^/api/v1/ppt/authored-vnext/export/artifacts/[A-Za-z0-9._~%-]+/download$')
    package_readback_digest: str = Field(alias='packageReadbackDigest', pattern='^sha256:[0-9a-f]{64}$')
    slide_count: int = Field(alias='slideCount', ge=1, le=1000)
    source_deck_digest: str = Field(alias='sourceDeckDigest', pattern='^sha256:[0-9a-f]{64}$')
    source_master_digest: str = Field(alias='sourceMasterDigest', pattern='^sha256:[0-9a-f]{64}$')
    source_spec_manifest_digest: str = Field(alias='sourceSpecManifestDigest', pattern='^sha256:[0-9a-f]{64}$')
    source_asset_manifest_digest: str = Field(alias='sourceAssetManifestDigest', pattern='^sha256:[0-9a-f]{64}$')
    source_font_digest: str = Field(alias='sourceFontDigest', pattern='^sha256:[0-9a-f]{64}$')
    source_package_digest: str | None = Field(default=None, alias='sourcePackageDigest')
    quality_decision: QualityDecision = Field(alias='qualityDecision')


class CreateExportJobRequest(ExportContractModel):
    schema_version: Literal["authored-vnext-export/v1"] = Field(alias='schemaVersion')
    owner_scope: OwnerScope = Field(alias='ownerScope')
    deck_id: str = Field(alias='deckId', pattern='^deck:[a-z0-9][a-z0-9._-]*$')
    expected_deck_revision: int = Field(alias='expectedDeckRevision', ge=1)
    expected_deck_digest: str = Field(alias='expectedDeckDigest', pattern='^sha256:[0-9a-f]{64}$')
    master_id: str = Field(alias='masterId', pattern='^master:[a-z0-9][a-z0-9._-]*$')
    expected_master_revision: int = Field(alias='expectedMasterRevision', ge=1)
    expected_master_digest: str = Field(alias='expectedMasterDigest', pattern='^sha256:[0-9a-f]{64}$')
    expected_active_artifact_revision: int | None = Field(alias='expectedActiveArtifactRevision')
    expected_active_artifact_digest: str | None = Field(alias='expectedActiveArtifactDigest')
    slide_spec_digests: list[SlideSpecDigest] = Field(alias='slideSpecDigests', min_length=1, max_length=1000)
    asset_digests: list[AssetDigest] = Field(alias='assetDigests', max_length=10000)
    font_digest: str = Field(alias='fontDigest', pattern='^sha256:[0-9a-f]{64}$')
    package_digest: str | None = Field(alias='packageDigest')
    idempotency_key: str = Field(alias='idempotencyKey', pattern='^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$')
    operation_fence: int = Field(alias='operationFence', ge=1)
    timeout_ms: int = Field(alias='timeoutMs', ge=1000, le=3600000)
    max_attempts: int = Field(alias='maxAttempts', ge=1, le=10)


class CreateExportJobResponse(ExportContractModel):
    schema_version: Literal["authored-vnext-export/v1"] = Field(alias='schemaVersion')
    job_id: str = Field(alias='jobId', pattern='^export-job:[a-z0-9][a-z0-9._-]*$')
    state: Literal["queued"] = Field(alias='state')
    idempotency_key: str = Field(alias='idempotencyKey')
    operation_fence: int = Field(alias='operationFence', ge=1)
    created_at: str = Field(alias='createdAt')
    status_url: str = Field(alias='statusUrl', pattern='^/api/v1/ppt/authored-vnext/export/jobs/[A-Za-z0-9._~%-]+$')


class ExportError(ExportContractModel):
    code: str = Field(alias='code', min_length=1, max_length=120)
    message: str = Field(alias='message', min_length=1, max_length=2000)
    retryable: bool = Field(alias='retryable')
    failed_closed: Literal[True] = Field(alias='failedClosed')


class ExportJobStatusResponse(ExportContractModel):
    schema_version: Literal["authored-vnext-export/v1"] = Field(alias='schemaVersion')
    job_id: str = Field(alias='jobId', pattern='^export-job:[a-z0-9][a-z0-9._-]*$')
    state: Literal["queued", "running", "retry_wait", "succeeded", "failed", "cancelled"] = Field(alias='state')
    operation_fence: int = Field(alias='operationFence', ge=1)
    attempt_count: int = Field(alias='attemptCount', ge=0, le=10)
    max_attempts: int = Field(alias='maxAttempts', ge=1, le=10)
    resumable: bool = Field(alias='resumable')
    cancel_requested: bool = Field(alias='cancelRequested')
    created_at: str = Field(alias='createdAt')
    updated_at: str = Field(alias='updatedAt')
    next_attempt_at: str | None = Field(default=None, alias='nextAttemptAt')
    artifact: ArtifactReceipt | None = Field(alias='artifact')
    error: ExportError | None = Field(alias='error')


class CancelExportJobRequest(ExportContractModel):
    schema_version: Literal["authored-vnext-export/v1"] = Field(alias='schemaVersion')
    owner_scope: OwnerScope = Field(alias='ownerScope')
    expected_operation_fence: int = Field(alias='expectedOperationFence', ge=1)


class RollbackArtifactPublicationRequest(ExportContractModel):
    schema_version: Literal["authored-vnext-export/v1"] = Field(alias='schemaVersion')
    owner_scope: OwnerScope = Field(alias='ownerScope')
    expected_active_artifact_revision: int = Field(alias='expectedActiveArtifactRevision', ge=1)
    expected_active_artifact_digest: str = Field(alias='expectedActiveArtifactDigest', pattern='^sha256:[0-9a-f]{64}$')
    target_artifact_id: str = Field(alias='targetArtifactId', pattern='^artifact:[a-z0-9][a-z0-9._-]*$')
    target_artifact_revision: int = Field(alias='targetArtifactRevision', ge=1)
    target_artifact_digest: str = Field(alias='targetArtifactDigest', pattern='^sha256:[0-9a-f]{64}$')
    idempotency_key: str = Field(alias='idempotencyKey', pattern='^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$')
    operation_fence: int = Field(alias='operationFence', ge=1)


class WorkerSlide(ExportContractModel):
    slide_id: str = Field(alias='slideId', pattern='^slide:[a-z0-9][a-z0-9._-]*$')
    layout_id: str = Field(alias='layoutId', pattern='^layout:[a-z0-9][a-z0-9._-]*$')
    values: dict[str, Any] = Field(alias='values')


class WorkerAsset(ExportContractModel):
    asset_id: str = Field(alias='assetId', pattern='^asset:[a-z0-9][a-z0-9._-]*$')
    media_type: str = Field(alias='mediaType', min_length=1, max_length=200)
    bytes_base64: str = Field(alias='bytesBase64', max_length=268435456)
    sha256: str = Field(alias='sha256', pattern='^[0-9a-f]{64}$')


class WorkerDigests(ExportContractModel):
    master_sha256: str = Field(alias='masterSha256', pattern='^[0-9a-f]{64}$')
    spec_sha256: str = Field(alias='specSha256', pattern='^[0-9a-f]{64}$')
    asset_manifest_sha256: str = Field(alias='assetManifestSha256', pattern='^[0-9a-f]{64}$')
    font_manifest_sha256: str = Field(alias='fontManifestSha256', pattern='^[0-9a-f]{64}$')
    package_sha256: str | None = Field(alias='packageSha256')


class WorkerPackageReceipt(ExportContractModel):
    package_digest: str = Field(alias='packageDigest', pattern='^[0-9a-f]{64}$')
    relationship_digest: str = Field(alias='relationshipDigest', pattern='^[0-9a-f]{64}$')
    closure_digest: str = Field(alias='closureDigest', pattern='^[0-9a-f]{64}$')
    slide_count: int = Field(alias='slideCount', ge=1, le=1000)
    master_count: int = Field(alias='masterCount', ge=1)
    layout_count: int = Field(alias='layoutCount', ge=1)
    macro_detected: bool = Field(alias='macroDetected')
    quarantine_reasons: list[str] = Field(alias='quarantineReasons')


class WorkerPreservationReceipt(ExportContractModel):
    silent_loss_count: int = Field(alias='silentLossCount', ge=0)
    missing_parts: list[str] = Field(alias='missingParts')
    unexpected_changed_parts: list[str] = Field(alias='unexpectedChangedParts')


class WorkerSlideReceipt(ExportContractModel):
    part_name: str = Field(alias='partName', min_length=1)
    slide_id: str | None = Field(alias='slideId')
    layout_id: str | None = Field(alias='layoutId')
    master_digest: str | None = Field(alias='masterDigest')


class WorkerMasterReceipt(ExportContractModel):
    contract_digest: str = Field(alias='contractDigest', pattern='^sha256:[0-9a-f]{64}$')
    semantic_digest: str = Field(alias='semanticDigest', pattern='^sha256:[0-9a-f]{64}$')
    furniture_count: int = Field(alias='furnitureCount', ge=0)
    logo_count: int = Field(alias='logoCount', ge=0)
    page_number_count: int = Field(alias='pageNumberCount', ge=0)
    font_file_count: int = Field(alias='fontFileCount', ge=0)
    duplicate_furniture_count: Literal[0] = Field(alias='duplicateFurnitureCount')
    font_files_mode: Literal["REFERENCE_METADATA_VERIFIED"] = Field(alias='fontFilesMode')


class WorkerReadBackReceipt(ExportContractModel):
    adapter_revision: int = Field(alias='adapterRevision', ge=1)
    validation: Literal["candidate-reopened"] = Field(alias='validation')
    candidate_digest_verified: Literal[True] = Field(alias='candidateDigestVerified')
    package: WorkerPackageReceipt = Field(alias='package')
    preservation: WorkerPreservationReceipt = Field(alias='preservation')
    master: WorkerMasterReceipt = Field(alias='master')
    slides: list[WorkerSlideReceipt] = Field(alias='slides', min_length=1)


class WorkerResumeReceipt(ExportContractModel):
    attempt: int = Field(alias='attempt', ge=1, le=10)
    checkpoint_digest: str = Field(alias='checkpointDigest', pattern='^[0-9a-f]{64}$')


class WorkerRequestEnvelope(ExportContractModel):
    schema_version: Literal["authored-vnext-ooxml-worker/v1"] = Field(alias='schemaVersion')
    operation: Literal["EXPORT_PPTX"] = Field(alias='operation')
    operation_id: str = Field(alias='operationId', min_length=8, max_length=200)
    job_id: str | None = Field(default=None, alias='jobId', pattern='^export-job:[a-z0-9][a-z0-9._-]*$')
    fencing_token: int = Field(alias='fencingToken', ge=1)
    attempt: int | None = Field(default=None, alias='attempt', ge=1, le=10)
    timeout_ms: int | None = Field(default=None, alias='timeoutMs', ge=1000, le=3600000)
    deadline: str | None = Field(default=None, alias='deadline')
    master: dict[str, Any] = Field(alias='master')
    slides: list[WorkerSlide] = Field(alias='slides', min_length=1, max_length=1000)
    assets: list[WorkerAsset] = Field(alias='assets', max_length=10000)
    digests: WorkerDigests = Field(alias='digests')
    resume_from_receipt: WorkerResumeReceipt | None = Field(default=None, alias='resumeFromReceipt')


class BufferedArtifact(ExportContractModel):
    content_base64: str = Field(alias='contentBase64', max_length=536870912)
    sha256: str = Field(alias='sha256', pattern='^[0-9a-f]{64}$')
    byte_length: int = Field(alias='byteLength', ge=1, le=402653184)
    media_type: Literal["application/vnd.openxmlformats-officedocument.presentationml.presentation"] = Field(alias='mediaType')
    filename_extension: Literal[".pptx"] = Field(alias='filenameExtension')


class WorkerResponseEnvelope(ExportContractModel):
    schema_version: Literal["authored-vnext-ooxml-worker/v1"] = Field(alias='schemaVersion')
    operation: Literal["EXPORT_PPTX"] = Field(alias='operation')
    operation_id: str = Field(alias='operationId', min_length=8, max_length=200)
    fencing_token: int = Field(alias='fencingToken', ge=1)
    status: Literal["SUCCEEDED"] = Field(alias='status')
    artifact: BufferedArtifact = Field(alias='artifact')
    receipt: WorkerReadBackReceipt = Field(alias='receipt')


class WorkerError(ExportContractModel):
    code: str = Field(alias='code', pattern='^[A-Z][A-Z0-9_]{2,119}$')
    message: str = Field(alias='message', min_length=1, max_length=2000)
    retryable: bool = Field(alias='retryable')
    failed_closed: Literal[True] = Field(alias='failedClosed')


class WorkerErrorEnvelope(ExportContractModel):
    schema_version: Literal["authored-vnext-ooxml-worker/v1"] = Field(alias='schemaVersion')
    status: Literal["FAILED"] = Field(alias='status')
    error: WorkerError = Field(alias='error')


__all__ = ['OwnerScope', 'SlideSpecDigest', 'AssetDigest', 'QualityIssue', 'QualityDecision', 'ArtifactReceipt', 'CreateExportJobRequest', 'CreateExportJobResponse', 'ExportError', 'ExportJobStatusResponse', 'CancelExportJobRequest', 'RollbackArtifactPublicationRequest', 'WorkerSlide', 'WorkerAsset', 'WorkerDigests', 'WorkerPackageReceipt', 'WorkerPreservationReceipt', 'WorkerSlideReceipt', 'WorkerMasterReceipt', 'WorkerReadBackReceipt', 'WorkerResumeReceipt', 'WorkerRequestEnvelope', 'BufferedArtifact', 'WorkerResponseEnvelope', 'WorkerError', 'WorkerErrorEnvelope']
