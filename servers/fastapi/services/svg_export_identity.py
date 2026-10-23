"""SVG exports through the digest-bound export identity (``svg-native``).

The same single-flight authority authored exports use
(``services/public_export_identity.py`` + ``services/public_export_join.py``),
joined through the unchanged ``CanonicalExportIdentity.export_key()``:

1. each slide's svg source is persisted as a ``slide-svg`` artifact at
   ``checkpoint`` retention and becomes a ``SlideCheckpointArtifact``;
2. the five evidence digests are built from pre-export inputs only
   (``services.generation_core.export.svg_evidence``);
3. ``prepare_canonical_export_execution`` persists the identity checkpoint,
   binds the slide checkpoints to the live operation fence and claims;
4. ``resolve_export_join`` resolves every non-leading disposition: a completed
   identity hands back a consumer ref to its immutable bytes (no worker, no
   asset derivation), a live one is waited on;
5. only a ``claimed`` identity runs ``execute`` -- the existing svg worker path
   -- under the identity lease keeper, then completes the identity.

Publication stays with the caller (``publish_workspace_export_artifact``), the
same split as authored.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
from typing import Any, Awaitable, Callable, Sequence
from time import monotonic
from sqlalchemy import select

from models.sql.public_artifact import ArtifactRef, PublicArtifact, PublicExportIdentity
from models.sql.workspace import WorkspaceOperation
from models.sql.public_admission import PublicOperationAdmission
from services.artifact_store import configured_public_artifact_service
from services.generation_core.export.svg_evidence import (
    SVG_SLIDE_CHECKPOINT_STAGE,
    build_svg_export_evidence_digests,
    svg_quality_inputs,
    svg_slide_checkpoint_digests,
    svg_slide_quality,
)
from services.public_export_identity import (
    CanonicalExportIdentity,
    SlideCheckpointArtifact,
    complete_canonical_export_identity,
    checkpoint_canonical_export_slides,
    restore_canonical_export_identity,
)
from services.public_export_errors import PublicExportOperationError
from services.public_export_join import export_identity_lease_keeper, resolve_export_join
from services.public_export_operation import (
    PublicWorkerArtifactResult,
    prepare_canonical_export_execution,
)
from services.svg_contract import SVG_CONTRACT_VERSION
from utils.env_settings.public_service import get_public_worker_node_id
from workers.public_svg_export_worker import CONVERTER_VERSION, converter_digest

SVG_EXPORT_OUTPUT_MODE = "svg-native"
SVG_RENDER_ADAPTER = "public-isolated-svg"

#: Runs the svg worker for a claimed identity, given the slide checkpoint refs.
SvgExportExecutor = Callable[[Sequence[ArtifactRef]], Awaitable[PublicWorkerArtifactResult]]


@dataclass(frozen=True, slots=True)
class SvgExportRequest:
    """Everything an svg export's bytes and quality verdict depend on, pre-export."""

    export_format: str
    svgs: Sequence[str]
    notes: Sequence[str]
    canvas: dict
    slides: Sequence[Any]
    source_assets: Sequence[Any] | None = None
    source_preservation: Sequence[Any] | None = None
    source_manifest: Any = None
    source_manifest_encoding: Any = None
    native_objects: Sequence[Any] | None = None
    font_embedding: bool = False
    advisory: Any = None
    quality_policy: str | None = None

    @property
    def bound_native_objects(self) -> Sequence[Any] | None:
        # A PDF bundle never carries NativeObjects, so they never split its identity.
        return self.native_objects if self.export_format == "pptx" else None


async def persist_svg_slide_checkpoints(claim, *, tenant_id, request: SvgExportRequest,
                                        session_factory) -> tuple[SlideCheckpointArtifact, ...]:
    service = configured_public_artifact_service()
    assets = request.source_assets or [None] * len(request.svgs)
    native = request.bound_native_objects or [None] * len(request.svgs)
    preservation = request.source_preservation or [None] * len(request.svgs)
    checkpoints = []
    async with session_factory() as session:
        for index, source in enumerate(request.svgs):
            ref = await service.persist_bytes(
                session, owner_id=claim.owner_id, tenant_id=tenant_id, kind="slide-svg",
                payload=source.encode(), media_type="image/svg+xml",
                created_by_operation_id=claim.operation_id, retention_class="checkpoint")
            input_digest, validation_digest = svg_slide_checkpoint_digests(
                index=index, svg_sha256=ref.sha256, note=request.notes[index],
                asset_manifest=assets[index], native_object=native[index],
                source_preservation=preservation[index], quality=svg_slide_quality(request.slides[index]))
            checkpoints.append(SlideCheckpointArtifact(
                slide_index=index, artifact=ref, validation_digest=validation_digest,
                input_digest=input_digest, stage_version=SVG_SLIDE_CHECKPOINT_STAGE))
        await session.commit()
    return tuple(checkpoints)


def build_svg_export_identity(claim, *, tenant_id, request: SvgExportRequest,
                              checkpoints: Sequence[SlideCheckpointArtifact]) -> CanonicalExportIdentity:
    from services.svg_export_whole_page_verifier import whole_page_verifier_digest

    quality_inputs = svg_quality_inputs(slides=request.slides, advisory=request.advisory,
                                        quality_policy=request.quality_policy)
    quality_inputs = {**quality_inputs, "wholePageVerifierDigest": whole_page_verifier_digest()}
    header_bindings = [
        ((slide.content or {}).get("svgReceipt") or {}).get("system_furniture") for slide in request.slides]
    if any(isinstance(value, dict) and
           ((value.get("generated_common_header") or {}).get("contract") or {}).get("version") == "svg-common-header/v2"
           for value in header_bindings):
        from services.svg_common_header_export_evidence import common_header_verifier_digest
        quality_inputs = {**quality_inputs, "commonHeaderVerifierDigest": common_header_verifier_digest(),
                          "commonHeaderBindings": header_bindings}
    if request.source_manifest is not None:
        from services.svg_export_bundle import sealed_manifest_digest
        quality_inputs = {**quality_inputs,
            "sealedSourceManifestDigest": sealed_manifest_digest(
                request.source_manifest, request.source_manifest_encoding)}
    evidence = build_svg_export_evidence_digests(
        svgs=request.svgs, notes=request.notes, canvas=request.canvas,
        source_assets=request.source_assets, source_preservation=request.source_preservation,
        native_objects=request.bound_native_objects,
        quality_inputs=quality_inputs,
        converter_version=CONVERTER_VERSION, converter_digest=converter_digest(),
        contract_version=SVG_CONTRACT_VERSION, font_embedding=request.font_embedding)
    return CanonicalExportIdentity.build(
        owner_id=claim.owner_id, tenant_id=tenant_id, presentation_id=claim.presentation_id,
        source_revision=claim.expected_revision, export_format=request.export_format,
        export_mode=SVG_EXPORT_OUTPUT_MODE, render_adapter=SVG_RENDER_ADAPTER,
        exporter_version=CONVERTER_VERSION, evidence=evidence, slides=tuple(checkpoints))


async def _producer_readback(session_factory, *, producer_id, claim, tenant_id, artifact, required: bool,
                             identity=None, producer_identity=None, include_worker_evidence=False) -> dict | None:
    """The leading export's worker readback, which the reused bytes still carry.

    A reuse runs no worker, but the source-preserving receipt judges content
    integrity from the export readback. The identity binds the same scene, so
    the producer's readback describes these exact bytes; only that one key is
    copied, and only from a succeeded producer. The identity completes before
    its producer publishes, so a joiner that needs the readback while the
    producer is still publishing retries (as the hybrid editability evidence
    does) instead of judging the export without it. A producer that is this
    operation itself (a retry after completion) has no stored evidence; the
    receipt then records the readback domain as incomplete.
    """
    if producer_id is None or producer_id == claim.operation_id:
        return None
    async with session_factory() as session:
        producer = await session.get(WorkspaceOperation, producer_id)
        admission = await session.scalar(select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == producer_id,
            PublicOperationAdmission.owner_id == claim.owner_id,
            PublicOperationAdmission.tenant_id == tenant_id))
        identity_row = producer_artifact = None
        if include_worker_evidence and identity is not None:
            producer_identity = producer_identity or identity
            identity_row = await session.scalar(select(PublicExportIdentity).where(
                PublicExportIdentity.canonical_digest == producer_identity.canonical_digest,
                PublicExportIdentity.owner_id == claim.owner_id,
                PublicExportIdentity.tenant_id == tenant_id,
                PublicExportIdentity.presentation_id == claim.presentation_id,
                PublicExportIdentity.revision == claim.expected_revision,
                PublicExportIdentity.claimed_by_operation_id == producer_id,
                PublicExportIdentity.state == "succeeded"))
            if identity_row is not None and identity_row.result_artifact_id is not None:
                producer_artifact = await session.get(PublicArtifact, identity_row.result_artifact_id)
    if producer is None or producer.state != "succeeded":
        if required:
            raise PublicExportOperationError("public_export_readback_evidence_pending")
        return None
    if (producer.owner_id != claim.owner_id or producer.owner_scope != claim.owner_scope
            or producer.presentation_id != claim.presentation_id
            or producer.expected_revision != claim.expected_revision or admission is None
            or artifact.owner_id != claim.owner_id or artifact.tenant_id != tenant_id):
        raise PublicExportOperationError("svg_export_readback_binding_invalid")
    result = producer.result if isinstance(producer.result, dict) else {}
    receipt = result.get("quality_receipt")
    if isinstance(receipt, dict) and (
            receipt.get("operationId") != str(producer_id)
            or receipt.get("presentationId") != str(claim.presentation_id)
            or receipt.get("sourceRevision") != claim.expected_revision
            or receipt.get("artifactDigest") != "sha256:" + artifact.sha256):
        raise PublicExportOperationError("svg_export_readback_binding_invalid")
    evidence = result.get("public_export_worker_evidence")
    readback = evidence.get("structuralReadback") if isinstance(evidence, dict) else None
    if include_worker_evidence:
        if (identity_row is None or producer_artifact is None
                or producer_artifact.owner_id != claim.owner_id or producer_artifact.tenant_id != tenant_id
                or producer_artifact.created_by_operation_id != producer_id
                or producer_artifact.sha256 != artifact.sha256 or producer_artifact.kind != artifact.kind
                or producer_artifact.storage_key != artifact.storage_key
                or producer_artifact.size_bytes != artifact.size_bytes
                or producer_artifact.media_type != artifact.media_type or not isinstance(evidence, dict)
                or not isinstance(readback, dict)):
            raise PublicExportOperationError("svg_export_readback_binding_invalid")
        if producer_identity.canonical_digest != identity.canonical_digest:
            render = readback.get("finalPptxRender")
            native = readback.get("genericNativeCoverageOoxml")
            # Cross-policy provenance comes directly from a physical worker,
            # never from a previous consumer's already wrapped readback.
            if (evidence.get("reusedCompletedIdentity") is True or evidence.get("reuseProducerBinding")
                    or not isinstance(render, dict) or render.get("operationId") != str(producer_id)
                    or (isinstance(native, dict) and native.get("operationId") != str(producer_id))):
                raise PublicExportOperationError("svg_export_readback_binding_invalid")
        from utils.hashing import canonical_json_sha256

        binding = dict(schemaVersion="svg-export-reuse-producer-binding/v1",
            producerOperationId=str(producer_id), consumerOperationId=str(claim.operation_id),
            canonicalIdentityDigest=identity.canonical_digest, ownerId=str(claim.owner_id),
            producerCanonicalIdentityDigest=producer_identity.canonical_digest,
            ownerScope=str(claim.owner_scope), tenantId=tenant_id,
            presentationId=str(claim.presentation_id), sourceRevision=claim.expected_revision,
            artifactDigest="sha256:" + artifact.sha256,
            producerWorkerEvidenceDigest="sha256:" + canonical_json_sha256(evidence),
            producerArtifactRef=producer_artifact.to_ref().model_dump(mode="json", by_alias=True),
            consumerArtifactRef=artifact.model_dump(mode="json", by_alias=True))
        preserved = deepcopy(evidence)
        preserved["reuseProducerWorkerEvidence"] = deepcopy(evidence)
        preserved["reuseProducerBinding"] = {
            **binding, "digest": "sha256:" + canonical_json_sha256(binding)}
        return preserved
    return dict(readback) if isinstance(readback, dict) else None


async def _release_predecessor(claim, *, tenant_id, request, checkpoints, identity,
                               session_factory, execution=None, consumer_artifact=None):
    """Reuse only a completed, exact pre-release identity; release quality is judged later."""
    if request.quality_policy != "release-v1" or request.export_format != "pptx":
        return None
    service = configured_public_artifact_service()
    for policy in ("interactive-v1", None):
        predecessor = build_svg_export_identity(claim, tenant_id=tenant_id,
            request=replace(request, quality_policy=policy), checkpoints=checkpoints)
        async with session_factory() as session:
            row = await session.scalar(select(PublicExportIdentity).where(
                PublicExportIdentity.canonical_digest == predecessor.canonical_digest,
                PublicExportIdentity.owner_id == claim.owner_id,
                PublicExportIdentity.tenant_id == tenant_id,
                PublicExportIdentity.presentation_id == claim.presentation_id,
                PublicExportIdentity.revision == claim.expected_revision,
                PublicExportIdentity.identity_contract_version == "digest-bound/v2",
                PublicExportIdentity.reuse_eligible.is_(True),
                PublicExportIdentity.state == "succeeded"))
            if row is None:
                continue
            producer = await session.get(WorkspaceOperation, row.claimed_by_operation_id)
            if producer is None or producer.state != "succeeded":
                continue
            if (producer.kind != claim.kind or producer.result_revision != claim.expected_revision
                    or producer.cancel_requested_at is not None):
                raise PublicExportOperationError("svg_export_readback_binding_invalid")
            checkpoint = await session.get(PublicArtifact, row.source_artifact_id)
            artifact = await session.get(PublicArtifact, row.result_artifact_id) if row.result_artifact_id else None
        if checkpoint is None or artifact is None:
            raise PublicExportOperationError("svg_export_readback_binding_invalid")
        restored = restore_canonical_export_identity(
            checkpoint.to_ref(), await service.read_ref_bytes(checkpoint.to_ref()))
        if restored.canonical_wire() != predecessor.canonical_wire():
            raise PublicExportOperationError("svg_export_readback_binding_invalid")
        if consumer_artifact is not None and any(getattr(artifact, field) != getattr(consumer_artifact, field)
                for field in ("owner_id", "tenant_id", "sha256", "size_bytes", "kind", "media_type", "storage_key")):
            # A separately produced release package can legitimately have other
            # bytes. In that case its own completed identity remains authoritative.
            continue
        async with session_factory() as session:
            # The consumer is registered only under this operation's current fence.
            await checkpoint_canonical_export_slides(session, identity=identity, operation_claim=claim)
            consumer = consumer_artifact or await service.register_consumer_ref(session, artifact.to_ref(),
                created_by_operation_id=claim.operation_id)
            if consumer.created_by_operation_id != claim.operation_id or consumer.retention_class != "published":
                raise PublicExportOperationError("svg_export_readback_binding_invalid")
            evidence = await _producer_readback(session_factory, producer_id=row.claimed_by_operation_id,
                claim=claim, tenant_id=tenant_id, artifact=consumer, required=True,
                identity=identity, producer_identity=predecessor, include_worker_evidence=True)
            await checkpoint_canonical_export_slides(session, identity=identity, operation_claim=claim)
            if execution is not None:
                await complete_canonical_export_identity(session, execution=execution,
                    operation_claim=claim, result_artifact=consumer)
            await session.commit()
        return PublicWorkerArtifactResult(artifact=consumer, resource_metrics={
            "public_export_worker_evidence": {**evidence, "reusedCompletedIdentity": True,
                "requestedExportMode": identity.export_mode, "deliveredExportMode": identity.export_mode,
                "joined": True, "joinedOperationId": str(row.claimed_by_operation_id),
                "physicalProducerOperationId": str(row.claimed_by_operation_id),
                "releasePredecessorReuse": True}})
    return None


async def export_svg_through_identity(claim, *, tenant_id, request: SvgExportRequest, session_factory,
                                      grant, execute: SvgExportExecutor):
    """Export once per identity; returns ``(result, identity)``.

    ``result.resource_metrics["public_export_worker_evidence"]`` says whether
    the bytes were produced here or reused (``reusedCompletedIdentity``).
    """
    started_at = monotonic()
    checkpoints = await persist_svg_slide_checkpoints(
        claim, tenant_id=tenant_id, request=request, session_factory=session_factory)
    identity = build_svg_export_identity(claim, tenant_id=tenant_id, request=request, checkpoints=checkpoints)
    async with session_factory() as session:
        execution = await prepare_canonical_export_execution(
            session, claim, identity=identity,
            worker_id=get_public_worker_node_id("export-controller"))
        await session.commit()
    join = await resolve_export_join(claim, execution=execution, session_factory=session_factory,
                                     artifact_service_factory=configured_public_artifact_service, grant=grant)
    if join.result is not None:
        artifact, metrics = join.result
        evidence = dict(metrics.get("public_export_worker_evidence") or {})
        origin = await _release_predecessor(claim, tenant_id=tenant_id, request=request,
            checkpoints=checkpoints, identity=identity, session_factory=session_factory, consumer_artifact=artifact)
        readback = (origin.resource_metrics["public_export_worker_evidence"] if origin is not None
            else await _producer_readback(
                session_factory, producer_id=join.leading_operation_id, claim=claim,
                tenant_id=tenant_id, artifact=artifact,
                required=request.source_preservation is not None or request.export_format == "pptx",
                identity=identity, include_worker_evidence=request.export_format == "pptx"))
        if readback is not None:
            if request.export_format == "pptx":
                evidence = {**readback, **evidence, "structuralReadback": readback["structuralReadback"]}
            else:
                evidence["structuralReadback"] = readback
        return PublicWorkerArtifactResult(artifact=artifact, resource_metrics={
            **metrics, "public_export_worker_evidence": evidence,
            "svg_export_timing": {"identityAndReuseMs": (monotonic() - started_at) * 1000,
                                  "workerExecuted": False, "cacheReuse": True}}), identity
    execution = join.execution
    if not execution.should_execute:
        raise PublicExportOperationError("public_export_execution_disposition_invalid")
    slide_refs = [checkpoint.artifact for checkpoint in checkpoints]
    worker_started_at = monotonic()
    async with export_identity_lease_keeper(execution=execution, session_factory=session_factory):
        result = await _release_predecessor(claim, tenant_id=tenant_id, request=request,
            checkpoints=checkpoints, identity=identity, execution=execution, session_factory=session_factory)
        if result is not None:
            return PublicWorkerArtifactResult(artifact=result.artifact, resource_metrics={
                **result.resource_metrics,
                "svg_export_timing": {"identityAndReuseMs": (monotonic() - started_at) * 1000,
                                      "workerExecuted": False, "cacheReuse": True}}), identity
        result = await execute(slide_refs)
        worker_finished_at = monotonic()
        async with session_factory() as session:
            await complete_canonical_export_identity(
                session, execution=execution, operation_claim=claim, result_artifact=result.artifact)
            await session.commit()
    evidence = dict(result.resource_metrics.get("public_export_worker_evidence") or {})
    evidence.update(join.evidence())
    return PublicWorkerArtifactResult(artifact=result.artifact, resource_metrics={
        **result.resource_metrics, "public_export_worker_evidence": evidence,
        "svg_export_timing": {"identityAndJoinMs": (worker_started_at - started_at) * 1000,
                              "workerMs": (worker_finished_at - worker_started_at) * 1000,
                              "identityCompletionMs": (monotonic() - worker_finished_at) * 1000,
                              "workerExecuted": True, "cacheReuse": False}}), identity


__all__ = [
    "SVG_EXPORT_OUTPUT_MODE",
    "SVG_RENDER_ADAPTER",
    "SvgExportRequest",
    "build_svg_export_identity",
    "export_svg_through_identity",
    "persist_svg_slide_checkpoints",
]
