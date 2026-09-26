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

import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from models.sql.public_artifact import ArtifactRef
from models.sql.workspace import WorkspaceOperation
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
)
from services.public_export_errors import PublicExportOperationError
from services.public_export_join import export_identity_lease_keeper, resolve_export_join
from services.public_export_operation import (
    PublicWorkerArtifactResult,
    prepare_canonical_export_execution,
)
from services.svg_contract import SVG_CONTRACT_VERSION
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
    evidence = build_svg_export_evidence_digests(
        svgs=request.svgs, notes=request.notes, canvas=request.canvas,
        source_assets=request.source_assets, source_preservation=request.source_preservation,
        native_objects=request.bound_native_objects,
        quality_inputs=svg_quality_inputs(slides=request.slides, advisory=request.advisory,
                                          quality_policy=request.quality_policy),
        converter_version=CONVERTER_VERSION, converter_digest=converter_digest(),
        contract_version=SVG_CONTRACT_VERSION, font_embedding=request.font_embedding)
    return CanonicalExportIdentity.build(
        owner_id=claim.owner_id, tenant_id=tenant_id, presentation_id=claim.presentation_id,
        source_revision=claim.expected_revision, export_format=request.export_format,
        export_mode=SVG_EXPORT_OUTPUT_MODE, render_adapter=SVG_RENDER_ADAPTER,
        exporter_version=CONVERTER_VERSION, evidence=evidence, slides=tuple(checkpoints))


async def _producer_readback(session_factory, *, producer_id, operation_id, required: bool) -> dict | None:
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
    if producer_id is None or producer_id == operation_id:
        return None
    async with session_factory() as session:
        producer = await session.get(WorkspaceOperation, producer_id)
    if producer is None or producer.state != "succeeded":
        if required:
            raise PublicExportOperationError("public_export_readback_evidence_pending")
        return None
    result = producer.result if isinstance(producer.result, dict) else {}
    evidence = result.get("public_export_worker_evidence")
    readback = evidence.get("structuralReadback") if isinstance(evidence, dict) else None
    return dict(readback) if isinstance(readback, dict) else None


async def export_svg_through_identity(claim, *, tenant_id, request: SvgExportRequest, session_factory,
                                      grant, execute: SvgExportExecutor):
    """Export once per identity; returns ``(result, identity)``.

    ``result.resource_metrics["public_export_worker_evidence"]`` says whether
    the bytes were produced here or reused (``reusedCompletedIdentity``).
    """
    checkpoints = await persist_svg_slide_checkpoints(
        claim, tenant_id=tenant_id, request=request, session_factory=session_factory)
    identity = build_svg_export_identity(claim, tenant_id=tenant_id, request=request, checkpoints=checkpoints)
    async with session_factory() as session:
        execution = await prepare_canonical_export_execution(
            session, claim, identity=identity,
            worker_id=os.getenv("PUBLIC_WORKER_NODE_ID") or "export-controller")
        await session.commit()
    join = await resolve_export_join(claim, execution=execution, session_factory=session_factory,
                                     artifact_service_factory=configured_public_artifact_service, grant=grant)
    if join.result is not None:
        artifact, metrics = join.result
        evidence = dict(metrics.get("public_export_worker_evidence") or {})
        readback = await _producer_readback(
            session_factory, producer_id=join.leading_operation_id, operation_id=claim.operation_id,
            required=request.source_preservation is not None)
        if readback is not None:
            evidence["structuralReadback"] = readback
        return PublicWorkerArtifactResult(artifact=artifact, resource_metrics={
            **metrics, "public_export_worker_evidence": evidence}), identity
    execution = join.execution
    if not execution.should_execute:
        raise PublicExportOperationError("public_export_execution_disposition_invalid")
    slide_refs = [checkpoint.artifact for checkpoint in checkpoints]
    async with export_identity_lease_keeper(execution=execution, session_factory=session_factory):
        result = await execute(slide_refs)
    async with session_factory() as session:
        await complete_canonical_export_identity(
            session, execution=execution, operation_claim=claim, result_artifact=result.artifact)
        await session.commit()
    evidence = dict(result.resource_metrics.get("public_export_worker_evidence") or {})
    evidence.update(join.evidence())
    return PublicWorkerArtifactResult(artifact=result.artifact, resource_metrics={
        **result.resource_metrics, "public_export_worker_evidence": evidence}), identity


__all__ = [
    "SVG_EXPORT_OUTPUT_MODE",
    "SVG_RENDER_ADAPTER",
    "SvgExportRequest",
    "build_svg_export_identity",
    "export_svg_through_identity",
    "persist_svg_slide_checkpoints",
]
