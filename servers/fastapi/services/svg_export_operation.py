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
from services.document_canvas import parse_canvas_metadata
from services.public_export_operation import (
    PublicWorkerArtifactResult,
    _public_worker_output,
    _worker_ref,
    execute_isolated_export_operation,
)
from services.public_export_deadline import load_public_export_deadline_grant
from services.public_export_errors import PublicExportOperationError
from services.svg_contract import SVG_CONTRACT_VERSION, source_digest as svg_source_digest, validate_svg
from workers.public_svg_export_worker import (
    SVG_EXPORT_SCHEMA,
    SVG_EXPORT_SCHEMA_V2,
    SVG_EXPORT_SCHEMA_V3,
    CONVERTER_VERSION,
    converter_digest,
)

LOGGER = logging.getLogger(__name__)


def _require_deadline_budget(grant):
    """Fail fast once the pinned admission deadline has no worker budget left.

    Checked before and between the expensive preparation phases (asset
    derivatives, bundle persist, worker cache staging), so an operation whose
    deadline already passed stops in milliseconds instead of spending minutes
    preparing inputs for a worker grant that can no longer be issued.  The
    floor and the error code are the grant's own
    (``public_export_admission_deadline_exhausted``).
    """
    if grant is not None:
        grant.export_worker_seconds()


def _manifest_digests(assets):
    return [(a["sourceManifestDigest"], a["placementManifestDigest"]) if a else None for a in assets]


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_preservation_receipts(presentation, slides, canvas):
    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    svg_theme = theme.get("svg") if isinstance(theme.get("svg"), dict) else {}
    marker = svg_theme.get("sourcePreservation")
    receipts = [
        (slide.content or {}).get("svgReceipt", {}).get("source_preservation")
        if isinstance((slide.content or {}).get("svgReceipt"), dict) else None
        for slide in slides
    ]
    if marker is None and not any(receipt is not None for receipt in receipts):
        return None
    if (not isinstance(marker, dict)
            or set(marker) != {"schemaVersion", "manifestDigest", "pageCount"}
            or marker.get("schemaVersion") != "svg-source-preservation/v1"
            or marker.get("pageCount") != len(slides)):
        raise PublicExportOperationError("svg_source_export_marker_invalid")
    manifest_digest = marker.get("manifestDigest")
    source_digest = None
    for index, (slide, receipt) in enumerate(zip(slides, receipts, strict=True)):
        if not isinstance(receipt, dict):
            raise PublicExportOperationError("svg_source_export_receipt_required")
        expected = {
            "contract_version": "svg-source-preservation/v1",
            "manifest_digest": manifest_digest,
            "source_page_index": index,
            "page_count": len(slides),
            "svg_digest": svg_source_digest(slide.svg_content),
            "canvas": {"width": canvas.width_px, "height": canvas.height_px},
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise PublicExportOperationError("svg_source_export_receipt_input_mismatch")
        if receipt.get("rendering_verified") is not True:
            raise PublicExportOperationError("svg_source_export_rendering_unverified")
        pinned_template = svg_theme.get("styleSnapshot", {}).get("source_template")
        if pinned_template and receipt.get("layout_schema_version") != "svg-source-layout/v2":
            raise PublicExportOperationError("svg_source_export_template_contract_required")
        if receipt.get("layout_schema_version") == "svg-source-layout/v2":
            from services.svg_export_quality import TEMPLATE_CHECKS
            snapshot = svg_theme.get("styleSnapshot", {})
            template = snapshot.get("source_template", {})
            application = receipt.get("template_application", {})
            quality = (slide.content or {}).get("svgReceipt", {}).get("quality", {})
            if (not template.get("digest") or receipt.get("template_digest") != template["digest"]
                    or receipt.get("style_digest") != snapshot.get("digest")
                    or application.get("template_digest") != template["digest"]
                    or application.get("layout_id") != receipt.get("layout_id")
                    or receipt.get("layout_id") not in {layout["id"] for layout in template.get("layouts", [])}
                    or application.get("passed") is not True
                    or any(application.get("checks", {}).get(c) is not True for c in TEMPLATE_CHECKS)):
                raise PublicExportOperationError("svg_source_export_template_evidence_invalid")
            if (quality.get("source_digest") != receipt["svg_digest"]
                    or quality.get("passed") is not True or quality.get("measurement_complete") is not True):
                raise PublicExportOperationError("svg_source_export_geometry_evidence_invalid")
        checks = receipt.get("checks")
        if (not isinstance(checks, dict)
                or any(checks.get(key) is not True for key in (
                    "source_ids_exact", "text_exact", "table_structure_exact", "image_occurrences_exact"
                ))):
            raise PublicExportOperationError("svg_source_export_preservation_failed")
        if not all(isinstance(receipt.get(key), str) and receipt[key] for key in (
            "manifest_digest", "source_digest", "source_page_id", "page_digest", "plan_digest", "style_digest"
        )):
            raise PublicExportOperationError("svg_source_export_receipt_invalid")
        if source_digest is None:
            source_digest = receipt["source_digest"]
        elif receipt["source_digest"] != source_digest:
            raise PublicExportOperationError("svg_source_export_source_digest_mismatch")
        bindings = receipt.get("bindings")
        if not isinstance(bindings, list) or not bindings:
            raise PublicExportOperationError("svg_source_export_bindings_invalid")
        element_ids = [binding.get("element_id") for binding in bindings if isinstance(binding, dict)]
        if (len(element_ids) != len(bindings) or any(not value for value in element_ids)
                or len(set(element_ids)) != len(element_ids)):
            raise PublicExportOperationError("svg_source_export_bindings_invalid")
        for binding in bindings:
            kind = binding.get("kind")
            if kind not in {"text", "table_cell", "image", "shape"} or not binding.get("source_id"):
                raise PublicExportOperationError("svg_source_export_bindings_invalid")
            if kind in {"text", "table_cell"} and not isinstance(binding.get("text"), str):
                raise PublicExportOperationError("svg_source_export_text_binding_invalid")
            if kind == "image" and (not binding.get("slot_id") or not binding.get("asset_digest")):
                raise PublicExportOperationError("svg_source_export_image_binding_invalid")
    return receipts

async def render_and_export_svgs(claim, *, tenant_id, svgs, notes, export_format, canvas, session_factory,
                                 asset_bindings=None, source_preservation=None, deadline_grant=None,
                                 font_embedding=False):
    if (claim.owner_id is None or claim.kind != f"export.{export_format}"
            or not 1 <= len(svgs) <= 100 or len(notes) != len(svgs)):
        raise PublicExportOperationError("svg_export_input_invalid")
    if asset_bindings is not None and len(asset_bindings) != len(svgs):
        raise PublicExportOperationError("svg_export_asset_count_invalid")
    if source_preservation is not None and len(source_preservation) != len(svgs):
        raise PublicExportOperationError("svg_source_export_receipt_count_invalid")
    if source_preservation is not None and export_format != "pptx":
        raise PublicExportOperationError("svg_source_export_format_unsupported")
    if font_embedding and export_format != "pptx":
        raise PublicExportOperationError("svg_font_embedding_pptx_only")
    resolved_canvas = parse_canvas_metadata(canvas)
    canvas = resolved_canvas.model_dump(mode="json", by_alias=True)
    for index, source in enumerate(svgs):
        if asset_bindings is None or asset_bindings[index] is None:
            validate_svg(source,width=resolved_canvas.width_px,height=resolved_canvas.height_px)
    grant = deadline_grant or await load_public_export_deadline_grant(
        session_factory, operation_id=claim.operation_id)
    _require_deadline_budget(grant)
    service = configured_public_artifact_service()
    async with session_factory() as session:
        refs = []
        asset_refs = []
        assets = []
        for index, source in enumerate(svgs):
            _require_deadline_budget(grant)
            binding = asset_bindings[index] if asset_bindings is not None else None
            if binding is not None:
                from services.svg_export_assets import prepare_export_assets
                wire, bound = await prepare_export_assets(binding, svg=source, service=service,
                    session=session, claim=claim, tenant_id=tenant_id, export_format=export_format,
                    width=resolved_canvas.width_px, height=resolved_canvas.height_px)
                assets.append(wire)
                asset_refs.extend(bound)
            else:
                assets.append(None)
            refs.append(await service.persist_bytes(
                session, owner_id=claim.owner_id, tenant_id=tenant_id, kind="slide-svg",
                payload=source.encode(), media_type="image/svg+xml",
                created_by_operation_id=claim.operation_id, retention_class="ephemeral"))
        payload = {
            "schemaVersion": SVG_EXPORT_SCHEMA,
            "contractVersion": SVG_CONTRACT_VERSION,
            "converterVersion": CONVERTER_VERSION,
            "converterDigest": converter_digest(),
            "exportFormat": export_format,
            "canvas": canvas,
            "fontEmbedding": bool(font_embedding),
            "slides": [{"slideIndex": i, "artifactRef": _worker_ref(ref).to_wire(), "notes": notes[i]}
                       for i, ref in enumerate(refs)],
        }
        payload["canonicalExportIdentity"] = _digest({
            "revision": claim.expected_revision, "sources": [ref.sha256 for ref in refs], "notes": notes,
            "canvas": canvas, "format": export_format, "converter": payload["converterDigest"],
            "contract": SVG_CONTRACT_VERSION, "fontEmbedding": payload["fontEmbedding"],
        })
        if source_preservation is not None:
            payload.update(schemaVersion=SVG_EXPORT_SCHEMA_V3, contractVersion="svg-static/v2",
                           presentationId=str(claim.presentation_id), sourceRevision=claim.expected_revision)
            for entry, asset, receipt in zip(payload["slides"], assets, source_preservation, strict=True):
                entry["assets"] = asset
                entry["sourcePreservation"] = receipt
            payload["canonicalExportIdentity"] = _digest({
                "legacyIdentity": payload["canonicalExportIdentity"],
                "sourcePreservation": [_digest(receipt) for receipt in source_preservation],
                "manifestDigests": _manifest_digests(assets),
            })
        elif any(asset is not None for asset in assets):
            payload.update(schemaVersion=SVG_EXPORT_SCHEMA_V2, contractVersion="svg-static/v2",
                           presentationId=str(claim.presentation_id), sourceRevision=claim.expected_revision)
            for entry, asset in zip(payload["slides"], assets, strict=True):
                entry["assets"] = asset
            payload["canonicalExportIdentity"] = _digest({"legacyIdentity": payload["canonicalExportIdentity"],
                "manifestDigests": _manifest_digests(assets)})
        bundle = await service.persist_bytes(
            session, owner_id=claim.owner_id, tenant_id=tenant_id, kind="export-bundle",
            payload=json.dumps(payload).encode(), media_type="application/vnd.presenton.export-bundle+json",
            created_by_operation_id=claim.operation_id, retention_class="ephemeral")
        await session.commit()
    # The worker receives only digest-verified transport bytes, never database
    # credentials or an assumption that the authority is a local filesystem.
    _require_deadline_budget(grant)
    cache = configured_worker_artifact_cache()
    await cache.maintain()
    # One eviction pass for the whole staging batch instead of one full cache
    # walk per object (services/worker_artifact_cache_maintenance.py).
    async with cache.maintenance_batch():
        for reference in [*refs, *asset_refs, bundle]:
            _require_deadline_budget(grant)
            await service.stream_ref_to_object_store(reference, cache)
    exported = await execute_isolated_export_operation(
        claim, input_artifact=_worker_ref(bundle),
        deadline_seconds=grant.export_worker_seconds() if grant else None)
    if not exported.ok:
        raise PublicExportOperationError(exported.error_code or "svg_export_worker_failed")
    ref = _public_worker_output(dict(exported.output_artifact or {}), retention_class="published")
    if (ref.owner_id != claim.owner_id or ref.tenant_id != tenant_id
            or ref.created_by_operation_id != claim.operation_id or ref.kind != f"presentation-{export_format}"):
        raise PublicExportOperationError("svg_export_output_binding_invalid")
    async with session_factory() as session:
        registered = await service.register_ref(session, ref)
        await session.commit()
    return PublicWorkerArtifactResult(artifact=registered, resource_metrics={
        "role": exported.role, **exported.resource_metrics,
        "public_export_worker_evidence": dict(exported.execution_evidence)})

async def execute_svg_workspace_export(context, *, presentation, admission, session_factory):
    from services.workspace_operation_handler_registry import (
        NonRetryableWorkspaceOperationError,
        WorkspaceOperationResult,
    )
    from services.presentation_export_attempt_service import (
        begin_export_attempt,
        terminalize_export_attempt_failed_if_current_workspace_claim,
    )
    from services.workspace_artifact_publication_service import (
        prepare_workspace_export_artifact_ref,
        publish_workspace_export_artifact,
    )
    claim = context.claim
    fmt = claim.kind.removeprefix("export.")
    if admission is None or admission.state != "active" or admission.owner_id != claim.owner_id:
        raise NonRetryableWorkspaceOperationError("svg_export_active_admission_required")
    if presentation.owner_id != claim.owner_id:
        raise NonRetryableWorkspaceOperationError("presentation_not_found")
    if presentation.revision != claim.expected_revision:
        raise NonRetryableWorkspaceOperationError("stale_presentation_revision")
    command_context = claim.request_payload.get("context") or {}
    quality_policy = command_context.get("quality_policy") or claim.request_payload.get("quality_policy")
    if quality_policy not in (None, "release-v1"):
        raise NonRetryableWorkspaceOperationError("svg_quality_policy_invalid")
    font_embedding = bool(
        command_context.get("font_embedding") is True
        or claim.request_payload.get("font_embedding") is True
    )
    if font_embedding and fmt != "pptx":
        raise NonRetryableWorkspaceOperationError("svg_font_embedding_pptx_only")
    async with session_factory() as session:
        slides = (await session.execute(
            select(SlideModel).where(SlideModel.presentation == claim.presentation_id)
            .order_by(SlideModel.index, SlideModel.id))).scalars().all()
    if not slides or any(slide.source_format != "svg" or not slide.svg_content for slide in slides):
        raise NonRetryableWorkspaceOperationError("svg_export_source_required")
    if (len(slides) != presentation.n_slides
            or [slide.index for slide in slides] != list(range(len(slides)))):
        raise NonRetryableWorkspaceOperationError("svg_export_incomplete_presentation")
    svgs = [slide.svg_content for slide in slides]
    notes = [slide.speaker_note or "" for slide in slides]
    source_digest = _digest({"svgs": svgs, "notes": notes})
    canvas = presentation_canvas_metadata(presentation.theme)
    source_assets = [(slide.content or {}).get("svgAssetManifest") for slide in slides]
    source_preservation = _source_preservation_receipts(presentation, slides, parse_canvas_metadata(canvas))
    if any(asset is not None for asset in source_assets):
        source_digest = _digest({"svgs": svgs, "notes": notes, "assets": source_assets})
    if source_preservation is not None:
        source_digest = _digest({"svgSourceSnapshot": source_digest,
            "sourcePreservation": source_preservation})
    provenance = {
        "schema_version": 1,
        "adapter_chain": ["general"],
        "capability_evidence": {"selected_adapter": "general", "execution_profile": "svg-native",
                                "source_format": "svg", "visual_quality_verified": False},
        "fallback_reason": None,
        "asset_digests": {},
        "compiler_runtime": {"compiler": "slide-master", "compiler_version": CONVERTER_VERSION,
                             "runtime": "public-export-worker", "runtime_version": SVG_EXPORT_SCHEMA},
        "freshness": {"source_revision": claim.expected_revision, "source_snapshot_sha256": source_digest,
                      "asset_digests_match": True, "package_digest_match": False},
    }
    if any(asset is not None for asset in source_assets):
        provenance["compiler_runtime"]["runtime_version"] = SVG_EXPORT_SCHEMA_V2
    if source_preservation is not None:
        provenance["compiler_runtime"]["runtime_version"] = SVG_EXPORT_SCHEMA_V3
        provenance["capability_evidence"]["source_preservation"] = True
    async with session_factory() as session:
        from models.sql.workspace import WorkspaceOperation
        operation = await session.get(WorkspaceOperation, claim.operation_id)
        attempt = await begin_export_attempt(
            session, presentation_id=claim.presentation_id, owner_id=claim.owner_id,
            owner_scope=claim.owner_scope, workspace_operation_id=claim.operation_id, export_format=fmt,
            source_revision=claim.expected_revision, source_snapshot_sha256=source_digest,
            request_hash=operation.request_hash, idempotency_key_hash=operation.idempotency_key_hash,
            requested_adapter="general", provenance=provenance)
    try:
        # Before any preparation: an expired admission must not spend minutes
        # deriving assets and staging a bundle on every retry.
        grant = await load_public_export_deadline_grant(session_factory, operation_id=claim.operation_id)
        _require_deadline_budget(grant)
        await context.progress(stage="export_rendering", percent=10)
        asset_bindings = None
        if any((slide.content or {}).get("svgAssetManifest") is not None for slide in slides):
            from services.svg_export_assets import build_slide_export_assets
            service = configured_public_artifact_service()
            asset_bindings = []
            async with session_factory() as session:
                for slide in slides:
                    await context.raise_if_cancelled()
                    _require_deadline_budget(grant)
                    asset_bindings.append(await build_slide_export_assets(
                        slide, service=service, session=session, claim=claim, tenant_id=admission.tenant_id,
                        export_format=fmt, canvas=canvas))
                await session.commit()
        result = await render_and_export_svgs(
            claim, tenant_id=admission.tenant_id, svgs=svgs, notes=notes, export_format=fmt, canvas=canvas,
            session_factory=session_factory, asset_bindings=asset_bindings,
            source_preservation=source_preservation, deadline_grant=grant, font_embedding=font_embedding)
        ref = result.artifact
        receipt_args = dict(
            quality_receipt_id=f"quality:{claim.operation_id}:{ref.artifact_id}",
            operation_id=str(claim.operation_id), presentation_id=str(claim.presentation_id),
            artifact_digest=f"sha256:{ref.sha256}", source_revision=claim.expected_revision,
            scene_digest=f"sha256:{source_digest}", evaluated_at=datetime.now(timezone.utc))
        from services.svg_export_quality import svg_public_quality_receipt
        receipt = svg_public_quality_receipt(slides=slides, advisory=((presentation.theme or {}).get("svg")
            or {}).get("advisory"), source_preservation=source_preservation,
            worker_metrics=result.resource_metrics, exporter_version=CONVERTER_VERSION, **receipt_args)
        if quality_policy == "release-v1":
            from services.svg_export_release_gate import require_release_v1_receipt
            require_release_v1_receipt(receipt)
        quality_digest = _digest(receipt)
        from services.generation_core.export.svg_evidence import build_svg_export_evidence_digests
        receipt["evidenceDigests"] = build_svg_export_evidence_digests(
            svgs=svgs, notes=notes, source_assets=source_assets, source_preservation=source_preservation,
            quality_receipt=receipt, converter_version=CONVERTER_VERSION, converter_digest=converter_digest(),
        ).to_wire()
        provenance["freshness"].update({"package_sha256": ref.sha256, "package_digest_match": True})
        await context.raise_if_cancelled()
        async with session_factory() as session:
            artifact = await prepare_workspace_export_artifact_ref(
                session, reference=ref, operation_id=claim.operation_id, presentation_id=claim.presentation_id,
                source_revision=claim.expected_revision, kind=claim.kind, export_attempt_id=attempt.id)
            revision = await publish_workspace_export_artifact(
                session, artifact=artifact, attempt_token=claim.attempt_token, fencing_token=claim.fencing_token,
                export_format=fmt, export_source_snapshot_sha256=source_digest, export_selected_adapter="general",
                export_provenance=provenance, worker_resource_metrics=result.resource_metrics,
                public_artifact_id=ref.artifact_id, public_tenant_id=admission.tenant_id,
                public_attempt_number=claim.attempt_number, public_quality_digest=quality_digest,
                public_quality_receipt=receipt)
        return WorkspaceOperationResult(
            payload={"status": "completed", "format": fmt, **result.resource_metrics}, result_revision=revision)
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
