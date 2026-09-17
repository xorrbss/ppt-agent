"""The Composer deck loop for ``source_content_restyle``.

``run_composer_restyle`` is entered from ``generate_presentation_handler``
once the immutable manifest and outline are admitted and the request carries
``_composer_restyle``.  It classifies every source page into a typed plan,
fits each slide through the ladder under bounded Chromium concurrency, runs
the source readback gates as assertions, publishes the deck and writes the
``composer-receipt/v1`` document.  No provider call is made anywhere here.

The fit ladder itself now runs off this coroutine's event loop, one worker
thread per slide (``services.composer.pipeline_fit``): every image the
deck's slides reference is preloaded into memory before any slide is handed
to a thread, since a worker thread must never open a database session.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from services.composer.assets import AssetInliningMeasurer, AssetResolver, ComposerAssetError, inline_result_assets
from services.composer.contracts import Classifier, Estimator, Measurer, Renderer
from services.composer.pipeline_assertions import (
    RecordingMeasurer,
    assert_slide_bindings,
    assert_verbatim_readback,
    classify_deck,
    resolve_pngs,
)
from services.composer.pipeline_fit import (
    fit_deck,
    guard_shared_chromium_state,
    maybe_extend_lease,
    preload_assets_for_fit,
)
from services.composer.pipeline_inputs import (
    build_deck_context,
    composer_tokens,
    deck_title_px,
    render_concurrency,
    source_page_emu,
    source_text_bindings,
    units_in_outline_order,
)
from services.composer.plan import ComposerDeckPlan, ComposerSlideResult
from services.composer.receipt import (
    ComposerAssertionError,
    ComposerPipelineError,
    StageTimer,
    build_composer_receipt,
    write_composer_receipt,
)

LOGGER = logging.getLogger(__name__)

PROGRESS_STAGE_PLAN = "authored_outline"
PROGRESS_STAGE_RENDER = "authored_render"
PROGRESS_STAGE_PUBLISH = "authored_publication"

#: How far ``extend_lease`` widens the lease before the publish stage's one
#: long write transaction (deck-wide slide/HTML/PNG persistence). Generous
#: rather than tuned: widening too far only delays reclaiming a genuinely
#: dead attempt, while widening too little reproduces the defect this exists
#: to fix. The composer pipeline deliberately does not import a workspace
#: lease constant here to stay decoupled from that subsystem's internals.
EXTEND_LEASE_SECONDS_FOR_PUBLISH = 600

Publisher = Callable[..., Awaitable[Mapping[str, Any]]]


def _record_stage(request: Any, stage: str, elapsed_ms: int) -> None:
    recorder = getattr(request, "_authored_generation_timing_recorder", None)
    record = getattr(recorder, "record_stage_ms", None)
    if callable(record):
        try:
            record(stage, max(0, int(elapsed_ms)))
        except ValueError:
            pass
    try:
        object.__setattr__(request, "_authored_timing_stage", stage)
    except (AttributeError, TypeError):
        pass


def _log_stage(job_id: Any, stage: str, elapsed_ms: int, **extra: Any) -> None:
    """One INFO line per stage boundary: the only trace once a lease is lost
    or the worker crashes mid-run, to pinpoint which stage a future stall sits in."""

    details = " ".join(f"{key}={value}" for key, value in extra.items())
    LOGGER.info(
        "composer_restyle stage=%s job=%s elapsed_ms=%d %s", stage, job_id, elapsed_ms, details
    )


async def _report(progress_reporter: Any, **update: Any) -> None:
    if progress_reporter is None:
        return
    if await progress_reporter(**update) is False:
        raise ComposerPipelineError("operation_lease_lost", retryable=True)


def _resolve_style(request: Any) -> Any:
    style = getattr(request, "_authored_style_snapshot", None)
    if style is not None:
        return style
    from utils.authored_styles import resolve_authored_style

    return resolve_authored_style(getattr(request, "authored_style", None))


def _default_classifier() -> Classifier:
    from services.composer.classify import classify_unit

    return classify_unit


def _default_renderer() -> Renderer:
    from services.composer.render import render_slide

    return render_slide


def _default_measurer() -> Measurer:
    from services.composer.measure import ChromiumMeasurer

    # The fit ladder now runs in worker threads (services.composer.
    # pipeline_fit): guard the shared utils.slide_capture module state that
    # only the real Chromium measurer touches. A caller-injected measurer
    # (every existing test) is never wrapped -- see fit_deck's docstring.
    return guard_shared_chromium_state(ChromiumMeasurer())


def _default_estimator() -> Estimator:
    from services.composer.estimate import estimate_fit

    return estimate_fit


def _default_publisher() -> Publisher:
    from services.composer.publish import publish_composer_deck

    return publish_composer_deck


async def run_composer_restyle(
    *,
    request: Any,
    presentation_id: Any,
    generation_job: Any,
    progress_reporter: Any,
    sql_session: Any,
    async_status: Any = None,
    export_cookie_header: str | None = None,
    measurer: Measurer | None = None,
    classifier: Classifier | None = None,
    renderer: Renderer | None = None,
    estimator: Estimator | None = None,
    publisher: Publisher | None = None,
    asset_resolver: AssetResolver | None = None,
    extend_lease: Callable[..., Awaitable[bool]] | None = None,
) -> Any:
    """Compose, verify, publish and receipt one restyle deck; returns the edit path model."""

    del export_cookie_header  # publication makes no export request; export is its own operation
    manifest = getattr(request, "_source_content_manifest", None)
    outline = getattr(request, "_source_document_outline", None)
    if manifest is None or outline is None:
        raise ComposerPipelineError("source_content_manifest_invalid")
    job_id = getattr(generation_job, "id", None)
    timer = StageTimer()
    results: list[ComposerSlideResult] = []
    publication: Mapping[str, Any] | None = None
    readback: Mapping[str, Any] | None = None
    structure_readback: Mapping[str, Any] | None = None
    stamping_rebound: dict[int, bool] = {}
    recording = RecordingMeasurer(measurer or _default_measurer())
    try:
        timer.start("classify")
        if (
            getattr(request, "_composer_fit_policy", None)
            and getattr(manifest, "source_layout_version", None) != "source-layout/v2"
        ):
            raise ComposerPipelineError("composer_source_reanalysis_required")
        units = units_in_outline_order(manifest, outline)
        from services.authored_canvas import canvas_metadata_for_aspect_ratio, request_canvas_contract

        canvas = (
            request_canvas_contract(request)
            if hasattr(request, "aspect_ratio")
            else canvas_metadata_for_aspect_ratio("16:9").canvas_contract()
        )
        deck_context = build_deck_context(manifest, units)
        style = _resolve_style(request)
        tokens = composer_tokens(style, deck_title_text=deck_context.deck_title)
        await _report(
            progress_reporter, stage=PROGRESS_STAGE_PLAN, current=0, total=len(units), percent=5,
            message="원본 페이지를 분류하고 있습니다.",
        )
        plans = classify_deck(units, deck_context, classifier or _default_classifier())
        from services.composer.contracts import source_px_scale

        emu = source_page_emu(manifest)
        fit_policy = getattr(request, "_composer_fit_policy", None) or {}
        deck = ComposerDeckPlan(
            slides=plans, tokens=tokens, canvas=canvas,
            style_profile=getattr(style, "composer_profile", None),
            source_page_emu=emu, source_px_scale=source_px_scale(emu, canvas),
            title_px=deck_title_px(plans, tokens, canvas=canvas),
            fit_policy_revision=fit_policy.get("revision"),
            font_floor_px=fit_policy.get("font_floor_px"),
        )
        classify_ms = timer.stop("classify")
        _record_stage(request, "outline", classify_ms)
        _log_stage(job_id, "classify", classify_ms, slides=len(plans))

        timer.start("fit")
        total = len(plans)
        thread_resolver = await preload_assets_for_fit(deck, manifest, sql_session, asset_resolver)
        inlining = AssetInliningMeasurer(recording, thread_resolver)

        async def on_slide(result: ComposerSlideResult, completed: int) -> None:
            # Retain completed-page evidence even when another page fails.
            results.append(result)
            await _report(
                progress_reporter, stage=PROGRESS_STAGE_RENDER, current=completed, total=total,
                percent=10 + int(80 * completed / max(1, total)),
                message=f"슬라이드 {completed}/{total} 구성을 확인했습니다.",
            )

        results = await fit_deck(
            deck,
            renderer=renderer or _default_renderer(),
            measurer=inlining,
            estimator=estimator or _default_estimator(),
            concurrency=render_concurrency(os.getenv("PUBLIC_RENDER_MAX_CONCURRENCY")),
            on_slide=on_slide,
            extend_lease=extend_lease,
        )
        fit_ms = timer.stop("fit")
        _record_stage(request, "render", fit_ms)
        _log_stage(job_id, "fit", fit_ms, slides=len(results))

        timer.start("assert")
        _log_stage(job_id, "assert_begin", 0, slides=len(results))
        try:
            results = await inline_result_assets(results, thread_resolver)
        except ComposerAssetError as error:
            raise ComposerAssertionError(error.slide_index, error.code, error.detail) from error
        assert_slide_bindings(results, source_text_bindings(units, outline), stamping_rebound)
        htmls = [result.html for result in results]
        readback = assert_verbatim_readback(manifest, outline, htmls)
        if deck.fit_policy_revision == "composer-source-fit/v2":
            from services.composer.source_structure_readback import assert_source_structure_readback

            structure_readback = assert_source_structure_readback(manifest, htmls)
        pngs = await resolve_pngs(results, {plan.slide_index: plan for plan in plans}, recording)
        assert_ms = timer.stop("assert")
        _record_stage(request, "quality", assert_ms)
        _log_stage(job_id, "assert", assert_ms)

        timer.start("publish")
        await maybe_extend_lease(extend_lease, EXTEND_LEASE_SECONDS_FOR_PUBLISH)
        await _report(
            progress_reporter, stage=PROGRESS_STAGE_PUBLISH, current=total, total=total, percent=95,
            message="최종 결과를 게시하고 있습니다.",
        )
        receipt = build_composer_receipt(
            presentation_id=presentation_id, job_id=job_id, page_count=total, results=results,
            timings_ms=timer.as_dict(), stamping_rebound=stamping_rebound,
            fit_policy=getattr(request, "_composer_fit_policy", None),
        )
        receipt["sourceReadback"] = dict(readback)
        if structure_readback is not None:
            receipt["sourceStructureReadback"] = dict(structure_readback)
        _log_stage(job_id, "publish_begin", 0, pages=total)
        publication = await (publisher or _default_publisher())(
            sql_session=sql_session, request=request, presentation_id=presentation_id,
            generation_job=generation_job, async_status=async_status, htmls=htmls, pngs=pngs,
            results=results, deck=deck, receipt=receipt,
        )
        publish_ms = timer.stop("publish")
        _record_stage(request, "publication", publish_ms)
        _log_stage(job_id, "publish_end", publish_ms)
    except ComposerPipelineError as error:
        failure = {
            "code": error.code, "detail": error.detail,
            "slideIndex": getattr(error, "slide_index", None),
            "retryable": error.retryable,
            "stage": timer.active_stage,
            "fitPolicy": getattr(request, "_composer_fit_policy", None),
        }
        await write_composer_receipt(
            build_composer_receipt(
                presentation_id=presentation_id, job_id=job_id,
                page_count=len(getattr(outline, "slides", ()) or ()), results=results,
                timings_ms=timer.as_dict(), failure=failure, stamping_rebound=stamping_rebound,
                fit_policy=getattr(request, "_composer_fit_policy", None), source_readback=readback,
                source_structure_readback=structure_readback,
            ),
            job_id=job_id, presentation_id=presentation_id,
        )
        raise
    await write_composer_receipt(
        build_composer_receipt(
            presentation_id=presentation_id, job_id=job_id, page_count=len(results), results=results,
            timings_ms=timer.as_dict(), publication=publication, stamping_rebound=stamping_rebound,
            fit_policy=getattr(request, "_composer_fit_policy", None), source_readback=readback,
            source_structure_readback=structure_readback,
        ),
        job_id=job_id, presentation_id=presentation_id,
    )
    _log_stage(job_id, "receipt_written", 0)
    from models.presentation_and_path import PresentationPathAndEditPath

    edit_path = str(publication.get("editPath") or f"/presentation?id={presentation_id}")
    path = str(publication.get("path") or edit_path)
    return PresentationPathAndEditPath(presentation_id=presentation_id, path=path, edit_path=edit_path)


__all__ = [
    "AssetInliningMeasurer",
    "ComposerAssertionError",
    "ComposerPipelineError",
    "RecordingMeasurer",
    "assert_slide_bindings",
    "assert_verbatim_readback",
    "classify_deck",
    "fit_deck",
    "inline_result_assets",
    "resolve_pngs",
    "run_composer_restyle",
]
