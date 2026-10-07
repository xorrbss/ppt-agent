"""Server-owned final artifact review; clients cannot submit review outcomes."""
from __future__ import annotations

from pathlib import Path

from services.generation_core.quality.advisory_record import advisory_ran, content_digest
from utils.hashing import canonical_json_sha256, sha256_hex


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
        and all(type(page.get("index")) is int and page["index"] == index
                and isinstance(page.get("pngSha256"), str) and len(page["pngSha256"]) == 64
                for index, page in enumerate(pages)))


async def review_final_render(*, claim, tenant_id, artifact, worker_metrics, slides,
                            session_factory, context):
    from services.artifact_store import configured_public_artifact_service
    from services.generation_core.quality.vision_advisory import run_vision_advisory
    from services.public_export_operation import _public_worker_output
    from services.provider_usage_instrumentation import public_operation_scope, provider_usage_scope
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError

    structural = worker_metrics.get("public_export_worker_evidence", {}).get("structuralReadback", {})
    proof = structural.get("finalPptxRender")
    sources = [sha256_hex((slide.svg_content or "").encode()) for slide in slides]
    if not final_render_current(proof, artifact_digest="sha256:" + artifact.sha256,
            operation_id=proof.get("operationId") if isinstance(proof, dict) else claim.operation_id,
            presentation_id=claim.presentation_id, source_revision=claim.expected_revision,
            source_digests=sources, owner_id=claim.owner_id, tenant_id=tenant_id):
        raise NonRetryableWorkspaceOperationError("svg_final_pptx_render_unavailable")
    service = configured_public_artifact_service()
    pngs = []
    async with session_factory() as session:
        for page in proof["pages"]:
            ref = _public_worker_output(page["artifactRef"], retention_class="ephemeral")
            if (str(ref.owner_id) != proof["ownerId"] or ref.tenant_id != tenant_id
                    or str(ref.created_by_operation_id) != proof["operationId"]
                    or ref.kind != "slide-png" or ref.media_type != "image/png"
                    or ref.sha256 != page["pngSha256"]):
                raise NonRetryableWorkspaceOperationError("svg_final_pptx_render_binding_invalid")
            await service.register_ref(session, ref)
            pngs.append(await service.read_ref_bytes(ref))
        await session.commit()
    await context.raise_if_cancelled()
    with public_operation_scope(claim.operation_id, attempt_token=claim.attempt_token,
            fencing_token=claim.fencing_token), provider_usage_scope(job_id=None,
            operation="quality_review", stage="final_pptx_vqa", attempt_number=claim.attempt_number):
        vision = await run_vision_advisory(pngs, slide_count=len(pngs), required=True)
    await context.raise_if_cancelled()
    if not advisory_ran({"vision": vision}):
        code = ("public_provider_result_unknown"
                if vision.get("skipped") == "provider_outcome:public_provider_result_unknown"
                else "svg_final_pptx_visual_review_unavailable")
        raise NonRetryableWorkspaceOperationError(code)
    review = dict(schemaVersion="svg-final-pptx-visual-review/v1", operationId=str(claim.operation_id),
        ownerId=str(claim.owner_id), ownerScope=str(claim.owner_scope), tenantId=tenant_id,
        presentationId=str(claim.presentation_id), sourceRevision=claim.expected_revision,
        artifactDigest="sha256:" + artifact.sha256, renderEvidenceDigest=proof["evidenceDigest"],
        pngDigests=[page["pngSha256"] for page in proof["pages"]], vision=vision)
    worker_metrics["final_pptx_visual_review"] = {**review, "digest": content_digest(review)}


def final_pptx_visual_domain(source_domain, *, worker_metrics, artifact_digest,
                             operation_id, presentation_id, source_revision, source_digests):
    """Keep source review and final artifact review independently required."""
    if source_domain.get("status") != "passed":
        return source_domain
    proof = worker_metrics.get("public_export_worker_evidence", {}).get("structuralReadback", {}).get("finalPptxRender")
    if not final_render_current(proof, artifact_digest=artifact_digest,
            operation_id=proof.get("operationId") if isinstance(proof, dict) else operation_id,
            presentation_id=presentation_id, source_revision=source_revision, source_digests=source_digests):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_render_unavailable")
    review = worker_metrics.get("final_pptx_visual_review")
    if not isinstance(review, dict) or not advisory_ran(review):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_visual_review_unavailable")
    unsigned = {key: value for key, value in review.items() if key != "digest"}
    if (review.get("digest") != content_digest(unsigned)
            or review.get("artifactDigest") != artifact_digest
            or review.get("operationId") != str(operation_id)
            or review.get("presentationId") != str(presentation_id)
            or review.get("sourceRevision") != source_revision
            or review.get("ownerId") != proof["ownerId"] or review.get("tenantId") != proof["tenantId"]
            or review.get("renderEvidenceDigest") != proof["evidenceDigest"]
            or review.get("pngDigests") != [page["pngSha256"] for page in proof["pages"]]):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_visual_review_stale")
    if (review["vision"].get("promptVersion") != "svg-required-visual-review/v1"
            or review["vision"].get("slideCount") != len(proof["pages"])
            or review["vision"].get("reviewedPageIndices") != list(range(len(proof["pages"])) )):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_visual_review_coverage_incomplete")
    if any(entry.get("issues") for entry in review["vision"]["slides"]):
        return dict(required=True, status="failed", reasonCode="svg_final_pptx_visual_review_flagged")
    evidence = dict(source_domain["evidence"])
    evidence["outputDigest"] = content_digest({"sourceReview": evidence["outputDigest"],
                                             "finalPptxReview": review["digest"]})
    evidence["measurements"] = {**evidence["measurements"], "finalPptxRenderMeasured": True,
        "finalPptxReviewedPages": len(proof["pages"]), "finalPptxReviewDigest": review["digest"]}
    return dict(required=True, status="passed", evidence=evidence)
