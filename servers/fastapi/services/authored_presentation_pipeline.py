from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from models.authored_chapter_plan import AuthoredChapterPlan
from models.authored_header_contract import AuthoredHeaderContract
from models.authored_illustration_plan import AuthoredIllustrationPlan
from models.presentation_outline_model import PresentationOutlineModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.authored_checkpoint_service import (
    AuthoredStageError,
    fail_stage,
    get_checkpoint,
    initialize_checkpoints,
    is_retryable_exception,
    read_valid_render,
    read_valid_evidence,
    should_inject_failure,
    start_stage,
)
from services.authored_presentation_artifacts import (
    _complete_stage_after_external_success,
)
from services.authored_illustration_stage import illustrate_slide
from services.authored_presentation_contracts import AuthoredGenerationPipelineResult
from services.authored_evidence_ledger import DeckEvidenceLedger
from services.image_generation_service import ImageGenerationService
from services.authored_source_images import plan_slide_source_images
from services.authored_composition_policy import (
    AuthoredCapacityPlan,
    CompositionProfile,
    blocking_safety_issues,
    capacity_violations,
    capacity_overflow,
    ensure_authored_semantic_roles,
)
from services.authored_quality_receipt_v2 import public_attempt_id
from services.authored_vnext.generation.scene_checkpoint import (
    persist_scene_envelope,
    read_scene_checkpoint_state,
)
from services.authored_vnext.generation.scene_envelope import (
    AuthoredSlideSceneEnvelope,
    seal_deck_scene_envelope,
    seal_slide_scene_envelope,
)
from services.provider_usage_instrumentation import provider_usage_scope, record_local_stage
from services.source_images.models import SourceImageCandidate
from services.source_content.browser_readback import stamp_source_block_ids, BrowserSourceReadbackError
from utils.asset_directory_utils import get_images_directory
from utils.authored_illustrations import (
    enforce_strict_source_image_size_floor,
    validate_bound_source_images,
    validate_visible_source_images,
)
from utils.authored_dom_quality import ensure_authored_qids, inspect_authored_dom
from utils.llm_calls.author_deck import (
    AuthoredDeckResult,
    author_planned_slide,
    plan_deck_roles,
    prepare_authored_deck,
)
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    NO_PAGE_NUMBER_MODE,
    STYLE_REFERENCE,
    Brand,
    is_fallback_slide_html,
    normalize_reference_mode,
    validate_authored_slide_html,
)
from utils.renderer_isolation import RendererPolicyError
from utils.slide_capture import (
    _placeholder_png,
    ensure_browser_metrics,
    get_cached_browser_metrics,
    render_html_to_png,
)


# ``AUTHORED_SLIDE_PIPELINE_CONCURRENCY`` was historically both the number of
# provider calls and the number of whole-slide workers.  A worker spent most of
# its time in the provider, but it remained occupied by Chromium/checkpoint work
# after the response had returned.  Keep that setting as the provider ceiling,
# then give the downstream work its own bounded window.  This lets the next
# slide enter authoring as soon as one author response is durable without ever
# exceeding the configured provider or Chromium capacities.
_AUTHORED_AUTHOR_WORKERS = max(
    1,
    int(
        os.getenv(
            "AUTHORED_SLIDE_AUTHOR_CONCURRENCY",
            os.getenv("AUTHORED_SLIDE_PIPELINE_CONCURRENCY", "8"),
        )
    ),
)
_AUTHORED_RENDER_WORKERS = max(
    1,
    int(
        os.getenv("AUTHORED_SLIDE_RENDER_CONCURRENCY", str(_AUTHORED_AUTHOR_WORKERS))
    ),
)
_AUTHORED_SLIDE_WORKERS = _AUTHORED_AUTHOR_WORKERS + _AUTHORED_RENDER_WORKERS


def _evaluate_slide_quality(
    *,
    index: int,
    html: str,
    browser_metrics: Mapping[str, Any] | None,
    capacity_plan: AuthoredCapacityPlan | None,
    composition_profile: CompositionProfile | None,
    reference_mode: str,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Evaluate one exact render before its durable checkpoint is advanced."""

    if (
        composition_profile is None
        or not composition_profile.strict_dom_gate
        or reference_mode == EXACT_RECONSTRUCTION
    ):
        return None, None
    issues: list[dict[str, Any]] = []
    if capacity_plan is not None:
        issues.extend(
            {
                "category": "capacity",
                "severity": "error",
                "message": violation,
            }
            for violation in capacity_violations(
                html,
                browser_metrics,
                capacity_plan.at(index),
            )
        )
    report = inspect_authored_dom(
        html,
        browser_metrics,
        reference_mode=reference_mode,
        strict_structural_off_canvas=(composition_profile.profile_id != "ordinary"),
    )
    defects = blocking_safety_issues(report)
    if report.telemetry_status != "available":
        detail = (
            ", ".join(
                f"{issue.category}:{issue.severity}" for issue in defects[:6]
            )
            or f"telemetry:{report.telemetry_status}"
        )
        raise AuthoredStageError(
            slide_index=index,
            stage="render",
            code="authored_composition_safety_gate_failed",
            retryable=True,
            message=f"Slide {index + 1} failed the composition safety gate ({detail})",
        )
    issues.extend(
        issue.to_dict() if hasattr(issue, "to_dict") else {"message": str(issue)}
        for issue in defects
    )
    return report, ({"slide_index": index, "issues": issues} if issues else None)


async def _author_illustrate_render_pipeline(
    outline: PresentationOutlineModel,
    brand: Brand,
    style,
    *,
    generation_job: PresentationGenerationJob | None = None,
    structured_slide_contexts: Sequence[Mapping[str, Any] | None] | None = None,
    # Decks are unnumbered unless their own request asked for numbering; the
    # numbered layout is opted into by the caller that resolved the contract.
    page_number_mode: str = NO_PAGE_NUMBER_MODE,
    header_contract: AuthoredHeaderContract | None = None,
    chapter_plan: AuthoredChapterPlan | None = None,
    # Pictures the caller already owns, offered to the author instead of an
    # invented illustration. Empty means the feature is off for this deck.
    source_images: Sequence[SourceImageCandidate] = (),
    source_images_locked_to_source_slide: bool = False,
    require_all_source_images: bool = False,
    disable_generated_illustrations: bool = False,
    composition_profile: CompositionProfile | None = None,
    # The deck's already-decided illustration slots. None means no plan ran, and
    # the deck keeps the pre-plan behaviour in which any slide may author one.
    illustration_plan: AuthoredIllustrationPlan | None = None,
    instructions: str | None = None,
    source_block_bindings: Sequence[Sequence[tuple[str, str]]] | None = None,
    source_image_block_bindings: Sequence[Sequence[str]] | None = None,
) -> AuthoredGenerationPipelineResult:
    """Pipeline slides through durable, fenced checkpoints with bounded workers."""
    started = time.monotonic()
    # Keep one evidence ledger for the entire attempt.  Unlike the renderer's
    # bounded LRUs this retains every slide's HTML/PNG/metrics tuple, including
    # decks larger than 32 slides and retry/repair revisions.
    evidence_attempt_id = public_attempt_id(
        generation_job.attempt_token
        if generation_job is not None and generation_job.attempt_token
        else str(uuid.uuid4())
    )
    evidence_ledger = DeckEvidenceLedger(attempt_id=evidence_attempt_id)
    plan = prepare_authored_deck(
        outline,
        brand,
        style,
        structured_slide_contexts=structured_slide_contexts,
        page_number_mode=page_number_mode,
        header_contract=header_contract,
        chapter_plan=chapter_plan,
        illustration_plan=illustration_plan,
        instructions=instructions,
        composition_profile=composition_profile,
    )
    capacity_plan: AuthoredCapacityPlan | None = getattr(plan, "capacity_plan", None)
    slide_source_images = plan_slide_source_images(
        plan.contents,
        source_images,
        source_slide_locked=source_images_locked_to_source_slide,
    )
    if capacity_plan is not None:
        if slide_source_images is not None:
            capacity_plan = AuthoredCapacityPlan(
                profile=capacity_plan.profile,
                slides=tuple(
                    capacity_plan.at(index).with_source_images(
                        len(slide_source_images[index])
                    )
                    for index in range(len(capacity_plan.slides))
                ),
            )
        impossible = capacity_overflow(plan.contents, capacity_plan)
        if impossible:
            index, actual, limit = impossible[0]
            raise AuthoredStageError(
                slide_index=index,
                stage="author",
                code="authored_source_verbatim_capacity_exceeded",
                retryable=False,
                message=(
                    "Source-verbatim content cannot fit the locked slide envelope "
                    f"({actual} > {limit} visible characters)"
                ),
            )
    # Read defensively: the checkpoint regression tests drive this pipeline with
    # stand-in plan objects that carry only the fields they exercise.
    illustration_slots = getattr(plan, "illustration_slots", ())
    plan_reference_mode = normalize_reference_mode(
        getattr(
            plan,
            "reference_mode",
            getattr(style, "reference_mode", STYLE_REFERENCE),
        )
    )
    roles = plan_deck_roles(outline, chapter_plan)
    if plan_reference_mode == EXACT_RECONSTRUCTION:
        # Reproducing a source slide-for-slide may not gain a picture the
        # source does not have, whatever the caller uploaded.
        source_images = ()
    image_service = (
        ImageGenerationService(get_images_directory())
        if not disable_generated_illustrations
        and plan_reference_mode != EXACT_RECONSTRUCTION
        and getattr(style, "illustration_prompt", None)
        else None
    )
    attempt_token = generation_job.attempt_token if generation_job else None
    if generation_job is not None:
        if not attempt_token:
            raise AuthoredStageError(
                slide_index=0,
                stage="persist",
                code="authored_attempt_lost",
            )
        await initialize_checkpoints(
            job_id=generation_job.id,
            attempt_token=attempt_token,
            roles=roles,
        )

    # These are attempt-local on purpose.  Provider admission owns the process
    # wide/rate-limit policy; these gates only keep one large deck from filling
    # every local worker while previous slides are in a different stage.
    author_gate = asyncio.Semaphore(_AUTHORED_AUTHOR_WORKERS)
    render_gate = asyncio.Semaphore(_AUTHORED_RENDER_WORKERS)

    async def process_slide(
        index: int,
    ) -> tuple[
        int,
        str,
        bytes,
        float,
        float,
        float,
        Any | None,
        dict[str, Any] | None,
        AuthoredSlideSceneEnvelope,
    ]:
        author_work = 0.0
        illustration_work = 0.0
        render_work = 0.0
        browser_metrics = None
        quality_report = None
        quality_failure = None
        # The authoring call and the substitution step must see the identical
        # set: substitution resolves a placed id against this mapping, so an id
        # the author was never offered has nowhere to resolve.
        offered = (
            slide_source_images[index] if slide_source_images is not None else None
        )
        checkpoint = (
            await get_checkpoint(generation_job.id, index)
            if generation_job is not None
            else None
        )
        durable_evidence = None
        if checkpoint is not None and checkpoint.render_status == "completed":
            try:
                durable_evidence = read_valid_evidence(checkpoint)
            except Exception as exc:
                raise AuthoredStageError(
                    slide_index=index,
                    stage="render",
                    code="authored_checkpoint_evidence_corrupt",
                    cause=exc,
                    retryable=True,
                ) from exc
            if durable_evidence is not None:
                browser_metrics = durable_evidence.metrics
                if durable_evidence.html == getattr(checkpoint, "illustrated_html", None):
                    evidence_ledger.hydrate_slide(
                        attempt_id=evidence_attempt_id,
                        slide_index=index,
                        html=durable_evidence.html,
                        png=durable_evidence.png,
                        browser_metrics=durable_evidence.metrics,
                        checkpoint_revision=durable_evidence.revision,
                    )

        try:
            html = checkpoint.author_html if checkpoint else None
            if not html or checkpoint.author_status != "completed":
                if checkpoint and should_inject_failure(
                    checkpoint, slide_index=index, stage="author"
                ):
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_injected_failure",
                    )
                if generation_job is not None:
                    await start_stage(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="author",
                        attempt_token=attempt_token,
                    )
                author_started = time.monotonic()
                try:
                    # Do not make a completed author response wait behind its
                    # own Chromium/quality/checkpoint tail.  A spare slide
                    # worker can acquire this gate while this worker advances
                    # downstream, so authoring and rendering overlap.
                    async with author_gate:
                        with provider_usage_scope(
                            job_id=(
                                generation_job.id
                                if generation_job is not None
                                else None
                            ),
                            operation="authored_generation",
                            stage="author",
                            attempt_number=(
                                generation_job.attempt_number
                                if generation_job
                                else 0
                            ),
                            slide_index=index,
                        ):
                            author_kwargs: dict[str, Any] = {}
                            if generation_job is not None:
                                author_kwargs["raise_on_failure"] = True
                            if offered:
                                author_kwargs["source_images"] = tuple(
                                    offered.values()
                                )
                                author_kwargs["require_all_source_images"] = (
                                    require_all_source_images
                                )
                            html = await author_planned_slide(
                                plan, index, **author_kwargs
                            )
                except RendererPolicyError as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_slide_author_failed",
                        cause=exc,
                        retryable=False,
                    ) from exc
                except Exception as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_slide_author_failed",
                        cause=exc,
                        retryable=is_retryable_exception(exc),
                    ) from exc
                finally:
                    author_work = time.monotonic() - author_started
                    await record_local_stage(latency_ms=author_work * 1000)
                if is_fallback_slide_html(html):
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_slide_author_failed",
                        message=(
                            "Authored slide generation failed after retries for "
                            f"slide(s): {index + 1}. The incomplete fallback deck "
                            "was not published."
                        ),
                    )
                try:
                    html = validate_authored_slide_html(
                        html,
                        allow_illustration_slots=True,
                    )
                except RendererPolicyError as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_slide_author_failed",
                        cause=exc,
                        retryable=False,
                    ) from exc
                if generation_job is not None:
                    checkpoint = await _complete_stage_after_external_success(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="author",
                        attempt_token=attempt_token,
                        html=html,
                    )
            else:
                try:
                    html = validate_authored_slide_html(
                        html,
                        allow_illustration_slots=True,
                    )
                except RendererPolicyError as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_slide_author_failed",
                        cause=exc,
                        retryable=False,
                    ) from exc

            illustrated_html, checkpoint, illustration_work = await illustrate_slide(
                html=html,
                checkpoint=checkpoint,
                index=index,
                style=style,
                image_service=image_service,
                # The identical set the authoring call was offered. Substitution
                # resolves a placed id against this mapping, so an id the author
                # was never offered has nowhere to resolve; that is the feature's
                # security boundary. ``None`` means no candidates exist for this
                # deck at all, which keeps ``data-asset-id`` inert.
                source_images=offered,
                slide_content=(
                    plan.contents[index] if index < len(plan.contents) else ""
                ),
                # A deck without a plan lets every slide keep the slot it wrote,
                # which is exactly how this pipeline behaved before plans existed.
                illustration_allowed=(
                    False
                    if disable_generated_illustrations
                    else (illustration_slots[index] if illustration_slots else True)
                ),
                generation_job=generation_job,
                attempt_token=attempt_token,
            )
            if require_all_source_images:
                try:
                    illustrated_html = validate_bound_source_images(
                        illustrated_html,
                        offered or {},
                    )
                    illustrated_html = enforce_strict_source_image_size_floor(
                        illustrated_html,
                        offered or {},
                    )
                except ValueError as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="illustrate",
                        code="authored_slide_illustrate_failed",
                        cause=exc,
                        retryable=False,
                    ) from exc

            if source_block_bindings is not None and index < len(source_block_bindings):
                try:
                    illustrated_html = stamp_source_block_ids(
                        illustrated_html, source_block_bindings[index]
                    )
                except BrowserSourceReadbackError as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code=exc.code,
                        cause=exc,
                        retryable=False,
                        message=exc.detail,
                    ) from exc
            if source_image_block_bindings is not None and index < len(source_image_block_bindings):
                try:
                    from services.source_content.browser_readback import stamp_source_image_block_ids
                    illustrated_html = stamp_source_image_block_ids(
                        illustrated_html, source_image_block_bindings[index]
                    )
                except BrowserSourceReadbackError as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code=exc.code,
                        cause=exc,
                        retryable=False,
                        message=exc.detail,
                    ) from exc

            # Canonicalize semantic ids before the first render.  The renderer
            # instruments the same ids internally, so doing this here keeps the
            # source and render digests identical for the subsequent DOM gate;
            # the gate can then reuse the metrics captured with this PNG instead
            # of launching a second browser probe in shadow semantic mode.
            if plan_reference_mode != EXACT_RECONSTRUCTION:
                illustrated_html = ensure_authored_semantic_roles(
                    ensure_authored_qids(illustrated_html)
                )
            else:
                illustrated_html = ensure_authored_qids(illustrated_html)

            scene_envelope: AuthoredSlideSceneEnvelope
            if generation_job is not None:
                try:
                    scene_state = read_scene_checkpoint_state(
                        generation_job.id,
                        index,
                    )
                    if scene_state is not None:
                        if scene_state.envelope.html_projection != illustrated_html:
                            raise ValueError(
                                "authored_scene_checkpoint_projection_mismatch"
                            )
                        scene_envelope = scene_state.envelope
                    else:
                        scene_envelope = seal_slide_scene_envelope(
                            illustrated_html,
                            slide_index=index,
                        )
                        persist_scene_envelope(generation_job.id, scene_envelope)
                except (TypeError, ValueError) as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_scene_envelope_invalid",
                        cause=exc,
                        retryable=False,
                    ) from exc
            else:
                scene_envelope = seal_slide_scene_envelope(
                    illustrated_html,
                    slide_index=index,
                )
            illustrated_html = scene_envelope.html_projection

            png = read_valid_render(checkpoint) if checkpoint else None
            html_changed = bool(
                checkpoint is not None
                and checkpoint.illustrated_html
                and checkpoint.illustrated_html != illustrated_html
            )
            if png is None or checkpoint.render_status != "completed" or html_changed:
                if html_changed:
                    browser_metrics = None
                if checkpoint and should_inject_failure(
                    checkpoint, slide_index=index, stage="render"
                ):
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="render",
                        code="authored_injected_failure",
                    )
                if generation_job is not None:
                    await start_stage(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="render",
                        attempt_token=attempt_token,
                    )
                render_started = time.monotonic()
                try:
                    # Chromium is independently bounded.  The author gate is
                    # released before this point, allowing the next queued
                    # slide to call the provider while this exact HTML is
                    # rendered, checked, and checkpointed.
                    async with render_gate:
                        with provider_usage_scope(
                            job_id=(
                                generation_job.id
                                if generation_job is not None
                                else None
                            ),
                            operation="authored_generation",
                            stage="render",
                            attempt_number=(
                                generation_job.attempt_number
                                if generation_job
                                else 0
                            ),
                            slide_index=index,
                        ):
                            png = await render_html_to_png(illustrated_html)
                            browser_metrics = get_cached_browser_metrics(
                                illustrated_html
                            )
                            if png == _placeholder_png():
                                raise RuntimeError("renderer returned a placeholder")
                except Exception as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="render",
                        code="authored_slide_render_failed",
                        cause=exc,
                        retryable=is_retryable_exception(exc),
                    ) from exc
                finally:
                    render_work = time.monotonic() - render_started
                    await record_local_stage(latency_ms=render_work * 1000)
            if require_all_source_images:
                try:
                    if browser_metrics is None:
                        browser_metrics = await ensure_browser_metrics(illustrated_html)
                    illustrated_html = validate_visible_source_images(
                        illustrated_html,
                        offered or {},
                        browser_metrics,
                    )
                except ValueError as exc:
                    # Re-open authoring on a durable retry. Marking the author
                    # stage failed makes start_stage invalidate the old
                    # illustrate/render checkpoints instead of replaying the
                    # same hidden-image HTML forever.
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_slide_author_failed",
                        cause=exc,
                        retryable=True,
                    ) from exc

            quality_report, quality_failure = _evaluate_slide_quality(
                index=index,
                html=illustrated_html,
                browser_metrics=browser_metrics,
                capacity_plan=capacity_plan,
                composition_profile=composition_profile,
                reference_mode=plan_reference_mode,
            )

            # Quality is evaluated against the exact capture before advancing
            # the durable render pointer. Different slides can therefore be in
            # author, Chromium, quality, and checkpoint stages concurrently.
            if (
                generation_job is not None
                and (
                    checkpoint.render_status != "completed"
                    or checkpoint.illustrated_html != illustrated_html
                )
            ):
                checkpoint = await _complete_stage_after_external_success(
                    job_id=generation_job.id,
                    slide_index=index,
                    stage="render",
                    attempt_token=attempt_token,
                    html=illustrated_html,
                    png=png,
                    browser_metrics=browser_metrics,
                )

            if generation_job is not None and checkpoint.persist_status != "completed":
                persist_stage_started = time.monotonic()
                with provider_usage_scope(
                    job_id=generation_job.id,
                    operation="authored_generation",
                    stage="persist",
                    attempt_number=generation_job.attempt_number,
                    slide_index=index,
                ):
                    try:
                        await start_stage(
                            job_id=generation_job.id,
                            slide_index=index,
                            stage="persist",
                            attempt_token=attempt_token,
                        )
                        await _complete_stage_after_external_success(
                            job_id=generation_job.id,
                            slide_index=index,
                            stage="persist",
                            attempt_token=attempt_token,
                        )
                    finally:
                        await record_local_stage(
                            latency_ms=(time.monotonic() - persist_stage_started)
                            * 1000
                        )
            # RenderCapture already collected browser telemetry in the same
            # browser invocation.  Read only the cache here: a cache miss is
            # retained as unavailable evidence and must fail closed downstream,
            # rather than launching a second browser for an unchanged slide.
            if browser_metrics is None:
                browser_metrics = get_cached_browser_metrics(illustrated_html)
            if durable_evidence is not None and durable_evidence.html == illustrated_html:
                evidence = evidence_ledger.hydrate_slide(
                    attempt_id=evidence_attempt_id,
                    slide_index=index,
                    html=illustrated_html,
                    png=png,
                    browser_metrics=browser_metrics,
                    checkpoint_revision=durable_evidence.revision,
                )
            else:
                evidence = evidence_ledger.record_slide(
                    attempt_id=evidence_attempt_id,
                    slide_index=index,
                    html=illustrated_html,
                    png=png,
                    browser_metrics=browser_metrics,
                    checkpoint_revision=int(getattr(checkpoint, "revision", 0) or 0),
                )
            return (
                index,
                illustrated_html,
                png,
                author_work,
                illustration_work,
                render_work,
                quality_report,
                quality_failure,
                scene_envelope,
            )
        except AuthoredStageError as exc:
            if generation_job is not None:
                await fail_stage(
                    job_id=generation_job.id,
                    slide_index=index,
                    stage=exc.stage,
                    attempt_token=attempt_token,
                    error=exc,
                    request_id=generation_job.request_id,
                )
            raise

    results: list[tuple[Any, ...] | None] = [None] * len(plan.contents)
    worker_count = min(_AUTHORED_SLIDE_WORKERS, len(plan.contents))
    # The producer can be at most two worker windows ahead. This is real
    # backpressure rather than an unbounded queue prefilled with every slide.
    queue: asyncio.Queue[int | object] = asyncio.Queue(
        maxsize=max(1, worker_count * 2)
    )
    sentinel = object()
    stop_workers = asyncio.Event()

    async def producer() -> None:
        try:
            for index in range(len(plan.contents)):
                if stop_workers.is_set():
                    break
                await queue.put(index)
        finally:
            for _ in range(worker_count):
                await queue.put(sentinel)

    async def worker() -> None:
        while True:
            item = await queue.get()
            try:
                if item is sentinel:
                    return
                if stop_workers.is_set():
                    continue
                index = int(item)
                try:
                    results[index] = await process_slide(index)
                except BaseException:
                    # Already-returned provider work is checkpointed by the
                    # process_slide boundary. Other workers drain without
                    # claiming more billable slides.
                    stop_workers.set()
                    raise
            finally:
                queue.task_done()

    producer_task = asyncio.create_task(producer())
    worker_tasks = [asyncio.create_task(worker()) for _ in range(worker_count)]
    try:
        worker_outcomes = await asyncio.gather(
            *worker_tasks, return_exceptions=True
        )
    except asyncio.CancelledError:
        # User/server cancellation still interrupts the deck attempt. Any
        # provider result that already returned is checkpointed by the helper
        # above before the worker can finish cancellation.
        producer_task.cancel()
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(producer_task, *worker_tasks, return_exceptions=True)
        raise
    failures = [
        outcome
        for outcome in worker_outcomes
        if isinstance(outcome, BaseException)
    ]
    if failures:
        producer_task.cancel()
        await asyncio.gather(producer_task, return_exceptions=True)
        raise failures[0]
    await producer_task
    completed_results = [result for result in results if result is not None]
    if len(completed_results) != len(plan.contents):
        raise RuntimeError("authored slide worker exited before completion")
    htmls = [result[1] for result in completed_results]
    pngs = [result[2] for result in completed_results]
    quality_gate_reports = [
        result[6] for result in completed_results if result[6] is not None
    ]
    quality_gate_failures = [
        result[7] for result in completed_results if result[7] is not None
    ]
    quality_gate_failed_indices = [
        int(failure["slide_index"]) for failure in quality_gate_failures
    ]
    scene_envelope = seal_deck_scene_envelope(
        result[8] for result in completed_results
    )
    return AuthoredGenerationPipelineResult(
        deck=AuthoredDeckResult(htmls=htmls, design_system=plan.design_system),
        pngs=pngs,
        elapsed=time.monotonic() - started,
        author_work=sum(result[3] for result in completed_results),
        illustration_work=sum(result[4] for result in completed_results),
        render_work=sum(result[5] for result in completed_results),
        quality_gate_reports=tuple(quality_gate_reports),
        quality_gate_failed_indices=tuple(quality_gate_failed_indices),
        quality_gate_failures=tuple(quality_gate_failures),
        evidence_ledger=evidence_ledger,
        evidence_attempt_id=evidence_attempt_id,
        scene_envelope=scene_envelope,
    )
