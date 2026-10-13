"""SVG export admission using the existing fenced worker and artifact authority."""
from __future__ import annotations
import json
import logging
import uuid
from collections.abc import Mapping
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
from services.svg_contract import validate_svg
from services.svg_export_bundle import build_svg_export_bundle, digest as _digest
from services.svg_export_source_preservation import (
    source_preservation_receipts as _source_preservation_receipts,
    sealed_whole_page_manifest as _sealed_whole_page_manifest,
)
from workers.public_svg_export_worker import (
    SVG_EXPORT_SCHEMA,
    SVG_EXPORT_SCHEMA_V2,
    SVG_EXPORT_SCHEMA_V3,
    SVG_EXPORT_SCHEMA_V4,
    CONVERTER_VERSION,
)
from utils.hashing import sha256_hex

LOGGER = logging.getLogger(__name__)


def svg_export_admission_snapshot(presentation, slides):
    """Bind an edited SVG revision to its published canonical page evidence."""
    from services.svg_contract import source_digest
    if (not slides or len(slides) != presentation.n_slides
            or [slide.index for slide in slides] != list(range(len(slides)))):
        raise ValueError("svg_export_source_snapshot_invalid")
    pages = []
    for slide in slides:
        content = slide.content or {}
        quality = (content.get("svgReceipt") or {}).get("quality") or {}
        if (slide.owner_id != presentation.owner_id or slide.source_format != "svg"
                or not slide.svg_content or quality.get("passed") is not True
                or quality.get("verified") is not True
                or quality.get("source_digest") != source_digest(slide.svg_content)):
            raise ValueError("svg_export_published_source_required")
        pages.append({"id": str(slide.id), "index": slide.index, "svg": slide.svg_content,
                      "content": content, "notes": slide.speaker_note or ""})
    _quality_recovery_export_gate(presentation, slides)
    return _digest({"presentationId": str(presentation.id), "ownerId": str(presentation.owner_id),
                    "revision": presentation.revision, "theme": presentation.theme, "pages": pages})


def _quality_recovery_export_gate(presentation, slides):
    from services.svg_authoring_profile import read_quality_recovery_policy
    meta = (presentation.theme or {}).get("svg", {})
    snapshot = meta.get("styleSnapshot") or {}
    policy = read_quality_recovery_policy(snapshot)
    if policy is None:
        return None
    from services.generation_core.drivers.svg.quality import _current_gate
    from services.generation_core.quality.advisory_record import content_digest
    from uuid import UUID
    payloads = [({**((slide.content or {}).get("svgReceipt") or {}),
                  "svg_content": slide.svg_content}, None) for slide in slides]
    gate = _current_gate(meta.get("advisory"), payloads)
    publication = meta.get("qualityRecoveryPublication")
    if not isinstance(publication, dict):
        raise ValueError("svg_quality_recovery_export_review_required")
    body = {key: value for key, value in publication.items() if key != "digest"}
    try:
        UUID(publication.get("parentOperationId", ""))
        UUID(publication.get("parentJobId", ""))
        if publication.get("schema") == "svg-quality-recovery-publication/v2":
            UUID(publication.get("reviewOperationId", ""))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("svg_quality_recovery_export_parent_invalid") from error
    if (gate is None or gate.get("status") != "passed" or gate.get("policy") != policy
            or gate.get("pageCoverageComplete") is not True
            or gate.get("snapshotDigest") != content_digest(snapshot)
            or publication.get("schema") not in {"svg-quality-recovery-publication/v1", "svg-quality-recovery-publication/v2"}
            or publication.get("digest") != content_digest(body)
            or publication.get("gateDigest") != gate.get("digest")
            or publication.get("sourceRevision") != presentation.revision
            or publication.get("snapshotDigest") != content_digest(snapshot)
            or publication.get("sourceContentDigests") != [content_digest((slide.content or {}).get("source")) for slide in slides]
            or publication.get("inputDigests") != [payload.get("input_digest") for payload, _ in payloads]
            or (gate.get("binding") or {}).get("presentationId") != str(presentation.id)
            or (gate.get("binding") or {}).get("ownerId") != str(presentation.owner_id)
            or (gate.get("binding") or {}).get("operationId") != publication.get("reviewOperationId", publication["parentOperationId"])
            or (gate.get("binding") or {}).get("jobId") != publication["parentJobId"]):
        raise ValueError("svg_quality_recovery_export_review_required")
    return publication


async def require_quality_recovery_export_parent(session, presentation, slides):
    """The closed generation remains the parent; export has its own admission."""
    publication = _quality_recovery_export_gate(presentation, slides)
    if publication is None:
        return
    await require_quality_recovery_source_parent(session, presentation, publication)
    if publication.get("schema") == "svg-quality-recovery-publication/v2":
        from uuid import UUID
        from models.sql.workspace import WorkspaceOperation
        from models.sql.public_admission import PublicOperationAdmission
        from utils.owner_context import get_current_tenant_id
        review = await session.get(WorkspaceOperation, UUID(publication["reviewOperationId"]))
        admission = await session.scalar(select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == UUID(publication["reviewOperationId"])))
        context = ((review.request_payload or {}).get("context") or {}) if review is not None else {}
        if (review is None or admission is None or review.state != "succeeded"
                or review.kind != "quality.review" or review.owner_id != presentation.owner_id
                or review.presentation_id != presentation.id or review.owner_scope != publication.get("ownerScope")
                or review.expected_revision != publication["sourceRevision"]
                or review.result_revision != publication["sourceRevision"] or review.max_attempts != 1
                or admission.owner_id != presentation.owner_id or admission.tenant_id != str(get_current_tenant_id())
                or admission.planned_request_limit != 1 or admission.retry_request_limit != 0
                or context.get("mode", "analyze_only") != "analyze_only"
                or context.get("target", "source_svg") != "source_svg"):
            raise ValueError("svg_quality_recovery_export_review_operation_invalid")


async def require_quality_recovery_source_parent(session, presentation, publication):
    """Validate immutable generation lineage even when an authorized edit advanced the deck."""
    from uuid import UUID
    from models.sql.workspace import WorkspaceOperation
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.public_admission import PublicOperationAdmission
    from utils.owner_context import get_current_tenant_id
    parent = await session.get(WorkspaceOperation, UUID(publication["parentOperationId"]))
    job = await session.get(PresentationGenerationJob, UUID(publication["parentJobId"]))
    admission = await session.scalar(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == UUID(publication["parentOperationId"])))
    if (parent is None or job is None or admission is None or parent.state != "succeeded"
            or parent.kind != "presentation.generate" or job.state != "published"
            or parent.owner_id != presentation.owner_id or admission.owner_id != presentation.owner_id
            or admission.tenant_id != str(get_current_tenant_id())
            or parent.presentation_id != presentation.id or job.presentation_id != presentation.id
            or job.workspace_operation_id != parent.id
            or parent.result_revision != publication.get("sourceGenerationRevision", publication["sourceRevision"])
            or type(parent.result_revision) is not int or parent.result_revision > presentation.revision
            or job.svg_style_snapshot != presentation.theme["svg"]["styleSnapshot"]
            or (job.request_payload or {}).get("_internal_svg_style_snapshot") != job.svg_style_snapshot):
        raise ValueError("svg_quality_recovery_export_parent_invalid")


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


def _native_object_entries(slides):
    """Each slide's NativeObjects sidecar as a bundle entry; ``None`` when no
    slide has one (identity digests then stay what they were without them).
    An invalid stored sidecar does not fail the export: its objects are
    recorded as fallbacks by the worker's export scene."""
    from services.generation_core.drivers.svg.export_native_wire import bundle_entry
    entries = [bundle_entry(slide.content) for slide in slides]
    return entries if any(entry is not None for entry in entries) else None


async def render_and_export_svgs(claim, *, tenant_id, svgs, notes, export_format, canvas, session_factory,
                                 asset_bindings=None, source_preservation=None, deadline_grant=None,
                                 font_embedding=False, native_objects=None, slide_refs=None,
                                 source_manifest=None):
    """Run the svg worker; ``slide_refs`` are pre-persisted slide svg checkpoints
    (``services.svg_export_identity``), otherwise the sources persist ephemeral."""
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
    if native_objects is not None and len(native_objects) != len(svgs):
        raise PublicExportOperationError("svg_export_native_objects_count_invalid")
    if slide_refs is not None and (len(slide_refs) != len(svgs) or any(
            ref.kind != "slide-svg" or ref.sha256 != sha256_hex(source.encode())
            or ref.owner_id != claim.owner_id or ref.tenant_id != tenant_id
            for ref, source in zip(slide_refs, svgs, strict=True))):
        raise PublicExportOperationError("svg_export_slide_checkpoint_binding_invalid")
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
                receipt_args = ({"source_preservation": source_preservation[index]}
                                if source_preservation is not None else {})
                wire, bound = await prepare_export_assets(binding, svg=source, service=service,
                    session=session, claim=claim, tenant_id=tenant_id, export_format=export_format,
                    width=resolved_canvas.width_px, height=resolved_canvas.height_px, **receipt_args)
                assets.append(wire)
                asset_refs.extend(bound)
            else:
                assets.append(None)
            refs.append(slide_refs[index] if slide_refs is not None else await service.persist_bytes(
                session, owner_id=claim.owner_id, tenant_id=tenant_id, kind="slide-svg",
                payload=source.encode(), media_type="image/svg+xml",
                created_by_operation_id=claim.operation_id, retention_class="ephemeral"))
        payload = build_svg_export_bundle(
            claim=claim, slide_refs=[_worker_ref(ref).to_wire() for ref in refs],
            source_digests=[ref.sha256 for ref in refs], notes=notes, canvas=canvas,
            export_format=export_format, font_embedding=font_embedding, assets=assets,
            source_preservation=source_preservation, native_objects=native_objects,
            source_manifest=source_manifest)
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

async def svg_source_review_context(session, presentation):
    svg_meta = (presentation.theme or {}).get("svg", {})
    source_review = dict(snapshot=svg_meta.get('styleSnapshot'), publication=svg_meta.get('qualityRecoveryPublication'), producer=None)
    publication = source_review['publication']
    if isinstance(publication, Mapping):
        try:
            producer_id = uuid.UUID(str(publication.get('parentOperationId')))
        except (ValueError, TypeError):
            producer_id = None
        if producer_id is not None:
            from models.sql.workspace import WorkspaceOperation
            producer = await session.get(WorkspaceOperation, producer_id)
            if producer is not None:
                source_review['producer'] = dict(operationId=str(producer.id), presentationId=str(producer.presentation_id), ownerId=str(producer.owner_id), ownerScope=str(producer.owner_scope), sourceRevision=producer.result_revision, state=producer.state, kind=producer.kind)
    return source_review


async def current_svg_artifact_fidelity(session, presentation, review_operation):
    from copy import deepcopy
    from sqlalchemy import select
    from models.sql.public_artifact import PublicArtifact
    from models.sql.workspace import WorkspaceArtifact, WorkspaceOperation
    from models.sql.slide import SlideModel
    from services.svg_export_visual_review import svg_visual_review
    from services.svg_final_pptx_review import final_pptx_visual_domain
    from utils.hashing import sha256_hex
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    meta = (presentation.theme or {}).get("svg") or {}
    reviews = meta.get("finalPptxReviews") or {}
    entries = [entry for entry in reviews.values() if isinstance(entry, dict) and entry.get("operationId") == str(review_operation.id)]
    if not entries:
        return None
    review = entries[0]
    view = dict(artifactId=review.get("artifactId"), artifactDigest=review.get("artifactDigest"),
                sourceRevision=review.get("sourceRevision"), exportOperationId=review.get("parentExportOperationId"),
                reviewOperationId=str(review_operation.id), reviewDigest=review.get("digest"))

    def unavailable(code):
        return dict(view, status="failed", reasonCode=code)

    if (len(entries) != 1 or review_operation.kind != "quality.review" or review_operation.state != "succeeded"
            or review_operation.owner_id != presentation.owner_id or get_current_owner_id() != presentation.owner_id
            or review_operation.presentation_id != presentation.id or review_operation.result_revision != presentation.revision
            or review.get("sourceRevision") != presentation.revision or review.get("ownerId") != str(presentation.owner_id)
            or review.get("ownerScope") != str(review_operation.owner_scope)):
        return unavailable("svg_final_pptx_visual_review_owner_stale")
    try:
        public_id = uuid.UUID(str(review.get("artifactId")))
        export_id = uuid.UUID(str(review.get("parentExportOperationId")))
    except (ValueError, TypeError):
        return unavailable("svg_final_pptx_artifact_binding_invalid")
    from models.svg_quality_review import SvgQualityReviewRequest
    request_payload = await session.scalar(select(WorkspaceOperation.request_payload).where(
        WorkspaceOperation.id == review_operation.id, WorkspaceOperation.owner_id == presentation.owner_id))
    try:
        request = SvgQualityReviewRequest.model_validate((request_payload or {}).get("context") or {})
    except ValueError:
        return unavailable("svg_final_pptx_visual_review_stale")
    if request.target != "final_pptx" or request.artifact_id != public_id or request.scope != "all":
        return unavailable("svg_final_pptx_visual_review_stale")
    if (request_payload or {}).get("principal_tenant_id") != get_current_tenant_id():
        return unavailable("svg_final_pptx_visual_review_owner_stale")
    public = await session.get(PublicArtifact, public_id)
    binding = await session.scalar(select(WorkspaceArtifact).where(WorkspaceArtifact.public_artifact_id == public_id, WorkspaceArtifact.presentation_id == presentation.id, WorkspaceArtifact.owner_id == presentation.owner_id, WorkspaceArtifact.source_revision == presentation.revision, WorkspaceArtifact.kind == 'export.pptx'))
    if (public is None or binding is None or public.kind != "presentation-pptx"
            or public.owner_id != presentation.owner_id or public.tenant_id != get_current_tenant_id()
            or binding.public_artifact_id != public_id or binding.presentation_id != presentation.id
            or binding.owner_id != presentation.owner_id or binding.source_revision != presentation.revision
            or binding.operation_id != export_id or review.get("artifactDigest") != "sha256:" + public.sha256
            or review.get("tenantId") != public.tenant_id):
        return unavailable("svg_final_pptx_artifact_binding_invalid")
    export = await session.get(WorkspaceOperation, export_id)
    if (export is None or export.kind != "export.pptx" or export.state != "succeeded"
            or export.owner_id != presentation.owner_id or export.presentation_id != presentation.id
            or export.expected_revision != presentation.revision or export.result_revision != presentation.revision
            or str(export.owner_scope) != str(review_operation.owner_scope)):
        return unavailable("svg_final_pptx_export_not_succeeded")
    export_payload = await session.scalar(select(WorkspaceOperation.request_payload).where(
        WorkspaceOperation.id == export.id, WorkspaceOperation.owner_id == presentation.owner_id))
    if (export_payload or {}).get("principal_tenant_id") != get_current_tenant_id():
        return unavailable("svg_final_pptx_visual_review_owner_stale")
    slides = list((await session.scalars(select(SlideModel).where(SlideModel.presentation == presentation.id).order_by(SlideModel.index))).all())
    try:
        svg_export_admission_snapshot(presentation, slides)
    except ValueError as error:
        code = str(error)
        allowed = {"svg_export_published_source_required", "svg_export_source_snapshot_invalid",
                   "svg_quality_recovery_export_parent_invalid", "svg_quality_recovery_export_review_required",
                   "svg_authoring_profile_invalid", "svg_authoring_profile_snapshot_digest_mismatch", "svg_quality_recovery_profile_invalid"}
        return unavailable(code if code in allowed else "svg_export_source_snapshot_invalid")
    identity = dict(presentationId=str(presentation.id), ownerId=str(presentation.owner_id),
                    ownerScope=str(review_operation.owner_scope), sourceRevision=presentation.revision)
    source_context = await svg_source_review_context(session, presentation)
    source = svg_visual_review(slides=slides, advisory=meta.get("advisory"),
                               expected_binding=dict(identity, _generationSourceReview=source_context))
    metrics = deepcopy(export.result or {})
    metrics["final_pptx_visual_review"] = deepcopy(review)
    domain = final_pptx_visual_domain(source, worker_metrics=metrics, artifact_digest=review["artifactDigest"],
                                     operation_id=str(export.id), presentation_id=str(presentation.id),
                                     source_revision=presentation.revision,
                                     source_digests=[sha256_hex((slide.svg_content or "").encode()) for slide in slides],
                                     expected_binding=dict(identity, tenantId=public.tenant_id))
    return dict(view, status=domain["status"], reasonCode=domain.get("reasonCode"))


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
    if quality_policy not in (None, "interactive-v1", "release-v1"):
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
    if any(slide.owner_id != claim.owner_id for slide in slides):
        raise NonRetryableWorkspaceOperationError("presentation_not_found")
    if (len(slides) != presentation.n_slides
            or [slide.index for slide in slides] != list(range(len(slides)))):
        raise NonRetryableWorkspaceOperationError("svg_export_incomplete_presentation")
    try:
        if _quality_recovery_export_gate(presentation, slides) is not None:
            async with session_factory() as session:
                await require_quality_recovery_export_parent(session, presentation, slides)
    except ValueError as error:
        raise NonRetryableWorkspaceOperationError(str(error)) from error
    canvas = presentation_canvas_metadata(presentation.theme)
    revision_binding = claim.request_payload.get("svg_source_revision_binding")
    if revision_binding is not None:
        try:
            snapshot = svg_export_admission_snapshot(presentation, slides)
        except ValueError as error:
            raise NonRetryableWorkspaceOperationError(str(error)) from error
        if (not isinstance(revision_binding, dict)
                or revision_binding.get("sourceRevision") != claim.expected_revision
                or revision_binding.get("snapshotSha256") != snapshot):
            raise NonRetryableWorkspaceOperationError("svg_export_source_snapshot_changed")
    measured_slides = slides
    try:
        measured_source_preservation = _source_preservation_receipts(
            presentation, slides, parse_canvas_metadata(canvas))
        source_manifest = None
        if measured_source_preservation is not None:
            async with session_factory() as session:
                source_manifest = await _sealed_whole_page_manifest(
                    session, claim=claim, presentation=presentation, slides=slides,
                    receipts=measured_source_preservation)
    except PublicExportOperationError as error:
        raise NonRetryableWorkspaceOperationError(str(error)) from error
    if fmt == "pptx" and measured_source_preservation is not None:
        from services.svg_source_export_projection import project_cached_source_tables
        async with session_factory() as session:
            slides = await project_cached_source_tables(session, presentation=presentation, slides=slides,
                receipts=measured_source_preservation, canvas=parse_canvas_metadata(canvas), owner_id=claim.owner_id)
    svgs = [slide.svg_content for slide in slides]
    notes = [slide.speaker_note or "" for slide in slides]
    source_digest = _digest({"svgs": svgs, "notes": notes})
    source_assets = [(slide.content or {}).get("svgAssetManifest") for slide in slides]
    source_preservation = ([(slide.content or {})["svgReceipt"]["source_preservation"] for slide in slides]
                           if measured_source_preservation is not None else None)
    if any(asset is not None for asset in source_assets):
        source_digest = _digest({"svgs": svgs, "notes": notes, "assets": source_assets})
    if source_preservation is not None:
        source_digest = _digest({"svgSourceSnapshot": source_digest,
            "sourcePreservation": source_preservation})
    if source_manifest is not None:
        source_digest = _digest({"svgSourceSnapshot": source_digest,
            "sealedSourceManifest": source_manifest})
    native_objects = _native_object_entries(slides) if fmt == "pptx" else None
    if native_objects is not None:
        source_digest = _digest({"svgSourceSnapshot": source_digest, "nativeObjects": native_objects})
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
        provenance["compiler_runtime"]["runtime_version"] = (
            SVG_EXPORT_SCHEMA_V4 if source_manifest is not None else SVG_EXPORT_SCHEMA_V3)
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
        svg_meta = ((presentation.theme or {}).get("svg") or {})
        advisory = svg_meta.get("advisory")
        async with session_factory() as producer_session:
            source_review = await svg_source_review_context(producer_session, presentation)

        async def run_worker(slide_refs):
            asset_bindings = None
            if any(asset is not None for asset in source_assets):
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
            return await render_and_export_svgs(
                claim, tenant_id=admission.tenant_id, svgs=svgs, notes=notes, export_format=fmt, canvas=canvas,
                session_factory=session_factory, asset_bindings=asset_bindings,
                source_preservation=source_preservation, deadline_grant=grant, font_embedding=font_embedding,
                native_objects=native_objects, slide_refs=slide_refs,
                source_manifest=source_manifest)

        # One export per identity: a completed identity is reused without the
        # worker or asset derivation; only a claimed one runs ``run_worker``.
        from services.svg_export_identity import SvgExportRequest, export_svg_through_identity
        result, identity = await export_svg_through_identity(claim, tenant_id=admission.tenant_id,
            request=SvgExportRequest(
                export_format=fmt, svgs=svgs, notes=notes,
                canvas=parse_canvas_metadata(canvas).model_dump(mode="json", by_alias=True), slides=slides,
                source_assets=source_assets if any(asset is not None for asset in source_assets) else None,
                source_preservation=source_preservation, native_objects=native_objects,
                source_manifest=source_manifest,
                font_embedding=font_embedding, advisory=advisory, quality_policy=quality_policy),
            session_factory=session_factory, grant=grant, execute=run_worker)
        ref = result.artifact
        final_reviews = ((presentation.theme or {}).get("svg") or {}).get("finalPptxReviews") or {}
        if isinstance(final_reviews, dict) and isinstance(final_reviews.get(ref.sha256), dict):
            result.resource_metrics["final_pptx_visual_review"] = final_reviews[ref.sha256]
        receipt_args = dict(
            quality_receipt_id=f"quality:{claim.operation_id}:{ref.artifact_id}",
            operation_id=str(claim.operation_id), presentation_id=str(claim.presentation_id),
            artifact_digest=f"sha256:{ref.sha256}", source_revision=claim.expected_revision,
            scene_digest=f"sha256:{source_digest}", evaluated_at=datetime.now(timezone.utc))
        from services.svg_export_quality import svg_public_quality_receipt
        receipt = svg_public_quality_receipt(slides=measured_slides, advisory=advisory, source_preservation=source_preservation,
            source_preservation_parents=measured_source_preservation,
            worker_metrics=result.resource_metrics, exporter_version=CONVERTER_VERSION,
            require_native_coverage=fmt == "pptx",
            export_identity_digest=identity.canonical_digest, export_tenant_id=admission.tenant_id,
            review_binding=dict(presentationId=str(claim.presentation_id), ownerId=str(claim.owner_id),
                                ownerScope=str(claim.owner_scope), sourceRevision=claim.expected_revision,
                                _generationSourceReview=source_review),
            **receipt_args)
        # release-v1 stays a receipt-content gate; the identity join decides
        # only whether bytes are produced or reused, not whether they release.
        if quality_policy == "release-v1":
            from services.svg_export_release_gate import require_release_v1_receipt
            require_release_v1_receipt(receipt)
        # Post-export: the receipt's own digest is the publication quality
        # digest. ``evidenceDigests`` are the pre-export identity inputs.
        quality_digest = _digest(receipt)
        # Pre-export evidence stays in the immutable identity checkpoint. The
        # public receipt is a strict QualityReceiptV3; adding identity fields
        # here makes status projection discard the artifact-bound receipt.
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
