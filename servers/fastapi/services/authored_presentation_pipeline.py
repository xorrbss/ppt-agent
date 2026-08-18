from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

from models.authored_chapter_plan import AuthoredChapterPlan
from models.authored_header_contract import AuthoredHeaderContract
from models.presentation_outline_model import PresentationOutlineModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.authored_checkpoint_service import (
    AuthoredStageError,
    fail_stage,
    get_checkpoint,
    initialize_checkpoints,
    is_retryable_exception,
    read_valid_render,
    should_inject_failure,
    start_stage,
)
from services.authored_presentation_artifacts import (
    _complete_stage_after_external_success,
)
from services.authored_presentation_contracts import AuthoredGenerationPipelineResult
from services.image_generation_service import ImageGenerationService
from services.provider_usage_instrumentation import provider_usage_scope, record_local_stage
from utils.asset_directory_utils import get_images_directory
from utils.authored_illustrations import apply_authored_illustration
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
from utils.slide_capture import _placeholder_png, render_html_to_png


_AUTHORED_SLIDE_WORKERS = max(
    1, int(os.getenv("AUTHORED_SLIDE_PIPELINE_CONCURRENCY", "8"))
)


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
) -> AuthoredGenerationPipelineResult:
    """Pipeline slides through durable, fenced checkpoints with bounded workers."""
    started = time.monotonic()
    plan = prepare_authored_deck(
        outline,
        brand,
        style,
        structured_slide_contexts=structured_slide_contexts,
        page_number_mode=page_number_mode,
        header_contract=header_contract,
        chapter_plan=chapter_plan,
    )
    plan_reference_mode = normalize_reference_mode(
        getattr(
            plan,
            "reference_mode",
            getattr(style, "reference_mode", STYLE_REFERENCE),
        )
    )
    roles = plan_deck_roles(outline, chapter_plan)
    image_service = (
        ImageGenerationService(get_images_directory())
        if plan_reference_mode != EXACT_RECONSTRUCTION
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

    async def process_slide(
        index: int,
    ) -> tuple[int, str, bytes, float, float, float]:
        author_work = 0.0
        illustration_work = 0.0
        render_work = 0.0
        checkpoint = (
            await get_checkpoint(generation_job.id, index)
            if generation_job is not None
            else None
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
                with provider_usage_scope(
                    job_id=generation_job.id if generation_job is not None else None,
                    operation="authored_generation",
                    stage="author",
                    attempt_number=(generation_job.attempt_number if generation_job else 0),
                    slide_index=index,
                ):
                    try:
                        if generation_job is None:
                            html = await author_planned_slide(plan, index)
                        else:
                            html = await author_planned_slide(
                                plan, index, raise_on_failure=True
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

            illustrated_html = (
                checkpoint.illustrated_html
                if checkpoint
                and checkpoint.illustrate_status in ("completed", "skipped")
                and checkpoint.illustrated_html
                else None
            )
            if illustrated_html is None:
                if checkpoint and should_inject_failure(
                    checkpoint, slide_index=index, stage="illustrate"
                ):
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="illustrate",
                        code="authored_injected_failure",
                    )
                if generation_job is not None:
                    await start_stage(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="illustrate",
                        attempt_token=attempt_token,
                    )
                illustrated_html = html
                skipped = image_service is None
                if image_service is not None:
                    illustration_started = time.monotonic()
                    with provider_usage_scope(
                        job_id=generation_job.id if generation_job is not None else None,
                        operation="authored_generation",
                        stage="illustrate",
                        attempt_number=(generation_job.attempt_number if generation_job else 0),
                        slide_index=index,
                    ):
                        try:
                            if generation_job is None:
                                illustrated_html = await apply_authored_illustration(
                                    html, style, image_service, slide_index=index
                                )
                            else:
                                illustrated_html = await apply_authored_illustration(
                                    html,
                                    style,
                                    image_service,
                                    slide_index=index,
                                    raise_on_failure=True,
                                )
                        except Exception as exc:
                            raise AuthoredStageError(
                                slide_index=index,
                                stage="illustrate",
                                code="authored_slide_illustrate_failed",
                                cause=exc,
                                retryable=is_retryable_exception(exc),
                            ) from exc
                        finally:

                            illustration_work = time.monotonic() - illustration_started
                            await record_local_stage(
                                latency_ms=illustration_work * 1000
                            )
                try:
                    illustrated_html = validate_authored_slide_html(
                        illustrated_html,
                        finalized=True,
                    )
                except RendererPolicyError as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="illustrate",
                        code="authored_slide_illustrate_failed",
                        cause=exc,
                        retryable=False,
                    ) from exc
                if generation_job is not None:
                    checkpoint = await _complete_stage_after_external_success(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="illustrate",
                        attempt_token=attempt_token,
                        html=illustrated_html,
                        skipped=skipped,
                    )
            else:
                try:
                    illustrated_html = validate_authored_slide_html(
                        illustrated_html,
                        finalized=True,
                    )
                except RendererPolicyError as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="illustrate",
                        code="authored_slide_illustrate_failed",
                        cause=exc,
                        retryable=False,
                    ) from exc

            png = read_valid_render(checkpoint) if checkpoint else None
            if png is None or checkpoint.render_status != "completed":
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
                with provider_usage_scope(
                    job_id=generation_job.id if generation_job is not None else None,
                    operation="authored_generation",
                    stage="render",
                    attempt_number=(generation_job.attempt_number if generation_job else 0),
                    slide_index=index,
                ):
                    try:
                        png = await render_html_to_png(illustrated_html)
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
                if generation_job is not None:
                    checkpoint = await _complete_stage_after_external_success(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="render",
                        attempt_token=attempt_token,
                        png=png,
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
            return (
                index,
                illustrated_html,
                png,
                author_work,
                illustration_work,
                render_work,
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

    results: list[tuple[int, str, bytes, float, float, float] | None] = [
        None
    ] * len(plan.contents)
    queue: asyncio.Queue[int] = asyncio.Queue()
    for index in range(len(plan.contents)):
        queue.put_nowait(index)

    stop_workers = asyncio.Event()

    async def worker() -> None:
        while not stop_workers.is_set():
            try:
                index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                results[index] = await process_slide(index)
            except BaseException:
                # Do not cancel sibling provider calls that are already in
                # flight: they may already be billable. Let each active slide
                # finish its cancellation-safe checkpoint, but prevent workers
                # from claiming more queued slides.
                stop_workers.set()
                raise
            finally:
                queue.task_done()

    worker_tasks = [
        asyncio.create_task(worker())
        for _ in range(min(_AUTHORED_SLIDE_WORKERS, len(plan.contents)))
    ]
    try:
        worker_outcomes = await asyncio.gather(
            *worker_tasks, return_exceptions=True
        )
    except asyncio.CancelledError:
        # User/server cancellation still interrupts the deck attempt. Any
        # provider result that already returned is checkpointed by the helper
        # above before the worker can finish cancellation.
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        raise
    failures = [
        outcome
        for outcome in worker_outcomes
        if isinstance(outcome, BaseException)
    ]
    if failures:
        raise failures[0]
    completed_results = [result for result in results if result is not None]
    if len(completed_results) != len(plan.contents):
        raise RuntimeError("authored slide worker exited before completion")
    htmls = [html for _, html, _, _, _, _ in completed_results]
    pngs = [png for _, _, png, _, _, _ in completed_results]
    return AuthoredGenerationPipelineResult(
        deck=AuthoredDeckResult(htmls=htmls, design_system=plan.design_system),
        pngs=pngs,
        elapsed=time.monotonic() - started,
        author_work=sum(result[3] for result in completed_results),
        illustration_work=sum(result[4] for result in completed_results),
        render_work=sum(result[5] for result in completed_results),
    )
