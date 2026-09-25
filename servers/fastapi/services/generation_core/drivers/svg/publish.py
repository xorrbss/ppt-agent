"""Publish a verified SVG deck: source-preservation receipt check, slide
persistence, and the ordered presentation-revision handoff.

Moved out of ``services/svg_presentation_publish.py`` into the SVG driver
package (P0 split, see ``docs/generation-core-architecture-20260925.md``).
``services/svg_presentation_publish.py`` stays as a one-line facade re-export
for importers and tests; ``services/svg_presentation_service.py`` re-exports
``_publish`` from there in turn.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import delete, select

from models.presentation_and_path import PresentationPathAndEditPath
from models.sql.presentation import PresentationModel, PRESENTATION_LIFECYCLE_PUBLISHED
from models.sql.slide import SlideModel
from services.authored_canvas import merge_presentation_canvas, request_canvas_metadata
from services.authored_checkpoint_service import begin_publish, complete_publish
from services.authored_presentation_artifacts import _save_slide_pngs, authored_title
from services.svg_presentation_checkpoint import _save_source_page_pngs


async def _publish(request, presentation_id, outline, language, session, job, snapshot, roles, pages,
                   *, quality_summary=None, advisory=None):
    source_preservation = None
    if getattr(request, "_source_content_svg_reflow", False):
        from services.svg_source_layout import source_pages, verify_source_receipt
        manifest = request._source_content_manifest
        if len(source_pages(manifest)) != len(pages):
            raise ValueError("svg_source_page_count_mismatch")
        for index, (payload, _) in enumerate(pages):
            verify_source_receipt(payload["svg_content"], payload.get("source_preservation"), manifest, index)
            if payload.get("terminal"):
                continue  # flag-only: verbatim (checked above), flagged, reviewed as unverified
            if payload.get("quality", {}).get("passed") is not True:
                raise ValueError("svg_source_quality_failed")
            if payload["source_preservation"].get("rendering_verified") is not True:
                raise ValueError("svg_source_quality_unverified")
        source_preservation = {"schemaVersion": "svg-source-preservation/v1",
            "manifestDigest": manifest.manifest_digest, "pageCount": len(pages)}
    from services.account_deletion_service import require_active_account_generation
    from services.compatibility_migration.admission import stamp_new_svg_presentation
    from services.presentation_publication_service import publish_presentation_revision
    from utils.owner_context import get_current_owner_id

    owner = get_current_owner_id()
    if owner is None:
        raise RuntimeError("svg_owner_required")
    job_id = job.id if job else None
    attempt_token = job.attempt_token if job else None
    operation_id = job.workspace_operation_id if job else None
    fencing_token = getattr(job, "_workspace_fencing_token", None) if job else None
    await session.rollback()  # release read transaction before ordered publication locks
    await require_active_account_generation(session, user_id=owner)
    if job:
        await begin_publish(session, job_id=job_id, attempt_token=attempt_token,
                            fencing_token=fencing_token)
    presentation = await session.scalar(select(PresentationModel).where(
        PresentationModel.id == presentation_id, PresentationModel.owner_id == owner).with_for_update())
    if presentation is None:
        presentation = PresentationModel(id=presentation_id, owner_id=owner, content=request.content,
                                         n_slides=len(pages), language=language or "ko")
        session.add(presentation)
        await session.flush()
    expected_revision = presentation.revision
    if presentation.mode not in (None, "authored", "svg") or (expected_revision > 0 and not presentation.is_svg()):
        raise RuntimeError("svg_document_mode_conflict")
    if source_preservation:
        image_refs = await asyncio.to_thread(_save_source_page_pngs, presentation_id, pages, attempt_token)
    else:
        image_refs = await asyncio.to_thread(_save_slide_pngs, presentation_id,
                                             [png for _, png in pages], attempt_token)
    stamp_new_svg_presentation(presentation)
    presentation.title = authored_title(request, outline)
    presentation.n_slides = len(pages)
    presentation.outlines = outline.model_dump(mode="json")
    presentation.content = request.content
    presentation.instructions = request.instructions
    presentation.language = language or "ko"
    presentation.layout = None
    presentation.structure = None
    presentation.reference_snapshot_ids = [str(x) for x in request.reference_snapshot_ids] or None
    quality = [payload["quality"] for payload, _ in pages]
    initial_quality = [payload.get("initial_quality", payload["quality"]) for payload, _ in pages]
    review = [i for i, item in enumerate(quality)
              if item.get("verified") is not True or item.get("passed") is not True]
    # Non-blocking; a cheap sum over evidence already computed per page.
    render_advisory_count = sum(len(payload.get("rasterRenderAdvisories", [])) for payload, _ in pages)
    presentation.theme = merge_presentation_canvas({
        **(presentation.theme or {}), "mode": "svg", "style": request.authored_style,
        "svg": {"schemaVersion": "svg-document/v1", "styleSnapshot": snapshot,
                **({"sourcePreservation": source_preservation} if source_preservation else {}),
                "qualityStatus": "review_required" if review else "passed",
                "reviewPages": review, "quality": quality,
                **({"qualitySummary": {key: value for key, value in quality_summary.items()
                                       if key != "flaggedSlides"},
                    "flaggedSlides": quality_summary.get("flaggedSlides", [])}
                   if quality_summary else {}),
                **({"advisory": advisory} if advisory else {}),
                "initialQuality": initial_quality,
                **({"renderAdvisoryCount": render_advisory_count} if render_advisory_count else {}),
                "codexGenerationPreset": request.codex_generation_preset},
    }, request_canvas_metadata(request))
    presentation.lifecycle_status = PRESENTATION_LIFECYCLE_PUBLISHED
    await session.execute(delete(SlideModel).where(SlideModel.presentation == presentation_id))
    for index, (payload, _) in enumerate(pages):
        slide_content = {"__svg__": True, "role": roles[index], "source": outline.slides[index].content,
                         **({"svgAssetManifest": payload["svgAssetManifest"]}
                            if payload.get("svgAssetManifest") else {}),
                         "svgReceipt": {key: value for key, value in payload.items()
                                       if key not in {"svg_content", "attempts", "svgAssetManifest"}}}
        session.add(SlideModel(presentation=presentation_id, owner_id=owner,
                              layout_group="svg", layout="svg:content", index=index,
                              content=slide_content,
                              svg_content=payload["svg_content"], source_format="svg",
                              properties={"image": image_refs[index]}, speaker_note=outline.slides[index].content))
    session.add(presentation)
    await session.flush()
    # complete_publish still requires the running operation; terminalize the
    # public operation only after its linked job transition in this transaction.
    if job:
        await complete_publish(session, job_id=job_id, attempt_token=attempt_token,
                               fencing_token=fencing_token)
    result = {"presentation_id": str(presentation_id), "source_format": "svg",
              "quality_status": "review_required" if review else "passed",
              "quality_pages": {"passed": len(pages) - len(review), "flagged": len(review)},
              "edit_path": f"/presentation?id={presentation_id}", "path": ""}
    await publish_presentation_revision(session, presentation_id=presentation_id,
                                       expected_revision=expected_revision, operation_id=operation_id,
                                       attempt_token=attempt_token, result=result,
                                       operation_kind="presentation.generate")
    await session.commit()
    return PresentationPathAndEditPath(presentation_id=presentation_id, path="",
                                       edit_path=f"/presentation?id={presentation_id}")


async def svg_completion_status(session, presentation_id):
    """``(message, quality_gate_status, page_counts)`` for a published SVG deck.

    Read back from what publication recorded, so the generation status can never
    claim a verdict the deck does not carry.
    """
    theme = await session.scalar(select(PresentationModel.theme).where(PresentationModel.id == presentation_id))
    svg = (theme or {}).get("svg") or {}
    review = list(svg.get("reviewPages") or [])
    total = len(svg.get("quality") or [])
    pages = {"passed": total - len(review), "flagged": len(review)}
    status = "review_required" if review else "passed"
    detail = ("preservation verified" if svg.get("sourcePreservation") and not review
              else f"{pages['passed']} passed, {pages['flagged']} flagged for review")
    return f"SVG presentation generation completed; {detail}", status, pages
