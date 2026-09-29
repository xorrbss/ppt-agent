"""Digest-bound export identity and publication contracts.

This module is the authority for the Phase 3/5 export design.  It composes the
database-backed ``PublicExportIdentity`` single-flight row with immutable slide
``ArtifactRef`` checkpoints.  Every public export submission passes through it:
the unique identity key decides whether a submission executes, joins an
in-flight execution, or reuses a completed result.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import threading
from typing import AsyncIterator, Awaitable, Callable, Literal, Sequence
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.public_artifact import (
    ArtifactRef,
    PublicArtifact,
    PublicArtifactPublication,
    PublicSlideCheckpoint,
    normalize_sha256,
)
from models.sql.workspace import WorkspaceOperation
from services.artifact_store import (
    ExportClaim,
    ExportIdentityKey,
    claim_export_identity,
    complete_export_identity,
    put_slide_checkpoint_if_current,
    promote_artifact_publication_if_current,
)
from services.workspace_operation_handler_registry import WorkspaceOperationClaim
from utils.get_env import get_environment_name


EXPORT_IDENTITY_SCHEMA_VERSION = "public-export-identity/v2"
EXPORT_CHECKPOINT_SCHEMA_VERSION = "public-export-checkpoint/v1"
SLIDE_RENDER_IDENTITY_SCHEMA_VERSION = "public-slide-render-identity/v1"
SLIDE_RENDER_STAGE_VERSION = "public-fidelity-render/v2"
MAX_EXPORT_CHECKPOINT_BYTES = 4 * 1024 * 1024

ExportFormat = Literal["pptx", "pdf"]
#: ``svg-native`` covers both svg export shapes (the native-svg-to-pptx
#: conversion and the vector PDF render) -- a single output_mode value per
#: source_format, not per export_format. See ``docs/generation-core-architecture-20260925.md`` §2.1.
ExportMode = Literal["fidelity-raster", "editable-hybrid", "svg-native"]


class PublicExportIdentityError(RuntimeError):
    pass


class PublicExportIdentityBindingError(PublicExportIdentityError):
    pass


class PublicExportFenceRejected(PublicExportIdentityError):
    pass


class PublicSlideRenderSingleflightError(PublicExportIdentityError):
    pass


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
class CanonicalSlideRenderIdentity:
    """Pre-render identity for one public Fidelity raster slide.

    The identity intentionally excludes the final PPTX/PDF format so the same
    validated PNG can serve either raster export. Owner, tenant, presentation,
    revision, and quality inputs remain part of the digest, preventing reuse
    across authority or evidence boundaries.
    """

    owner_id: uuid.UUID
    tenant_id: str
    presentation_id: uuid.UUID
    source_revision: int
    slide_index: int
    html_digest: str
    scene_digest: str
    quality_digest: str
    render_adapter: str
    renderer_version: str

    def __post_init__(self) -> None:
        if self.source_revision < 0 or self.slide_index < 0:
            raise ValueError("slide render position is invalid")
        object.__setattr__(
            self,
            "tenant_id",
            _normalized_text(self.tenant_id, field="render tenant", maximum=128),
        )
        for field_name in ("html_digest", "scene_digest", "quality_digest"):
            object.__setattr__(
                self,
                field_name,
                normalize_sha256(getattr(self, field_name)),
            )
        adapter = _normalized_text(
            self.render_adapter,
            field="render adapter",
            maximum=128,
        )
        if adapter != "public-isolated-fidelity":
            raise PublicExportIdentityBindingError(
                "public_slide_render_adapter_invalid"
            )
        object.__setattr__(self, "render_adapter", adapter)
        object.__setattr__(
            self,
            "renderer_version",
            _normalized_text(
                self.renderer_version,
                field="renderer version",
                maximum=128,
            ),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            **self.content_wire(),
            "slideIndex": self.slide_index,
        }

    def content_wire(self) -> dict[str, object]:
        """Return the position-independent inputs that determine PNG bytes."""

        return {
            "schemaVersion": SLIDE_RENDER_IDENTITY_SCHEMA_VERSION,
            "ownerId": str(self.owner_id),
            "tenantId": self.tenant_id,
            "presentationId": str(self.presentation_id),
            "sourceRevision": self.source_revision,
            "htmlDigest": self.html_digest,
            "sceneDigest": self.scene_digest,
            "qualityDigest": self.quality_digest,
            "renderAdapter": self.render_adapter,
            "rendererVersion": self.renderer_version,
        }

    @property
    def canonical_digest(self) -> str:
        return sha256(
            json.dumps(
                self.content_wire(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @property
    def stage_version(self) -> str:
        return SLIDE_RENDER_STAGE_VERSION


_slide_render_flights_lock = threading.Lock()
_slide_render_flights: dict[
    tuple[int, str], asyncio.Future[ArtifactRef]
] = {}


async def run_slide_render_singleflight(
    identity: CanonicalSlideRenderIdentity,
    producer: Callable[[], Awaitable[ArtifactRef]],
    *,
    max_in_flight: int,
    wait_seconds: float,
    on_follower_wait: Callable[[], None] | None = None,
) -> tuple[ArtifactRef, bool]:
    """Run or join one bounded, cancellation-safe in-process render flight.

    The leader remains the producer task, so cancelling its operation cancels
    the actual Chromium render. Followers shield the shared future so their
    own cancellation cannot cancel another operation's work.
    """

    if max_in_flight < 1 or max_in_flight > 4096 or wait_seconds <= 0:
        raise ValueError("slide render singleflight bounds are invalid")
    loop = asyncio.get_running_loop()
    key = (id(loop), identity.canonical_digest)
    bypass_local_flight = False
    with _slide_render_flights_lock:
        shared = _slide_render_flights.get(key)
        leader = shared is None
        if leader:
            if len(_slide_render_flights) >= max_in_flight:
                # This map is only an in-process optimization.  Capacity must
                # never reject an otherwise authoritative DB-backed render.
                bypass_local_flight = True
            else:
                shared = loop.create_future()
                _slide_render_flights[key] = shared
    if bypass_local_flight:
        return await producer(), False
    assert shared is not None
    if not leader:
        if on_follower_wait is not None:
            on_follower_wait()
        try:
            return (
                await asyncio.wait_for(
                    asyncio.shield(shared),
                    timeout=wait_seconds,
                ),
                True,
            )
        except TimeoutError as error:
            raise PublicSlideRenderSingleflightError(
                "public_render_singleflight_timeout"
            ) from error
        except asyncio.CancelledError as error:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            raise PublicSlideRenderSingleflightError(
                "public_render_singleflight_leader_cancelled"
            ) from error

    try:
        artifact = await producer()
        if not shared.done():
            shared.set_result(artifact)
        return artifact, False
    except asyncio.CancelledError:
        if not shared.done():
            shared.cancel()
        raise
    except BaseException as error:
        if not shared.done():
            shared.set_exception(error)
            # A leader can have no followers; retrieving preserves the original
            # producer exception without emitting an orphaned-Future warning.
            shared.exception()
        raise
    finally:
        with _slide_render_flights_lock:
            if _slide_render_flights.get(key) is shared:
                del _slide_render_flights[key]


def _slide_render_advisory_key(identity: CanonicalSlideRenderIdentity) -> int:
    """Map one canonical render digest to PostgreSQL's signed bigint key."""

    return int.from_bytes(
        bytes.fromhex(identity.canonical_digest)[:8],
        byteorder="big",
        signed=True,
    )


@asynccontextmanager
async def acquire_slide_render_authority(
    session: AsyncSession,
    *,
    identity: CanonicalSlideRenderIdentity,
    wait_seconds: float,
    on_authority_wait: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    """Acquire the cross-process render authority before producing PNG bytes.

    PostgreSQL uses one transaction-scoped advisory lock per canonical slide
    render.  The connection owns the authority, so process loss releases it;
    the operation checkpoint fence still owns publication.  SQLite is allowed
    only for explicit local/test development and is reported truthfully as a
    process-local optimization, never as multi-replica singleflight.
    """

    if wait_seconds <= 0:
        raise ValueError("slide render authority wait must be positive")
    bind = session.get_bind()
    dialect = bind.dialect.name
    if dialect != "postgresql":
        environment = get_environment_name("production")
        if environment not in {"local", "development", "test"}:
            raise PublicSlideRenderSingleflightError(
                "public_render_database_authority_required"
            )
        yield "process-local-development"
        return

    advisory_key = _slide_render_advisory_key(identity)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    wait_notified = False
    while True:
        async with session.begin():
            acquired = await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:advisory_key)"),
                {"advisory_key": advisory_key},
            )
            if acquired is True:
                yield "postgresql-advisory-xact"
                return
        if not wait_notified:
            wait_notified = True
            if on_authority_wait is not None:
                on_authority_wait()
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise PublicSlideRenderSingleflightError(
                "public_render_database_authority_timeout"
            )
        await asyncio.sleep(min(0.1, remaining))


async def find_reusable_slide_render_checkpoint(
    session: AsyncSession,
    *,
    identity: CanonicalSlideRenderIdentity,
) -> ArtifactRef | None:
    """Resolve a committed owner-scoped PNG checkpoint from any prior export."""

    artifact = await session.scalar(
        select(PublicArtifact)
        .join(
            PublicSlideCheckpoint,
            PublicSlideCheckpoint.artifact_id == PublicArtifact.id,
        )
        .join(
            WorkspaceOperation,
            WorkspaceOperation.id == PublicSlideCheckpoint.operation_id,
        )
        .where(
            WorkspaceOperation.owner_id == identity.owner_id,
            WorkspaceOperation.presentation_id == identity.presentation_id,
            WorkspaceOperation.kind.in_(("export.pptx", "export.pdf")),
            PublicSlideCheckpoint.revision == identity.source_revision,
            PublicSlideCheckpoint.input_digest == identity.canonical_digest,
            PublicSlideCheckpoint.stage_version == identity.stage_version,
            PublicArtifact.owner_id == identity.owner_id,
            PublicArtifact.tenant_id == identity.tenant_id,
            PublicArtifact.kind == "slide-png",
            PublicArtifact.media_type == "image/png",
            PublicArtifact.retention_class == "checkpoint",
            PublicArtifact.lifecycle_state == "committed",
        )
        .order_by(PublicSlideCheckpoint.committed_at.desc())
        .limit(1)
    )
    return artifact.to_ref() if artifact is not None else None


def _validate_slide_render_claim(
    claim: WorkspaceOperationClaim,
    identity: CanonicalSlideRenderIdentity,
) -> None:
    if claim.kind not in {"export.pptx", "export.pdf"}:
        raise PublicExportIdentityBindingError(
            "public_render_operation_kind_invalid"
        )
    if claim.owner_id != identity.owner_id:
        raise PublicExportIdentityBindingError(
            "public_render_operation_owner_mismatch"
        )
    if claim.presentation_id != identity.presentation_id:
        raise PublicExportIdentityBindingError(
            "public_render_operation_presentation_mismatch"
        )
    if claim.expected_revision != identity.source_revision:
        raise PublicExportIdentityBindingError(
            "public_render_operation_revision_mismatch"
        )
    if claim.attempt_number < 1 or claim.fencing_token < 1:
        raise PublicExportIdentityBindingError(
            "public_render_operation_fence_invalid"
        )
    _attempt_token_hash(claim.attempt_token)


async def bind_slide_render_checkpoint(
    session: AsyncSession,
    *,
    identity: CanonicalSlideRenderIdentity,
    operation_claim: WorkspaceOperationClaim,
    artifact: ArtifactRef,
) -> None:
    """CAS a fresh or reused PNG checkpoint under the current operation fence."""

    _validate_slide_render_claim(operation_claim, identity)
    if (
        artifact.owner_id != identity.owner_id
        or artifact.tenant_id != identity.tenant_id
        or artifact.kind != "slide-png"
        or artifact.media_type != "image/png"
        or artifact.retention_class != "checkpoint"
    ):
        raise PublicExportIdentityBindingError(
            "public_render_checkpoint_artifact_invalid"
        )
    pointer = await put_slide_checkpoint_if_current(
        session,
        operation_id=operation_claim.operation_id,
        owner_id=identity.owner_id,
        tenant_id=identity.tenant_id,
        attempt_number=operation_claim.attempt_number,
        attempt_token=operation_claim.attempt_token,
        fencing_token=operation_claim.fencing_token,
        revision=identity.source_revision,
        slide_index=identity.slide_index,
        input_digest=identity.canonical_digest,
        stage_version=identity.stage_version,
        artifact_id=artifact.artifact_id,
    )
    if pointer is None:
        raise PublicExportFenceRejected("public_render_checkpoint_fence_stale")


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
        if self.artifact.kind not in {"slide-png", "slide-svg"}:  # svg-native slides are svg sources
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

    def identity_wire(self) -> dict[str, object]:
        """Stable final-export identity without row- or operation-local UUIDs."""

        return {
            "slideIndex": self.slide_index,
            "artifactDigest": self.artifact.sha256,
            "artifactBytes": self.artifact.size_bytes,
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
        if self.export_mode not in {"fidelity-raster", "editable-hybrid", "svg-native"}:
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
        if self.export_format == "pdf" and self.export_mode not in {
            "fidelity-raster", "svg-native",
        }:
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

    def canonical_wire(self) -> dict[str, object]:
        """Return only content/version evidence that determines final bytes."""

        value = self.to_wire()
        value["slides"] = [checkpoint.identity_wire() for checkpoint in self.slides]
        return value

    def checkpoint_payload(self) -> bytes:
        payload = {
            "schemaVersion": EXPORT_CHECKPOINT_SCHEMA_VERSION,
            "canonicalDigest": self.canonical_digest,
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
        return sha256(
            json.dumps(
                self.canonical_wire(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def export_key(self, source_checkpoint: ArtifactRef) -> ExportIdentityKey:
        if (
            source_checkpoint.kind != "checkpoint"
            or source_checkpoint.retention_class != "checkpoint"
            or source_checkpoint.owner_id != self.owner_id
            or source_checkpoint.tenant_id != self.tenant_id
            or source_checkpoint.sha256
            != sha256(self.checkpoint_payload()).hexdigest()
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
            source_artifact_digest=source_checkpoint.sha256,
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
        fields={"schemaVersion", "canonicalDigest", "identity"},
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
    try:
        checkpoint_canonical_digest = normalize_sha256(
            str(root["canonicalDigest"])
        )
    except ValueError as error:
        raise PublicExportIdentityBindingError(
            "public_export_identity_checkpoint_binding_invalid"
        ) from error
    if (
        identity.owner_id != source_checkpoint.owner_id
        or identity.tenant_id != source_checkpoint.tenant_id
        or identity.checkpoint_payload() != payload
        or identity.canonical_digest != checkpoint_canonical_digest
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
    """Join the cluster-wide single-flight for this export identity."""

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
    "CanonicalSlideRenderIdentity",
    "EXPORT_CHECKPOINT_SCHEMA_VERSION",
    "EXPORT_IDENTITY_SCHEMA_VERSION",
    "ExportEvidenceDigests",
    "FencedExportPublicationCandidate",
    "MAX_EXPORT_CHECKPOINT_BYTES",
    "SLIDE_RENDER_IDENTITY_SCHEMA_VERSION",
    "SLIDE_RENDER_STAGE_VERSION",
    "PublicExportFenceRejected",
    "PublicExportIdentityBindingError",
    "PublicExportIdentityError",
    "PublicSlideRenderSingleflightError",
    "SlideCheckpointArtifact",
    "acquire_slide_render_authority",
    "bind_slide_render_checkpoint",
    "claim_canonical_export_identity",
    "checkpoint_canonical_export_slides",
    "complete_canonical_export_identity",
    "find_reusable_slide_render_checkpoint",
    "promote_canonical_export_publication",
    "restore_canonical_export_identity",
    "reuse_completed_export_identity",
    "run_slide_render_singleflight",
]
