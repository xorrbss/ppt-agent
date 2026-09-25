"""The per-deck SVG generation loop: fan out one durable page-attempt task per
slide, author/repair/render/verify each page against its checkpoint, then
hand the verified pages to publication.

Extracted out of ``svg_presentation_service.py`` (kept at/under the file-size
ratchet). ``_generate`` shadows the facade's mutable hooks (``async_session_maker``,
``_checkpoint``, ``_read_png``, ``_write_png``, ``_publish``) onto locals read from
``services.svg_presentation_service`` at call time, so a test that monkeypatches an
attribute on that facade module still reaches the nested ``page``/``tracked_page``
closures below -- exactly as it would if this were still one file.
"""
from __future__ import annotations

import asyncio
import json
import re
from hashlib import sha256

from services.authored_checkpoint_errors import AuthoredStageError
from services.provider_usage_instrumentation import provider_usage_scope
from services.svg_presentation_checkpoint import (
    _collect_pages_or_fail, _digest, _provider_attempt_count, _required_numeric_claims,
)
from services.svg_presentation_prepare import _prepare_generation


async def _generate(request, presentation_id, outline, language, session, job):
    from services.svg_contract import finalize_svg
    from services.svg_renderer import render_svg_with_metrics
    from services.svg_source_layout import verify_source_receipt
    from utils.llm_calls.author_slide_svg import author_slide_svg

    from services import svg_presentation_service as _facade
    async_session_maker = _facade.async_session_maker
    _checkpoint = _facade._checkpoint
    _read_png = _facade._read_png
    _write_png = _facade._write_png
    _publish = _facade._publish

    ctx = await _prepare_generation(request, presentation_id, outline, session, job)
    source_reflow = ctx.source_reflow
    raster_reconstruction = ctx.raster_reconstruction
    raster_analysis = ctx.raster_analysis
    raster_pin = ctx.raster_pin
    source_document = ctx.source_document
    source_units = ctx.source_units
    canonical_assets = ctx.canonical_assets
    canvas = ctx.canvas
    physical_kwargs = ctx.physical_kwargs
    snapshot = ctx.snapshot
    source_font_profile = ctx.source_font_profile
    count = ctx.count
    roles = ctx.roles
    manifests = ctx.manifests
    planned = ctx.planned
    source_manifest = ctx.source_manifest
    source_pointer = ctx.source_pointer
    asset_context = ctx.asset_context
    asset_revision = ctx.asset_revision
    service = ctx.service
    semaphore = ctx.semaphore
    render_semaphore = ctx.render_semaphore
    fatal_error = None

    async def page(index):
        async with semaphore:
            if fatal_error is not None:
                raise fatal_error
            content = outline.slides[index].content
            input_digest = _digest({"content": content, "style": snapshot, "index": index,
                                    "count": count, "instructions": request.instructions,
                                    "language": language, "preset": request.codex_generation_preset})
            input_digest = _digest({"page": input_digest, "assets": manifests[index]})
            if source_reflow:
                input_digest = _digest({"page": input_digest,
                    "source_manifest": source_document.manifest_digest,
                    "font_profile": source_font_profile,
                    "source_page": source_units[index].model_dump(mode="json"),
                    "source_layout_version": "svg-source-layout/v2"})
            if raster_reconstruction:
                input_digest = _digest({"page": input_digest, "rasterApproval": raster_pin.approval_digest})
            saved = await _checkpoint(job, index) or {}
            if saved.get("input_digest") and saved.get("input_digest") != input_digest:
                raise RuntimeError("svg_checkpoint_input_conflict")
            png = _read_png(saved)
            if png is not None and saved.get("svg_content") and saved.get("quality"):
                if source_reflow:
                    verify_source_receipt(saved["svg_content"], saved.get("source_preservation"),
                                         source_document, index)
                    if saved["quality"].get("passed") is not True:
                        raise ValueError("svg_source_quality_failed")
                    if saved["source_preservation"].get("rendering_verified") is not True:
                        raise ValueError("svg_source_quality_unverified")
                return saved, None if source_reflow else png
            payload = saved or {"schema_version": "svg-slide-checkpoint/v1", "source_format": "svg",
                                "input_digest": input_digest, "attempts": []}
            payload.setdefault("attempts", [])
            payload.setdefault("input_digest", input_digest)
            if canonical_assets:
                payload.update(schema_version="svg-slide-checkpoint/v2", source_format="svg",
                               source_manifest_ref=source_pointer, source_manifest_digest=source_manifest.digest)
            svg = payload.get("svg_content")
            if raster_reconstruction:
                if payload.get("raster_approval_digest") not in {None, raster_pin.approval_digest}:
                    raise ValueError("raster_checkpoint_approval_mismatch")
                if svg is None:
                    from services.raster_redesign_generation import (
                        raster_render_advisories_for_page, render_raster_reconstruction_page_svg,
                    )

                    svg = render_raster_reconstruction_page_svg(
                        raster_analysis, raster_pin, index + 1,
                        style_id=getattr(request, "authored_style", None) or "screenshot-blue",
                        instructions=request.instructions,
                    )
                    payload["svg_content"] = svg
                    # Non-blocking receipt evidence: which pixel-carried
                    # regions the dense-page fallback substituted for a group.
                    payload["rasterRenderAdvisories"] = raster_render_advisories_for_page(
                        raster_analysis, index + 1, svg)
                payload["raster_approval_digest"] = raster_pin.approval_digest
                payload["repair_unavailable"] = True
                if not payload.get("attempts"):
                    payload["attempts"] = [{"kind": "local", "receipt": {}}]
            # At most one model correction. Persist the ordinal before purchase;
            # an uncertain provider attempt is not silently bought again on resume.
            if svg is None and payload.get("inflight"):
                raise AuthoredStageError(slide_index=index, stage="author",
                                        code="svg_provider_attempt_uncertain", retryable=False)
            hint = payload.get("repair_hint")
            while svg is not None or _provider_attempt_count(payload) < 2:
                if fatal_error is not None:
                    raise fatal_error
                provider_count = _provider_attempt_count(payload)
                ordinal = provider_count if svg is None else max(0, provider_count - 1)
                if svg is None:
                    provider_ordinal = ordinal + len(payload.get("provider_failures", [])) + 1
                    offered_images = manifests[index]
                    if canonical_assets:
                        from services.svg_generation_assets import author_images
                        async with async_session_maker() as asset_session:
                            offered_images = await author_images(asset_session, service, context=asset_context,
                                manifest=source_manifest, page_index=index)
                    local_authored = None
                    if source_reflow and not payload.get("local_layout_attempted"):
                        from services.svg_source_template_layout import try_template_source_layout
                        from services.document_canvas_generation import generation_font_floor
                        page_task = asyncio.current_task()
                        def check_local_cancelled(page_task=page_task):
                            if fatal_error is not None or (page_task is not None and page_task.cancelling()):
                                raise asyncio.CancelledError()
                        image_slots = {candidate.occurrence_id: slot
                                      for slot, candidate in planned[index].items()}
                        min_font_size_px = generation_font_floor(
                            request.instructions, getattr(request, "_document_canvas", None))
                        try:
                            local_result = await asyncio.to_thread(
                                try_template_source_layout, source_document, index, snapshot,
                                source_images=offered_images,
                                image_slots=image_slots,
                                min_font_size_px=min_font_size_px,
                                width=canvas.width_px, height=canvas.height_px,
                                check_cancelled=check_local_cancelled,
                                _validated_pages=source_units,
                            )
                        except ValueError as error:
                            code = str(error).split(":", 1)[0]
                            if not re.fullmatch(r"svg_[a-z_]+", code):
                                code = "svg_source_local_layout_failed"
                            raise AuthoredStageError(slide_index=index, stage="author",
                                code=code, retryable=False) from error
                        if fatal_error is not None:
                            raise fatal_error
                        payload["local_layout_attempted"] = True
                        if isinstance(local_result, dict):
                            allowed = {"layout_candidate_unavailable", "layout_overflow_unresolved",
                                       "layout_collision_unresolved", "layout_search_budget_exhausted"}
                            if local_result.get("escalation_reason") not in allowed:
                                raise ValueError("svg_source_local_escalation_invalid")
                            if local_result.get("model_layout_allowed") is not True:
                                raise AuthoredStageError(slide_index=index, stage="author",
                                    code="svg_source_template_layout_unavailable", retryable=False)
                            payload["local_layout"] = local_result
                            hint = json.dumps(local_result, ensure_ascii=False)[:3000]
                            payload["repair_hint"] = hint
                        else:
                            local_authored = local_result
                            payload["local_layout"] = {"model_calls": 0, "status": "resolved"}
                        await _checkpoint(job, index, payload=payload)
                    async def before_dispatch(provider_ordinal=provider_ordinal):
                        # A checkpoint is a recovery witness, not a separate
                        # purchase ledger. Admission still owns reservation.
                        if fatal_error is not None:
                            raise fatal_error
                        payload["inflight"] = provider_ordinal
                        await _checkpoint(job, index, payload=payload)
                    with provider_usage_scope(job_id=job.id if job else None,
                                              operation="authored_generation",
                                              stage="author" if ordinal == 0 else "repair",
                                              slide_index=index,
                                              attempt_number=job.attempt_number if job else 0):
                        from utils.provider_overload import ProviderOutcomeError
                        try:
                            if local_authored is not None:
                                authored = local_authored
                            elif source_reflow:
                                from utils.llm_calls.author_source_svg_layout import author_source_svg_layout
                                from services.document_canvas_generation import generation_font_floor
                                authored = await author_source_svg_layout(
                                    source_document, index, snapshot,
                                    source_images=offered_images,
                                    image_slots={candidate.occurrence_id: slot
                                                for slot, candidate in planned[index].items()},
                                    min_font_size_px=generation_font_floor(
                                        request.instructions, getattr(request, "_document_canvas", None)),
                                    width=canvas.width_px, height=canvas.height_px,
                                    instructions=request.instructions, correction_hint=hint,
                                    receipt_attempt_ordinal=provider_ordinal,
                                    before_dispatch=before_dispatch,
                                )
                            else:
                                authored = await author_slide_svg(
                                    content=content, style_snapshot=snapshot, role=roles[index],
                                    index=index, n=count, instructions=request.instructions,
                                    correction_hint=hint,
                                    receipt_attempt_ordinal=provider_ordinal,
                                    source_images=offered_images,
                                    before_dispatch=before_dispatch,
                                    canonical_source_images=canonical_assets,
                                    **physical_kwargs,
                                )
                        except ProviderOutcomeError as error:
                            if error.code != "public_provider_overload" or not error.recovery_allowed:
                                raise
                            failures = payload.setdefault("provider_failures", [])
                            failures.append({"ordinal": provider_ordinal, "code": error.code})
                            payload["inflight"] = None
                            if payload.get("last_valid_svg"):
                                svg = payload["svg_content"] = payload["last_valid_svg"]
                                payload["repair_unavailable"] = True
                                await _checkpoint(job, index, payload=payload)
                                continue
                            await _checkpoint(job, index, payload=payload)
                            if len(failures) >= 2:
                                raise
                            await asyncio.sleep(min(60., max(30., error.cooldown_seconds or 30.)))
                            continue
                    if fatal_error is not None:
                        raise fatal_error
                    svg = authored.svg_content
                    payload["attempts"].append({"raw_response": authored.raw_response,
                                                "receipt": authored.receipt,
                                                "kind": "local" if local_authored is not None else "provider"})
                    payload["model_attempt_count"] = _provider_attempt_count(payload)
                    payload["inflight"] = None
                    payload["svg_content"] = svg
                    if source_reflow:
                        payload["source_preservation"] = authored.receipt.get("source_preservation")
                        if payload["source_preservation"] is not None:
                            payload["source_preservation"]["font_profile"] = source_font_profile
                    await _checkpoint(job, index, payload=payload)
                try:
                    if payload["attempts"][-1]["receipt"].get("validation_error"):
                        raise ValueError(payload["attempts"][-1]["receipt"]["validation_error"])
                    if source_reflow or raster_reconstruction:
                        final = svg
                    else:
                        finalize_source_manifest = ({slot: {"required": item["policy"]["required"],
                                "allowOmit": item["policy"]["allowOmit"]}
                                for slot, item in manifests[index].items()}
                            if canonical_assets else None)
                        allow_image_only = bool(
                            raster_reconstruction and raster_analysis is not None and raster_pin is not None
                            and raster_analysis.pages[index].render_mode == 'full_page_preserve'
                            and raster_analysis.pages[index].page_number in raster_pin.full_page_preserve_pages)
                        final = finalize_svg(svg, index=index, n=count, width=canvas.width_px,
                                             height=canvas.height_px, style_snapshot=snapshot,
                                             source_manifest=finalize_source_manifest,
                                             allow_image_only=allow_image_only)
                    if source_reflow:
                        verify_source_receipt(final, payload.get("source_preservation"), source_document, index)
                    materialized = None
                    if raster_reconstruction:
                        from services.artifact_store import configured_public_artifact_service
                        from services.raster_source_assets import prepare_raster_page_assets
                        from services.svg_generation_assets import publication_context

                        async with async_session_maker() as asset_session:
                            raster_asset_context, raster_asset_revision = await publication_context(asset_session, job)
                            raster_assets = await prepare_raster_page_assets(
                                asset_session, configured_public_artifact_service(),
                                approval_pin=raster_pin,
                                owner_id=raster_asset_context.owner_id,
                                tenant_id=raster_asset_context.tenant_id,
                                publication_context=raster_asset_context,
                                presentation_id=presentation_id,
                                source_revision=raster_asset_revision,
                                slide_revision=raster_asset_revision,
                                page_number=index + 1,
                                output_page_index=index,
                                canonical_svg=final,
                                width=canvas.width_px,
                                height=canvas.height_px,
                            )
                            await asset_session.commit()
                        payload["svgAssetManifest"] = raster_assets.screen_envelope
                        payload["rasterCropLineage"] = list(raster_assets.crop_lineage)
                        payload["rasterAnalysisDigest"] = raster_pin.analysis_digest
                        payload["rasterApprovalPin"] = raster_pin.model_dump(mode="json", by_alias=True)
                        payload["rasterSourcePageNumber"] = index + 1
                        materialized = raster_assets.materialized_svg
                    if canonical_assets:
                        from services.svg_generation_assets import screen_placement
                        # Candidate SVG is durable before image rendering. A
                        # derivative failure resumes here without rebuying it.
                        payload["svg_content"] = final
                        await _checkpoint(job, index, payload=payload)
                        try:
                            async with async_session_maker() as asset_session:
                                envelope, materialized = await screen_placement(asset_session, service,
                                    context=asset_context, revision=asset_revision, manifest=source_manifest,
                                    source_pointer=source_pointer, svg=final, page_index=index,
                                    candidates=planned[index], width=canvas.width_px, height=canvas.height_px,
                                    saved_envelope=payload.get("svgAssetManifest"))
                                await asset_session.commit()
                            payload["svgAssetManifest"] = envelope
                            await _checkpoint(job, index, payload=payload)
                        except (ValueError, OSError) as error:
                            raise AuthoredStageError(slide_index=index, stage="render",
                                code="svg_asset_screen_derivative_failed", retryable=False) from error
                    try:
                        required_text = (
                            [binding["text"] for binding in payload["source_preservation"]["bindings"]
                             if binding.get("kind") in {"text", "table_cell"} and binding.get("text", "").strip()]
                            if source_reflow else _required_numeric_claims(content)
                        )
                        font_profile = source_font_profile if source_reflow else None
                        raster_authorized_measurement = False
                        if raster_reconstruction:
                            from services.raster_redesign_generation import raster_measurement_inputs
                            required_text, raster_authorized_measurement, font_profile = raster_measurement_inputs(
                                raster_analysis, raster_pin, index + 1, final)
                        async with render_semaphore:
                            rendered = await render_svg_with_metrics(final, width=canvas.width_px,
                                                                 height=canvas.height_px,
                                                                 instructions=request.instructions,
                                                                 required_text=required_text,
                                                                 **({"allow_image_only": True}
                                                                    if raster_authorized_measurement else {}),
                                                                 **({"source_font_profile": font_profile}
                                                                    if font_profile else {}),
                                                                 materialized_svg=materialized, **physical_kwargs)
                    except ValueError as error:
                        if canonical_assets:
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
                    svg = None
                    await _checkpoint(job, index, payload=payload)
                    if ordinal == 1 or raster_reconstruction:
                        code = str(error).split(":", 1)[0]
                        source_plan_failure = source_reflow and code.startswith("svg_source_")
                        raise AuthoredStageError(slide_index=index,
                            stage="author" if source_plan_failure else "render",
                            code=code if source_plan_failure else "svg_validation_failed",
                            retryable=False) from error
                    continue
                payload["attempts"][-1]["quality"] = rendered.quality
                if ordinal == 0:
                    payload["initial_quality"] = {
                        **rendered.quality, "stage": "first_response",
                    }
                payload["last_valid_svg"] = final
                if rendered.quality.get("passed") is False and ordinal == 0 and not payload.get("repair_unavailable"):
                    hint = json.dumps(rendered.quality, ensure_ascii=False)[:3000]
                    payload["repair_hint"] = hint
                    payload["svg_content"] = None
                    svg = None
                    await _checkpoint(job, index, payload=payload)
                    continue
                if payload.get("repair_unavailable") and not raster_reconstruction:
                    rendered.quality["passed"] = False
                    rendered.quality.setdefault("failures", []).append({"kind": "provider_repair_unavailable"})
                if canonical_assets:
                    import xml.etree.ElementTree as ET
                    used = {node.get("href") for node in ET.fromstring(final).iter()
                            if node.tag.rsplit("}", 1)[-1] == "image"}
                    omitted = sorted(set(manifests[index]) - used)
                    rendered.quality["sourceImages"] = {
                        "offered": sorted(manifests[index]), "used": sorted(used), "omitted": omitted,
                    }
                    if omitted:
                        rendered.quality["passed"] = False
                        rendered.quality.setdefault("failures", []).append({
                            "kind": "source_image_omitted", "slotIds": omitted,
                        })
                payload.update(svg_content=final, source_digest=sha256(final.encode()).hexdigest(),
                               quality=rendered.quality, **_write_png(rendered.png))
                if source_reflow:
                    if rendered.quality.get("passed") is not True:
                        raise AuthoredStageError(slide_index=index, stage="render",
                            code="svg_source_quality_failed", retryable=False)
                    rendered.quality.update(verified=True, semantic_review="source_preservation_verified")
                    payload["source_preservation"]["rendering_verified"] = True
                await _checkpoint(job, index, payload=payload)
                return payload, None if source_reflow else rendered.png
            raise AuthoredStageError(slide_index=index, stage="author",
                                    code="svg_repair_budget_exhausted", retryable=False)

    async def tracked_page(index):
        nonlocal fatal_error
        try:
            return await page(index)
        except AuthoredStageError as error:
            if fatal_error is error:
                raise
            fatal_error = fatal_error or error
            if job is not None and error.code != "authored_attempt_lost":
                from services.authored_checkpoint_service import fail_stage
                await fail_stage(job_id=job.id, slide_index=index, stage=error.stage,
                    attempt_token=job.attempt_token, error=error,
                    request_id=getattr(request, "_request_id", None),
                    fencing_token=getattr(job, "_workspace_fencing_token", None))
            raise
        except Exception as error:
            fatal_error = fatal_error or error
            raise

    tasks = [asyncio.create_task(tracked_page(i)) for i in range(count)]
    try:
        pages = await _collect_pages_or_fail(tasks)
        if fatal_error is not None:
            raise fatal_error
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return await _publish(request, presentation_id, outline, language, session, job, snapshot, roles, pages)
