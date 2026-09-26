"""Turn one author attempt's SVG into a measured, quality-scored page: the
``render_svg_with_metrics`` call, PNG write, quality-dict assembly (including
the canonical ``sourceImages`` omitted check), and validation-error handling
that decides whether the deck's attempt ``while`` loop retries or terminates.

Split out of ``services/svg_presentation_generate.py`` (P0 split, see
``docs/generation-core-architecture-20260925.md``); ``collect.py`` calls
:func:`attempt_render` once per author attempt and interprets the ``RETRY``
sentinel by continuing its own loop.
"""
from __future__ import annotations

from hashlib import sha256
import json

from services.authored_checkpoint_errors import AuthoredStageError
from services.svg_presentation_checkpoint import _required_numeric_claims

RETRY = object()


async def attempt_render(*, ctx, request, presentation_id, index, content, payload, svg, ordinal,
                         job, checkpoint, async_session_maker, write_png):
    from services.svg_contract import finalize_svg
    from services.svg_renderer import render_svg_with_metrics
    from services.svg_source_layout import verify_source_receipt

    try:
        if payload["attempts"][-1]["receipt"].get("validation_error"):
            raise ValueError(payload["attempts"][-1]["receipt"]["validation_error"])
        if ctx.source_reflow or ctx.raster_reconstruction:
            final = svg
        else:
            finalize_source_manifest = ({slot: {"required": item["policy"]["required"],
                    "allowOmit": item["policy"]["allowOmit"]}
                    for slot, item in ctx.manifests[index].items()}
                if ctx.canonical_assets else None)
            allow_image_only = bool(
                ctx.raster_reconstruction and ctx.raster_analysis is not None and ctx.raster_pin is not None
                and ctx.raster_analysis.pages[index].render_mode == 'full_page_preserve'
                and ctx.raster_analysis.pages[index].page_number in ctx.raster_pin.full_page_preserve_pages)
            final = finalize_svg(svg, index=index, n=ctx.count, width=ctx.canvas.width_px,
                                 height=ctx.canvas.height_px, style_snapshot=ctx.snapshot,
                                 source_manifest=finalize_source_manifest,
                                 allow_image_only=allow_image_only)
        if ctx.source_reflow:
            verify_source_receipt(final, payload.get("source_preservation"), ctx.source_document, index)
        materialized = None
        if ctx.raster_reconstruction:
            from services.artifact_store import configured_public_artifact_service
            from services.raster_source_assets import prepare_raster_page_assets
            from services.svg_generation_assets import publication_context

            async with async_session_maker() as asset_session:
                raster_asset_context, raster_asset_revision = await publication_context(asset_session, job)
                raster_assets = await prepare_raster_page_assets(
                    asset_session, configured_public_artifact_service(),
                    approval_pin=ctx.raster_pin,
                    owner_id=raster_asset_context.owner_id,
                    tenant_id=raster_asset_context.tenant_id,
                    publication_context=raster_asset_context,
                    presentation_id=presentation_id,
                    source_revision=raster_asset_revision,
                    slide_revision=raster_asset_revision,
                    page_number=index + 1,
                    output_page_index=index,
                    canonical_svg=final,
                    width=ctx.canvas.width_px,
                    height=ctx.canvas.height_px,
                )
                await asset_session.commit()
            payload["svgAssetManifest"] = raster_assets.screen_envelope
            payload["rasterCropLineage"] = list(raster_assets.crop_lineage)
            payload["rasterAnalysisDigest"] = ctx.raster_pin.analysis_digest
            payload["rasterApprovalPin"] = ctx.raster_pin.model_dump(mode="json", by_alias=True)
            payload["rasterSourcePageNumber"] = index + 1
            materialized = raster_assets.materialized_svg
        if ctx.canonical_assets:
            from services.svg_generation_assets import screen_placement
            # Candidate SVG is durable before image rendering. A
            # derivative failure resumes here without rebuying it.
            payload["svg_content"] = final
            await checkpoint(job, index, payload=payload)
            try:
                async with async_session_maker() as asset_session:
                    envelope, materialized = await screen_placement(asset_session, ctx.service,
                        context=ctx.asset_context, revision=ctx.asset_revision, manifest=ctx.source_manifest,
                        source_pointer=ctx.source_pointer, svg=final, page_index=index,
                        candidates=ctx.planned[index], width=ctx.canvas.width_px, height=ctx.canvas.height_px,
                        saved_envelope=payload.get("svgAssetManifest"))
                    await asset_session.commit()
                payload["svgAssetManifest"] = envelope
                await checkpoint(job, index, payload=payload)
            except (ValueError, OSError) as error:
                raise AuthoredStageError(slide_index=index, stage="render",
                    code="svg_asset_screen_derivative_failed", retryable=False) from error
        try:
            required_text = (
                [binding["text"] for binding in payload["source_preservation"]["bindings"]
                 if binding.get("kind") in {"text", "table_cell"} and binding.get("text", "").strip()]
                if ctx.source_reflow else _required_numeric_claims(content)
            )
            font_profile = ctx.source_font_profile if ctx.source_reflow else None
            raster_authorized_measurement = False
            if ctx.raster_reconstruction:
                from services.raster_redesign_generation import raster_measurement_inputs
                required_text, raster_authorized_measurement, font_profile = raster_measurement_inputs(
                    ctx.raster_analysis, ctx.raster_pin, index + 1, final)
            async with ctx.render_semaphore:
                rendered = await render_svg_with_metrics(final, width=ctx.canvas.width_px,
                                                     height=ctx.canvas.height_px,
                                                     instructions=request.instructions,
                                                     required_text=required_text,
                                                     **({"allow_image_only": True}
                                                        if raster_authorized_measurement else {}),
                                                     **({"source_font_profile": font_profile}
                                                        if font_profile else {}),
                                                     materialized_svg=materialized, **ctx.physical_kwargs)
        except ValueError as error:
            if ctx.canonical_assets:
                raise AuthoredStageError(slide_index=index, stage="render",
                    code="svg_asset_capture_failed", retryable=False) from error
            raise
    except ValueError as error:
        hint = str(error)[:600]
        payload["repair_hint"] = hint
        payload["attempts"][-1]["validation_error"] = hint
        if ordinal == 0:
            payload["initial_quality"] = {
                "stage": "first_response", "passed": False, "verified": False,
                "failures": [{"kind": "validation_failed", "detail": hint}],
            }
        payload["svg_content"] = None
        await checkpoint(job, index, payload=payload)
        if ordinal == 1 or ctx.raster_reconstruction:
            code = str(error).split(":", 1)[0]
            source_plan_failure = ctx.source_reflow and code.startswith("svg_source_")
            raise AuthoredStageError(slide_index=index,
                stage="author" if source_plan_failure else "render",
                code=code if source_plan_failure else "svg_validation_failed",
                retryable=False) from error
        return RETRY
    payload["attempts"][-1]["quality"] = rendered.quality
    if payload.get("fontAdvisories"):
        rendered.quality.setdefault("advisories", []).extend(payload["fontAdvisories"])
    if ordinal == 0:
        payload["initial_quality"] = {
            **rendered.quality, "stage": "first_response",
        }
    payload["last_valid_svg"] = final
    if rendered.quality.get("passed") is False and ordinal == 0 and not payload.get("repair_unavailable"):
        hint = json.dumps(rendered.quality, ensure_ascii=False)[:3000]
        payload["repair_hint"] = hint
        payload["svg_content"] = None
        await checkpoint(job, index, payload=payload)
        return RETRY
    if payload.get("repair_unavailable") and not ctx.raster_reconstruction:
        rendered.quality["passed"] = False
        rendered.quality.setdefault("failures", []).append({"kind": "provider_repair_unavailable"})
    if ctx.canonical_assets:
        import xml.etree.ElementTree as ET
        used = {node.get("href") for node in ET.fromstring(final).iter()
                if node.tag.rsplit("}", 1)[-1] == "image"}
        omitted = sorted(set(ctx.manifests[index]) - used)
        rendered.quality["sourceImages"] = {
            "offered": sorted(ctx.manifests[index]), "used": sorted(used), "omitted": omitted,
        }
        if omitted:
            rendered.quality["passed"] = False
            rendered.quality.setdefault("failures", []).append({
                "kind": "source_image_omitted", "slotIds": omitted,
            })
    from services.generation_core.drivers.svg.embed_assets import apply_vector_icon_quality
    apply_vector_icon_quality(rendered.quality, payload)
    payload.update(svg_content=final, source_digest=sha256(final.encode()).hexdigest(),
                   quality=rendered.quality, **write_png(rendered.png))
    if ctx.source_reflow:
        if rendered.quality.get("passed") is not True:
            raise AuthoredStageError(slide_index=index, stage="render",
                code="svg_source_quality_failed", retryable=False)
        rendered.quality.update(verified=True, semantic_review="source_preservation_verified")
        payload["source_preservation"]["rendering_verified"] = True
    await checkpoint(job, index, payload=payload)
    return payload, None if ctx.source_reflow else rendered.png
