"""Server-owned final artifact review; clients cannot submit review outcomes."""
from __future__ import annotations
from services.generation_core.quality.review_coverage import review_coverage

from pathlib import Path
import asyncio
import tempfile
from threading import Event

from services.generation_core.quality.advisory_record import advisory_ran, content_digest
from utils.hashing import canonical_json_sha256, sha256_hex

FINAL_PPTX_RENDER_TIMEOUT_SECONDS = 120


def final_render_current(proof, *, artifact_digest, operation_id, presentation_id,
                         source_revision, source_digests, owner_id=None, tenant_id=None):
    if not isinstance(proof, dict):
        return False
    from workers import public_svg_pptx_render
    from workers.public_svg_pptx_convert import converter_digest
    pages = proof.get("pages")
    unsigned = {key: value for key, value in proof.items() if key != "evidenceDigest"}
    return (proof.get("schemaVersion") == public_svg_pptx_render.SCHEMA
        and proof.get("status") == "measured"
        and proof.get("artifactDigest") == artifact_digest
        and proof.get("artifactDigestAfter") == artifact_digest
        and proof.get("hashInvariant") is True and proof.get("openReadOnly") is True
        and proof.get("openWithWindow") is False
        and proof.get("operationId") == str(operation_id)
        and ("renderOperationId" not in proof or (
            proof.get("renderOperationId") == str(operation_id)
            and proof.get("renderIsolation") == "windows-job-object"
            and proof.get("rendererQuit") is True))
        and proof.get("presentationId") == str(presentation_id)
        and proof.get("sourceRevision") == source_revision
        and proof.get("sourceDigests") == source_digests
        and (owner_id is None or proof.get("ownerId") == str(owner_id))
        and (tenant_id is None or proof.get("tenantId") == tenant_id)
        and proof.get("converterDigest") == converter_digest()
        and proof.get("checkerCodeDigest") == sha256_hex(Path(public_svg_pptx_render.__file__).read_bytes())
        and proof.get("evidenceDigest") == "sha256:" + canonical_json_sha256(unsigned)
        and isinstance(proof.get("availablePhysicalMemoryBytes"), int)
        and proof["availablePhysicalMemoryBytes"] >= public_svg_pptx_render.MINIMUM_AVAILABLE_MEMORY
        and isinstance(pages, list) and len(pages) == len(source_digests) > 0
        and proof.get("pageCount") == len(pages)
        and all(isinstance(page, dict) and type(page.get("index")) is int and page["index"] == index
                and isinstance(page.get("pngSha256"), str) and len(page["pngSha256"]) == 64
                for index, page in enumerate(pages)))


async def load_final_review_inputs(session, *, request, presentation, slides):
    from sqlalchemy import select
    from models.sql.public_artifact import PublicArtifact
    from models.sql.workspace import WorkspaceArtifact, WorkspaceOperation
    from services.artifact_store import configured_public_artifact_service
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError
    from utils.owner_context import get_current_tenant_id
    from services.svg_export_operation import require_current_header_revision_authority, svg_export_admission_snapshot
    header_authority = await require_current_header_revision_authority(session, presentation, slides)
    if header_authority is not None:
        svg_export_admission_snapshot(presentation, slides, header_revision_authority=header_authority)
    public = await session.get(PublicArtifact, request.artifact_id)
    binding = await session.scalar(select(WorkspaceArtifact).where(
        WorkspaceArtifact.public_artifact_id == request.artifact_id,
        WorkspaceArtifact.presentation_id == presentation.id,
        WorkspaceArtifact.owner_id == presentation.owner_id,
        WorkspaceArtifact.source_revision == presentation.revision,
        WorkspaceArtifact.kind == "export.pptx"))
    if (public is None or binding is None or public.owner_id != presentation.owner_id
            or public.kind != "presentation-pptx" or public.tenant_id != get_current_tenant_id()):
        raise NonRetryableWorkspaceOperationError("svg_final_pptx_artifact_binding_invalid")
    export = await session.get(WorkspaceOperation, binding.operation_id)
    if (export is None or export.state != "succeeded" or export.owner_id != presentation.owner_id
            or export.presentation_id != presentation.id or export.expected_revision != presentation.revision):
        raise NonRetryableWorkspaceOperationError("svg_final_pptx_export_not_succeeded")
    sources = [sha256_hex((slide.svg_content or "").encode()) for slide in slides]
    service = configured_public_artifact_service()
    from services.authored_canvas import presentation_canvas_contract
    canvas = presentation_canvas_contract(presentation.theme)
    payload = await service.read_ref_bytes(public.to_ref())
    return dict(artifactId=str(public.id), artifactDigest="sha256:" + public.sha256,
                parentExportOperationId=str(export.id), artifactBytes=payload,
                sourceDigests=sources, ownerId=str(presentation.owner_id), tenantId=public.tenant_id,
                presentationId=str(presentation.id), sourceRevision=presentation.revision,
                widthPx=canvas.width_px, heightPx=canvas.height_px)


async def render_final_review_inputs(inputs, claim):
    """Only the admitted review executes Office, after its read transaction ends."""
    from workers.public_svg_pptx_render import render_final_pptx
    from workers.public_svg_pptx_convert import converter_digest
    from services.workspace_operation_handler_registry import (
        NonRetryableWorkspaceOperationError, RetryableWorkspaceOperationError,
    )
    cancellation_event = Event()

    def render():
        with tempfile.TemporaryDirectory(prefix="svg-final-review-") as temporary:
            directory = Path(temporary)
            target = directory / "artifact.pptx"
            target.write_bytes(inputs["artifactBytes"])
            proof = render_final_pptx(target, directory, page_count=len(inputs["sourceDigests"]),
                width_px=inputs["widthPx"], height_px=inputs["heightPx"], timeout=FINAL_PPTX_RENDER_TIMEOUT_SECONDS,
                cancellation_event=cancellation_event)
            if proof.get("status") != "measured":
                if proof.get("retryable") is True:
                    raise RetryableWorkspaceOperationError(proof["reasonCode"])
                raise NonRetryableWorkspaceOperationError("svg_final_pptx_render_unavailable")
            proof.update(operationId=str(claim.operation_id), renderOperationId=str(claim.operation_id),
                parentExportOperationId=inputs["parentExportOperationId"], converterDigest=converter_digest(),
                **{key: inputs[key] for key in (
                    "ownerId", "tenantId", "presentationId", "sourceRevision", "sourceDigests")})
            proof["evidenceDigest"] = "sha256:" + canonical_json_sha256(proof)
            if not final_render_current(proof, artifact_digest=inputs["artifactDigest"],
                    operation_id=claim.operation_id, presentation_id=claim.presentation_id,
                    source_revision=claim.expected_revision, source_digests=inputs["sourceDigests"],
                    owner_id=claim.owner_id, tenant_id=inputs["tenantId"]):
                raise NonRetryableWorkspaceOperationError("svg_final_pptx_render_binding_invalid")
            pngs = [(directory / f"page-{page['index']:03d}.png").read_bytes() for page in proof["pages"]]
            if any(sha256_hex(png) != page["pngSha256"] for png, page in zip(pngs, proof["pages"], strict=True)):
                raise NonRetryableWorkspaceOperationError("svg_final_pptx_render_binding_invalid")
            return dict(inputs, proof=proof, pngs=pngs)

    try:
        return await asyncio.to_thread(render)
    except asyncio.CancelledError:
        cancellation_event.set()
        raise


async def persist_final_review_render(session, inputs, claim):
    from services.artifact_store import configured_public_artifact_service
    service = configured_public_artifact_service()
    proof = inputs["proof"]
    for page, png in zip(proof["pages"], inputs["pngs"], strict=True):
        ref = await service.persist_bytes(session, owner_id=claim.owner_id, tenant_id=inputs["tenantId"],
            kind="slide-png", payload=png, media_type="image/png", created_by_operation_id=claim.operation_id,
            retention_class="ephemeral")
        page["artifactRef"] = ref.model_dump(mode="json")
    proof["evidenceDigest"] = "sha256:" + canonical_json_sha256(
        {key: value for key, value in proof.items() if key != "evidenceDigest"})


def seal_final_review(claim, inputs, vision):
    proof = inputs["proof"]
    review = dict(schemaVersion="svg-final-pptx-visual-review/v1", operationId=str(claim.operation_id),
        ownerId=str(claim.owner_id), ownerScope=str(claim.owner_scope), tenantId=proof["tenantId"],
        presentationId=str(claim.presentation_id), sourceRevision=claim.expected_revision,
        artifactId=inputs["artifactId"], artifactDigest=inputs["artifactDigest"],
        parentExportOperationId=inputs["parentExportOperationId"], renderOperationId=proof["operationId"],
        renderEvidenceDigest=proof["evidenceDigest"], sourceDigests=proof["sourceDigests"],
        pngDigests=[page["pngSha256"] for page in proof["pages"]], vision=vision)
    if "renderOperationId" in proof:
        review["finalPptxRender"] = proof
    return {**review, "digest": content_digest(review)}


def final_pptx_visual_domain(source_domain, *, worker_metrics, artifact_digest,
                             operation_id, presentation_id, source_revision, source_digests, expected_binding=None):
    """Keep source review and final artifact review independently required."""
    if source_domain.get("status") != "passed":
        return source_domain
    review = worker_metrics.get("final_pptx_visual_review")
    # Historical exports remain readable, subject to the same checker freshness gate.
    proof = (review.get("finalPptxRender") if isinstance(review, dict) else None)
    render_operation_id = review.get("operationId") if isinstance(proof, dict) else None
    if proof is None:
        structural = worker_metrics.get("public_export_worker_evidence", {}).get("structuralReadback", {})
        proof = structural.get("finalPptxRender")
    if not final_render_current(proof, artifact_digest=artifact_digest,
            operation_id=render_operation_id or (proof.get("operationId") if isinstance(proof, dict) else operation_id),
            presentation_id=presentation_id, source_revision=source_revision, source_digests=source_digests):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_render_unavailable")
    if not isinstance(review, dict) or not advisory_ran(review):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_visual_review_unavailable")
    if "finalPptxRender" in review and (
            proof.get("renderOperationId") != review.get("operationId")
            or proof.get("parentExportOperationId") != review.get("parentExportOperationId")
            or any(not isinstance(page.get("artifactRef"), dict) or any(
                page["artifactRef"].get(key) != value for key, value in {
                    "owner_id": proof["ownerId"], "tenant_id": proof["tenantId"],
                    "created_by_operation_id": review["operationId"], "sha256": page["pngSha256"],
                    "kind": "slide-png", "media_type": "image/png"}.items()) for page in proof["pages"])):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_visual_review_stale")
    unsigned = {key: value for key, value in review.items() if key != "digest"}
    if (review.get("digest") != content_digest(unsigned)
            or review.get("artifactDigest") != artifact_digest
            or not review.get("operationId") or not review.get("parentExportOperationId")
            or review.get("renderOperationId") != proof["operationId"]
            or review.get("presentationId") != str(presentation_id)
            or review.get("sourceRevision") != source_revision
            or review.get("sourceDigests") != source_digests
            or review.get("ownerId") != proof["ownerId"] or review.get("tenantId") != proof["tenantId"]
            or review.get("renderEvidenceDigest") != proof["evidenceDigest"]
            or review.get("pngDigests") != [page["pngSha256"] for page in proof["pages"]]):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_visual_review_stale")
    if expected_binding is not None and any(review.get(key) != value for key, value in expected_binding.items()):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_visual_review_owner_stale")
    if (review["vision"].get("promptVersion") != "svg-required-visual-review/v1"
            or review_coverage(review["vision"]).slide_count != len(proof["pages"])
            or not review_coverage(review["vision"]).matches(
                range(len(proof["pages"])), [page["pngSha256"] for page in proof["pages"]])):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_visual_review_coverage_incomplete")
    if any(entry.get("issues") for entry in review_coverage(review["vision"]).findings()):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_visual_review_flagged")
    evidence = dict(source_domain["evidence"])
    evidence["outputDigest"] = content_digest({"sourceReview": evidence["outputDigest"],
                                             "finalPptxReview": review["digest"]})
    evidence["measurements"] = {**evidence["measurements"], "finalPptxRenderMeasured": True,
        "finalPptxReviewedPages": len(proof["pages"]), "finalPptxReviewDigest": review["digest"],
        "finalPptxRenderOperationId": proof["operationId"], "finalPptxReviewOperationId": review["operationId"]}
    return dict(required=True, status="passed", evidence=evidence)
