"""The per-deck SVG generation loop: fan out one durable page-attempt task per
slide, drive each page's attempt ``while`` loop (author -> render, at most one
model correction), then classify every page, run the deck's vision advisory and
hand the pages to publication.

A page that runs out of attempts no longer cancels the deck by itself: the
deck's ``TerminalPolicy`` (``terminal.py``) may replace it with a fallback page
or keep its last rendered page, flagged; only ``fail`` keeps the old outcome.

``_generate`` is the entry point moved out of
``services/svg_presentation_generate.py`` (P0 split, see
``docs/generation-core-architecture-20260925.md``). It shadows the facade's
mutable hooks (``async_session_maker``, ``_checkpoint``, ``_read_png``,
``_write_png``, ``_publish``, ``run_vision_advisory``) onto locals read from
``services.svg_presentation_service`` at call time and passes them down to
``_page``/``tracked_page``/``author.attempt_author``/``render.attempt_render``
as plain arguments, so a test that monkeypatches an attribute on that facade
module still reaches every one of them -- exactly as it would if this were
still one file.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg import author, render
from services.generation_core.drivers.svg import quality as deck_quality
from services.generation_core.drivers.svg import timing
from services import svg_outline_spans
from services.generation_core.drivers.svg.terminal import rescue_terminal_page
from services.svg_presentation_checkpoint import _collect_pages_or_fail, _digest, _provider_attempt_count
from services.svg_presentation_prepare import _prepare_generation


@dataclass
class _RunState:
    """Mutable state shared across one deck's concurrent page attempts."""
    fatal_error: BaseException | None = None


async def _generate(request, presentation_id, outline, language, session, job):
    from services import svg_presentation_service as _facade
    async_session_maker = _facade.async_session_maker
    checkpoint = _facade._checkpoint
    read_png = _facade._read_png
    write_png = _facade._write_png
    publish = _facade._publish

    spans = svg_outline_spans.current() or svg_outline_spans.begin()
    with spans.span("prepare_total"):
        ctx = await _prepare_generation(request, presentation_id, outline, session, job)
    timing.record_outline(request)
    spans.emit(presentation_id)
    timing.enter_deck_stage(request, "author")
    state = _RunState()
    hooks = _PageHooks(checkpoint=checkpoint, async_session_maker=async_session_maker,
                       read_png=read_png, write_png=write_png)

    async def tracked_page(index):
        try:
            return await _page(ctx, request, presentation_id, outline, language, job, index, state, hooks)
        except AuthoredStageError as error:
            if state.fatal_error is error:
                raise
            state.fatal_error = state.fatal_error or error
            if job is not None and error.code != "authored_attempt_lost":
                from services.authored_checkpoint_service import fail_stage
                await fail_stage(job_id=job.id, slide_index=index, stage=error.stage,
                    attempt_token=job.attempt_token, error=error,
                    request_id=getattr(request, "_request_id", None),
                    fencing_token=getattr(job, "_workspace_fencing_token", None))
            raise
        except Exception as error:
            state.fatal_error = state.fatal_error or error
            raise

    tasks = [asyncio.create_task(tracked_page(i)) for i in range(ctx.count)]
    try:
        pages = await _collect_pages_or_fail(tasks)
        if state.fatal_error is not None:
            raise state.fatal_error
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    timing.settle_page_loop(request)
    timing.enter_deck_stage(request, "quality")
    with timing.observe_stage(request, "quality"):
        summary = await deck_quality.classify_pages(pages, job=job, checkpoint=checkpoint)
        advisory = await deck_quality.deck_advisory(ctx, pages, job=job, read_png=read_png,
                                                    run_advisory=_facade.run_vision_advisory)
    timing.enter_deck_stage(request, "publication")
    with timing.observe_stage(request, "publication"):
        return await publish(request, presentation_id, outline, language, session, job,
                             ctx.snapshot, ctx.roles, pages, quality_summary=summary, advisory=advisory)


@dataclass
class _PageHooks:
    """The facade hooks a page attempt needs, resolved once per ``_generate`` call."""
    checkpoint: object
    async_session_maker: object
    read_png: object
    write_png: object


async def _page(ctx, request, presentation_id, outline, language, job, index, state, hooks):
    from services.svg_source_layout import verify_source_receipt

    async with ctx.semaphore:
        if state.fatal_error is not None:
            raise state.fatal_error
        content = outline.slides[index].content
        input_digest = _digest({"content": content, "style": ctx.snapshot, "index": index,
                                "count": ctx.count, "instructions": request.instructions,
                                "language": language, "preset": request.codex_generation_preset})
        input_digest = _digest({"page": input_digest, "assets": ctx.manifests[index]})
        if ctx.source_reflow:
            input_digest = _digest({"page": input_digest,
                "source_manifest": ctx.source_document.manifest_digest,
                "font_profile": ctx.source_font_profile,
                "source_page": ctx.source_units[index].model_dump(mode="json"),
                "source_layout_version": "svg-source-layout/v2"})
        if ctx.raster_reconstruction:
            input_digest = _digest({"page": input_digest, "rasterApproval": ctx.raster_pin.approval_digest})
        saved = await hooks.checkpoint(job, index) or {}
        if saved.get("input_digest") and saved.get("input_digest") != input_digest:
            raise RuntimeError("svg_checkpoint_input_conflict")
        png = hooks.read_png(saved)
        if png is not None and saved.get("svg_content") and saved.get("quality"):
            if ctx.source_reflow:
                verify_source_receipt(saved["svg_content"], saved.get("source_preservation"),
                                     ctx.source_document, index)
                if saved.get("terminal"):
                    return saved, None  # a flag-only page: verbatim, flagged, not verified
                if saved["quality"].get("passed") is not True:
                    raise ValueError("svg_source_quality_failed")
                if saved["source_preservation"].get("rendering_verified") is not True:
                    raise ValueError("svg_source_quality_unverified")
            return saved, None if ctx.source_reflow else png
        payload = saved or {"schema_version": "svg-slide-checkpoint/v1", "source_format": "svg",
                            "input_digest": input_digest, "attempts": []}
        payload.setdefault("attempts", [])
        payload.setdefault("input_digest", input_digest)
        if ctx.canonical_assets:
            payload.update(schema_version="svg-slide-checkpoint/v2", source_format="svg",
                           source_manifest_ref=ctx.source_pointer, source_manifest_digest=ctx.source_manifest.digest)
        svg = payload.get("svg_content")
        if ctx.raster_reconstruction:
            if payload.get("raster_approval_digest") not in {None, ctx.raster_pin.approval_digest}:
                raise ValueError("raster_checkpoint_approval_mismatch")
            free_redesign = author.free_raster_redesign(ctx, index)
            if svg is None and not free_redesign:
                with timing.observe_stage(request, "author"):
                    svg, advisories = await author.raster_reconstruct_svg(
                        ctx.raster_analysis, ctx.raster_pin, request, index)
                payload["svg_content"] = svg
                # Non-blocking receipt evidence: which pixel-carried
                # regions the dense-page fallback substituted for a group.
                payload["rasterRenderAdvisories"] = advisories
            payload["raster_approval_digest"] = ctx.raster_pin.approval_digest
            if not free_redesign:
                payload["repair_unavailable"] = True
            else:
                payload.pop("repair_unavailable", None)
                payload["rasterCompositionMode"] = "free-svg"
            if not payload.get("attempts") and not free_redesign:
                payload["attempts"] = [{"kind": "local", "receipt": {}}]
        # At most one model correction. Persist the ordinal before purchase;
        # an uncertain provider attempt is not silently bought again on resume.
        if svg is None and payload.get("inflight"):
            raise AuthoredStageError(slide_index=index, stage="author",
                                    code="svg_provider_attempt_uncertain", retryable=False)
        try:
            return await _attempt_loop(ctx, request, presentation_id, index, content, payload, job,
                                       state, hooks)
        except AuthoredStageError as error:
            if error is state.fatal_error:
                raise
            with timing.observe_stage(request, "render"):
                rescued = await rescue_terminal_page(ctx=ctx, request=request, index=index, content=content,
                    payload=payload, error=error, job=job, checkpoint=hooks.checkpoint,
                    write_png=hooks.write_png)
            if rescued is None:
                raise
            return rescued


async def _attempt_loop(ctx, request, presentation_id, index, content, payload, job, state, hooks):
    """Author -> render until the page verifies, with at most one model correction."""
    role = ctx.roles[index]
    while payload.get("svg_content") is not None or _provider_attempt_count(payload) < 2:
        if state.fatal_error is not None:
            raise state.fatal_error
        provider_count = _provider_attempt_count(payload)
        svg = payload.get("svg_content")
        ordinal = provider_count if svg is None else max(0, provider_count - 1)
        if svg is None:
            provider_ordinal = ordinal + len(payload.get("provider_failures", [])) + 1
            with timing.observe_stage(request, "author"):
                author_result = await author.attempt_author(
                    ctx=ctx, request=request, index=index, content=content, role=role, count=ctx.count,
                    payload=payload, ordinal=ordinal, provider_ordinal=provider_ordinal, job=job,
                    state=state, checkpoint=hooks.checkpoint, async_session_maker=hooks.async_session_maker)
            if author_result.retry:
                continue
            svg = author_result.svg
        with timing.observe_stage(request, "render"):
            render_result = await render.attempt_render(
                ctx=ctx, request=request, presentation_id=presentation_id, index=index, content=content,
                payload=payload, svg=svg, ordinal=ordinal, job=job, checkpoint=hooks.checkpoint,
                async_session_maker=hooks.async_session_maker, write_png=hooks.write_png)
        if render_result is render.RETRY:
            continue
        return render_result
    raise AuthoredStageError(slide_index=index, stage="author",
                            code="svg_repair_budget_exhausted", retryable=False)
