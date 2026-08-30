"""PPT Core adapter from a fenced WorkspaceOperation claim to isolated workers."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import os
import pathlib
import time
from dataclasses import replace
from datetime import datetime
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.public_artifact import (
    ArtifactRef as PublicArtifactRef,
    normalize_sha256,
)
from services.artifact_store import configured_public_artifact_service
from services.database import async_session_maker
from services.public_export_identity import (
    CanonicalExportExecution,
    CanonicalExportIdentity,
    ExportEvidenceDigests,
    FencedExportPublicationCandidate,
    SlideCheckpointArtifact,
    checkpoint_canonical_export_slides,
    claim_canonical_export_identity,
    complete_canonical_export_identity,
    promote_canonical_export_publication,
    public_export_identity_v2_enabled,
    require_public_export_identity_v2_enabled,
    restore_canonical_export_identity,
    reuse_completed_export_identity,
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


class PublicExportOperationError(RuntimeError):
    pass


def load_worker_capability_signing_key() -> bytes:
    """Load a mounted workload key without copying it into child argv or env."""

    value = os.getenv("PUBLIC_WORKER_CAPABILITY_KEY_FILE", "").strip()
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
    effective_deadline = (
        (600 if role == "export" else 120)
        if deadline_seconds is None
        else deadline_seconds
    )
    maximum_deadline = 600 if role == "export" else 120
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
) -> IsolatedWorkerResult:
    """Run an export profile whose result still requires operation CAS publish."""

    job = build_isolated_operation_job(
        claim,
        role="export",
        input_artifact=input_artifact,
        signing_key=load_worker_capability_signing_key(),
    )
    return await execute_isolated_worker_job(job, presentation_id=claim.presentation_id)


async def execute_isolated_render_operation(
    claim: WorkspaceOperationClaim,
    *,
    input_artifact: ArtifactRef,
) -> IsolatedWorkerResult:
    job = build_isolated_operation_job(
        claim,
        role="render",
        input_artifact=input_artifact,
        signing_key=load_worker_capability_signing_key(),
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


def build_validated_checkpoint_export_bundle(
    identity: CanonicalExportIdentity,
) -> bytes:
    """Build an export input from durable validated slides without re-rendering."""

    return json.dumps(
        {
            "schemaVersion": "public-export-bundle/v2",
            "canonicalExportIdentity": identity.canonical_digest,
            "exportFormat": identity.export_format,
            "exportMode": identity.export_mode,
            "renderAdapter": identity.render_adapter,
            "slides": [
                {
                    "slideIndex": checkpoint.slide_index,
                    "artifactRef": _worker_ref(checkpoint.artifact).to_wire(),
                    "validationDigest": checkpoint.validation_digest,
                }
                for checkpoint in identity.slides
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


async def prepare_canonical_export_execution(
    session: AsyncSession,
    claim: WorkspaceOperationClaim,
    *,
    identity: CanonicalExportIdentity,
    worker_id: str,
    lease_seconds: int = 120,
) -> CanonicalExportExecution:
    """Persist the cross-node identity checkpoint and join shadow single-flight.

    The caller owns the surrounding database transaction.  This boundary is
    inert unless ``PUBLIC_EXPORT_IDENTITY_V2_ENABLED`` is explicitly enabled.
    """

    require_public_export_identity_v2_enabled()
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


async def resume_canonical_export_execution(
    session: AsyncSession,
    claim: WorkspaceOperationClaim,
    *,
    source_checkpoint: PublicArtifactRef,
    worker_id: str,
    lease_seconds: int = 120,
    now: datetime | None = None,
) -> CanonicalExportExecution:
    """Resume from object-store checkpoint bytes after strict digest rebinding."""

    require_public_export_identity_v2_enabled()
    if claim.owner_id is None:
        raise PublicExportOperationError("public_export_owner_required")
    artifact_service = configured_public_artifact_service()
    payload = await artifact_service.read_ref_bytes(source_checkpoint)
    identity = restore_canonical_export_identity(source_checkpoint, payload)
    return await claim_canonical_export_identity(
        session,
        identity=identity,
        source_checkpoint=source_checkpoint,
        operation_claim=claim,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        now=now,
    )


async def execute_and_publish_canonical_export_execution(
    claim: WorkspaceOperationClaim,
    *,
    execution: CanonicalExportExecution,
    publication_name: str,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> FencedExportPublicationCandidate:
    """Run or reuse one DB-authoritative export and fence its publication.

    Child execution happens outside a database transaction.  The returned
    worker bytes must already be durable in object storage; result metadata,
    single-flight completion, and publication pointer are then committed in
    one transaction.  An in-flight duplicate never performs local fallback.
    """

    require_public_export_identity_v2_enabled()
    if execution.singleflight.disposition == "in_flight":
        raise PublicExportOperationError("public_export_singleflight_in_flight")
    if execution.singleflight.disposition == "completed":
        candidate = reuse_completed_export_identity(
            execution=execution,
            operation_claim=claim,
        )
        async with session_factory() as session:
            await promote_canonical_export_publication(
                session,
                candidate=candidate,
                operation_claim=claim,
                publication_name=publication_name,
                revision=execution.identity.source_revision,
                quality_digest=execution.identity.evidence.quality_digest,
            )
            await session.commit()
        return candidate
    if not execution.should_execute:
        raise PublicExportOperationError("public_export_execution_disposition_invalid")

    artifact_service = configured_public_artifact_service()
    bundle = build_validated_checkpoint_export_bundle(execution.identity)
    async with session_factory() as session:
        bundle_ref = await artifact_service.persist_bytes(
            session,
            owner_id=execution.identity.owner_id,
            tenant_id=execution.identity.tenant_id,
            kind="export-bundle",
            payload=bundle,
            media_type="application/vnd.presenton.export-bundle+json",
            created_by_operation_id=claim.operation_id,
            retention_class="ephemeral",
        )
        await session.commit()

    exported = await execute_isolated_export_operation(
        claim,
        input_artifact=_worker_ref(bundle_ref),
    )
    if not exported.ok:
        raise PublicExportOperationError(
            exported.error_code or "public_export_worker_failed"
        )
    result_ref = _public_worker_output(
        dict(exported.output_artifact or {}),
        retention_class="published",
    )
    async with session_factory() as session:
        registered = await artifact_service.register_ref(session, result_ref)
        candidate = await complete_canonical_export_identity(
            session,
            execution=execution,
            operation_claim=claim,
            result_artifact=registered,
        )
        await promote_canonical_export_publication(
            session,
            candidate=candidate,
            operation_claim=claim,
            publication_name=publication_name,
            revision=execution.identity.source_revision,
            quality_digest=execution.identity.evidence.quality_digest,
        )
        await session.commit()
    return candidate


async def execute_canonical_export_execution(
    claim: WorkspaceOperationClaim,
    *,
    execution: CanonicalExportExecution,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> PublicArtifactRef:
    """Execute/join single-flight without publishing the public pointer.

    The workspace export handler owns the only final transaction that promotes
    the pointer, terminalizes the operation, appends the result event, and
    exposes the authorized download. This boundary only makes the immutable
    result durable and completes the reusable export identity.
    """

    require_public_export_identity_v2_enabled()
    if execution.singleflight.disposition == "in_flight":
        raise PublicExportOperationError("public_export_singleflight_in_flight")
    if execution.singleflight.disposition == "completed":
        return reuse_completed_export_identity(
            execution=execution,
            operation_claim=claim,
        ).artifact
    if not execution.should_execute:
        raise PublicExportOperationError("public_export_execution_disposition_invalid")

    artifact_service = configured_public_artifact_service()
    bundle = build_validated_checkpoint_export_bundle(execution.identity)
    async with session_factory() as session:
        bundle_ref = await artifact_service.persist_bytes(
            session,
            owner_id=execution.identity.owner_id,
            tenant_id=execution.identity.tenant_id,
            kind="export-bundle",
            payload=bundle,
            media_type="application/vnd.presenton.export-bundle+json",
            created_by_operation_id=claim.operation_id,
            retention_class="ephemeral",
        )
        await session.commit()
    exported = await execute_isolated_export_operation(
        claim,
        input_artifact=_worker_ref(bundle_ref),
    )
    if not exported.ok:
        raise PublicExportOperationError(
            exported.error_code or "public_export_worker_failed"
        )
    result_ref = _public_worker_output(
        dict(exported.output_artifact or {}),
        retention_class="published",
    )
    async with session_factory() as session:
        registered = await artifact_service.register_ref(session, result_ref)
        await complete_canonical_export_identity(
            session,
            execution=execution,
            operation_claim=claim,
            result_artifact=registered,
        )
        await session.commit()
    return registered


async def render_and_export_authored_htmls(
    claim: WorkspaceOperationClaim,
    *,
    tenant_id: str,
    htmls: list[str],
    export_format: str,
    scene_digest: str,
    quality_digest: str,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> PublicArtifactRef:
    """Materialize authored HTML through network-none render and export roles."""

    if claim.owner_id is None:
        raise PublicExportOperationError("public_export_owner_required")
    if export_format not in {"pptx", "pdf"} or claim.kind != f"export.{export_format}":
        raise PublicExportOperationError("public_export_operation_kind_invalid")
    if not htmls or len(htmls) > 100 or any(not isinstance(html, str) or not html for html in htmls):
        raise PublicExportOperationError("public_export_authored_slides_invalid")
    normalized_scene_digest = normalize_sha256(
        scene_digest.removeprefix("sha256:")
    )
    normalized_quality_digest = normalize_sha256(
        quality_digest.removeprefix("sha256:")
    )

    artifact_service = configured_public_artifact_service()
    html_refs: list[PublicArtifactRef] = []
    async with session_factory() as session:
        for html in htmls:
            html_refs.append(
                await artifact_service.persist_bytes(
                    session,
                    owner_id=claim.owner_id,
                    tenant_id=tenant_id,
                    kind="slide-html",
                    payload=html.encode("utf-8"),
                    media_type="text/html",
                    created_by_operation_id=claim.operation_id,
                    retention_class="ephemeral",
                )
            )
        await session.commit()

    concurrency = max(1, min(8, int(os.getenv("PUBLIC_RENDER_MAX_CONCURRENCY", "4"))))
    gate = asyncio.Semaphore(concurrency)

    canonical_enabled = public_export_identity_v2_enabled()

    async def render_one(reference: PublicArtifactRef) -> PublicArtifactRef:
        async with gate:
            result = await execute_isolated_render_operation(
                claim,
                input_artifact=_worker_ref(reference),
            )
        if not result.ok:
            raise PublicExportOperationError(
                result.error_code or "public_render_worker_failed"
            )
        rendered = _public_worker_output(
            dict(result.output_artifact or {}),
            retention_class=("checkpoint" if canonical_enabled else "ephemeral"),
        )
        async with session_factory() as session:
            registered = await artifact_service.register_ref(session, rendered)
            await session.commit()
            return registered

    png_refs = list(await asyncio.gather(*(render_one(reference) for reference in html_refs)))
    if canonical_enabled:
        if claim.expected_revision is None:
            raise PublicExportOperationError("public_export_revision_required")
        semantic_digest = sha256(
            json.dumps(
                htmls,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        exporter_version = "public-worker-protocol/v2"
        identity = CanonicalExportIdentity.build(
            owner_id=claim.owner_id,
            tenant_id=tenant_id,
            presentation_id=claim.presentation_id,
            source_revision=claim.expected_revision,
            export_format=export_format,  # type: ignore[arg-type]
            export_mode="fidelity-raster",
            render_adapter="public-isolated-fidelity",
            exporter_version=exporter_version,
            evidence=ExportEvidenceDigests(
                scene_digest=normalized_scene_digest,
                semantic_digest=semantic_digest,
                quality_digest=normalized_quality_digest,
                exporter_digest=sha256(exporter_version.encode("utf-8")).hexdigest(),
                font_manifest_digest=sha256(
                    b"fidelity-raster:no-font-manifest"
                ).hexdigest(),
            ),
            slides=tuple(
                SlideCheckpointArtifact(
                    slide_index=index,
                    artifact=reference,
                    validation_digest=sha256(
                        json.dumps(
                            {
                                "slideIndex": index,
                                "htmlDigest": html_refs[index].sha256,
                                "pngDigest": reference.sha256,
                                "sceneDigest": normalized_scene_digest,
                                "qualityDigest": normalized_quality_digest,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                )
                for index, reference in enumerate(png_refs)
            ),
        )
        async with session_factory() as session:
            execution = await prepare_canonical_export_execution(
                session,
                claim,
                identity=identity,
                worker_id=(os.getenv("PUBLIC_WORKER_NODE_ID") or "export-controller"),
            )
            await session.commit()
        return await execute_canonical_export_execution(
            claim,
            execution=execution,
            session_factory=session_factory,
        )
    bundle = json.dumps(
        {
            "schemaVersion": "public-export-bundle/v1",
            "exportFormat": export_format,
            "slides": [
                _worker_ref(reference).to_wire() for reference in png_refs
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    async with session_factory() as session:
        bundle_ref = await artifact_service.persist_bytes(
            session,
            owner_id=claim.owner_id,
            tenant_id=tenant_id,
            kind="export-bundle",
            payload=bundle,
            media_type="application/vnd.presenton.export-bundle+json",
            created_by_operation_id=claim.operation_id,
            retention_class="ephemeral",
        )
        await session.commit()

    exported = await execute_isolated_export_operation(
        claim,
        input_artifact=_worker_ref(bundle_ref),
    )
    if not exported.ok:
        raise PublicExportOperationError(
            exported.error_code or "public_export_worker_failed"
        )
    published = _public_worker_output(
        dict(exported.output_artifact or {}),
        retention_class="published",
    )
    async with session_factory() as session:
        registered = await artifact_service.register_ref(session, published)
        await session.commit()
        return registered


__all__ = [
    "PublicExportOperationError",
    "build_validated_checkpoint_export_bundle",
    "build_isolated_operation_job",
    "execute_isolated_export_operation",
    "execute_isolated_render_operation",
    "execute_and_publish_canonical_export_execution",
    "execute_canonical_export_execution",
    "load_worker_capability_signing_key",
    "prepare_canonical_export_execution",
    "render_and_export_authored_htmls",
    "resume_canonical_export_execution",
]
