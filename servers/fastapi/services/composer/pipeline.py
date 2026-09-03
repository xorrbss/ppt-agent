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

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from services.composer.assets import AssetInliningMeasurer, AssetResolver, ComposerAssetError, inline_result_assets
from services.composer.contracts import Classifier, DeckContext, Estimator, Measurement, Measurer, Renderer
from services.composer.pipeline_fit import fit_deck, guard_shared_chromium_state, preload_assets_for_fit
from services.composer.pipeline_inputs import (
    build_deck_context,
    composer_tokens,
    deck_title_px,
    render_concurrency,
    source_page_emu,
    source_text_bindings,
    units_in_outline_order,
)
from services.composer.plan import ComposerDeckPlan, ComposerSlidePlan, ComposerSlideResult
from services.composer.receipt import (
    ComposerAssertionError,
    ComposerPipelineError,
    StageTimer,
    build_composer_receipt,
    is_accepted_residual,
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


class RecordingMeasurer:
    """Wrap a ``Measurer`` and keep the PNG of every render, keyed by its HTML."""

    def __init__(self, inner: Measurer) -> None:
        self.inner = inner
        self.pngs: dict[str, bytes] = {}

    async def __call__(self, html: str, plan: ComposerSlidePlan) -> Measurement:
        measurement = await self.inner(html, plan)
        if isinstance(measurement.png, bytes) and measurement.png:
            self.pngs[html] = measurement.png
        return measurement


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


async def _maybe_extend_lease(extend_lease: Any, seconds: int) -> None:
    """Widen the attempt's lease before the publish stage's one long write
    transaction, if a caller offered one (see ``services.workspace_operation_lease``:
    a single-writer backend blocks every other session, heartbeat included, for as
    long as that transaction holds the write lock). The current production caller
    only wires ``context.progress`` through as ``progress_reporter``, not
    ``context.extend_lease`` -- closing that gap needs a change outside this
    package. Best-effort: no call, or a ``False`` return, never fails the
    operation; publication's own fencing protects correctness either way.
    """

    if extend_lease is None:
        return
    try:
        await extend_lease(seconds=seconds)
    except Exception:
        LOGGER.warning("composer_restyle lease extension before publish failed", exc_info=True)


def _resolve_style(request: Any) -> Any:
    style = getattr(request, "_authored_style_snapshot", None)
    if style is not None:
        return style
    from utils.authored_styles import resolve_authored_style

    return resolve_authored_style(getattr(request, "authored_style", None))


def classify_deck(
    units: Sequence[Mapping[str, Any]],
    deck_context: DeckContext,
    classifier: Classifier,
) -> list[ComposerSlidePlan]:
    context = deck_context.as_mapping()
    plans: list[ComposerSlidePlan] = []
    for index, unit in enumerate(units):
        plan = classifier(unit=unit, slide_index=index, deck_context=context)
        if plan.slide_index != index:
            raise ComposerAssertionError(index, "composer_plan_index_mismatch", f"plan index {plan.slide_index}")
        plans.append(plan)
    return plans


def assert_slide_bindings(
    results: Sequence[ComposerSlideResult],
    bindings: Sequence[Sequence[tuple[str, str]]],
    stamping_rebound: dict[int, bool] | None = None,
) -> None:
    """Every accepted slide is free of blocking defects and already sealed.

    Rung 5's own residual ``overlap`` issues are accepted (see
    ``services.composer.receipt.is_accepted_residual``); any other residual
    issue, at any rung, still fails closed.

    The stamper is only required not to raise. For a block rendered as
    several bound run elements it may rebind the coarsest exclusive owner
    (typically the parent wrapper) instead of every individual run element,
    which changes the stamped bytes without dropping or misattributing any
    run -- a valid alternative binding, not a defect; the Chromium readback
    that follows is the authoritative binding gate. When ``stamping_rebound``
    is given, each slide's index is recorded there so the receipt can report
    whether that happened, purely for observability.
    """

    from services.source_content.text_stamping import stamp_source_blocks

    if len(bindings) != len(results):
        raise ComposerAssertionError(0, "composer_binding_cardinality_mismatch")
    for result, slide_bindings in zip(results, bindings, strict=True):
        index = result.slide_index
        issues = result.receipt.blocking_issues
        if issues and not is_accepted_residual(result.receipt.rung, issues):
            raise ComposerAssertionError(index, "composer_blocking_issues_remaining", "; ".join(issues))
        try:
            stamped = stamp_source_blocks(result.html, list(slide_bindings))
        except Exception as error:
            code = str(getattr(error, "code", "") or "composer_source_binding_missing")
            raise ComposerAssertionError(index, code, str(getattr(error, "detail", error))) from error
        if stamping_rebound is not None:
            stamping_rebound[index] = stamped.html != result.html


def assert_verbatim_readback(manifest: Any, outline: Any, htmls: Sequence[str]) -> Mapping[str, Any]:
    from services.source_content.readback import SourceContentReadbackError, verify_source_verbatim_readback

    try:
        return verify_source_verbatim_readback(manifest=manifest, outline=outline, htmls=list(htmls)).as_dict()
    except SourceContentReadbackError as error:
        indices = tuple(error.slide_indices or ())
        raise ComposerAssertionError(indices[0] if indices else 0, error.code, error.detail) from error


async def resolve_pngs(
    results: Sequence[ComposerSlideResult],
    plans: Mapping[int, ComposerSlidePlan],
    measurer: RecordingMeasurer,
) -> list[bytes]:
    """The PNG of each accepted render; one extra capture only when none was kept."""

    pngs: list[bytes] = []
    for result in results:
        png = measurer.pngs.get(result.html)
        if png is None and result.png_path:
            try:
                png = await asyncio.to_thread(lambda path=result.png_path: open(path, "rb").read())
            except OSError:
                png = None
        if png is None:
            png = (await measurer(result.html, plans[result.slide_index])).png
        if not isinstance(png, bytes) or not png:
            raise ComposerAssertionError(result.slide_index, "composer_png_unavailable")
        pngs.append(png)
    return pngs


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
    stamping_rebound: dict[int, bool] = {}
    recording = RecordingMeasurer(measurer or _default_measurer())
    try:
        timer.start("classify")
        units = units_in_outline_order(manifest, outline)
        deck_context = build_deck_context(manifest, units)
        tokens = composer_tokens(_resolve_style(request), deck_title_text=deck_context.deck_title)
        await _report(
            progress_reporter, stage=PROGRESS_STAGE_PLAN, current=0, total=len(units), percent=5,
            message="원본 페이지를 분류하고 있습니다.",
        )
        plans = classify_deck(units, deck_context, classifier or _default_classifier())
        from services.composer.contracts import source_px_scale

        emu = source_page_emu(manifest)
        deck = ComposerDeckPlan(
            slides=plans, tokens=tokens, source_page_emu=emu, source_px_scale=source_px_scale(emu),
            title_px=deck_title_px(plans, tokens),
        )
        classify_ms = timer.stop("classify")
        _record_stage(request, "outline", classify_ms)
        _log_stage(job_id, "classify", classify_ms, slides=len(plans))

        timer.start("fit")
        total = len(plans)
        thread_resolver = await preload_assets_for_fit(deck, manifest, sql_session, asset_resolver)
        inlining = AssetInliningMeasurer(recording, thread_resolver)

        async def on_slide(result: ComposerSlideResult, completed: int) -> None:
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
        pngs = await resolve_pngs(results, {plan.slide_index: plan for plan in plans}, recording)
        assert_ms = timer.stop("assert")
        _record_stage(request, "quality", assert_ms)
        _log_stage(job_id, "assert", assert_ms)

        timer.start("publish")
        await _maybe_extend_lease(extend_lease, EXTEND_LEASE_SECONDS_FOR_PUBLISH)
        await _report(
            progress_reporter, stage=PROGRESS_STAGE_PUBLISH, current=total, total=total, percent=95,
            message="최종 결과를 게시하고 있습니다.",
        )
        receipt = build_composer_receipt(
            presentation_id=presentation_id, job_id=job_id, page_count=total, results=results,
            timings_ms=timer.as_dict(), stamping_rebound=stamping_rebound,
        )
        receipt["sourceReadback"] = dict(readback)
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
        failure = {"code": error.code, "detail": error.detail, "slideIndex": getattr(error, "slide_index", None)}
        await write_composer_receipt(
            build_composer_receipt(
                presentation_id=presentation_id, job_id=job_id,
                page_count=len(getattr(outline, "slides", ()) or ()), results=results,
                timings_ms=timer.as_dict(), failure=failure, stamping_rebound=stamping_rebound,
            ),
            job_id=job_id, presentation_id=presentation_id,
        )
        raise
    await write_composer_receipt(
        build_composer_receipt(
            presentation_id=presentation_id, job_id=job_id, page_count=len(results), results=results,
            timings_ms=timer.as_dict(), publication=publication, stamping_rebound=stamping_rebound,
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
