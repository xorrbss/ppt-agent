"""PPT Core adapter from a fenced WorkspaceOperation claim to isolated workers."""

from __future__ import annotations

import pathlib
import time
from dataclasses import dataclass, replace
from typing import Any, Mapping
import uuid

from sqlalchemy.ext.asyncio import AsyncSession


from models.sql.public_artifact import (
    ArtifactRef as PublicArtifactRef,
)
from services.artifact_store import (
    configured_public_artifact_service,
)
from services.public_render_batching import (
    MAX_PUBLIC_RENDER_BATCH_SLIDES,
)
from services.public_export_deadline import (
    worker_deadline_bounds,
)
from services.public_export_errors import PublicExportOperationError
from services.public_render_runtime_identity import (
    MAX_PUBLIC_RENDER_OPERATION_SLIDES,
    PUBLIC_RENDER_TEST_FONT_DIGEST_ENV,
    PUBLIC_RENDER_WORKER_FONT_DIGEST_ENV,
    PUBLIC_RENDER_WORKER_IMAGE_DIGEST_ENV,
    PUBLIC_RENDER_WORKER_PROTOCOL_VERSION,
    PUBLIC_SLIDE_RENDERER_IDENTITY_VERSION,
    public_deck_render_identity_digest,
    public_release_review_deck_render_identity_digest,
    public_release_review_runtime_binding,
    public_render_runtime_binding,
    public_render_runtime_binding_digest,
    public_slide_renderer_version,
)
from services.public_export_identity import (
    CanonicalExportExecution,
    CanonicalExportIdentity,
    checkpoint_canonical_export_slides,
    claim_canonical_export_identity,
)
from services.isolated_worker_contracts import (
    ArtifactRef,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
    WorkerRole,
    issue_worker_capability,
)
from services.isolated_worker_process import (
    IsolatedWorkerResult,
    execute_isolated_worker_job,
)
from services.workspace_operation_handler_registry import WorkspaceOperationClaim
from utils.env_settings.public_service import (
    get_public_worker_settings,
)


@dataclass(frozen=True, slots=True)
class PublicWorkerArtifactResult:
    artifact: PublicArtifactRef
    resource_metrics: Mapping[str, Any]


def load_worker_capability_signing_key() -> bytes:
    """Load a mounted workload key without copying it into child argv or env."""

    value = get_public_worker_settings().capability_key_file or ""
    if not value or not pathlib.Path(value).is_absolute():
        raise PublicExportOperationError("public_worker_capability_key_file_required")
    try:
        key = pathlib.Path(value).read_bytes()
    except OSError as error:
        raise PublicExportOperationError(
            "public_worker_capability_key_unavailable"
        ) from error
    if len(key) < 32 or len(key) > 4096:
        raise PublicExportOperationError("public_worker_capability_key_invalid")
    return key


HYBRID_EDITABILITY_SCHEMA = "public-hybrid-editability/v1"
HYBRID_EDITABILITY_VERDICTS = frozenset(
    {
        "fully-editable",
        "partially-editable",
        "not-editable",
        "not-applicable",
        "unknown",
    }
)


def build_isolated_operation_job(
    claim: WorkspaceOperationClaim,
    *,
    role: WorkerRole,
    input_artifact: ArtifactRef,
    signing_key: bytes,
    deadline_seconds: int | None = None,
    now: int | None = None,
) -> IsolatedWorkerJob:
    """Bind immutable input to the claim; no session argument exists by design."""

    if role == "export" and claim.kind not in {"export.pptx", "export.pdf"}:
        raise PublicExportOperationError("public_export_operation_kind_invalid")
    if role == "render" and claim.kind not in {
        "presentation.generate",
        "export.pptx",
        "export.pdf",
    }:
        raise PublicExportOperationError("public_render_operation_kind_invalid")
    if claim.owner_id is None or input_artifact.owner_id != claim.owner_id:
        raise PublicExportOperationError("public_worker_artifact_owner_mismatch")
    if not claim.attempt_token or claim.fencing_token < 0:
        raise PublicExportOperationError("public_worker_operation_fence_invalid")
    fallback_deadline, maximum_deadline = worker_deadline_bounds(role)
    # Product callers pass what is left of the pinned admission deadline; the
    # fallback serves only callers without an admission row (no session here).
    effective_deadline = fallback_deadline if deadline_seconds is None else deadline_seconds
    if not 1 <= effective_deadline <= maximum_deadline:
        raise PublicExportOperationError("public_worker_deadline_invalid")
    issued_at = int(time.time()) if now is None else now
    job = IsolatedWorkerJob(
        role=role,
        operation_id=claim.operation_id,
        attempt_token=claim.attempt_token,
        fencing_token=claim.fencing_token,
        input_digest=input_artifact.sha256,
        input_artifact=input_artifact,
        capability="pending",
        deadline_epoch_seconds=issued_at + effective_deadline,
    )
    try:
        capability = issue_worker_capability(
            job,
            signing_key=signing_key,
            ttl_seconds=min(300, effective_deadline),
            now=issued_at,
        )
    except IsolatedWorkerContractError as error:
        raise PublicExportOperationError(error.args[0]) from error
    return replace(job, capability=capability)


async def execute_isolated_export_operation(
    claim: WorkspaceOperationClaim,
    *,
    input_artifact: ArtifactRef,
    deadline_seconds: int | None = None,
) -> IsolatedWorkerResult:
    """Run an export profile whose result still requires operation CAS publish."""

    job = build_isolated_operation_job(
        claim,
        role="export",
        input_artifact=input_artifact,
        signing_key=load_worker_capability_signing_key(),
        deadline_seconds=deadline_seconds,
    )
    return await execute_isolated_worker_job(job, presentation_id=claim.presentation_id)


def _worker_ref(reference: PublicArtifactRef) -> ArtifactRef:
    return ArtifactRef.from_wire(reference.model_dump(mode="json", by_alias=True))


def _public_worker_output(
    payload: object,
    *,
    retention_class: str,
) -> PublicArtifactRef:
    if not isinstance(payload, dict):
        raise PublicExportOperationError("public_worker_output_artifact_missing")
    value = dict(payload)
    value["artifactId"] = str(uuid.uuid4())
    value["retentionClass"] = retention_class
    try:
        return PublicArtifactRef.model_validate(value)
    except Exception as error:
        raise PublicExportOperationError("public_worker_output_artifact_invalid") from error


async def prepare_canonical_export_execution(
    session: AsyncSession,
    claim: WorkspaceOperationClaim,
    *,
    identity: CanonicalExportIdentity,
    worker_id: str,
    lease_seconds: int = 120,
) -> CanonicalExportExecution:
    """Persist the cross-node identity checkpoint and join the single-flight.

    The caller owns the surrounding database transaction.  The returned
    disposition is the cluster-wide authority: ``claimed`` executes,
    ``in_flight`` joins a live attempt, ``completed`` reuses the result.
    """

    if claim.owner_id is None:
        raise PublicExportOperationError("public_export_owner_required")
    artifact_service = configured_public_artifact_service()
    source_checkpoint = await artifact_service.persist_bytes(
        session,
        owner_id=identity.owner_id,
        tenant_id=identity.tenant_id,
        kind="checkpoint",
        payload=identity.checkpoint_payload(),
        media_type="application/json",
        created_by_operation_id=claim.operation_id,
        retention_class="checkpoint",
    )
    await checkpoint_canonical_export_slides(
        session,
        identity=identity,
        operation_claim=claim,
    )
    return await claim_canonical_export_identity(
        session,
        identity=identity,
        source_checkpoint=source_checkpoint,
        operation_claim=claim,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )


__all__ = [
    "PublicExportOperationError",
    "PublicWorkerArtifactResult",
    "MAX_PUBLIC_RENDER_BATCH_SLIDES",
    "MAX_PUBLIC_RENDER_OPERATION_SLIDES",
    "PUBLIC_RENDER_WORKER_PROTOCOL_VERSION",
    "PUBLIC_RENDER_WORKER_FONT_DIGEST_ENV",
    "PUBLIC_RENDER_WORKER_IMAGE_DIGEST_ENV",
    "PUBLIC_RENDER_TEST_FONT_DIGEST_ENV",
    "PUBLIC_SLIDE_RENDERER_IDENTITY_VERSION",
    "build_isolated_operation_job",
    "execute_isolated_export_operation",
    "load_worker_capability_signing_key",
    "prepare_canonical_export_execution",
    "public_deck_render_identity_digest",
    "public_render_runtime_binding",
    "public_render_runtime_binding_digest",
    "public_release_review_deck_render_identity_digest",
    "public_release_review_runtime_binding",
    "public_slide_renderer_version",
]
