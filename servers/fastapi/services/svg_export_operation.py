"""SVG export admission using the existing fenced worker and artifact authority."""
from __future__ import annotations
import hashlib
import json
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from models.sql.slide import SlideModel
from services.artifact_store import configured_public_artifact_service
from services.artifact_store import configured_worker_artifact_cache
from services.authored_canvas import presentation_canvas_metadata
from services.public_export_operation import PublicWorkerArtifactResult, _worker_ref, _public_worker_output, execute_isolated_export_operation
from services.public_export_deadline import load_public_export_deadline_grant
from services.public_export_errors import PublicExportOperationError
from services.svg_contract import SVG_CONTRACT_VERSION, validate_svg
from workers.public_svg_export_worker import SVG_EXPORT_SCHEMA, SVG_EXPORT_SCHEMA_V2, CONVERTER_VERSION, converter_digest

LOGGER = logging.getLogger(__name__)

def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()).hexdigest()

async def render_and_export_svgs(claim, *, tenant_id, svgs, notes, export_format, canvas, session_factory,
                                 asset_bindings=None):
    if claim.owner_id is None or claim.kind != f"export.{export_format}" or not 1 <= len(svgs) <= 100 or len(notes) != len(svgs):
        raise PublicExportOperationError("svg_export_input_invalid")
    if asset_bindings is not None and len(asset_bindings) != len(svgs):
        raise PublicExportOperationError("svg_export_asset_count_invalid")
    for index, source in enumerate(svgs):
        if asset_bindings is None or asset_bindings[index] is None:
            validate_svg(source,width=canvas["widthPx"],height=canvas["heightPx"])
    service=configured_public_artifact_service()
    async with session_factory() as session:
        refs=[]; asset_refs=[]; assets=[]
        for index, source in enumerate(svgs):
            binding = asset_bindings[index] if asset_bindings is not None else None
            if binding is not None:
                from services.svg_export_assets import prepare_export_assets
                wire, bound = await prepare_export_assets(binding, svg=source, service=service,
                    session=session, claim=claim, tenant_id=tenant_id, export_format=export_format,
                    width=canvas["widthPx"], height=canvas["heightPx"])
                assets.append(wire); asset_refs.extend(bound)
            else:
                assets.append(None)
            refs.append(await service.persist_bytes(session,owner_id=claim.owner_id,tenant_id=tenant_id,kind="slide-svg",payload=source.encode(),media_type="image/svg+xml",created_by_operation_id=claim.operation_id,retention_class="ephemeral"))
        payload={"schemaVersion":SVG_EXPORT_SCHEMA,"contractVersion":SVG_CONTRACT_VERSION,"converterVersion":CONVERTER_VERSION,"converterDigest":converter_digest(),"exportFormat":export_format,"canvas":canvas,"slides":[{"slideIndex":i,"artifactRef":_worker_ref(ref).to_wire(),"notes":notes[i]} for i,ref in enumerate(refs)]}
        payload["canonicalExportIdentity"]=_digest({"revision":claim.expected_revision,"sources":[ref.sha256 for ref in refs],"notes":notes,"canvas":canvas,"format":export_format,"converter":payload["converterDigest"],"contract":SVG_CONTRACT_VERSION})
        if any(asset is not None for asset in assets):
            payload.update(schemaVersion=SVG_EXPORT_SCHEMA_V2, contractVersion="svg-static/v2",
                           presentationId=str(claim.presentation_id), sourceRevision=claim.expected_revision)
            for entry, asset in zip(payload["slides"], assets):
                entry["assets"] = asset
            payload["canonicalExportIdentity"] = _digest({"legacyIdentity": payload["canonicalExportIdentity"],
                "manifestDigests": [(a["sourceManifestDigest"], a["placementManifestDigest"]) if a else None for a in assets]})
        bundle=await service.persist_bytes(session,owner_id=claim.owner_id,tenant_id=tenant_id,kind="export-bundle",payload=json.dumps(payload).encode(),media_type="application/vnd.presenton.export-bundle+json",created_by_operation_id=claim.operation_id,retention_class="ephemeral")
        await session.commit()
    # The worker receives only digest-verified transport bytes, never database
    # credentials or an assumption that the authority is a local filesystem.
    cache = configured_worker_artifact_cache()
    await cache.maintain()
    for reference in [*refs, *asset_refs, bundle]:
        await service.stream_ref_to_object_store(reference, cache)
    grant=await load_public_export_deadline_grant(session_factory,operation_id=claim.operation_id)
    exported=await execute_isolated_export_operation(claim,input_artifact=_worker_ref(bundle),deadline_seconds=grant.export_worker_seconds() if grant else None)
    if not exported.ok:
        raise PublicExportOperationError(exported.error_code or "svg_export_worker_failed")
    ref=_public_worker_output(dict(exported.output_artifact or {}),retention_class="published")
    if ref.owner_id != claim.owner_id or ref.tenant_id != tenant_id or ref.created_by_operation_id != claim.operation_id or ref.kind != f"presentation-{export_format}":
        raise PublicExportOperationError("svg_export_output_binding_invalid")
    async with session_factory() as session:
        registered=await service.register_ref(session,ref);await session.commit()
    return PublicWorkerArtifactResult(artifact=registered,resource_metrics={"role":exported.role,**exported.resource_metrics,"public_export_worker_evidence":dict(exported.execution_evidence)})

async def execute_svg_workspace_export(context, *, presentation, admission, session_factory):
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError, WorkspaceOperationResult
    from services.presentation_export_attempt_service import (
        begin_export_attempt,
        terminalize_export_attempt_failed_if_current_workspace_claim,
    )
    from services.workspace_artifact_publication_service import prepare_workspace_export_artifact_ref, publish_workspace_export_artifact
    from services.workspace_export_quality_evidence import _legacy_fidelity_public_quality_receipt
    claim=context.claim;fmt=claim.kind.removeprefix("export.")
    if admission is None or admission.state != "active" or admission.owner_id != claim.owner_id:
        raise NonRetryableWorkspaceOperationError("svg_export_active_admission_required")
    if presentation.owner_id != claim.owner_id:
        raise NonRetryableWorkspaceOperationError("presentation_not_found")
    if presentation.revision != claim.expected_revision:
        raise NonRetryableWorkspaceOperationError("stale_presentation_revision")
    if claim.request_payload.get("quality_policy") == "release-v1":
        raise NonRetryableWorkspaceOperationError("svg_release_quality_review_not_supported")
    command_context = claim.request_payload.get("context") or {}
    if command_context.get("font_embedding") is True or claim.request_payload.get("font_embedding") is True:
        raise NonRetryableWorkspaceOperationError("svg_font_embedding_not_supported")
    async with session_factory() as session:
        slides=(await session.execute(select(SlideModel).where(SlideModel.presentation==claim.presentation_id).order_by(SlideModel.index,SlideModel.id))).scalars().all()
    if not slides or any(slide.source_format != "svg" or not slide.svg_content for slide in slides):
        raise NonRetryableWorkspaceOperationError("svg_export_source_required")
    if (len(slides) != presentation.n_slides
            or [slide.index for slide in slides] != list(range(len(slides)))):
        raise NonRetryableWorkspaceOperationError("svg_export_incomplete_presentation")
    svgs=[slide.svg_content for slide in slides];notes=[slide.speaker_note or "" for slide in slides]
    source_digest=_digest({"svgs":svgs,"notes":notes});canvas=presentation_canvas_metadata(presentation.theme)
    source_assets = [(slide.content or {}).get("svgAssetManifest") for slide in slides]
    if any(asset is not None for asset in source_assets):
        source_digest = _digest({"svgs": svgs, "notes": notes, "assets": source_assets})
    provenance={"schema_version":1,"adapter_chain":["general"],"capability_evidence":{"selected_adapter":"general","execution_profile":"svg-native","source_format":"svg","visual_quality_verified":False},"fallback_reason":None,"asset_digests":{},"compiler_runtime":{"compiler":"slide-master","compiler_version":CONVERTER_VERSION,"runtime":"public-export-worker","runtime_version":SVG_EXPORT_SCHEMA},"freshness":{"source_revision":claim.expected_revision,"source_snapshot_sha256":source_digest,"asset_digests_match":True,"package_digest_match":False}}
    if any(asset is not None for asset in source_assets):
        provenance["compiler_runtime"]["runtime_version"] = SVG_EXPORT_SCHEMA_V2
    async with session_factory() as session:
        from models.sql.workspace import WorkspaceOperation
        operation=await session.get(WorkspaceOperation,claim.operation_id)
        attempt=await begin_export_attempt(session,presentation_id=claim.presentation_id,owner_id=claim.owner_id,owner_scope=claim.owner_scope,workspace_operation_id=claim.operation_id,export_format=fmt,source_revision=claim.expected_revision,source_snapshot_sha256=source_digest,request_hash=operation.request_hash,idempotency_key_hash=operation.idempotency_key_hash,requested_adapter="general",provenance=provenance)
    try:
        await context.progress(stage="export_rendering",percent=10)
        asset_bindings = None
        if any((slide.content or {}).get("svgAssetManifest") is not None for slide in slides):
            from services.svg_export_assets import build_slide_export_assets
            service = configured_public_artifact_service()
            asset_bindings = []
            async with session_factory() as session:
                for slide in slides:
                    await context.raise_if_cancelled()
                    asset_bindings.append(await build_slide_export_assets(slide, service=service,
                        session=session, claim=claim, tenant_id=admission.tenant_id, export_format=fmt, canvas=canvas))
                await session.commit()
        result=await render_and_export_svgs(claim,tenant_id=admission.tenant_id,svgs=svgs,notes=notes,export_format=fmt,canvas=canvas,session_factory=session_factory,asset_bindings=asset_bindings)
        ref=result.artifact
        receipt=_legacy_fidelity_public_quality_receipt(quality_receipt_id=f"quality:{claim.operation_id}:{ref.artifact_id}",operation_id=str(claim.operation_id),presentation_id=str(claim.presentation_id),artifact_digest=f"sha256:{ref.sha256}",source_revision=claim.expected_revision,scene_digest=f"sha256:{source_digest}",evaluated_at=datetime.now(timezone.utc))
        # The public V3 shape is shared, but missing evidence is SVG-specific.
        receipt=json.loads(json.dumps(receipt).replace("legacy_", "svg_"))
        quality_digest=_digest(receipt)
        provenance["freshness"].update({"package_sha256":ref.sha256,"package_digest_match":True})
        await context.raise_if_cancelled()
        async with session_factory() as session:
            artifact=await prepare_workspace_export_artifact_ref(session,reference=ref,operation_id=claim.operation_id,presentation_id=claim.presentation_id,source_revision=claim.expected_revision,kind=claim.kind,export_attempt_id=attempt.id)
            revision=await publish_workspace_export_artifact(session,artifact=artifact,attempt_token=claim.attempt_token,fencing_token=claim.fencing_token,export_format=fmt,export_source_snapshot_sha256=source_digest,export_selected_adapter="general",export_provenance=provenance,worker_resource_metrics=result.resource_metrics,public_artifact_id=ref.artifact_id,public_tenant_id=admission.tenant_id,public_attempt_number=claim.attempt_number,public_quality_digest=quality_digest,public_quality_receipt=receipt)
        return WorkspaceOperationResult(payload={"status":"completed","format":fmt,**result.resource_metrics},result_revision=revision)
    except BaseException as error:
        try:
            async with session_factory() as session:
                await terminalize_export_attempt_failed_if_current_workspace_claim(
                    session,
                    attempt_id=attempt.id,
                    expected_owner_id=claim.owner_id,
                    expected_owner_scope=claim.owner_scope,
                    expected_workspace_operation_id=claim.operation_id,
                    expected_attempt_token=claim.attempt_token,
                    expected_fencing_token=claim.fencing_token,
                    failure_code=(getattr(error, "code", None) or str(error) or "presentation_export_failed"),
                    failure_path=getattr(error, "path", None),
                    selected_adapter="general",
                )
        except Exception:
            LOGGER.exception(
                "Could not terminalize SVG export attempt %s for operation %s",
                attempt.id,
                claim.operation_id,
            )
        raise
