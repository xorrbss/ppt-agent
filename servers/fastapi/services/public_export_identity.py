"""Digest-bound, feature-gated export identity and publication contracts.

This module is a shadow boundary for the Phase 3/5 export design.  It composes
the existing database-backed ``PublicExportIdentity`` single-flight authority
with immutable slide ``ArtifactRef`` checkpoints.  Callers must explicitly opt
in; importing this module does not change the legacy export path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from typing import Literal, Sequence
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.public_artifact import (
    ArtifactRef,
    PublicArtifactPublication,
    normalize_sha256,
)
from services.artifact_store import (
    ExportClaim,
    ExportIdentityKey,
    claim_export_identity,
    complete_export_identity,
    put_slide_checkpoint_if_current,
    promote_artifact_publication_if_current,
)
from services.workspace_operation_handler_registry import WorkspaceOperationClaim


EXPORT_IDENTITY_SCHEMA_VERSION = "public-export-identity/v2"
EXPORT_CHECKPOINT_SCHEMA_VERSION = "public-export-checkpoint/v1"
PUBLIC_EXPORT_IDENTITY_V2_ENV = "PUBLIC_EXPORT_IDENTITY_V2_ENABLED"
MAX_EXPORT_CHECKPOINT_BYTES = 4 * 1024 * 1024

ExportFormat = Literal["pptx", "pdf"]
ExportMode = Literal["fidelity-raster", "editable-hybrid"]


class PublicExportIdentityError(RuntimeError):
    pass


class PublicExportIdentityV2Disabled(PublicExportIdentityError):
    pass


class PublicExportIdentityBindingError(PublicExportIdentityError):
    pass


class PublicExportFenceRejected(PublicExportIdentityError):
    pass


def public_export_identity_v2_enabled() -> bool:
    """Return an explicit opt-in; production authority is unchanged by default."""

    return (os.getenv(PUBLIC_EXPORT_IDENTITY_V2_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def require_public_export_identity_v2_enabled() -> None:
    if not public_export_identity_v2_enabled():
        raise PublicExportIdentityV2Disabled("public_export_identity_v2_disabled")


def _normalized_text(value: str, *, field: str, maximum: int = 160) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field} is invalid")
    return normalized


def _attempt_token_hash(value: str) -> str:
    if not value:
        raise PublicExportIdentityBindingError("public_export_attempt_token_required")
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ExportEvidenceDigests:
    """Every mutable export dependency that must split single-flight identity."""

    scene_digest: str
    semantic_digest: str
    quality_digest: str
    exporter_digest: str
    font_manifest_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "scene_digest",
            "semantic_digest",
            "quality_digest",
            "exporter_digest",
            "font_manifest_digest",
        ):
            object.__setattr__(
                self,
                field_name,
                normalize_sha256(getattr(self, field_name)),
            )

    def to_wire(self) -> dict[str, str]:
        return {
            "scene": self.scene_digest,
            "semantic": self.semantic_digest,
            "quality": self.quality_digest,
            "exporter": self.exporter_digest,
            "fontManifest": self.font_manifest_digest,
        }


@dataclass(frozen=True, slots=True)
class SlideCheckpointArtifact:
    """One validated slide checkpoint that another node can consume directly."""

    slide_index: int
    artifact: ArtifactRef
    validation_digest: str
    input_digest: str | None = None
    stage_version: str = "validated-slide/v1"

    def __post_init__(self) -> None:
        if self.slide_index < 0:
            raise ValueError("slide checkpoint index must be nonnegative")
        if self.artifact.kind != "slide-png":
            raise PublicExportIdentityBindingError(
                "public_export_checkpoint_kind_invalid"
            )
        if self.artifact.retention_class != "checkpoint":
            raise PublicExportIdentityBindingError(
                "public_export_checkpoint_retention_invalid"
            )
        object.__setattr__(
            self,
            "validation_digest",
            normalize_sha256(self.validation_digest),
        )
        object.__setattr__(
            self,
            "input_digest",
            normalize_sha256(self.input_digest or self.artifact.sha256),
        )
        object.__setattr__(
            self,
            "stage_version",
            _normalized_text(
                self.stage_version,
                field="checkpoint stage version",
                maximum=64,
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "slideIndex": self.slide_index,
            "artifactRef": self.artifact.model_dump(mode="json", by_alias=True),
            "validationDigest": self.validation_digest,
            "inputDigest": self.input_digest,
            "stageVersion": self.stage_version,
        }


@dataclass(frozen=True, slots=True)
class CanonicalExportIdentity:
    """Canonical, digest-bound identity for one exact export execution."""

    owner_id: uuid.UUID
    tenant_id: str
    presentation_id: uuid.UUID
    source_revision: int
    export_format: ExportFormat
    export_mode: ExportMode
    render_adapter: str
    exporter_version: str
    evidence: ExportEvidenceDigests
    slides: tuple[SlideCheckpointArtifact, ...]

    def __post_init__(self) -> None:
        if self.source_revision < 0:
            raise ValueError("export source revision must be nonnegative")
        if self.export_format not in {"pptx", "pdf"}:
            raise PublicExportIdentityBindingError("public_export_format_invalid")
        if self.export_mode not in {"fidelity-raster", "editable-hybrid"}:
            raise PublicExportIdentityBindingError("public_export_mode_invalid")
        object.__setattr__(
            self,
            "tenant_id",
            _normalized_text(self.tenant_id, field="export tenant", maximum=128),
        )
        object.__setattr__(
            self,
            "render_adapter",
            _normalized_text(self.render_adapter, field="render adapter"),
        )
        object.__setattr__(
            self,
            "exporter_version",
            _normalized_text(self.exporter_version, field="exporter version", maximum=128),
        )
        if self.export_format == "pdf" and self.export_mode != "fidelity-raster":
            raise PublicExportIdentityBindingError("public_export_pdf_mode_invalid")
        if not self.slides:
            raise PublicExportIdentityBindingError("public_export_checkpoints_required")
        expected_indices = tuple(range(len(self.slides)))
        actual_indices = tuple(checkpoint.slide_index for checkpoint in self.slides)
        if actual_indices != expected_indices:
            raise PublicExportIdentityBindingError(
                "public_export_checkpoint_indices_invalid"
            )
        for checkpoint in self.slides:
            reference = checkpoint.artifact
            if reference.owner_id != self.owner_id or reference.tenant_id != self.tenant_id:
                raise PublicExportIdentityBindingError(
                    "public_export_checkpoint_owner_tenant_mismatch"
                )

    @classmethod
    def build(
        cls,
        *,
        owner_id: uuid.UUID,
        tenant_id: str,
        presentation_id: uuid.UUID,
        source_revision: int,
        export_format: ExportFormat,
        export_mode: ExportMode,
        render_adapter: str,
        exporter_version: str,
        evidence: ExportEvidenceDigests,
        slides: Sequence[SlideCheckpointArtifact],
    ) -> "CanonicalExportIdentity":
        return cls(
            owner_id=owner_id,
            tenant_id=tenant_id,
            presentation_id=presentation_id,
            source_revision=source_revision,
            export_format=export_format,
            export_mode=export_mode,
            render_adapter=render_adapter,
            exporter_version=exporter_version,
            evidence=evidence,
            slides=tuple(slides),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "schemaVersion": EXPORT_IDENTITY_SCHEMA_VERSION,
            "ownerId": str(self.owner_id),
            "tenantId": self.tenant_id,
            "presentationId": str(self.presentation_id),
            "sourceRevision": self.source_revision,
            "output": {
                "format": self.export_format,
                "mode": self.export_mode,
                "renderAdapter": self.render_adapter,
                "exporterVersion": self.exporter_version,
            },
            "evidenceDigests": self.evidence.to_wire(),
            "slides": [checkpoint.to_wire() for checkpoint in self.slides],
        }

    def checkpoint_payload(self) -> bytes:
        payload = {
            "schemaVersion": EXPORT_CHECKPOINT_SCHEMA_VERSION,
            "identity": self.to_wire(),
        }
        return json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @property
    def canonical_digest(self) -> str:
        return sha256(self.checkpoint_payload()).hexdigest()

    def export_key(self, source_checkpoint: ArtifactRef) -> ExportIdentityKey:
        if (
            source_checkpoint.kind != "checkpoint"
            or source_checkpoint.retention_class != "checkpoint"
            or source_checkpoint.owner_id != self.owner_id
            or source_checkpoint.tenant_id != self.tenant_id
            or source_checkpoint.sha256 != self.canonical_digest
        ):
            raise PublicExportIdentityBindingError(
                "public_export_identity_checkpoint_binding_invalid"
            )
        return ExportIdentityKey(
            owner_id=self.owner_id,
            tenant_id=self.tenant_id,
            presentation_id=self.presentation_id,
            revision=self.source_revision,
            canonical_digest=self.canonical_digest,
            scene_digest=self.evidence.scene_digest,
            semantic_digest=self.evidence.semantic_digest,
            quality_digest=self.evidence.quality_digest,
            exporter_digest=self.evidence.exporter_digest,
            export_mode=self.export_format,
            output_mode=self.export_mode,
            font_manifest_digest=self.evidence.font_manifest_digest,
            exporter_version=self.exporter_version,
            render_adapter=self.render_adapter,
            source_artifact_id=source_checkpoint.artifact_id,
        )


def _exact_object(
    value: object,
    *,
    fields: set[str],
    error_code: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PublicExportIdentityBindingError(error_code)
    return value


def _required_int(value: object, *, error_code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PublicExportIdentityBindingError(error_code)
    return value


def restore_canonical_export_identity(
    source_checkpoint: ArtifactRef,
    payload: bytes,
) -> CanonicalExportIdentity:
    """Restore only an exact immutable checkpoint, never caller-supplied state.

    Object-store durability and DB registration are established by the artifact
    service.  This boundary independently rechecks the byte length, SHA-256,
    canonical JSON representation, and every identity/slide binding before the
    checkpoint may join cluster single-flight.
    """

    if (
        source_checkpoint.kind != "checkpoint"
        or source_checkpoint.retention_class != "checkpoint"
        or source_checkpoint.media_type != "application/json"
        or source_checkpoint.size_bytes < 1
        or source_checkpoint.size_bytes > MAX_EXPORT_CHECKPOINT_BYTES
    ):
        raise PublicExportIdentityBindingError(
            "public_export_identity_checkpoint_binding_invalid"
        )
    if len(payload) != source_checkpoint.size_bytes:
        raise PublicExportIdentityBindingError(
            "public_export_checkpoint_size_mismatch"
        )
    if sha256(payload).hexdigest() != source_checkpoint.sha256:
        raise PublicExportIdentityBindingError(
            "public_export_checkpoint_digest_mismatch"
        )
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicExportIdentityBindingError(
            "public_export_checkpoint_payload_invalid"
        ) from error
    root = _exact_object(
        decoded,
        fields={"schemaVersion", "identity"},
        error_code="public_export_checkpoint_payload_invalid",
    )
    if root["schemaVersion"] != EXPORT_CHECKPOINT_SCHEMA_VERSION:
        raise PublicExportIdentityBindingError(
            "public_export_checkpoint_schema_invalid"
        )
    identity_wire = _exact_object(
        root["identity"],
        fields={
            "schemaVersion",
            "ownerId",
            "tenantId",
            "presentationId",
            "sourceRevision",
            "output",
            "evidenceDigests",
            "slides",
        },
        error_code="public_export_checkpoint_identity_invalid",
    )
    if identity_wire["schemaVersion"] != EXPORT_IDENTITY_SCHEMA_VERSION:
        raise PublicExportIdentityBindingError(
            "public_export_identity_schema_invalid"
        )
    output = _exact_object(
        identity_wire["output"],
        fields={"format", "mode", "renderAdapter", "exporterVersion"},
        error_code="public_export_checkpoint_output_invalid",
    )
    evidence_wire = _exact_object(
        identity_wire["evidenceDigests"],
        fields={"scene", "semantic", "quality", "exporter", "fontManifest"},
        error_code="public_export_checkpoint_evidence_invalid",
    )
    slides_wire = identity_wire["slides"]
    if not isinstance(slides_wire, list):
        raise PublicExportIdentityBindingError(
            "public_export_checkpoint_slides_invalid"
        )
    try:
        slides = tuple(
            SlideCheckpointArtifact(
                slide_index=_required_int(
                    slide_wire["slideIndex"],
                    error_code="public_export_checkpoint_slide_index_invalid",
                ),
                artifact=ArtifactRef.model_validate(slide_wire["artifactRef"]),
                validation_digest=str(slide_wire["validationDigest"]),
                input_digest=str(slide_wire["inputDigest"]),
                stage_version=str(slide_wire["stageVersion"]),
            )
            for slide_wire in (
                _exact_object(
                    value,
                    fields={
                        "slideIndex",
                        "artifactRef",
                        "validationDigest",
                        "inputDigest",
                        "stageVersion",
                    },
                    error_code="public_export_checkpoint_slide_invalid",
                )
                for value in slides_wire
            )
        )
        identity = CanonicalExportIdentity.build(
            owner_id=uuid.UUID(str(identity_wire["ownerId"])),
            tenant_id=str(identity_wire["tenantId"]),
            presentation_id=uuid.UUID(str(identity_wire["presentationId"])),
            source_revision=_required_int(
                identity_wire["sourceRevision"],
                error_code="public_export_checkpoint_revision_invalid",
            ),
            export_format=str(output["format"]),  # type: ignore[arg-type]
            export_mode=str(output["mode"]),  # type: ignore[arg-type]
            render_adapter=str(output["renderAdapter"]),
            exporter_version=str(output["exporterVersion"]),
            evidence=ExportEvidenceDigests(
                scene_digest=str(evidence_wire["scene"]),
                semantic_digest=str(evidence_wire["semantic"]),
                quality_digest=str(evidence_wire["quality"]),
                exporter_digest=str(evidence_wire["exporter"]),
                font_manifest_digest=str(evidence_wire["fontManifest"]),
            ),
            slides=slides,
        )
    except PublicExportIdentityBindingError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise PublicExportIdentityBindingError(
            "public_export_checkpoint_payload_invalid"
        ) from error
    if (
        identity.owner_id != source_checkpoint.owner_id
        or identity.tenant_id != source_checkpoint.tenant_id
        or identity.checkpoint_payload() != payload
        or identity.canonical_digest != source_checkpoint.sha256
    ):
        raise PublicExportIdentityBindingError(
            "public_export_identity_checkpoint_binding_invalid"
        )
    return identity


@dataclass(frozen=True, slots=True)
class CanonicalExportExecution:
    identity: CanonicalExportIdentity
    source_checkpoint: ArtifactRef
    singleflight: ExportClaim
    operation_id: uuid.UUID
    operation_attempt_number: int
    operation_attempt_token_hash: str
    operation_fencing_token: int
    worker_id: str

    @property
    def should_execute(self) -> bool:
        return self.singleflight.disposition == "claimed"


@dataclass(frozen=True, slots=True)
class FencedExportPublicationCandidate:
    """A pointer candidate, not a publication; DB CAS remains authoritative."""

    operation_id: uuid.UUID
    operation_attempt_number: int
    operation_attempt_token_hash: str
    operation_fencing_token: int
    export_identity_id: uuid.UUID
    export_identity_fencing_token: int
    canonical_digest: str
    artifact: ArtifactRef
    reused_singleflight_result: bool

    def validate_for_claim(self, claim: WorkspaceOperationClaim) -> None:
        if (
            claim.operation_id != self.operation_id
            or claim.attempt_number != self.operation_attempt_number
            or _attempt_token_hash(claim.attempt_token)
            != self.operation_attempt_token_hash
            or claim.fencing_token != self.operation_fencing_token
        ):
            raise PublicExportFenceRejected("public_export_publication_fence_stale")


def _validate_operation_claim(
    claim: WorkspaceOperationClaim,
    identity: CanonicalExportIdentity,
) -> None:
    if claim.kind != f"export.{identity.export_format}":
        raise PublicExportIdentityBindingError("public_export_operation_kind_invalid")
    if claim.owner_id != identity.owner_id:
        raise PublicExportIdentityBindingError("public_export_operation_owner_mismatch")
    if claim.presentation_id != identity.presentation_id:
        raise PublicExportIdentityBindingError(
            "public_export_operation_presentation_mismatch"
        )
    if (
        claim.expected_revision is not None
        and claim.expected_revision != identity.source_revision
    ):
        raise PublicExportIdentityBindingError("public_export_operation_revision_mismatch")
    if claim.attempt_number < 1 or claim.fencing_token < 1:
        raise PublicExportIdentityBindingError("public_export_operation_fence_invalid")
    _attempt_token_hash(claim.attempt_token)


async def checkpoint_canonical_export_slides(
    session: AsyncSession,
    *,
    identity: CanonicalExportIdentity,
    operation_claim: WorkspaceOperationClaim,
) -> None:
    """Bind every committed slide checkpoint to the live operation fence."""

    require_public_export_identity_v2_enabled()
    _validate_operation_claim(operation_claim, identity)
    if operation_claim.owner_id is None:
        raise PublicExportIdentityBindingError("public_export_owner_required")
    for checkpoint in identity.slides:
        pointer = await put_slide_checkpoint_if_current(
            session,
            operation_id=operation_claim.operation_id,
            owner_id=operation_claim.owner_id,
            tenant_id=identity.tenant_id,
            attempt_number=operation_claim.attempt_number,
            attempt_token=operation_claim.attempt_token,
            fencing_token=operation_claim.fencing_token,
            revision=identity.source_revision,
            slide_index=checkpoint.slide_index,
            input_digest=checkpoint.input_digest or checkpoint.artifact.sha256,
            stage_version=checkpoint.stage_version,
            artifact_id=checkpoint.artifact.artifact_id,
        )
        if pointer is None:
            raise PublicExportFenceRejected("public_export_checkpoint_fence_stale")


async def claim_canonical_export_identity(
    session: AsyncSession,
    *,
    identity: CanonicalExportIdentity,
    source_checkpoint: ArtifactRef,
    operation_claim: WorkspaceOperationClaim,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> CanonicalExportExecution:
    """Join the cluster-wide single-flight without changing legacy call sites."""

    require_public_export_identity_v2_enabled()
    _validate_operation_claim(operation_claim, identity)
    worker_id = _normalized_text(worker_id, field="export worker")
    claim = await claim_export_identity(
        session,
        key=identity.export_key(source_checkpoint),
        operation_id=operation_claim.operation_id,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        now=now,
    )
    return CanonicalExportExecution(
        identity=identity,
        source_checkpoint=source_checkpoint,
        singleflight=claim,
        operation_id=operation_claim.operation_id,
        operation_attempt_number=operation_claim.attempt_number,
        operation_attempt_token_hash=_attempt_token_hash(
            operation_claim.attempt_token
        ),
        operation_fencing_token=operation_claim.fencing_token,
        worker_id=worker_id,
    )


def _validate_execution_claim(
    execution: CanonicalExportExecution,
    operation_claim: WorkspaceOperationClaim,
) -> None:
    _validate_operation_claim(operation_claim, execution.identity)
    if (
        execution.operation_id != operation_claim.operation_id
        or execution.operation_attempt_number != operation_claim.attempt_number
        or execution.operation_attempt_token_hash
        != _attempt_token_hash(operation_claim.attempt_token)
        or execution.operation_fencing_token != operation_claim.fencing_token
    ):
        raise PublicExportFenceRejected("public_export_publication_fence_stale")


def _validate_result_artifact(
    execution: CanonicalExportExecution,
    artifact: ArtifactRef,
    *,
    producer_operation_required: bool,
) -> None:
    expected_kind = f"presentation-{execution.identity.export_format}"
    if (
        artifact.owner_id != execution.identity.owner_id
        or artifact.tenant_id != execution.identity.tenant_id
        or artifact.kind != expected_kind
        or artifact.retention_class != "published"
    ):
        raise PublicExportIdentityBindingError(
            "public_export_result_artifact_binding_invalid"
        )
    if producer_operation_required and artifact.created_by_operation_id != execution.operation_id:
        raise PublicExportIdentityBindingError(
            "public_export_result_operation_binding_invalid"
        )


def _publication_candidate(
    execution: CanonicalExportExecution,
    artifact: ArtifactRef,
    *,
    reused: bool,
) -> FencedExportPublicationCandidate:
    return FencedExportPublicationCandidate(
        operation_id=execution.operation_id,
        operation_attempt_number=execution.operation_attempt_number,
        operation_attempt_token_hash=execution.operation_attempt_token_hash,
        operation_fencing_token=execution.operation_fencing_token,
        export_identity_id=execution.singleflight.identity_id,
        export_identity_fencing_token=execution.singleflight.fencing_token,
        canonical_digest=execution.identity.canonical_digest,
        artifact=artifact,
        reused_singleflight_result=reused,
    )


async def complete_canonical_export_identity(
    session: AsyncSession,
    *,
    execution: CanonicalExportExecution,
    operation_claim: WorkspaceOperationClaim,
    result_artifact: ArtifactRef,
) -> FencedExportPublicationCandidate:
    """Fence the result before it becomes eligible for publication-pointer CAS."""

    require_public_export_identity_v2_enabled()
    _validate_execution_claim(execution, operation_claim)
    if (
        execution.singleflight.disposition != "claimed"
        or execution.singleflight.attempt_token is None
    ):
        raise PublicExportIdentityBindingError("public_export_execution_not_claimed")
    _validate_result_artifact(
        execution,
        result_artifact,
        producer_operation_required=True,
    )
    completed = await complete_export_identity(
        session,
        identity_id=execution.singleflight.identity_id,
        worker_id=execution.worker_id,
        attempt_token=execution.singleflight.attempt_token,
        fencing_token=execution.singleflight.fencing_token,
        result_artifact_id=result_artifact.artifact_id,
    )
    if not completed:
        raise PublicExportFenceRejected("public_export_singleflight_fence_stale")
    return _publication_candidate(execution, result_artifact, reused=False)


def reuse_completed_export_identity(
    *,
    execution: CanonicalExportExecution,
    operation_claim: WorkspaceOperationClaim,
) -> FencedExportPublicationCandidate:
    """Bind an existing immutable result to the current operation's live fence."""

    require_public_export_identity_v2_enabled()
    _validate_execution_claim(execution, operation_claim)
    artifact = execution.singleflight.artifact
    if execution.singleflight.disposition != "completed" or artifact is None:
        raise PublicExportIdentityBindingError("public_export_result_not_completed")
    _validate_result_artifact(
        execution,
        artifact,
        producer_operation_required=False,
    )
    return _publication_candidate(execution, artifact, reused=True)


async def promote_canonical_export_publication(
    session: AsyncSession,
    *,
    candidate: FencedExportPublicationCandidate,
    operation_claim: WorkspaceOperationClaim,
    tenant_id: str,
    publication_name: str,
    revision: int,
    now: datetime | None = None,
) -> PublicArtifactPublication:
    """Publish a committed result only while its operation fence is current."""

    require_public_export_identity_v2_enabled()
    candidate.validate_for_claim(operation_claim)
    if operation_claim.owner_id is None:
        raise PublicExportIdentityBindingError("public_export_owner_required")
    if candidate.artifact.tenant_id != tenant_id:
        raise PublicExportIdentityBindingError(
            "public_export_publication_tenant_mismatch"
        )
    pointer = await promote_artifact_publication_if_current(
        session,
        operation_id=operation_claim.operation_id,
        owner_id=operation_claim.owner_id,
        tenant_id=tenant_id,
        attempt_number=operation_claim.attempt_number,
        attempt_token=operation_claim.attempt_token,
        fencing_token=operation_claim.fencing_token,
        publication_name=publication_name,
        revision=revision,
        artifact_id=candidate.artifact.artifact_id,
        quality_digest=candidate.identity.evidence.quality_digest,
        now=now,
    )
    if pointer is None:
        raise PublicExportFenceRejected("public_export_publication_fence_stale")
    if (
        pointer.artifact_id != candidate.artifact.artifact_id
        or pointer.fencing_token != operation_claim.fencing_token
        or pointer.attempt_token_hash != candidate.operation_attempt_token_hash
    ):
        raise PublicExportFenceRejected("public_export_publication_binding_invalid")
    return pointer


__all__ = [
    "CanonicalExportExecution",
    "CanonicalExportIdentity",
    "EXPORT_CHECKPOINT_SCHEMA_VERSION",
    "EXPORT_IDENTITY_SCHEMA_VERSION",
    "ExportEvidenceDigests",
    "FencedExportPublicationCandidate",
    "MAX_EXPORT_CHECKPOINT_BYTES",
    "PUBLIC_EXPORT_IDENTITY_V2_ENV",
    "PublicExportFenceRejected",
    "PublicExportIdentityBindingError",
    "PublicExportIdentityError",
    "PublicExportIdentityV2Disabled",
    "SlideCheckpointArtifact",
    "claim_canonical_export_identity",
    "checkpoint_canonical_export_slides",
    "complete_canonical_export_identity",
    "public_export_identity_v2_enabled",
    "promote_canonical_export_publication",
    "require_public_export_identity_v2_enabled",
    "restore_canonical_export_identity",
    "reuse_completed_export_identity",
]
