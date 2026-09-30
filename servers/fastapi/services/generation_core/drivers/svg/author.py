"""Everything that produces an SVG string for one SVG-driver page: the
pre-loop raster-reconstruction synthesis, the local template-layout attempt,
the ``author_source_svg_layout`` / ``author_slide_svg`` dispatch, source-image
and AssetOffer slots (planned illustrations/icons, see ``asset_slots.py``), and
inflight/ordinal bookkeeping around a single author attempt.

Split out of ``services/svg_presentation_generate.py`` (P0 split, see
``docs/generation-core-architecture-20260925.md``); ``collect.py`` owns the
attempt ``while`` loop and calls :func:`attempt_author` once per author
attempt, and :func:`raster_reconstruct_svg` once before that loop when the
deck is a raster-reconstruction deck.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re

from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg.native_declarations import bind_native_declarations
from services.provider_usage_instrumentation import provider_usage_scope
from services.svg_presentation_checkpoint import _provider_attempt_count

_LOCAL_ESCALATION_REASONS = {
    "layout_candidate_unavailable", "layout_overflow_unresolved",
    "layout_collision_unresolved", "layout_search_budget_exhausted",
}


async def raster_reconstruct_svg(raster_analysis, raster_pin, request, index):
    """Pre-loop SVG synthesis for the raster-reconstruction branch."""
    from services.raster_redesign_generation import (
        raster_render_advisories_for_page, render_raster_reconstruction_page_svg,
    )

    svg = render_raster_reconstruction_page_svg(
        raster_analysis, raster_pin, index + 1,
        style_id=getattr(request, "authored_style", None) or "screenshot-blue",
        instructions=request.instructions,
    )
    advisories = raster_render_advisories_for_page(raster_analysis, index + 1, svg)
    return svg, advisories


@dataclass
class AuthorAttempt:
    """Result of one author attempt; ``payload`` is mutated in place."""
    svg: str | None
    retry: bool = False


async def attempt_author(*, ctx, request, index, content, role, count, payload, ordinal,
                         provider_ordinal, job, state, checkpoint, async_session_maker) -> AuthorAttempt:
    from services.document_canvas_generation import source_restyle_font_floor
    from services.generation_core.authoring.author_slide_svg import author_slide_svg
    from utils.provider_overload import ProviderOutcomeError

    def raise_if_fatal():
        if state.fatal_error is not None:
            raise state.fatal_error

    offered_images = ctx.manifests[index]
    if ctx.canonical_assets:
        from services.svg_generation_assets import author_images
        async with async_session_maker() as asset_session:
            offered_images = await author_images(asset_session, ctx.service, context=ctx.asset_context,
                manifest=ctx.source_manifest, page_index=index)
    offer = getattr(ctx, "asset_offer", None)
    vector_icons = offer.vector[index] if offer is not None else {}
    local_authored = None
    hint = payload.get("repair_hint")
    if ctx.source_reflow and not payload.get("local_layout_attempted"):
        from services.svg_source_template_layout import try_template_source_layout
        page_task = asyncio.current_task()

        def check_local_cancelled(page_task=page_task):
            if state.fatal_error is not None or (page_task is not None and page_task.cancelling()):
                raise asyncio.CancelledError()

        image_slots = {candidate.occurrence_id: slot for slot, candidate in ctx.planned[index].items()}
        min_font_size_px = source_restyle_font_floor(request.instructions, getattr(request, "_document_canvas", None))
        try:
            local_result = await asyncio.to_thread(
                try_template_source_layout, ctx.source_document, index, ctx.snapshot,
                source_images=offered_images,
                image_slots=image_slots,
                min_font_size_px=min_font_size_px,
                width=ctx.canvas.width_px, height=ctx.canvas.height_px,
                check_cancelled=check_local_cancelled,
                _validated_pages=ctx.source_units,
            )
        except ValueError as error:
            code = str(error).split(":", 1)[0]
            if not re.fullmatch(r"svg_[a-z_]+", code):
                code = "svg_source_local_layout_failed"
            raise AuthoredStageError(slide_index=index, stage="author",
                code=code, retryable=False) from error
        raise_if_fatal()
        payload["local_layout_attempted"] = True
        if isinstance(local_result, dict):
            if local_result.get("escalation_reason") not in _LOCAL_ESCALATION_REASONS:
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
        await checkpoint(job, index, payload=payload)

    async def before_dispatch(provider_ordinal=provider_ordinal):
        # A checkpoint is a recovery witness, not a separate
        # purchase ledger. Admission still owns reservation.
        raise_if_fatal()
        payload["inflight"] = provider_ordinal
        await checkpoint(job, index, payload=payload)

    with provider_usage_scope(job_id=job.id if job else None,
                              operation="authored_generation",
                              stage="author" if ordinal == 0 else "repair",
                              slide_index=index,
                              attempt_number=job.attempt_number if job else 0):
        try:
            if local_authored is not None:
                authored = local_authored
            elif ctx.source_reflow:
                from services.generation_core.authoring.author_source_svg_layout import author_source_svg_layout
                authored = await author_source_svg_layout(
                    ctx.source_document, index, ctx.snapshot,
                    source_images=offered_images,
                    image_slots={candidate.occurrence_id: slot
                                for slot, candidate in ctx.planned[index].items()},
                    min_font_size_px=source_restyle_font_floor(
                        request.instructions, getattr(request, "_document_canvas", None)),
                    width=ctx.canvas.width_px, height=ctx.canvas.height_px,
                    instructions=request.instructions, correction_hint=hint,
                    receipt_attempt_ordinal=provider_ordinal,
                    before_dispatch=before_dispatch,
                )
            else:
                authored = await author_slide_svg(
                    content=content, style_snapshot=ctx.snapshot, role=role,
                    index=index, n=count, instructions=request.instructions,
                    correction_hint=hint,
                    receipt_attempt_ordinal=provider_ordinal,
                    source_images={**offered_images, **vector_icons},
                    before_dispatch=before_dispatch,
                    canonical_source_images=ctx.canonical_assets,
                    **ctx.physical_kwargs,
                )
        except ProviderOutcomeError as error:
            if error.code != "public_provider_overload" or not error.recovery_allowed:
                raise
            failures = payload.setdefault("provider_failures", [])
            failures.append({"ordinal": provider_ordinal, "code": error.code})
            payload["inflight"] = None
            if payload.get("last_valid_svg"):
                payload["svg_content"] = payload["last_valid_svg"]
                payload["repair_unavailable"] = True
                await checkpoint(job, index, payload=payload)
                return AuthorAttempt(svg=None, retry=True)
            await checkpoint(job, index, payload=payload)
            if len(failures) >= 2:
                raise
            await asyncio.sleep(min(60., max(30., error.cooldown_seconds or 30.)))
            return AuthorAttempt(svg=None, retry=True)
    raise_if_fatal()
    svg = authored.svg_content
    if not (ctx.source_reflow or ctx.raster_reconstruction):
        from services.svg_default_fonts import normalize_model_font_families

        svg, font_advisories = normalize_model_font_families(svg)
        if font_advisories:
            payload["fontAdvisories"] = font_advisories
        else:
            payload.pop("fontAdvisories", None)
    payload["attempts"].append({"raw_response": authored.raw_response,
                                "receipt": authored.receipt,
                                "kind": "local" if local_authored is not None else "provider"})
    payload["model_attempt_count"] = _provider_attempt_count(payload)
    payload["inflight"] = None
    payload["svg_content"] = svg
    # Declarations are bound to the digest of this exact drawing; any other SVG
    # this page ends with (correction, restore, fallback) does not match it.
    bind_native_declarations(payload, svg, authored.native_declarations)
    if offer is not None and offer.slots[index]:
        # Per-page AssetOffer receipt; raster slots are also accounted for by
        # the render step's sourceImages check, vector icons only here.
        payload["assetOffer"] = {"slots": offer.slots[index], "vectorIcons": {
            "offered": sorted(vector_icons), "used": authored.receipt.get("vector_icons_used", [])}}
    if ctx.source_reflow:
        payload["source_preservation"] = authored.receipt.get("source_preservation")
        if payload["source_preservation"] is not None:
            payload["source_preservation"]["font_profile"] = ctx.source_font_profile
    await checkpoint(job, index, payload=payload)
    return AuthorAttempt(svg=svg, retry=False)
