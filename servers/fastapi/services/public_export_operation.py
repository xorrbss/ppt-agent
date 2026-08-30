"""PPT Core adapter from a fenced WorkspaceOperation claim to isolated workers."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import os
import pathlib
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.public_artifact import (
    ArtifactRef as PublicArtifactRef,
    normalize_sha256,
)
from services.artifact_store import (
    configured_public_artifact_service,
    configured_worker_artifact_cache,
    public_artifact_store_metrics_delta,
    public_artifact_store_metrics_snapshot,
)
from services.database import async_session_maker
from services.public_export_identity import (
    CanonicalExportExecution,
    CanonicalExportIdentity,
    CanonicalSlideRenderIdentity,
    ExportEvidenceDigests,
    FencedExportPublicationCandidate,
    SlideCheckpointArtifact,
    acquire_slide_render_authority,
    bind_slide_render_checkpoint,
    checkpoint_canonical_export_slides,
    claim_canonical_export_identity,
    complete_canonical_export_identity,
    find_reusable_slide_render_checkpoint,
    promote_canonical_export_publication,
    public_export_identity_v2_enabled,
    require_public_export_identity_v2_enabled,
    restore_canonical_export_identity,
    reuse_completed_export_identity,
    run_slide_render_singleflight,
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


@dataclass(frozen=True, slots=True)
class PublicWorkerArtifactResult:
    artifact: PublicArtifactRef
    resource_metrics: Mapping[str, Any]


def _resource_summary(
    samples: list[Mapping[str, Any]],
    *,
    cache_only: bool = False,
) -> dict[str, Any]:
    if not samples:
        if cache_only:
            return {
                "worker_peak_memory_bytes": 0,
                "worker_peak_handle_count": 0,
                "worker_peak_process_count": 0,
                "worker_peak_cpu_millis": 0,
                "worker_recycle_failure_count": 0,
                "resource_limits_enforced": True,
                "worker_enforcement_layer": "cache-only",
                "worker_execution_count": 0,
                "worker_resource_samples": [],
                "cache_only_reuse": True,
            }
        raise PublicExportOperationError("public_worker_resource_metrics_missing")
    layers = {str(sample.get("enforcementLayer", "")) for sample in samples}
    durable_samples = []
    isolation_attestations: list[dict[str, Any]] = []
    for sample in samples:
        raw_limits = sample.get("limits")
        raw_limits = raw_limits if isinstance(raw_limits, Mapping) else {}
        raw_attestation = sample.get("isolationAttestation")
        attestation = (
            dict(raw_attestation) if isinstance(raw_attestation, Mapping) else {}
        )
        isolation_attestations.append(attestation)
        durable_samples.append(
            {
                "role": str(sample.get("role", "")),
                "enforcementLayer": str(sample.get("enforcementLayer", "")),
                "resourceLimitsEnforced": sample.get("resourceLimitsEnforced") is True,
                "controlEnforcementMethods": dict(
                    sample.get("controlEnforcementMethods", {})
                    if isinstance(sample.get("controlEnforcementMethods"), Mapping)
                    else {}
                ),
                "residualRisks": dict(
                    sample.get("residualRisks", {})
                    if isinstance(sample.get("residualRisks"), Mapping)
                    else {}
                ),
                "supervisorPollIntervalMillis": int(
                    sample.get("supervisorPollIntervalMillis", 0)
                ),
                "resourceLimits": {
                    "memory": int(raw_limits.get("memoryBytes", 0)),
                    "cpu": int(raw_limits.get("cpuMillis", 0)),
                    "pids": int(raw_limits.get("pidCount", 0)),
                    "openFiles": int(raw_limits.get("openFiles", 0)),
                    "outputBytes": int(raw_limits.get("outputBytes", 0)),
                    "wallClock": int(raw_limits.get("wallClockSeconds", 0)),
                },
                "peakRssBytes": int(sample.get("peakRssBytes", 0)),
                "peakPrivateBytes": int(sample.get("peakPrivateBytes", 0)),
                "peakHandleCount": int(sample.get("peakHandleCount", 0)),
                "peakProcessCount": int(sample.get("peakProcessCount", 0)),
                "peakCpuMillis": int(sample.get("peakCpuMillis", 0)),
                "recycledWithinBaseline": sample.get("recycledWithinBaseline") is True,
                "finalProcessCount": int(sample.get("finalProcessCount", 0)),
                "isolationAttestation": attestation,
            }
        )
    network_none_worker = all(
        attestation.get("schemaVersion")
        == "presenton.worker-isolation-attestation/v1"
        and attestation.get("productionEligible") is True
        and isinstance(attestation.get("controls"), Mapping)
        and isinstance(attestation["controls"].get("network-none"), Mapping)
        and attestation["controls"]["network-none"].get("status") == "enforced"
        for attestation in isolation_attestations
    )
    return {
        "worker_peak_memory_bytes": max(int(sample.get("peakPrivateBytes", 0)) for sample in samples),
        "worker_peak_handle_count": max(int(sample.get("peakHandleCount", 0)) for sample in samples),
        "worker_peak_process_count": max(int(sample.get("peakProcessCount", 0)) for sample in samples),
        "worker_peak_cpu_millis": max(int(sample.get("peakCpuMillis", 0)) for sample in samples),
        "worker_recycle_failure_count": sum(sample.get("recycledWithinBaseline") is not True for sample in samples),
        "resource_limits_enforced": all(sample.get("resourceLimitsEnforced") is True for sample in samples),
        "worker_enforcement_layer": layers.pop() if len(layers) == 1 else "mixed",
        "worker_execution_count": len(samples),
        "worker_resource_samples": durable_samples,
        "worker_isolation_attestations": isolation_attestations,
        "network_none_worker": network_none_worker,
    }


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
) -> PublicWorkerArtifactResult:
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
        return PublicWorkerArtifactResult(
            artifact=reuse_completed_export_identity(
                execution=execution,
                operation_claim=claim,
            ).artifact,
            resource_metrics={},
        )
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
    if exported.resource_metrics is None:
        raise PublicExportOperationError("public_worker_resource_metrics_missing")
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
    return PublicWorkerArtifactResult(
        artifact=registered,
        resource_metrics={"role": exported.role, **exported.resource_metrics},
    )


async def render_and_export_authored_htmls(
    claim: WorkspaceOperationClaim,
    *,
    tenant_id: str,
    htmls: list[str],
    export_format: str,
    scene_digest: str,
    quality_digest: str,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> PublicWorkerArtifactResult:
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

    artifact_metrics_before = public_artifact_store_metrics_snapshot()
    artifact_service = configured_public_artifact_service()
    worker_cache = configured_worker_artifact_cache()
    await worker_cache.maintain()

    async def stage_worker_input(reference: PublicArtifactRef) -> None:
        await artifact_service.stream_ref_to_object_store(reference, worker_cache)

    async def adopt_worker_output(
        reference: PublicArtifactRef,
        *,
        expected_kind: str,
        expected_media_type: str,
        retention_class: str,
    ) -> PublicArtifactRef:
        if (
            reference.owner_id != claim.owner_id
            or reference.tenant_id != tenant_id
            or reference.created_by_operation_id != claim.operation_id
            or reference.kind != expected_kind
            or reference.media_type != expected_media_type
            or reference.retention_class != retention_class
        ):
            raise PublicExportOperationError(
                "public_worker_output_artifact_binding_invalid"
            )
        opened = await worker_cache.open_read_stream(reference)
        try:
            async with session_factory() as session:
                adopted = await artifact_service.persist_stream(
                    session,
                    owner_id=claim.owner_id,
                    tenant_id=tenant_id,
                    kind=reference.kind,
                    source=opened.body,
                    expected_digest=reference.sha256,
                    size_bytes=reference.size_bytes,
                    media_type=reference.media_type,
                    created_by_operation_id=claim.operation_id,
                    retention_class=retention_class,
                )
                await session.commit()
                return adopted
        finally:
            opened.close()

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
    resource_samples: list[Mapping[str, Any]] = []

    canonical_enabled = public_export_identity_v2_enabled()
    if canonical_enabled and claim.expected_revision is None:
        raise PublicExportOperationError("public_export_revision_required")
    render_execution_count = 0
    render_checkpoint_reuse_count = 0
    render_singleflight_join_count = 0
    render_authority_modes: set[str] = set()
    singleflight_wait_seconds = max(
        1.0,
        min(
            600.0,
            float(os.getenv("PUBLIC_RENDER_SINGLEFLIGHT_WAIT_SECONDS", "125")),
        ),
    )

    async def render_fresh(reference: PublicArtifactRef) -> PublicArtifactRef:
        nonlocal render_execution_count
        await stage_worker_input(reference)
        async with gate:
            result = await execute_isolated_render_operation(
                claim,
                input_artifact=_worker_ref(reference),
            )
        render_execution_count += 1
        if not result.ok:
            raise PublicExportOperationError(
                result.error_code or "public_render_worker_failed"
            )
        if result.resource_metrics is None:
            raise PublicExportOperationError("public_worker_resource_metrics_missing")
        resource_samples.append({"role": result.role, **result.resource_metrics})
        rendered = _public_worker_output(
            dict(result.output_artifact or {}),
            retention_class=("checkpoint" if canonical_enabled else "ephemeral"),
        )
        return await adopt_worker_output(
            rendered,
            expected_kind="slide-png",
            expected_media_type="image/png",
            retention_class=("checkpoint" if canonical_enabled else "ephemeral"),
        )

    semantic_digest = sha256(
        json.dumps(
            htmls,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    renderer_version = "public-worker-protocol/v2"
    slide_identities = (
        tuple(
            CanonicalSlideRenderIdentity(
                owner_id=claim.owner_id,
                tenant_id=tenant_id,
                presentation_id=claim.presentation_id,
                source_revision=claim.expected_revision,
                slide_index=index,
                html_digest=reference.sha256,
                scene_digest=normalized_scene_digest,
                quality_digest=normalized_quality_digest,
                render_adapter="public-isolated-fidelity",
                renderer_version=renderer_version,
            )
            for index, reference in enumerate(html_refs)
        )
        if canonical_enabled and claim.expected_revision is not None
        else ()
    )

    async def render_canonical_slide(
        reference: PublicArtifactRef,
        identity: CanonicalSlideRenderIdentity,
    ) -> PublicArtifactRef:
        nonlocal render_checkpoint_reuse_count, render_singleflight_join_count
        async with session_factory() as session:
            reusable = await find_reusable_slide_render_checkpoint(
                session,
                identity=identity,
            )
        if reusable is not None:
            render_checkpoint_reuse_count += 1
            render_authority_modes.add("committed-checkpoint")
            async with session_factory() as session:
                await bind_slide_render_checkpoint(
                    session,
                    identity=identity,
                    operation_claim=claim,
                    artifact=reusable,
                )
                await session.commit()
            return reusable

        async def render_under_database_authority() -> PublicArtifactRef:
            nonlocal render_checkpoint_reuse_count
            async with session_factory() as authority_session:
                async with acquire_slide_render_authority(
                    authority_session,
                    identity=identity,
                    wait_seconds=singleflight_wait_seconds,
                ) as authority_mode:
                    render_authority_modes.add(authority_mode)
                    # Another process may have committed while this caller
                    # waited for the DB authority. Recheck before Chromium.
                    async with session_factory() as lookup_session:
                        authoritative_reuse = await find_reusable_slide_render_checkpoint(
                            lookup_session,
                            identity=identity,
                        )
                    if authoritative_reuse is not None:
                        render_checkpoint_reuse_count += 1
                        rendered = authoritative_reuse
                    else:
                        rendered = await render_fresh(reference)
                    # Commit the checkpoint before releasing the transaction-
                    # scoped authority so another replica cannot rerender.
                    async with session_factory() as bind_session:
                        await bind_slide_render_checkpoint(
                            bind_session,
                            identity=identity,
                            operation_claim=claim,
                            artifact=rendered,
                        )
                        await bind_session.commit()
                    return rendered

        rendered, joined = await run_slide_render_singleflight(
            identity,
            render_under_database_authority,
            max_in_flight=max(
                1,
                min(
                    256,
                    int(os.getenv("PUBLIC_RENDER_SINGLEFLIGHT_MAX", "64")),
                ),
            ),
            wait_seconds=singleflight_wait_seconds,
        )
        if joined:
            render_singleflight_join_count += 1
            # The shared producer binds only its own operation/slide pointer.
            async with session_factory() as session:
                await bind_slide_render_checkpoint(
                    session,
                    identity=identity,
                    operation_claim=claim,
                    artifact=rendered,
                )
                await session.commit()
        return rendered

    if canonical_enabled:
        png_refs = list(
            await asyncio.gather(
                *(
                    render_canonical_slide(reference, slide_identities[index])
                    for index, reference in enumerate(html_refs)
                )
            )
        )
    else:
        png_refs = list(
            await asyncio.gather(*(render_fresh(reference) for reference in html_refs))
        )
    await asyncio.gather(*(stage_worker_input(reference) for reference in png_refs))
    if canonical_enabled:
        exporter_version = renderer_version
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
                    input_digest=slide_identities[index].canonical_digest,
                    stage_version=slide_identities[index].stage_version,
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
        canonical_result = await execute_canonical_export_execution(
            claim,
            execution=execution,
            session_factory=session_factory,
        )
        if canonical_result.resource_metrics:
            resource_samples.append(canonical_result.resource_metrics)
        cache_only_reuse = not resource_samples
        await worker_cache.maintain()
        return PublicWorkerArtifactResult(
            artifact=canonical_result.artifact,
            resource_metrics={
                **_resource_summary(
                    resource_samples,
                    cache_only=cache_only_reuse,
                ),
                **public_artifact_store_metrics_delta(artifact_metrics_before),
                "render_execution_count": render_execution_count,
                "render_checkpoint_reuse_count": render_checkpoint_reuse_count,
                "render_singleflight_join_count": render_singleflight_join_count,
                "render_authority_modes": sorted(render_authority_modes),
            },
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
    await stage_worker_input(bundle_ref)

    exported = await execute_isolated_export_operation(
        claim,
        input_artifact=_worker_ref(bundle_ref),
    )
    if not exported.ok:
        raise PublicExportOperationError(
            exported.error_code or "public_export_worker_failed"
        )
    if exported.resource_metrics is None:
        raise PublicExportOperationError("public_worker_resource_metrics_missing")
    resource_samples.append({"role": exported.role, **exported.resource_metrics})
    published = _public_worker_output(
        dict(exported.output_artifact or {}),
        retention_class="published",
    )
    registered = await adopt_worker_output(
        published,
        expected_kind=f"presentation-{export_format}",
        expected_media_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            if export_format == "pptx"
            else "application/pdf"
        ),
        retention_class="published",
    )
    await worker_cache.maintain()
    return PublicWorkerArtifactResult(
        artifact=registered,
        resource_metrics={
            **_resource_summary(resource_samples),
            **public_artifact_store_metrics_delta(artifact_metrics_before),
            "render_execution_count": render_execution_count,
            "render_checkpoint_reuse_count": render_checkpoint_reuse_count,
            "render_singleflight_join_count": render_singleflight_join_count,
            "render_authority_modes": sorted(render_authority_modes),
        },
    )


__all__ = [
    "PublicExportOperationError",
    "PublicWorkerArtifactResult",
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
