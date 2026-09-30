"""SVG candidates on the existing preview-task and revision-CAS workflow."""
from __future__ import annotations

from copy import deepcopy
import xml.etree.ElementTree as ET

from sqlalchemy import update
from constants.user_messages import UserMessageKey, user_message
from models.sql.slide import SlideModel
from services.authored_canvas import presentation_canvas_contract, presentation_canvas_metadata
from services.authored_presentation_artifacts import _save_slide_pngs
from services.svg_contract import finalize_svg
from services.svg_renderer import render_svg_with_metrics
from services.generation_core.authoring.author_slide_svg import author_slide_svg
from utils.hashing import sha256_hex


async def preview_svg(presentation, slides, index, request, task, session):
    from services.authored_page_remake_service import (
        PREVIEW_KIND, _set_task, _canonical_digest, authored_page_source_digest,
    )
    target = slides[index]
    from services.svg_document_mutations import read_svg_slide_assets, rebound_placement, materialize_rebound_svg
    assets = await read_svg_slide_assets(session, presentation, target)
    canvas = presentation_canvas_contract(presentation.theme)
    from services.document_canvas import DocumentCanvasMetadata, parse_canvas_metadata
    metadata = parse_canvas_metadata(presentation_canvas_metadata(presentation.theme))
    physical_kwargs = {"physical_canvas": metadata} if isinstance(metadata, DocumentCanvasMetadata) else {}
    snapshot = presentation.theme["svg"]["styleSnapshot"]
    manifest = {}
    for number, node in enumerate(ET.fromstring(target.svg_content).iter()):
        if node.tag.endswith("}image"):
            manifest[f"source-slot-{number}"] = {"data_url": node.get("href"), "required": True,
                                                "description": "Preserve this existing source image"}
    contract = None
    if assets is not None:
        from types import SimpleNamespace
        from services.artifact_store import configured_public_artifact_service
        from services.svg_generation_assets import author_images
        from services.svg_asset_resolver import _contract_manifest
        from utils.owner_context import get_current_owner_id, get_current_tenant_id
        source_index = target.content["svgAssetManifest"].get("sourcePageIndex", index)
        manifest = await author_images(session, configured_public_artifact_service(),
            context=SimpleNamespace(owner_id=get_current_owner_id(), tenant_id=get_current_tenant_id()),
            manifest=assets[0], page_index=source_index)
        contract = {key: value for key, value in _contract_manifest(assets[0]).items() if key in manifest}
    source = target.content.get("source") or "\n".join(ET.fromstring(target.svg_content).itertext())
    baseline_digest = authored_page_source_digest(target)
    await _set_task(session, task, status="running",
                    message=user_message(UserMessageKey.REMAKE_SVG_RUNNING, page=index + 1))
    from utils.generation_model import generation_model_context
    instruction = "\n".join(value for value in [presentation.instructions, request.instruction] if value)
    with generation_model_context(presentation.theme["svg"].get("codexGenerationPreset")):
        result = await author_slide_svg(source, snapshot, target.content.get("role", "content"), index, len(slides),
                                        instructions=instruction, source_images=manifest,
                                        canonical_source_images=assets is not None, **physical_kwargs)
    if result.receipt.get("validation_error"):
        raise ValueError(result.receipt["validation_error"])
    final = finalize_svg(result.svg_content, index=index, n=len(slides), width=canvas.width_px,
                         height=canvas.height_px, style_snapshot=snapshot, source_manifest=contract)
    render_source = final
    if assets is not None:
        placement = rebound_placement(final, assets[0], assets[1], revision=presentation.revision + 1)
        render_source = await materialize_rebound_svg(session, presentation, final, assets[0], placement, source_index)
    render = await render_svg_with_metrics(render_source, width=canvas.width_px, height=canvas.height_px,
                                           instructions=instruction, **physical_kwargs)
    image = _save_slide_pngs(presentation.id, [render.png], str(task.id))[0]
    digest = _canonical_digest({"svg": final, "image": image})
    ready_message = user_message(UserMessageKey.REMAKE_SVG_READY, page=index + 1)
    await _set_task(session, task, status="completed", message=ready_message, data={
        "kind": PREVIEW_KIND, "phase": "preview_ready", "source_format": "svg",
        "presentation_id": str(presentation.id), "slide_id": str(target.id), "slide_index": index,
        "source_digest": baseline_digest, "candidate_digest": digest,
        "source_revision": presentation.revision,
        "candidate_svg": final, "candidate_image": image, "svg_quality": render.quality,
        "raw_response": result.raw_response, "provider_receipt": result.receipt,
        "instruction": instruction,
        "validation": {"candidate_render": "passed", "candidate_dom": "warning", "candidate_change": "changed",
                       "semantic_review": "required"},
        "publish": {"status": "not_started", "rollback_available": False},
    })


async def publish_svg_candidate(session, presentation, slides, index, task, *, snapshot=True,
                                operation_id=None, attempt_token=None, expected_revision=None):
    from services.authored_page_remake_service import (
        AuthoredPageRemakeError, PREVIEW_KIND, _canonical_digest, authored_page_source_digest,
        _json_cas_predicate, version_service,
    )
    target = slides[index]
    data = task.data or {}
    from services.svg_document_mutations import read_svg_slide_assets, rebind_svg_assets
    assets = await read_svg_slide_assets(session, presentation, target)
    if (task.status != "completed" or task.owner_id != presentation.owner_id
            or data.get("kind") != PREVIEW_KIND or data.get("phase") != "preview_ready"
            or data.get("presentation_id") != str(presentation.id) or data.get("slide_id") != str(target.id)
            or data.get("slide_index") != index or data.get("source_format") != "svg"
            or data.get("source_revision") != presentation.revision
            or data.get("source_digest") != authored_page_source_digest(target)):
        raise AuthoredPageRemakeError("svg_page_remake_source_changed", stage="publish_validation")
    svg, image = data.get("candidate_svg"), data.get("candidate_image")
    if not isinstance(svg, str) or not isinstance(image, str) or (
            data.get("candidate_digest") != _canonical_digest({"svg": svg, "image": image})):
        raise AuthoredPageRemakeError("svg_page_remake_candidate_invalid", stage="publish_validation")
    canvas = presentation_canvas_contract(presentation.theme)
    envelope = None
    render_source = svg
    if assets is not None:
        envelope, render_source = await rebind_svg_assets(session, presentation, target, svg, assets,
            page_index=index, operation_id=operation_id, attempt_token=attempt_token,
            expected_revision=expected_revision)
    from services.document_canvas import DocumentCanvasMetadata, parse_canvas_metadata
    metadata = parse_canvas_metadata(presentation_canvas_metadata(presentation.theme))
    physical_kwargs = {"physical_canvas": metadata} if isinstance(metadata, DocumentCanvasMetadata) else {}
    rendered = await render_svg_with_metrics(svg, width=canvas.width_px, height=canvas.height_px,
        materialized_svg=render_source if render_source != svg else None,
        instructions=data.get("instruction"), **physical_kwargs)
    if not image.endswith(f"-{sha256_hex(rendered.png)}.png"):
        raise AuthoredPageRemakeError("svg_page_remake_capture_changed", stage="publish_validation")
    if snapshot:
        await version_service.snapshot_slides(session, presentation.id,
            [item.model_dump(mode="json") for item in slides],
            label=user_message(UserMessageKey.VERSION_LABEL_PAGE_REMAKE, page=index + 1), force=True)
    content = deepcopy(target.content or {})
    if envelope is not None:
        content["svgAssetManifest"] = envelope
    from services.svg_page_publication import set_svg_page
    fields = {}
    set_svg_page(fields, svg_content=svg, content=content, quality=rendered.quality)
    properties = {**(target.properties or {}), "image": image}
    changed = await session.execute(update(SlideModel).where(
        SlideModel.id == target.id, SlideModel.presentation == presentation.id,
        SlideModel.owner_id == presentation.owner_id, SlideModel.svg_content == target.svg_content,
        _json_cas_predicate(session, SlideModel.content, target.content),
        _json_cas_predicate(session, SlideModel.properties, target.properties),
    ).values(**fields, properties=properties).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise AuthoredPageRemakeError("svg_page_remake_source_changed", stage="publish")
    await session.refresh(target)
    theme = dict(presentation.theme)
    svg_meta = dict(theme["svg"])
    qualities = list(svg_meta.get("quality", []))
    if index < len(qualities):
        qualities[index] = rendered.quality
    from services.generation_core.quality.page_outcome import review_page_indices
    review = review_page_indices(qualities)
    quality_status = "review_required" if review else "passed"
    svg_meta.update(quality=qualities, reviewPages=review, qualityStatus=quality_status)
    theme["svg"] = svg_meta
    presentation.theme = theme
    task.data = {**data, "phase": "published", "publish": {"status": "published", "rollback_available": True}}
    session.add_all([presentation, task])
    return {"status": "published", "slide_index": index, "preview_task_id": str(task.id),
            "source_format": "svg", "quality_status": quality_status}
