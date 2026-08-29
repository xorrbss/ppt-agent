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
    render_html_to_png_with_metrics,
)


_AUTHORED_SLIDE_WORKERS = max(
    1, int(os.getenv("AUTHORED_SLIDE_PIPELINE_CONCURRENCY", "8"))
)


async def _refresh_semantic_gate_evidence(
    *,
    htmls: Sequence[str],
    pngs: list[bytes],
    evidence_ledger: DeckEvidenceLedger,
    evidence_attempt_id: str,
) -> tuple[Mapping[str, Any] | None, ...]:
    """Bind final-gate metrics and pixels to the exact semantically stamped HTML.

    A durable render checkpoint can predate deterministic semantic-role stamping.
    Reusing its metrics would make the old role classification authoritative over
    the HTML admitted by the final gate.  Capture only slides whose ledger HTML or
    metrics do not match, append that evidence to the attempt ledger, and leave the
    immutable durable checkpoint untouched.
    """

    metrics: list[Mapping[str, Any] | None] = []
    for index, html in enumerate(htmls):
        latest = evidence_ledger.latest(evidence_attempt_id, index)
        if (
            latest is not None
            and latest.html == html
            and latest.browser_metrics is not None
        ):
            metrics.append(latest.browser_metrics)
            continue

        try:
            capture = await render_html_to_png_with_metrics(html)
        except Exception as exc:
            raise AuthoredStageError(
                slide_index=index,
                stage="render",
                code="authored_slide_render_failed",
                cause=exc,
                retryable=is_retryable_exception(exc),
            ) from exc
        if capture.png == _placeholder_png() or capture.browser_metrics is None:
            raise AuthoredStageError(
                slide_index=index,
                stage="render",
                code="authored_composition_safety_gate_failed",
                retryable=True,
                message=(
                    f"Slide {index + 1} did not produce browser evidence for "
                    "the final semantic gate"
                ),
            )
        pngs[index] = capture.png
        evidence_ledger.record_slide(
            attempt_id=evidence_attempt_id,
            slide_index=index,
            html=html,
            png=capture.png,
            browser_metrics=capture.browser_metrics,
            checkpoint_revision=(latest.checkpoint_revision if latest is not None else 0),
        )
        metrics.append(capture.browser_metrics)
    return tuple(metrics)


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

    async def process_slide(
        index: int,
    ) -> tuple[int, str, bytes, float, float, float]:
        author_work = 0.0
        illustration_work = 0.0
        render_work = 0.0
        browser_metrics = None
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
                with provider_usage_scope(
                    job_id=generation_job.id if generation_job is not None else None,
                    operation="authored_generation",
                    stage="author",
                    attempt_number=(generation_job.attempt_number if generation_job else 0),
                    slide_index=index,
                ):
                    author_kwargs: dict[str, Any] = {}
                    if generation_job is not None:
                        author_kwargs["raise_on_failure"] = True
                    if offered:
                        author_kwargs["source_images"] = tuple(offered.values())
                        author_kwargs["require_all_source_images"] = (
                            require_all_source_images
                        )
                    try:
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
                        # Snapshot metrics immediately with this render.  The
                        # process-wide metric LRU may evict an earlier slide
                        # while another worker is persisting its checkpoint.
                        browser_metrics = get_cached_browser_metrics(illustrated_html)
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
                        html=illustrated_html,
                        png=png,
                        browser_metrics=browser_metrics,
                    )

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
    quality_gate_reports: list[Any] = []
    quality_gate_failed_indices: list[int] = []
    quality_gate_failures: list[dict[str, Any]] = []
    if (
        composition_profile is not None
        and composition_profile.strict_dom_gate
        and plan_reference_mode != EXACT_RECONSTRUCTION
    ):
        # This is a local, deterministic admission gate.  It runs after the
        # slide render and before deck publication, so an ordinary Authored deck
        # cannot ship clipping/overflow/overlap merely because VQA was disabled.
        # Shadow semantic rollout decks may not yet contain model-declared QIDs;
        # inject the same deterministic ids used by the publication bridge for
        # telemetry only.  This does not change visible pixels.
        gate_htmls = [
            ensure_authored_semantic_roles(ensure_authored_qids(html))
            for html in htmls
        ]
        # The ledger remains the sole source for the final gate.  A checkpoint
        # recovered from an older semantic-role policy is not unchanged merely
        # because its pixels survived: bind one bounded recapture to the exact
        # gate HTML before reading its metrics.
        metrics = await _refresh_semantic_gate_evidence(
            htmls=gate_htmls,
            pngs=pngs,
            evidence_ledger=evidence_ledger,
            evidence_attempt_id=evidence_attempt_id,
        )
        htmls = gate_htmls
        for index, (html, browser_metrics) in enumerate(
            zip(gate_htmls, metrics, strict=True)
        ):
            slide_gate_issues: list[dict[str, Any]] = []
            if capacity_plan is not None:
                violations = capacity_violations(
                    html,
                    browser_metrics,
                    capacity_plan.at(index),
                )
                if violations:
                    # Capacity failures are slide-local and repairable. Returning
                    # them through the existing bounded repair path prevents a
                    # workspace retry from replaying the same completed render
                    # checkpoint until dead-letter.
                    slide_gate_issues.extend(
                        {
                            "category": "capacity",
                            "severity": "error",
                            "message": violation,
                        }
                        for violation in violations
                    )
            report = inspect_authored_dom(
                html,
                browser_metrics,
                reference_mode=plan_reference_mode,
                strict_structural_off_canvas=(
                    composition_profile.profile_id != "ordinary"
                ),
            )
            quality_gate_reports.append(report)
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
            if defects:
                slide_gate_issues.extend(
                    issue.to_dict() if hasattr(issue, "to_dict") else {"message": str(issue)}
                    for issue in defects
                )
            if slide_gate_issues:
                # Keep the first-pass deck intact and return the exact failing
                # indices to the caller. Generation performs one bounded
                # slide-only repair and re-runs both capacity and safety gates.
                quality_gate_failed_indices.append(index)
                quality_gate_failures.append(
                    {
                        "slide_index": index,
                        "issues": slide_gate_issues,
                    }
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
    )
