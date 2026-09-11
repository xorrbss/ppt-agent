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
from typing import Any, Mapping, Sequence
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel

from models.sql.public_artifact import (
    ArtifactRef as PublicArtifactRef,
    normalize_sha256,
)
from services.artifact_store import (
    configured_public_artifact_service,
    fail_export_identity,
    configured_worker_artifact_cache,
    public_artifact_store_metrics_delta,
    public_artifact_store_metrics_snapshot,
)
from services.database import async_session_maker
from services.public_render_batching import (
    MAX_PUBLIC_RENDER_BATCH_SLIDES,
    OperationRenderBatcher,
    admit_render_authority,
    gather_render_members,
)
from services.presentation_export_route_inputs import (
    resolve_authored_local_fit_receipts,
)
from services.public_export_hybrid_evidence import bounded_hybrid_worker_evidence
from services.public_export_worker_bundle import public_export_worker_bundle
from services.public_export_deadline import load_public_export_deadline_grant, worker_deadline_bounds
from services.public_export_deadline_policy import PUBLIC_EXPORT_DEADLINE_MAX_SECONDS
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
from services.trusted_executor.dispatch_evidence import private_dispatch_evidence_summary
from services.workspace_operation_handler_registry import WorkspaceOperationClaim
from utils.asset_directory_utils import resolve_image_path_to_filesystem


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
                **private_dispatch_evidence_summary([]),
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
        **private_dispatch_evidence_summary(samples),
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


#: Ships the published declared semantic graph to the isolated assembler.
#: Default off until the worker seam that forwards it exists; see
#: `build_isolated_hybrid_semantic_slides` for the exact wire shape.
PUBLIC_EXPORT_HYBRID_SEMANTIC_ENV = "PUBLIC_EXPORT_HYBRID_SEMANTIC_ENABLED"
#: The export bundle is capped at 8 MiB by the trusted transport; the declared
#: graph may take a quarter of that before the lane stays observed-only.
MAX_INLINE_DECLARED_GRAPH_BYTES = 2 * 1024 * 1024
# One number with the admission formula's ceiling and the executor's export cap.
MAX_PUBLIC_EXPORT_DEADLINE_SECONDS = PUBLIC_EXPORT_DEADLINE_MAX_SECONDS


class PublicExportHybridFallback(PublicExportOperationError):
    """The worker delivered the fidelity raster instead of an editable deck.

    The produced bytes are good; only the richer path failed. The editable
    identity is failed rather than completed so a later request retries Hybrid
    instead of inheriting a raster result forever.
    """

    def __init__(
        self,
        reason: str,
        *,
        evidence: Mapping[str, Any] | None = None,
        resource_metrics: Mapping[str, Any] | None = None,
    ) -> None:
        self.reason = reason
        #: What the assembler reported before giving up. Losing it would leave
        #: a fallback with a code and no account of itself.
        self.evidence = dict(evidence or {})
        self.resource_metrics = dict(resource_metrics or {})
        super().__init__(reason)


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


async def execute_isolated_render_operation(
    claim: WorkspaceOperationClaim,
    *,
    input_artifact: ArtifactRef,
    deadline_seconds: int | None = None,
) -> IsolatedWorkerResult:
    job = build_isolated_operation_job(
        claim,
        role="render",
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


def public_export_hybrid_semantic_enabled(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Explicit opt-in; an unset or malformed value keeps the graph out."""

    source = os.environ if environment is None else environment
    return source.get(PUBLIC_EXPORT_HYBRID_SEMANTIC_ENV, "").strip().lower() == "true"


def declared_semantic_slide_payload(
    bundle: Any, slide_index: int
) -> dict[str, Any] | None:
    """Project one published emission bundle into the assembler's wire shape.

    Returns ``None`` when the bundle carries no declared graph, which leaves
    that slide on the observed-only path rather than inventing a graph for it.
    """

    component = getattr(bundle, "semantic_component", None)
    elements = getattr(component, "elements", None)
    registry = getattr(component, "identity_registry", None)
    if not elements or registry is None:
        return None
    declared: list[dict[str, Any]] = []
    for element in elements:
        raw = element.model_dump(mode="json", by_alias=True, exclude_none=True)
        entry: dict[str, Any] = {
            "elementId": raw["elementId"],
            "kind": raw["kind"],
            # The assembler only requires a stable per-element digest to seal
            # its observation receipt; it never recomputes this server-side.
            "sourceDigest": "sha256:"
            + sha256(
                json.dumps(
                    raw, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
        }
        if raw.get("parentId") is not None:
            entry["parentId"] = raw["parentId"]
        if raw.get("childIds"):
            entry["childIds"] = list(raw["childIds"])
        if raw.get("requiredFeatures"):
            entry["requiredFeatures"] = list(raw["requiredFeatures"])
        payload = raw.get("payload")
        if raw["kind"] in {"chart", "table"} and isinstance(payload, dict):
            entry["payload"] = payload
        elif raw["kind"] == "image" and isinstance(payload, dict):
            asset_digest = payload.get("assetDigest")
            if isinstance(asset_digest, str):
                entry["assetContentDigest"] = asset_digest
        declared.append(entry)
    return {
        "schemaVersion": "public-hybrid-declared-semantic/v1",
        "slideIndex": slide_index,
        "bundleDigest": bundle.bundle_digest,
        "fontSetDigest": bundle.font_manifest.digest,
        "declaredElementIds": list(registry.active_qids),
        "declaredElements": declared,
    }


async def build_isolated_hybrid_semantic_slides(
    session: AsyncSession,
    *,
    claim: WorkspaceOperationClaim,
    tenant_id: str,
    slide_count: int,
) -> list[dict[str, Any]] | None:
    """Read the published declared graph in-process; never over HTTP.

    The isolated worker is network-none, so the controller resolves the graph
    from the emission tables and ships it as content-addressed input alongside
    the authored HTML. Returns ``None`` when the deck publishes no usable
    bundle graph, which keeps the lane observed-only instead of guessing.
    """

    from services.authored_vnext.semantic.query import get_active_authored_emission

    if claim.owner_id is None:
        return None
    try:
        active = await get_active_authored_emission(
            session,
            presentation_id=claim.presentation_id,
            owner_id=claim.owner_id,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
    except Exception:
        # A deck with no publication, or one whose emission no longer
        # validates, simply has no declared graph to ship.
        return None
    if (
        active is None
        or active.canonical_mode != "bundleGraph"
        or active.presentation_revision != claim.expected_revision
        or len(active.bundles) != slide_count
    ):
        return None
    payloads: list[dict[str, Any]] = []
    for index, bundle in enumerate(active.bundles):
        payload = declared_semantic_slide_payload(bundle, index)
        if payload is None:
            return None
        payloads.append(payload)
    encoded = len(
        json.dumps(payloads, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    )
    if encoded > MAX_INLINE_DECLARED_GRAPH_BYTES:
        # A deck whose graph would crowd out the bundle keeps the observed-only
        # path rather than failing, and the receipt records why.
        return None
    return payloads


def build_validated_checkpoint_export_bundle(
    identity: CanonicalExportIdentity,
    *,
    source_slides: Sequence[PublicArtifactRef] = (),
    semantic_slides: Sequence[Mapping[str, Any]] = (),
    export_attempt_id: str | None = None,
    renderer_source_digest: str | None = None,
    font_embedding: bool = False,
    local_fit_receipts: Sequence[Mapping[str, object]] = (),
) -> bytes:
    """Build an export input from durable validated slides without re-rendering.

    An editable-hybrid identity additionally names the authored HTML the worker
    measures. Those bytes are already bound to this identity through its scene
    digest; naming them here lets the isolated worker read them from its own
    verified transport cache instead of reaching for a database.
    """

    hybrid = identity.export_mode == "editable-hybrid"
    if hybrid and len(source_slides) != len(identity.slides):
        raise PublicExportOperationError("public_export_hybrid_sources_invalid")
    payload: dict[str, object] = {
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
    }
    if hybrid:
        payload["sourceSlides"] = [
            {
                "slideIndex": index,
                "artifactRef": _worker_ref(reference).to_wire(),
            }
            for index, reference in enumerate(source_slides)
        ]
        payload["fontEmbedding"] = bool(font_embedding)
        if local_fit_receipts:
            if not renderer_source_digest:
                raise PublicExportOperationError(
                    "public_export_local_fit_renderer_identity_invalid"
                )
            payload["rendererSourceDigest"] = renderer_source_digest
        if semantic_slides:
            if (
                len(semantic_slides) != len(identity.slides)
                or not export_attempt_id
                or not renderer_source_digest
            ):
                raise PublicExportOperationError(
                    "public_export_hybrid_semantic_binding_invalid"
                )
            payload["exportAttemptId"] = export_attempt_id
            payload["rendererSourceDigest"] = renderer_source_digest
            # The graph rides inline: it is small next to the rasters it
            # governs, and inlining binds it to the job capability by the same
            # digest as the rest of the input instead of by a second reference.
            payload["semanticSlides"] = [dict(slide) for slide in semantic_slides]
    if local_fit_receipts:
        if len(local_fit_receipts) != len(identity.slides):
            raise PublicExportOperationError(
                "public_export_local_fit_receipts_invalid"
            )
        payload["localFitReceipts"] = [
            dict(receipt) for receipt in local_fit_receipts
        ]
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def partition_public_render_references(
    references: list[PublicArtifactRef],
) -> tuple[list[PublicArtifactRef], ...]:
    """Split one operation into bounded workers, each with its own deadline."""

    if not 1 <= len(references) <= MAX_PUBLIC_RENDER_OPERATION_SLIDES:
        raise PublicExportOperationError("public_render_bundle_slide_count_invalid")
    return tuple(
        references[offset : offset + MAX_PUBLIC_RENDER_BATCH_SLIDES]
        for offset in range(0, len(references), MAX_PUBLIC_RENDER_BATCH_SLIDES)
    )


def build_public_render_bundle(references: list[PublicArtifactRef]) -> bytes:
    if not 1 <= len(references) <= MAX_PUBLIC_RENDER_BATCH_SLIDES:
        raise PublicExportOperationError("public_render_bundle_slide_count_invalid")
    return json.dumps(
        {
            "schemaVersion": "public-render-bundle/v1",
            "slides": [
                {
                    "slideIndex": index,
                    "artifactRef": _worker_ref(reference).to_wire(),
                }
                for index, reference in enumerate(references)
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def parse_public_render_result_bundle(
    payload: bytes,
    *,
    claim: WorkspaceOperationClaim,
    tenant_id: str,
    expected_count: int,
    retention_class: str,
) -> tuple[PublicArtifactRef, ...]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicExportOperationError("public_render_result_bundle_invalid") from error
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schemaVersion", "slides", "runtime"}
        or value.get("schemaVersion") != "public-render-result-bundle/v1"
        or not isinstance(value.get("slides"), list)
        or len(value["slides"]) != expected_count
        or not isinstance(value.get("runtime"), Mapping)
    ):
        raise PublicExportOperationError("public_render_result_bundle_invalid")
    expected_runtime = {
        "chromiumProcessLaunchCount": 1,
        "browserReuseCount": max(0, expected_count - 1),
        "pageSessionCount": expected_count,
    }
    if dict(value["runtime"]) != expected_runtime:
        raise PublicExportOperationError("public_render_result_runtime_invalid")
    outputs: list[PublicArtifactRef] = []
    for index, item in enumerate(value["slides"]):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"slideIndex", "artifactRef"}
            or item.get("slideIndex") != index
            or not isinstance(item.get("artifactRef"), Mapping)
        ):
            raise PublicExportOperationError("public_render_result_slide_invalid")
        output = _public_worker_output(
            dict(item["artifactRef"]), retention_class=retention_class
        )
        if (
            output.owner_id != claim.owner_id
            or output.tenant_id != tenant_id
            or output.created_by_operation_id != claim.operation_id
            or output.kind != "slide-png"
            or output.media_type != "image/png"
        ):
            raise PublicExportOperationError("public_render_result_binding_invalid")
        outputs.append(output)
    return tuple(outputs)


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
    source_slides: Sequence[PublicArtifactRef] = (),
    semantic_slides: Sequence[PublicArtifactRef] = (),
    export_attempt_id: str | None = None,
    renderer_source_digest: str | None = None,
    font_embedding: bool = False,
    local_fit_receipts: Sequence[Mapping[str, object]] = (),
    deadline_seconds: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> PublicWorkerArtifactResult:
    """Execute/join without publishing; the workspace handler owns publication.
    This boundary only durably completes the reusable export identity."""

    require_public_export_identity_v2_enabled()
    if execution.singleflight.disposition == "in_flight":
        raise PublicExportOperationError("public_export_singleflight_in_flight")
    if execution.singleflight.disposition == "completed":
        candidate = reuse_completed_export_identity(
            execution=execution,
            operation_claim=claim,
        )
        artifact_service = configured_public_artifact_service()
        async with session_factory() as session:
            # Keep the completed identity's producer immutable. The public
            # publisher requires a separately registered consumer operation
            # ref even when both refs share the same verified CAS bytes.
            registered = await artifact_service.register_consumer_ref(
                session,
                candidate.artifact,
                created_by_operation_id=claim.operation_id,
            )
            await session.commit()
        return PublicWorkerArtifactResult(
            artifact=registered,
            # No worker ran, but the identity being complete is itself the
            # statement of what these bytes are: an identity only completes
            # when the worker delivered the mode it names.
            resource_metrics={
                "public_export_worker_evidence": {
                    "requestedExportMode": execution.identity.export_mode,
                    "deliveredExportMode": execution.identity.export_mode,
                    "fallbackReason": None,
                    "reusedCompletedIdentity": True,
                }
            },
        )
    if not execution.should_execute:
        raise PublicExportOperationError("public_export_execution_disposition_invalid")

    artifact_service = configured_public_artifact_service()
    bundle = build_validated_checkpoint_export_bundle(
        execution.identity,
        source_slides=source_slides,
        semantic_slides=semantic_slides,
        export_attempt_id=export_attempt_id,
        renderer_source_digest=renderer_source_digest,
        font_embedding=font_embedding,
        local_fit_receipts=local_fit_receipts,
    )
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
        deadline_seconds=deadline_seconds,
    )
    if not exported.ok:
        raise PublicExportOperationError(
            exported.error_code or "public_export_worker_failed"
        )
    if exported.resource_metrics is None:
        raise PublicExportOperationError("public_worker_resource_metrics_missing")
    delivered_mode = str(
        exported.execution_evidence.get("deliveredExportMode")
        or execution.identity.export_mode
    )
    if delivered_mode != execution.identity.export_mode:
        reason = str(
            exported.execution_evidence.get("fallbackReason")
            or "public_export_delivered_mode_mismatch"
        )
        async with session_factory() as session:
            await fail_export_identity(
                session,
                identity_id=execution.singleflight.identity_id,
                worker_id=execution.worker_id,
                attempt_token=execution.singleflight.attempt_token or "",
                fencing_token=execution.singleflight.fencing_token,
                error_code="public_export_delivered_mode_mismatch",
            )
            await session.commit()
        raise PublicExportHybridFallback(
            reason,
            evidence=bounded_hybrid_worker_evidence(exported.execution_evidence),
            resource_metrics={"role": exported.role, **exported.resource_metrics},
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
    return PublicWorkerArtifactResult(
        artifact=registered,
        resource_metrics={
            "role": exported.role,
            **exported.resource_metrics,
            "public_export_worker_evidence": bounded_hybrid_worker_evidence(
                exported.execution_evidence
            ),
        },
    )


async def render_and_export_authored_htmls(
    claim: WorkspaceOperationClaim,
    *,
    tenant_id: str,
    htmls: list[str],
    export_format: str,
    scene_digest: str,
    quality_digest: str,
    pptx_mode: str = "fidelity",
    font_embedding: bool = False,
    export_attempt_id: str | None = None,
    expected_render_runtime_binding_digest: str | None = None,
    expected_deck_render_identity_digest: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> PublicWorkerArtifactResult:
    """Materialize authored HTML through network-none render and export roles."""

    operation_started = time.perf_counter()

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
    local_fit_receipts: tuple[Mapping[str, object], ...] = ()
    persisted_slides: list[SlideModel] = []
    if export_format == "pptx":
        async with session_factory() as session:
            presentation = await session.get(
                PresentationModel, claim.presentation_id
            )
            persisted_slides = (
                await session.execute(
                    select(SlideModel)
                    .where(SlideModel.presentation == claim.presentation_id)
                    .order_by(SlideModel.index, SlideModel.id)
                )
            ).scalars().all()
        if presentation is None:
            raise PublicExportOperationError("presentation_not_found")
        local_fit_receipts = resolve_authored_local_fit_receipts(
            presentation, persisted_slides
        )
        if local_fit_receipts and [
            slide.html_content for slide in persisted_slides
        ] != htmls:
            raise PublicExportOperationError(
                "public_export_local_fit_source_mismatch"
            )
    local_fit_receipt_digest = (
        sha256(
            json.dumps(
                list(local_fit_receipts),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if local_fit_receipts
        else ""
    )

    if pptx_mode not in {"fidelity", "hybrid"}:
        raise PublicExportOperationError("public_export_pptx_mode_invalid")
    # One deadline: every worker below (render batches, hybrid or raster
    # export) is granted what is left of the admission the lease fences on.
    grant = await load_public_export_deadline_grant(
        session_factory, operation_id=claim.operation_id
    )
    hybrid_requested = pptx_mode == "hybrid" and export_format == "pptx"
    if font_embedding and not hybrid_requested:
        raise PublicExportOperationError("public_export_font_embedding_invalid")
    # What the caller asked for, kept separate from what is still being
    # attempted after a fallback, so the receipt can report both truthfully.
    requested_export_mode = (
        "editable-hybrid" if hybrid_requested else "fidelity-raster"
    )
    hybrid_degraded_reason: str | None = None
    hybrid_attempt_evidence: dict[str, Any] = {}

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
        # TrustedWorkerWireTransport publishes every successful output to the
        # configured durable artifact authority before returning. Register the
        # exact worker reference there; the local cache is input staging only
        # and is deliberately not an S3 output mirror.
        async with session_factory() as session:
            adopted = await artifact_service.register_ref(session, reference)
            await session.commit()
            return adopted

    html_persist_started = time.perf_counter()
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
    html_persist_millis = int((time.perf_counter() - html_persist_started) * 1000)

    semantic_slides: list[dict[str, Any]] = []
    semantic_digest_binding: str | None = None
    if hybrid_requested and public_export_hybrid_semantic_enabled():
        async with session_factory() as session:
            declared = await build_isolated_hybrid_semantic_slides(
                session,
                claim=claim,
                tenant_id=tenant_id,
                slide_count=len(htmls),
            )
        if declared is None:
            hybrid_degraded_reason = "hybrid_declared_graph_unavailable"
        else:
            semantic_slides = declared
            # The declared graph changes what the assembler may promote, so it
            # is part of the export's identity. Without this, a graph-ful
            # request could reuse a graph-less completed result.
            semantic_digest_binding = sha256(
                json.dumps(
                    semantic_slides, ensure_ascii=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
    try:
        configured_concurrency = int(
            os.getenv("PUBLIC_RENDER_MAX_CONCURRENCY", "2").strip()
        )
    except ValueError as error:
        raise PublicExportOperationError(
            "public_render_concurrency_invalid"
        ) from error
    # The trusted scheduler's operation/presentation cap is two.  Keep the
    # controller at or below that boundary so "peak" reflects admitted worker
    # dispatches rather than a larger set waiting inside the dispatcher.
    concurrency = max(1, min(2, configured_concurrency))
    gate = asyncio.Semaphore(concurrency)
    resource_samples: list[Mapping[str, Any]] = []
    render_active = 0
    render_peak = 0
    render_gate_wait_millis = 0
    render_scheduler_queue_wait_millis = 0
    render_worker_millis = 0
    artifact_stage_millis = 0

    canonical_enabled = public_export_identity_v2_enabled()
    if canonical_enabled and claim.expected_revision is None:
        raise PublicExportOperationError("public_export_revision_required")
    render_execution_count = 0
    render_batch_execution_count = 0
    chromium_process_launch_count = 0
    browser_reuse_count = 0
    render_checkpoint_reuse_count = 0
    render_singleflight_join_count = 0
    render_authority_modes: set[str] = set()
    approved_png_refs: list[PublicArtifactRef] = []
    if local_fit_receipts:
        async with session_factory() as session:
            for _index, (slide, receipt) in enumerate(
                zip(persisted_slides, local_fit_receipts, strict=True)
            ):
                properties = (
                    slide.properties
                    if isinstance(slide.properties, Mapping)
                    else {}
                )
                content = (
                    slide.content
                    if isinstance(slide.content, Mapping)
                    else {}
                )
                image_ref = properties.get("image") or content.get("image")
                if not isinstance(image_ref, str) or not image_ref.strip():
                    raise PublicExportOperationError(
                        "public_export_approved_png_unavailable"
                    )
                served_ref = image_ref.strip()
                if not served_ref.startswith(("/app_data/", "/static/")):
                    served_ref = (
                        "/app_data/images/" + served_ref.lstrip("/\\")
                    )
                image_path = resolve_image_path_to_filesystem(served_ref)
                if image_path is None:
                    raise PublicExportOperationError(
                        "public_export_approved_png_unavailable"
                    )
                png = pathlib.Path(image_path).read_bytes()
                actual_digest = "sha256:" + sha256(png).hexdigest()
                if (
                    not png.startswith(b"\x89PNG\r\n\x1a\n")
                    or receipt.get("png_digest") != actual_digest
                ):
                    raise PublicExportOperationError(
                        "public_export_approved_png_identity_mismatch"
                    )
                approved_png_refs.append(
                    await artifact_service.persist_bytes(
                        session,
                        owner_id=claim.owner_id,
                        tenant_id=tenant_id,
                        kind="slide-png",
                        payload=png,
                        media_type="image/png",
                        created_by_operation_id=claim.operation_id,
                        retention_class=(
                            "checkpoint" if canonical_enabled else "ephemeral"
                        ),
                    )
                )
            await session.commit()
    singleflight_wait_seconds = max(
        1.0,
        min(
            600.0,
            float(os.getenv("PUBLIC_RENDER_SINGLEFLIGHT_WAIT_SECONDS", "125")),
        ),
    )

    async def render_fresh_batch(
        references: list[PublicArtifactRef],
    ) -> tuple[PublicArtifactRef, ...]:
        nonlocal artifact_stage_millis
        nonlocal render_active
        nonlocal render_batch_execution_count
        nonlocal browser_reuse_count
        nonlocal chromium_process_launch_count
        nonlocal render_execution_count
        nonlocal render_gate_wait_millis
        nonlocal render_peak
        nonlocal render_scheduler_queue_wait_millis
        nonlocal render_worker_millis
        if not references:
            return ()
        bundle = build_public_render_bundle(references)
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
        stage_started = time.perf_counter()
        await asyncio.gather(
            stage_worker_input(bundle_ref),
            *(stage_worker_input(reference) for reference in references),
        )
        artifact_stage_millis += int(
            (time.perf_counter() - stage_started) * 1000
        )
        queued_at = time.perf_counter()
        async with gate:
            render_gate_wait_millis += int(
                (time.perf_counter() - queued_at) * 1000
            )
            render_active += 1
            render_peak = max(render_peak, render_active)
            worker_started = time.perf_counter()
            try:
                result = await execute_isolated_render_operation(
                    claim,
                    input_artifact=_worker_ref(bundle_ref),
                    deadline_seconds=grant.render_batch_seconds(),
                )
            finally:
                render_worker_millis += int(
                    (time.perf_counter() - worker_started) * 1000
                )
                render_active -= 1
        render_execution_count += len(references)
        render_batch_execution_count += 1
        if not result.ok:
            raise PublicExportOperationError(
                result.error_code or "public_render_worker_failed"
            )
        if result.resource_metrics is None:
            raise PublicExportOperationError("public_worker_resource_metrics_missing")
        raw_scheduler_wait = result.resource_metrics.get(
            "trustedSchedulerQueueWaitMs", 0
        )
        if isinstance(raw_scheduler_wait, (int, float)) and not isinstance(
            raw_scheduler_wait, bool
        ):
            render_scheduler_queue_wait_millis += max(
                0, int(raw_scheduler_wait)
            )
        raw_launches = result.resource_metrics.get("chromiumProcessLaunchCount", 0)
        raw_reuse = result.resource_metrics.get("browserReuseCount", 0)
        if (
            isinstance(raw_launches, int)
            and not isinstance(raw_launches, bool)
            and raw_launches == 1
        ):
            chromium_process_launch_count += raw_launches
        else:
            raise PublicExportOperationError("public_render_runtime_metrics_invalid")
        if (
            isinstance(raw_reuse, int)
            and not isinstance(raw_reuse, bool)
            and raw_reuse == len(references) - 1
        ):
            browser_reuse_count += raw_reuse
        else:
            raise PublicExportOperationError("public_render_runtime_metrics_invalid")
        resource_samples.append({"role": result.role, **result.resource_metrics})
        manifest = _public_worker_output(
            dict(result.output_artifact or {}),
            retention_class="ephemeral",
        )
        if (
            manifest.kind != "export-bundle"
            or manifest.media_type
            != "application/vnd.presenton.export-bundle+json"
        ):
            raise PublicExportOperationError("public_render_result_bundle_invalid")
        payload = await artifact_service.read_ref_bytes(manifest)
        rendered = parse_public_render_result_bundle(
            payload,
            claim=claim,
            tenant_id=tenant_id,
            expected_count=len(references),
            retention_class=("checkpoint" if canonical_enabled else "ephemeral"),
        )
        return tuple(
            await asyncio.gather(
                *(
                    adopt_worker_output(
                        reference,
                        expected_kind="slide-png",
                        expected_media_type="image/png",
                        retention_class=(
                            "checkpoint" if canonical_enabled else "ephemeral"
                        ),
                    )
                    for reference in rendered
                )
            )
        )

    render_batcher = OperationRenderBatcher(
        participant_count=len(html_refs),
        render_batch=render_fresh_batch,
        partition=partition_public_render_references,
        error_type=PublicExportOperationError,
    )

    async def render_fresh(index: int, reference: PublicArtifactRef) -> PublicArtifactRef:
        return await render_batcher.render(index, reference)

    semantic_digest = sha256(
        json.dumps(
            [
                json.dumps(htmls, ensure_ascii=False, separators=(",", ":")),
                semantic_digest_binding or "",
                local_fit_receipt_digest,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    render_runtime_binding = public_render_runtime_binding()
    render_runtime_binding_digest = public_render_runtime_binding_digest(
        render_runtime_binding
    )
    deck_render_identity_digest = public_deck_render_identity_digest(
        htmls,
        runtime_binding=render_runtime_binding,
    )
    if (
        expected_render_runtime_binding_digest is not None
        and expected_render_runtime_binding_digest
        != render_runtime_binding_digest
    ):
        raise PublicExportOperationError(
            "release_quality_review_runtime_changed"
        )
    if (
        expected_deck_render_identity_digest is not None
        and expected_deck_render_identity_digest != deck_render_identity_digest
    ):
        raise PublicExportOperationError(
            "release_quality_review_render_identity_changed"
        )
    checkpoint_reuse_eligible = bool(
        render_runtime_binding.get("checkpointReuseEligible") is True
    )
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
                renderer_version=public_slide_renderer_version(
                    htmls[index], runtime_binding=render_runtime_binding
                ),
            )
            for index, reference in enumerate(html_refs)
        )
        if canonical_enabled and claim.expected_revision is not None
        else ()
    )

    async def render_canonical_slide(
        index: int,
        reference: PublicArtifactRef,
        identity: CanonicalSlideRenderIdentity,
    ) -> PublicArtifactRef:
        nonlocal render_checkpoint_reuse_count, render_singleflight_join_count
        if not checkpoint_reuse_eligible:
            # External pins are controller policy until the worker returns a
            # signed runtime attestation.  Neither durable reuse nor
            # process-local singleflight may share pixels across mixed workers.
            render_authority_modes.add("fresh-unattested-runtime")
            return await render_fresh(index, reference)
        reusable = None
        if checkpoint_reuse_eligible:
            async with session_factory() as session:
                reusable = await find_reusable_slide_render_checkpoint(
                    session,
                    identity=identity,
                )
        if reusable is not None:
            render_batcher.settle(index)
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
                async with admit_render_authority(
                    authority_session,
                    on_wait=lambda: render_batcher.settle(index),
                    wait_seconds=singleflight_wait_seconds,
                ), acquire_slide_render_authority(
                    authority_session,
                    identity=identity,
                    wait_seconds=singleflight_wait_seconds,
                    on_authority_wait=lambda: render_batcher.settle(index),
                ) as authority_mode:
                    render_authority_modes.add(authority_mode)
                    # Another process may have committed while this caller
                    # waited for the DB authority. Recheck before Chromium.
                    authoritative_reuse = None
                    if checkpoint_reuse_eligible:
                        async with session_factory() as lookup_session:
                            authoritative_reuse = await find_reusable_slide_render_checkpoint(
                                lookup_session,
                                identity=identity,
                            )
                    if authoritative_reuse is not None:
                        render_batcher.settle(index)
                        render_checkpoint_reuse_count += 1
                        rendered = authoritative_reuse
                    else:
                        rendered = await render_fresh(index, reference)
                    # Commit the checkpoint before releasing the transaction-
                    # scoped authority so another replica cannot rerender.
                    if checkpoint_reuse_eligible:
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
            on_follower_wait=lambda: render_batcher.settle(index),
        )
        if joined and checkpoint_reuse_eligible:
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

    render_batch_started = time.perf_counter()
    if approved_png_refs:
        png_refs = approved_png_refs
        await render_batcher.close()
        render_authority_modes.add("accepted-local-fit-artifact")
    else:
        try:
            if canonical_enabled:
                png_refs = await gather_render_members(
                    [render_canonical_slide(index, reference, slide_identities[index])
                     for index, reference in enumerate(html_refs)]
                )
            else:
                png_refs = await gather_render_members(
                    [render_fresh(index, reference)
                     for index, reference in enumerate(html_refs)]
                )
        finally:
            # Do not let an operation cancellation/fence failure leave the
            # operation-scoped drain task or an isolated Chromium child behind.
            await render_batcher.close()
    render_wall_millis = int((time.perf_counter() - render_batch_started) * 1000)
    png_stage_started = time.perf_counter()
    await asyncio.gather(*(stage_worker_input(reference) for reference in png_refs))
    artifact_stage_millis += int((time.perf_counter() - png_stage_started) * 1000)
    worker_bundle = public_export_worker_bundle(PUBLIC_RENDER_WORKER_PROTOCOL_VERSION)

    def metric_millis(metrics: Mapping[str, Any], name: str) -> int:
        raw = metrics.get(name, 0)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return 0
        return max(0, int(raw))

    def execution_receipt(
        *, export_millis: int, export_scheduler_queue_wait_millis: int
    ) -> dict[str, Any]:
        final_runtime_binding = public_render_runtime_binding()
        final_runtime_binding_digest = public_render_runtime_binding_digest(
            final_runtime_binding
        )
        final_deck_render_identity_digest = public_deck_render_identity_digest(
            htmls,
            runtime_binding=final_runtime_binding,
        )
        if (
            expected_render_runtime_binding_digest is not None
            and expected_render_runtime_binding_digest
            != final_runtime_binding_digest
        ):
            raise PublicExportOperationError(
                "release_quality_review_runtime_changed"
            )
        if (
            expected_deck_render_identity_digest is not None
            and expected_deck_render_identity_digest
            != final_deck_render_identity_digest
        ):
            raise PublicExportOperationError(
                "release_quality_review_render_identity_changed"
            )
        return {
            "schemaVersion": "public-export-execution-receipt/v1",
            "slideCount": len(html_refs),
            "renderConcurrencyLimit": concurrency,
            "renderConcurrencyPeak": render_peak,
            "renderGateWaitMillis": render_gate_wait_millis,
            "renderSchedulerQueueWaitMillis": render_scheduler_queue_wait_millis,
            "renderWorkerCumulativeMillis": render_worker_millis,
            "renderWallMillis": render_wall_millis,
            "artifactStageMillis": artifact_stage_millis,
            "htmlPersistMillis": html_persist_millis,
            "exportWorkerMillis": export_millis,
            "exportSchedulerQueueWaitMillis": export_scheduler_queue_wait_millis,
            "operationWallMillis": int(
                (time.perf_counter() - operation_started) * 1000
            ),
            "renderExecutionCount": render_execution_count,
            "renderBatchExecutionCount": render_batch_execution_count,
            "chromiumProcessLaunchCount": chromium_process_launch_count,
            "browserReuseCount": browser_reuse_count,
            "renderCheckpointReuseCount": render_checkpoint_reuse_count,
            "renderSingleflightJoinCount": render_singleflight_join_count,
            "renderRuntimeIdentity": {
                **final_runtime_binding,
                "bindingDigest": final_runtime_binding_digest,
            },
            "deckRenderIdentityDigest": final_deck_render_identity_digest,
            "exporterWorkerBundle": worker_bundle.evidence(),
        }

    if hybrid_requested and not canonical_enabled:
        # The hybrid input is addressed by the canonical export identity. Never
        # fail the request over a controller configuration gap: deliver the
        # fidelity raster and say why it is not editable.
        hybrid_requested = False
        hybrid_degraded_reason = "hybrid_requires_export_identity_v2"
    if canonical_enabled:
        def canonical_identity(*, hybrid: bool) -> CanonicalExportIdentity:
            return CanonicalExportIdentity.build(
                owner_id=claim.owner_id,
                tenant_id=tenant_id,
                presentation_id=claim.presentation_id,
                source_revision=claim.expected_revision,
                export_format=export_format,  # type: ignore[arg-type]
                export_mode=(
                    "editable-hybrid" if hybrid else "fidelity-raster"
                ),
                render_adapter=(
                    "public-isolated-hybrid"
                    if hybrid
                    else "public-isolated-fidelity"
                ),
                exporter_version=PUBLIC_RENDER_WORKER_PROTOCOL_VERSION,
                evidence=ExportEvidenceDigests(
                    scene_digest=normalized_scene_digest,
                    semantic_digest=semantic_digest,
                    quality_digest=normalized_quality_digest,
                    exporter_digest=worker_bundle.digest,
                    font_manifest_digest=sha256(
                        (
                            f"editable-hybrid:font-embedding={'on' if font_embedding else 'off'}"
                            if hybrid
                            else "fidelity-raster:no-font-manifest"
                        ).encode("utf-8")
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
                                    "localFitAcceptanceKey": (
                                        local_fit_receipts[index].get(
                                            "acceptanceKey"
                                        )
                                        if local_fit_receipts
                                        else None
                                    ),
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                    )
                    for index, reference in enumerate(png_refs)
                ),
            )

        async def run_canonical(*, hybrid: bool) -> PublicWorkerArtifactResult:
            async with session_factory() as session:
                execution = await prepare_canonical_export_execution(
                    session,
                    claim,
                    identity=canonical_identity(hybrid=hybrid),
                    worker_id=(
                        os.getenv("PUBLIC_WORKER_NODE_ID") or "export-controller"
                    ),
                )
                await session.commit()
            return await execute_canonical_export_execution(
                claim,
                execution=execution,
                source_slides=html_refs if hybrid else (),
                semantic_slides=semantic_slides if hybrid else (),
                export_attempt_id=(
                    str(export_attempt_id)
                    if hybrid and export_attempt_id is not None
                    else None
                ),
                renderer_source_digest=(
                    render_runtime_binding_digest if hybrid else None
                ),
                font_embedding=font_embedding if hybrid else False,
                local_fit_receipts=local_fit_receipts,
                deadline_seconds=grant.export_worker_seconds(),
                session_factory=session_factory,
            )

        export_started = time.perf_counter()
        try:
            canonical_result = await run_canonical(hybrid=hybrid_requested)
        except PublicExportHybridFallback as fallback:
            # The worker already produced a good raster deck; publish it under
            # the identity that honestly describes it, and keep the abandoned
            # attempt's account of itself so the receipt can explain the
            # downgrade instead of only naming it.
            hybrid_degraded_reason = fallback.reason
            hybrid_attempt_evidence = fallback.evidence
            if fallback.resource_metrics:
                resource_samples.append(fallback.resource_metrics)
            hybrid_requested = False
            canonical_result = await run_canonical(hybrid=False)
        # A completed identity only verifies/registers the consumer ref, and
        # reports only what the bytes are. Its elapsed work belongs to
        # operationWallMillis, not to a worker that never ran.
        worker_ran = "role" in canonical_result.resource_metrics
        export_millis = (
            int((time.perf_counter() - export_started) * 1000) if worker_ran else 0
        )
        if worker_ran:
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
                "requested_export_mode": requested_export_mode,
                "public_export_worker_evidence": dict(
                    canonical_result.resource_metrics.get(
                        "public_export_worker_evidence", {}
                    )
                    or {}
                ),
                **(
                    {"hybrid_degraded_reason": hybrid_degraded_reason}
                    if hybrid_degraded_reason
                    else {}
                ),
                **(
                    {"hybrid_attempt_evidence": hybrid_attempt_evidence}
                    if hybrid_attempt_evidence
                    else {}
                ),
                "public_export_execution_receipt": execution_receipt(
                    export_millis=export_millis,
                    export_scheduler_queue_wait_millis=metric_millis(
                        canonical_result.resource_metrics,
                        "trustedSchedulerQueueWaitMs",
                    ),
                ),
            },
        )
    bundle = json.dumps(
        {
            "schemaVersion": "public-export-bundle/v1",
            "exportFormat": export_format,
            "slides": [
                _worker_ref(reference).to_wire() for reference in png_refs
            ],
            **(
                {
                    "localFitReceipts": [
                        dict(receipt) for receipt in local_fit_receipts
                    ]
                }
                if local_fit_receipts
                else {}
            ),
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

    export_started = time.perf_counter()
    exported = await execute_isolated_export_operation(
        claim,
        input_artifact=_worker_ref(bundle_ref),
        deadline_seconds=grant.export_worker_seconds(),
    )
    export_millis = int((time.perf_counter() - export_started) * 1000)
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
            "requested_export_mode": requested_export_mode,
            "public_export_worker_evidence": dict(exported.execution_evidence),
            **(
                {"hybrid_degraded_reason": hybrid_degraded_reason}
                if hybrid_degraded_reason
                else {}
            ),
            "public_export_execution_receipt": execution_receipt(
                export_millis=export_millis,
                export_scheduler_queue_wait_millis=metric_millis(
                    exported.resource_metrics,
                    "trustedSchedulerQueueWaitMs",
                ),
            ),
        },
    )


__all__ = [
    "PublicExportHybridFallback",
    "PublicExportOperationError",
    "PublicWorkerArtifactResult",
    "MAX_PUBLIC_RENDER_BATCH_SLIDES",
    "MAX_PUBLIC_EXPORT_DEADLINE_SECONDS",
    "MAX_PUBLIC_RENDER_OPERATION_SLIDES",
    "PUBLIC_RENDER_WORKER_PROTOCOL_VERSION",
    "PUBLIC_RENDER_WORKER_FONT_DIGEST_ENV",
    "PUBLIC_RENDER_WORKER_IMAGE_DIGEST_ENV",
    "PUBLIC_RENDER_TEST_FONT_DIGEST_ENV",
    "PUBLIC_SLIDE_RENDERER_IDENTITY_VERSION",
    "build_public_render_bundle",
    "build_validated_checkpoint_export_bundle",
    "build_isolated_operation_job",
    "execute_isolated_export_operation",
    "execute_isolated_render_operation",
    "execute_and_publish_canonical_export_execution",
    "execute_canonical_export_execution",
    "load_worker_capability_signing_key",
    "partition_public_render_references",
    "prepare_canonical_export_execution",
    "public_deck_render_identity_digest",
    "public_render_runtime_binding",
    "public_render_runtime_binding_digest",
    "public_release_review_deck_render_identity_digest",
    "public_release_review_runtime_binding",
    "public_slide_renderer_version",
    "render_and_export_authored_htmls",
    "resume_canonical_export_execution",
]
