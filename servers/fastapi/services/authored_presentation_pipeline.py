from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from models.authored_chapter_plan import AuthoredChapterPlan
from models.authored_header_contract import AuthoredHeaderContract
from models.authored_illustration_plan import AuthoredIllustrationPlan
from models.authored_infographic_plan import AuthoredInfographicPlan
from models.presentation_outline_model import PresentationOutlineModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.authored_checkpoint_state import provider_attempt_count_in_epoch
from services.authored_author_stage_deferral import (
    DeferredAuthorBudgetExhausted,
    author_stage_failure as _author_stage_failure,
    defer_rescuable_author_failures,
    deferred_author_budget_error,
)
from services.authored_author_stage_rescue import rescue_author_stage
from services.authored_checkpoint_service import (
    AuthoredStageError,
    fail_stage,
    get_checkpoint,
    initialize_checkpoints,
    is_retryable_exception,
    reserve_provider_attempt,
    should_inject_failure,
    start_stage,
)
from services.authored_checkpoint_artifacts import read_valid_evidence_artifact, read_valid_render_artifact
from services.authored_checkpoint_html import checkpoint_stage_html
from services.authored_presentation_artifacts import _complete_stage_after_external_success
from services.authored_illustration_stage import illustrate_slide
from services.authored_infographic_assets import build_infographic_asset_context, deck_has_picture_source
from services.authored_quality_loop_stage import apply_slide_quality_loop
from services.authored_presentation_contracts import AuthoredGenerationPipelineResult
from services.authored_evidence_ledger import DeckEvidenceLedger
from services.image_generation_service import ImageGenerationService
from services.authored_source_images import plan_slide_source_images
from services.authored_measured_gates import measured_font_floor_px, measured_gate_issues
from services.authored_composition_policy import (
    AuthoredCapacityPlan,
    CompositionProfile,
    blocking_safety_issues,
    capacity_violations,
    capacity_overflow,
)
from services.authored_quality_receipt_v2 import public_attempt_id
from utils.authored_semantic_role_repair import canonicalize_authored_roles, plan_slide_role
from services.authored_fit_envelope import reseal_scene_envelope_after_fit
from services.authored_vnext.generation.scene_checkpoint import persist_scene_envelope, read_scene_checkpoint_state
from services.authored_vnext.generation.scene_envelope import (
    AuthoredSlideSceneEnvelope,
    seal_deck_scene_envelope,
    seal_slide_scene_envelope,
)
from services.provider_usage_instrumentation import provider_usage_scope, record_local_stage
from services.authored_generation_performance import authored_provider_concurrency
from services.authored_slide_worker_pool import run_slide_worker_pool
from services.authored_batch_policy import new_authored_batch_policy_state
from services.authored_tail_streaming import (
    AuthoredTailStreamCoordinator,
    run_authored_recovery_pass,
    run_streamed_slide_tail,
)
from services.authored_preauthor_scan import report_unresolved_slides, scan_preauthor_checkpoints
from services.authored_slide_ledger import assert_authored_ledger_disjoint, batch_recorder
from services.authored_style_anchor_stage import (
    accepts_style_anchor, anchor_blocking_defects, anchor_render_step,
    resolve_direct_lane_anchor, resolve_style_anchor,
)
from services.authored_wave_scheduler import AuthoredWaveSchedule, run_initial_authoring
from services.source_images.models import SourceImageCandidate
from services.source_content.browser_readback import BrowserSourceReadbackError
from services.source_content.header_pages import header_absent_pages
from services.source_content.text_stamping import stamp_source_blocks
from utils.asset_directory_utils import get_images_directory
from utils.authored_illustrations import (
    enforce_strict_source_image_size_floor, validate_bound_source_images, validate_visible_source_images,
)
from utils.authored_font_policy import enforce_authored_font_floor
from utils.authored_dom_quality import ensure_authored_qids, inspect_authored_dom
from utils.llm_calls.author_deck import (
    author_correction_from_stored_failure,
    AuthoredBatchFailure,
    AuthoredDeckResult,
    AuthoredPlannedBatchResult,
    author_planned_batch,
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
from utils.get_env_programme import is_authored_in_render_fit_enabled, is_authored_tail_streaming_enabled
from utils.llm_calls.author_prompt_cache_key import deck_prompt_cache_key, set_prompt_cache_key
from utils.slide_capture_deck_scope import set_render_deck_scope
from utils.llm_provider import get_llm_provider
from utils.slide_capture import _placeholder_png, ensure_browser_metrics, get_cached_browser_metrics, render_html_to_png
from utils.slide_capture import render_html_to_png_with_metrics

LOGGER = logging.getLogger(__name__)


# Provider authoring consumes one selected-provider policy shared with the
# author executor. Chromium keeps its independent local gate so completed
# provider responses can overlap render/checkpoint work without expanding
# provider concurrency.
try:
    _selected_provider = str(get_llm_provider().value)
except Exception:
    _selected_provider = None
_AUTHORED_AUTHOR_WORKERS = authored_provider_concurrency(_selected_provider)
_AUTHORED_RENDER_WORKERS = max(
    1,
    int(
        os.getenv("AUTHORED_SLIDE_RENDER_CONCURRENCY", str(_AUTHORED_AUTHOR_WORKERS))
    ),
)
_AUTHORED_SLIDE_WORKERS = _AUTHORED_AUTHOR_WORKERS + _AUTHORED_RENDER_WORKERS
_DEFAULT_AUTHOR_PLANNED_SLIDE = author_planned_slide


def _evaluate_slide_quality(
    *,
    index: int,
    html: str,
    browser_metrics: Mapping[str, Any] | None,
    capacity_plan: AuthoredCapacityPlan | None,
    composition_profile: CompositionProfile | None,
    reference_mode: str,
    fit_report: Mapping[str, Any] | None = None,
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
    issues.extend(measured_gate_issues(browser_metrics=browser_metrics, fit_report=fit_report))  # last
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
    # The deck's already-decided figures, one per final-outline slide. None
    # means no plan ran and every slide composes itself as before.
    infographic_plan: AuthoredInfographicPlan | None = None,
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
        # A restyled page keeps the deck's header only if its own source page
        # has one. A cover and a closing page carry neither the running header
        # nor a page number, and requiring a band there rejected a composition
        # that was faithful to the source.
        header_exempt_slides=header_absent_pages(source_block_bindings),
        chapter_plan=chapter_plan,
        illustration_plan=illustration_plan,
        infographic_plan=infographic_plan,
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
    # A restyle binds every slide's source blocks; nothing else does. That is
    # what makes this deck one a word may not be lost from, so it is also what
    # keeps the author-stage rescue off it.
    source_reflow = (
        source_image_block_bindings is not None or source_images_locked_to_source_slide
    )
    # D7 author-stage flags, keyed by slide index so parallel slide workers
    # produce one deterministic, index-ordered list for the receipt.
    author_flagged_slides: dict[int, dict[str, object]] = {}
    if plan_reference_mode == EXACT_RECONSTRUCTION:
        # Reproducing a source slide-for-slide may not gain a picture the
        # source does not have, whatever the caller uploaded.
        source_images = ()
    # A transparent-capable provider is a picture source too: a planned scene
    # takes its object cutout from it without any style illustration prompt.
    image_service = (
        ImageGenerationService(get_images_directory())
        if not disable_generated_illustrations
        and plan_reference_mode != EXACT_RECONSTRUCTION
        and deck_has_picture_source(style)
        else None
    )
    # Whether the deck may replace the figures' built-in icons and a scene's
    # slot with transparent PNGs from the icon pack. Decided once per deck;
    # None keeps every slide's library markup as authored.
    infographic_briefs = getattr(plan, "infographic_briefs", ())
    infographic_assets = (
        build_infographic_asset_context(
            style=style,
            brand=plan.brand,
            instructions=instructions,
            image_service=image_service,
        )
        if infographic_briefs and not disable_generated_illustrations
        else None
    )
    attempt_token = generation_job.attempt_token if generation_job else None
    fencing_token = getattr(generation_job, "_workspace_fencing_token", None)
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
            fencing_token=fencing_token,
            roles=roles,
        )

    # These are attempt-local on purpose.  Provider admission owns the process
    # wide/rate-limit policy; these gates only keep one large deck from filling
    # every local worker while previous slides are in a different stage.
    author_gate = asyncio.Semaphore(_AUTHORED_AUTHOR_WORKERS)
    render_gate = asyncio.Semaphore(_AUTHORED_RENDER_WORKERS)

    # Production authoring is filled before the local illustration/render tail
    # begins. One physical provider call returns at most three HTML documents;
    # accepted documents are checkpointed immediately and never enter recovery.
    # A monkeypatched single-slide callable keeps legacy fixture seams isolated
    # from provider I/O without weakening the product path.
    preauthored_htmls: dict[int, str] = {}
    preauthored_attempt_counts: dict[int, int] = {}
    preauthored_author_work: dict[int, float] = {}
    # Slides the pre-authoring pass could not author and handed to the tail's
    # author-stage rescue instead of failing the deck, keyed by slide index.
    author_rescue_deferrals: dict[int, DeferredAuthorBudgetExhausted] = {}
    # G0: the deck's style anchor, once one slide has been authored and gated.
    # ``None`` keeps every later prompt byte-identical to today's.
    style_anchor: Any | None = None
    # F3: seconds this attempt spent waiting out a provider cooldown before an
    # admission. A single-element list so the nested stages can write to it.
    authoring_cooldown_wait_seconds = [0.0]
    # Phase 3: with the tail streamed, a slide's illustrate/render/quality/
    # checkpoint tail starts as its own authoring lands instead of waiting out
    # the whole pre-authoring pass. ``None`` keeps the phase barrier: the pass
    # completes, then the pool runs, in exactly today's order.
    tail_stream = (
        AuthoredTailStreamCoordinator(len(plan.contents))
        if generation_job is not None
        and author_planned_slide is _DEFAULT_AUTHOR_PLANNED_SLIDE
        and is_authored_tail_streaming_enabled()
        else None
    )

    def _anchor_blocking_defects(html: str) -> tuple[Any, ...]:
        return anchor_blocking_defects(
            html,
            composition_profile=composition_profile,
            reference_mode=plan_reference_mode,
        )

    def _checkpoint_provider_attempt_count(checkpoint: object | None) -> int:
        """Provider calls this slide has already spent in *this* attempt.

        Ordinals from an earlier attempt of the operation are history: the
        retry exists to redo the slides that failed, and it cannot do that with
        an exhausted budget.  ``reserve_provider_attempt`` rolls them into
        ``provider_attempt_total`` when the fencing token advances.
        """

        error = getattr(checkpoint, "error", None)
        return provider_attempt_count_in_epoch(
            error if isinstance(error, Mapping) else None, fencing_token
        )

    async def _persist_batch_success(index: int, html: str) -> None:
        if is_fallback_slide_html(html):
            raise AuthoredStageError(
                slide_index=index,
                stage="author",
                code="authored_slide_author_failed",
            )
        try:
            admitted = validate_authored_slide_html(
                html, allow_illustration_slots=True
            )
        except RendererPolicyError as exc:
            raise AuthoredStageError(
                slide_index=index,
                stage="author",
                code="authored_slide_author_failed",
                cause=exc,
                retryable=False,
            ) from exc
        preauthored_htmls[index] = admitted
        if generation_job is not None:
            await _complete_stage_after_external_success(
                job_id=generation_job.id,
                slide_index=index,
                stage="author",
                attempt_token=attempt_token,
                fencing_token=fencing_token,
                html=admitted,
            )

    async def _run_author_batch(
        indexes: Sequence[int],
        *,
        recovery: bool,
        prior_failures: Mapping[int, AuthoredBatchFailure],
        style_anchor: Any | None = None,
    ):
        selected = tuple(indexes)
        attempt_observers: dict[int, Any] = {}
        attempt_reservers: dict[int, Any] = {}

        for index in selected:
            if generation_job is not None:
                checkpoint = await get_checkpoint(
                    generation_job.id,
                    index,
                    attempt_token=attempt_token,
                    fencing_token=fencing_token,
                )
                if checkpoint and should_inject_failure(
                    checkpoint, slide_index=index, stage="author"
                ):
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_injected_failure",
                    )
                await start_stage(
                    job_id=generation_job.id,
                    slide_index=index,
                    stage="author",
                    attempt_token=attempt_token,
                    fencing_token=fencing_token,
                )

            def observe(ordinal: int, *, slide_index: int = index) -> None:
                preauthored_attempt_counts[slide_index] = max(
                    preauthored_attempt_counts.get(slide_index, 0), int(ordinal)
                )

            attempt_observers[index] = observe
            if generation_job is not None:
                async def reserve(
                    ordinal: int, *, slide_index: int = index
                ) -> None:
                    await reserve_provider_attempt(
                        job_id=generation_job.id,
                        slide_index=slide_index,
                        attempt_token=attempt_token,
                        fencing_token=fencing_token,
                        ordinal=ordinal,
                    )

                attempt_reservers[index] = reserve

        sources_by_index = {
            index: tuple(
                (slide_source_images[index] or {}).values()
                if slide_source_images is not None
                else ()
            )
            for index in selected
        }
        started_batch = time.monotonic()
        try:
            with provider_usage_scope(
                job_id=generation_job.id if generation_job is not None else None,
                operation="authored_generation",
                stage="author",
                attempt_number=(generation_job.attempt_number if generation_job else 0),
                slide_index=None,
            ):
                batch_kwargs: dict[str, Any] = {}
                if style_anchor is not None and accepts_style_anchor(
                    author_planned_batch
                ):
                    batch_kwargs["style_anchor"] = style_anchor
                result = await author_planned_batch(
                    plan,
                    selected,
                    source_images_by_index=sources_by_index,
                    require_all_source_images=require_all_source_images,
                    recovery=recovery,
                    prior_failures=prior_failures,
                    raise_on_failure=generation_job is not None,
                    attempt_observers=attempt_observers,
                    attempt_reservers=attempt_reservers,
                    **batch_kwargs,
                )
            persisted_htmls: dict[int, str] = {}
            persisted_failures = dict(result.failures)
            for index, html in result.htmls.items():
                try:
                    await _persist_batch_success(index, html)
                    persisted_htmls[index] = html
                except BaseException as error:
                    # The provider output itself succeeded. Never spend the
                    # slide's recovery call to compensate for a checkpoint I/O
                    # failure; fail the attempt with the HTML retained in memory.
                    preauthored_attempt_counts[index] = 2
                    persisted_failures[index] = AuthoredBatchFailure(
                        AuthoredStageError(
                            slide_index=index,
                            stage="author",
                            code="authored_slide_persist_failed",
                            cause=error,
                            retryable=is_retryable_exception(error),
                            provider_attempt_count=1 if not recovery else 2,
                        ),
                        "Do not regenerate this accepted provider output.",
                        html,
                    )
            return AuthoredPlannedBatchResult(
                htmls=persisted_htmls,
                failures=persisted_failures,
            )
        finally:
            elapsed = time.monotonic() - started_batch
            # Count a shared provider wait once in deck totals, never once per
            # returned slide.
            if selected:
                preauthored_author_work[selected[0]] = (
                    preauthored_author_work.get(selected[0], 0.0) + elapsed
                )
            await record_local_stage(latency_ms=elapsed * 1000)

    async def _preauthor_missing_slides() -> None:
        if generation_job is None:
            return
        if author_planned_slide is not _DEFAULT_AUTHOR_PLANNED_SLIDE:
            return
        scan = await scan_preauthor_checkpoints(
            len(plan.contents),
            read_checkpoint=lambda index: get_checkpoint(
                generation_job.id,
                index,
                attempt_token=attempt_token,
                fencing_token=fencing_token,
            ),
            attempt_count=_checkpoint_provider_attempt_count,
        )
        initial, failures = scan.initial, scan.failures
        successful, carried_failures = scan.successful, scan.carried_failures
        preauthored_attempt_counts.update(scan.attempt_counts)

        nonlocal style_anchor
        schedule = AuthoredWaveSchedule(new_authored_batch_policy_state())
        pending = list(initial)

        # One ledger: a slide this call persisted stops being a failure, and a
        # slide already accepted never becomes one again.
        _record_initial = batch_recorder(
            successful=successful, failures=failures, publish=tail_stream
        )

        async def _author_anchor(
            _plan: Any, index: int, *, prior_failure_hint: str | None = None
        ) -> str:
            # G0: the anchor is authored through the ordinary batch path, so it
            # is checkpointed, admitted and counted exactly like any slide.
            prior = (
                {
                    index: AuthoredBatchFailure(
                        RuntimeError("authored_anchor_defective"), prior_failure_hint
                    )
                }
                if prior_failure_hint
                else carried_failures
            )
            try:
                outcome = await _run_author_batch(
                    (index,),
                    recovery=bool(prior_failure_hint),
                    prior_failures=prior,
                )
            except BaseException as error:
                schedule.advance(error, succeeded=_record_initial((index,), error))
                raise
            # ``_record_initial`` owns both containers: this call's HTML clears
            # the slide's failure entry, and a remake that fails after the
            # anchor's first call was accepted never re-opens one.
            schedule.advance(outcome, succeeded=_record_initial((index,), outcome))
            return preauthored_htmls.get(index, "")

        if pending:
            style_anchor, _anchor_html, anchor_index = await resolve_style_anchor(
                plan,
                author=_author_anchor,
                render=anchor_render_step(
                    render_gate,
                    deck_scope=render_deck_scope,
                    render_html=render_html_to_png,
                ),
                inspect_blocking=_anchor_blocking_defects,
                remaining_calls=2,
                eligible=pending,
            )
            pending = [index for index in pending if index != anchor_index]
        if tail_stream is not None:
            # The anchor's own result may still be replaced by its remake, so
            # nothing reaches the pool until this stage is over. Slides resumed
            # from an earlier attempt need no call and go straight through.
            tail_stream.resume()
            tail_stream.publish_many(successful)

        await run_initial_authoring(
            pending,
            schedule,
            run_batch=lambda indexes: _run_author_batch(
                indexes,
                recovery=False,
                prior_failures=carried_failures,
                style_anchor=style_anchor,
            ),
            record=_record_initial,
        )
        # A slide whose first call failed reaches the tail only once its one
        # recovery call has persisted HTML; a slide that fails both is never
        # published and fails the deck below.
        assert_authored_ledger_disjoint(
            successful, failures, stage="authored_recovery_planning"
        )
        await run_authored_recovery_pass(
            [
                index
                for index in [*initial, *scan.resumed_recovery]
                if index in failures and preauthored_attempt_counts.get(index, 0) < 2
            ],
            schedule=schedule,
            run_batch=lambda indexes: _run_author_batch(
                indexes,
                recovery=True,
                prior_failures=failures,
                style_anchor=style_anchor,
            ),
            successful=successful,
            failures=failures,
            publish=tail_stream,
        )
        authoring_cooldown_wait_seconds[0] = schedule.cooldown_wait_seconds

        # D7 at the author stage: a slide this pass could not author is the
        # tail's rescue to answer, not a reason to kill the deck. Deferred
        # slides are neither failed nor raised here; the pool claims them and
        # ``process_slide`` spends their one simplified attempt.
        deferred = defer_rescuable_author_failures(
            len(plan.contents),
            successful=successful,
            failures=failures,
            attempt_counts=preauthored_attempt_counts,
            profile=composition_profile,
            reference_mode=plan_reference_mode,
            source_reflow=source_reflow,
        )
        author_rescue_deferrals.update(deferred)
        if tail_stream is not None and deferred:
            tail_stream.publish_many(deferred)
        await report_unresolved_slides(
            len(plan.contents),
            successful=successful,
            failures={
                index: failure
                for index, failure in failures.items()
                if index not in deferred
            },
            attempt_counts=preauthored_attempt_counts,
            stage_failure=_author_stage_failure,
            generation_job=generation_job,
            attempt_token=attempt_token,
            fencing_token=fencing_token,
        )

    # G8: every authoring call of this deck shares one provider cache lane,
    # keyed by the deck-invariant prompt prefix rather than by any slide.
    deck_cache_key = deck_prompt_cache_key(getattr(plan, "design_system", None))
    # Every render of this deck shares one renderer-lease scope; tasks copy the
    # context at creation, so binding it around task creation covers their renders.
    render_deck_scope = str(getattr(generation_job, "presentation_id", None) or evidence_attempt_id)
    if tail_stream is None:
        with set_prompt_cache_key(deck_cache_key), set_render_deck_scope(render_deck_scope):
            await _preauthor_missing_slides()

            if generation_job is None:
                style_anchor = await resolve_direct_lane_anchor(
                    plan,
                    author_planned_slide=author_planned_slide,
                    authored_htmls=preauthored_htmls,
                    attempt_counts=preauthored_attempt_counts,
                )

    async def process_slide(
        index: int,
    ) -> tuple[
        int,
        str,
        bytes,
        float,
        float,
        float,
        float,
        Any | None,
        dict[str, Any] | None,
        AuthoredSlideSceneEnvelope,
        int,
    ]:
        author_work = preauthored_author_work.get(index, 0.0)
        illustration_work = 0.0
        render_work = 0.0
        quality_work = 0.0
        browser_metrics = None
        fit_report = None
        fitted_html = None
        quality_report = None
        quality_failure = None
        author_provider_attempt_count = preauthored_attempt_counts.get(index, 0)
        # The authoring call and the substitution step must see the identical
        # set: substitution resolves a placed id against this mapping, so an id
        # the author was never offered has nowhere to resolve.
        offered = (
            slide_source_images[index] if slide_source_images is not None else None
        )
        checkpoint = (
            await get_checkpoint(
                generation_job.id,
                index,
                attempt_token=attempt_token,
                fencing_token=fencing_token,
            )
            if generation_job is not None
            else None
        )
        prior_provider_attempt_count = _checkpoint_provider_attempt_count(checkpoint)
        author_provider_attempt_count = max(
            author_provider_attempt_count, prior_provider_attempt_count
        )
        durable_evidence = None
        if checkpoint is not None and checkpoint.render_status == "completed":
            try:
                durable_evidence = await read_valid_evidence_artifact(checkpoint)
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
                if durable_evidence.html == checkpoint_stage_html(checkpoint):
                    evidence_ledger.hydrate_slide(
                        attempt_id=evidence_attempt_id,
                        slide_index=index,
                        html=durable_evidence.html,
                        png=durable_evidence.png,
                        browser_metrics=durable_evidence.metrics,
                        checkpoint_revision=durable_evidence.revision,
                    )

        try:
            # E4 (docs/generation-programme-master-design-20260904.md section
            # 8): fired A2 normalisation rules from this slide's own
            # ``validate_authored_slide_html`` call(s), carried into this
            # slide's quality receipt below.
            normalization_receipt: list[dict] = []
            html = (
                checkpoint.author_html
                if checkpoint is not None and checkpoint.author_html
                else preauthored_htmls.get(index)
            )
            if (
                html
                and checkpoint is not None
                and checkpoint.author_status == "completed"
            ):
                # The old checkpoint schema has no slide-scoped provider-call
                # ordinal. Never risk a third billable call after recovery.
                if index not in preauthored_attempt_counts:
                    author_provider_attempt_count = 2
            author_completed = bool(
                html
                and (
                    index in preauthored_htmls
                    or (
                        checkpoint is not None
                        and checkpoint.author_status == "completed"
                    )
                )
            )
            # A spent author budget the rescue may still answer; the ordinary
            # authoring call below is skipped and its third, simplified attempt
            # runs in its place.
            spent_budget: DeferredAuthorBudgetExhausted | None = None
            if not author_completed:
                if author_provider_attempt_count >= 2:
                    spent_budget = deferred_author_budget_error(
                        index,
                        checkpoint=checkpoint,
                        deferrals=author_rescue_deferrals,
                        provider_attempt_count=author_provider_attempt_count,
                        profile=composition_profile,
                        reference_mode=plan_reference_mode,
                        source_reflow=source_reflow,
                    )
                if author_provider_attempt_count >= 2 and spent_budget is None:
                    prior_code = (
                        str(checkpoint.error.get("code") or "")
                        if checkpoint is not None
                        and isinstance(checkpoint.error, Mapping)
                        else ""
                    )
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code=(
                            prior_code
                            if prior_code.startswith("public_provider_")
                            else "authored_provider_attempt_budget_exhausted"
                        ),
                        retryable=False,
                        provider_attempt_count=author_provider_attempt_count,
                    )
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
                        fencing_token=fencing_token,
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
                            if "attempt_observer" in inspect.signature(
                                author_planned_slide
                            ).parameters:
                                def observe_author_attempt(ordinal: int) -> None:
                                    nonlocal author_provider_attempt_count
                                    author_provider_attempt_count = max(
                                        author_provider_attempt_count, int(ordinal)
                                    )

                                author_kwargs["attempt_observer"] = (
                                    observe_author_attempt
                                )
                            if "provider_attempt_offset" in inspect.signature(
                                author_planned_slide
                            ).parameters:
                                author_kwargs["provider_attempt_offset"] = (
                                    prior_provider_attempt_count
                                )
                            if style_anchor is not None and accepts_style_anchor(
                                author_planned_slide
                            ):
                                author_kwargs["style_anchor"] = style_anchor
                            carried_hint = author_correction_from_stored_failure(
                                getattr(checkpoint, "error", None)
                            )
                            if carried_hint and "prior_failure_hint" in inspect.signature(
                                author_planned_slide
                            ).parameters:
                                author_kwargs["prior_failure_hint"] = carried_hint
                            if (
                                generation_job is not None
                                and "attempt_reserver" in inspect.signature(
                                    author_planned_slide
                                ).parameters
                            ):
                                async def reserve_author_attempt(ordinal: int) -> None:
                                    await reserve_provider_attempt(
                                        job_id=generation_job.id,
                                        slide_index=index,
                                        attempt_token=attempt_token,
                                        ordinal=ordinal,
                                        fencing_token=fencing_token,
                                    )

                                author_kwargs["attempt_reserver"] = (
                                    reserve_author_attempt
                                )
                            try:
                                if spent_budget is not None:
                                    # Both ordinary calls are already spent, so
                                    # asking for a third here would only hit the
                                    # budget guard. Go straight to the rescue.
                                    raise spent_budget
                                html = await author_planned_slide(
                                    plan, index, **author_kwargs
                                )
                            except Exception as author_error:
                                # D7 at the author stage: one simplified attempt,
                                # then a flagged placeholder. ``None`` keeps
                                # today's fail-closed raise for this deck.
                                rescue = await rescue_author_stage(
                                    plan,
                                    index,
                                    author_error,
                                    author_kwargs=author_kwargs,
                                    profile=composition_profile,
                                    reference_mode=plan_reference_mode,
                                    source_reflow=source_reflow,
                                )
                                if rescue is None:
                                    raise
                                html = rescue.html
                                author_provider_attempt_count = max(
                                    author_provider_attempt_count,
                                    rescue.provider_attempt_count,
                                )
                                if rescue.flagged_entry is not None:
                                    author_flagged_slides[index] = rescue.flagged_entry
                except RendererPolicyError as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_slide_author_failed",
                        cause=exc,
                        retryable=False,
                    ) from exc
                except Exception as exc:
                    raise _author_stage_failure(
                        index,
                        exc,
                        provider_attempt_count=author_provider_attempt_count,
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
                        normalization_receipt=normalization_receipt,
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
                        fencing_token=fencing_token,
                        html=html,
                    )
            else:
                try:
                    html = validate_authored_slide_html(
                        html,
                        allow_illustration_slots=True,
                        normalization_receipt=normalization_receipt,
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
                infographic=(
                    infographic_briefs[index] if infographic_briefs else None
                ),
                infographic_assets=infographic_assets,
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
                    stamped_source = stamp_source_blocks(
                        illustrated_html, source_block_bindings[index]
                    )
                    illustrated_html = stamped_source.html
                    if stamped_source.deviations:
                        # Layout compromises, not lost text: the source stayed
                        # verbatim but no element owns it exclusively or the
                        # template placed one block's items out of source order.
                        LOGGER.info(
                            "source binding deviations slide=%d %s",
                            index,
                            [item.as_dict() for item in stamped_source.deviations],
                        )
                except BrowserSourceReadbackError as exc:
                    # One slide dropped source text the model was told to copy.
                    # The other slides are authored and durable, so this is the
                    # slide's failure, not the deck's: mark its author stage
                    # failed with the reason and let the retry re-author that
                    # one slide with the reason as its correction.
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code=exc.code,
                        cause=exc,
                        retryable=True,
                        message=exc.detail,
                    ) from exc
            if source_image_block_bindings is not None and index < len(source_image_block_bindings):
                try:
                    from services.source_content.browser_readback import stamp_source_image_block_ids
                    illustrated_html = stamp_source_image_block_ids(
                        illustrated_html, source_image_block_bindings[index]
                    )
                except BrowserSourceReadbackError as exc:
                    # A picture the slide was offered and did not place is the
                    # same shape of failure as dropped text: one slide's, and
                    # fixable by re-authoring that slide.
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code=exc.code,
                        cause=exc,
                        retryable=True,
                        message=exc.detail,
                    ) from exc

            # Clamp explicit CSS below the capacity brief's or the fragmentation
            # gate's floor before the first render, so one floor governs every stage.
            if capacity_plan is not None and plan_reference_mode != EXACT_RECONSTRUCTION:
                illustrated_html = enforce_authored_font_floor(
                    illustrated_html,
                    minimum_px=max(capacity_plan.at(index).min_font_px, measured_font_floor_px()),
                )

            # Canonicalize semantic ids before the first render.  The renderer
            # instruments the same ids internally, so doing this here keeps the
            # source and render digests identical for the subsequent DOM gate;
            # the gate can then reuse the metrics captured with this PNG instead
            # of launching a second browser probe in shadow semantic mode.
            if plan_reference_mode != EXACT_RECONSTRUCTION:
                illustrated_html = canonicalize_authored_roles(
                    illustrated_html,
                    role=plan_slide_role(plan, index),
                    demote=True,
                    receipt=normalization_receipt,
                )
            else:
                illustrated_html = ensure_authored_qids(illustrated_html)

            # Stage 4 best-of-N. Off unless AUTHORED_QUALITY_LOOP_ENABLED is
            # set; when on, the figure's density/emphasis classes are varied and
            # re-rendered (no provider call) and the measured winner replaces
            # this document *before* the envelope below is sealed.
            illustrated_html, quality_loop_receipt = await apply_slide_quality_loop(
                illustrated_html,
                slide_index=index,
                checkpoint=checkpoint,
                reference_mode=plan_reference_mode,
                composition_profile=composition_profile,
                slide_content=plan.contents[index] if index < len(plan.contents) else "",
                infographic=infographic_briefs[index] if infographic_briefs else None,
            )

            scene_envelope: AuthoredSlideSceneEnvelope
            if generation_job is not None:
                try:
                    scene_state = read_scene_checkpoint_state(
                        generation_job.id,
                        index,
                    )
                    if scene_state is not None:
                        if scene_state.envelope.html_projection != illustrated_html:
                            # The slide was authored again -- a source-binding
                            # rejection is a slide-scoped failure now, so its
                            # HTML legitimately changes between attempts. The
                            # sealed envelope has to follow the document that
                            # will actually be rendered, and persisting it
                            # keeps the slide's repair budget and failure
                            # fingerprint, so this cannot buy another repair.
                            LOGGER.info(
                                "resealing scene envelope after re-author slide=%d",
                                index,
                            )
                            scene_envelope = seal_slide_scene_envelope(
                                illustrated_html,
                                slide_index=index,
                            )
                            persist_scene_envelope(generation_job.id, scene_envelope)
                        else:
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

            png = await read_valid_render_artifact(checkpoint) if checkpoint else None
            html_changed = bool(
                checkpoint is not None
                and checkpoint_stage_html(checkpoint)
                and checkpoint_stage_html(checkpoint) != illustrated_html
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
                        fencing_token=fencing_token,
                    )
                render_started = time.monotonic()
                try:
                    # Chromium is independently bounded.  The author gate is
                    # released before this point, allowing the next queued
                    # slide to call the provider while this exact HTML is
                    # rendered, checked, and checkpointed.
                    async with render_gate:
                        with provider_usage_scope(
                            job_id=(generation_job.id if generation_job is not None else None),
                            operation="authored_generation",
                            stage="render",
                            attempt_number=(generation_job.attempt_number if generation_job else 0),
                            slide_index=index,
                        ):
                            if is_authored_in_render_fit_enabled():
                                capture = await render_html_to_png_with_metrics(illustrated_html, fit=True)
                                fitted_html = getattr(capture, "fitted_html", None)
                                illustrated_html = fitted_html if fitted_html is not None else illustrated_html
                                png, browser_metrics = capture.png, capture.browser_metrics
                                fit_report = getattr(capture, "fit_report", None)
                            else:
                                png = await render_html_to_png(illustrated_html)
                                browser_metrics = get_cached_browser_metrics(illustrated_html)
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
            scene_envelope = reseal_scene_envelope_after_fit(
                fitted_html=fitted_html, scene_envelope=scene_envelope, slide_index=index,
                generation_job_id=(generation_job.id if generation_job is not None else None),
            )
            illustrated_html = scene_envelope.html_projection
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

            quality_started = time.monotonic()
            try:
                quality_report, quality_failure = _evaluate_slide_quality(
                    index=index,
                    html=illustrated_html,
                    browser_metrics=browser_metrics,
                    capacity_plan=capacity_plan,
                    composition_profile=composition_profile,
                    reference_mode=plan_reference_mode,
                    fit_report=fit_report,
                )
            finally:
                quality_work = time.monotonic() - quality_started

            # Quality is evaluated against the exact capture before advancing
            # the durable render pointer. Different slides can therefore be in
            # author, Chromium, quality, and checkpoint stages concurrently.
            if (
                generation_job is not None
                and (
                    checkpoint.render_status != "completed"
                    or checkpoint_stage_html(checkpoint) != illustrated_html
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
                    fencing_token=fencing_token,
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
                            fencing_token=fencing_token,
                        )
                        await _complete_stage_after_external_success(
                            job_id=generation_job.id,
                            slide_index=index,
                            stage="persist",
                            attempt_token=attempt_token,
                            fencing_token=fencing_token,
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
                # Called for the ledger write; the returned record is read
                # back from the ledger by the stages that need it.
                evidence_ledger.hydrate_slide(
                    attempt_id=evidence_attempt_id,
                    slide_index=index,
                    html=illustrated_html,
                    png=png,
                    browser_metrics=browser_metrics,
                    checkpoint_revision=durable_evidence.revision,
                )
            else:
                evidence_ledger.record_slide(
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
                quality_work,
                quality_report,
                quality_failure,
                scene_envelope,
                author_provider_attempt_count,
                quality_loop_receipt,
                normalization_receipt,
                fit_report,
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
                    fencing_token=fencing_token,
                )
            raise

    with set_prompt_cache_key(deck_cache_key), set_render_deck_scope(render_deck_scope):
        completed_results = (
            await run_slide_worker_pool(
                process_slide,
                slide_count=len(plan.contents),
                worker_count=_AUTHORED_SLIDE_WORKERS,
            )
            if tail_stream is None
            else await run_streamed_slide_tail(
                process_slide,
                author_pass=_preauthor_missing_slides,
                coordinator=tail_stream,
                slide_count=len(plan.contents),
                worker_count=_AUTHORED_SLIDE_WORKERS,
            )
        )
    htmls = [result[1] for result in completed_results]
    pngs = [result[2] for result in completed_results]
    quality_gate_reports = [
        result[7] for result in completed_results if result[7] is not None
    ]
    quality_gate_failures = [
        result[8] for result in completed_results if result[8] is not None
    ]
    quality_gate_failed_indices = [
        int(failure["slide_index"]) for failure in quality_gate_failures
    ]
    scene_envelope = seal_deck_scene_envelope(
        result[9] for result in completed_results
    )
    slide_fit_reports = {int(r[0]): r[13] for r in completed_results if isinstance(r[13], Mapping)}
    return AuthoredGenerationPipelineResult(
        deck=AuthoredDeckResult(htmls=htmls, design_system=plan.design_system),
        pngs=pngs,
        elapsed=time.monotonic() - started,
        author_work=sum(result[3] for result in completed_results),
        illustration_work=sum(result[4] for result in completed_results),
        render_work=sum(result[5] for result in completed_results),
        quality_work=sum(result[6] for result in completed_results),
        quality_gate_reports=tuple(quality_gate_reports),
        quality_gate_failed_indices=tuple(quality_gate_failed_indices),
        quality_gate_failures=tuple(quality_gate_failures),
        evidence_ledger=evidence_ledger,
        evidence_attempt_id=evidence_attempt_id,
        scene_envelope=scene_envelope,
        author_provider_attempt_counts=tuple(
            int(result[10]) for result in completed_results
        ),
        author_flagged_slides=tuple(
            author_flagged_slides[index] for index in sorted(author_flagged_slides)
        ),
        cooldown_wait_seconds=authoring_cooldown_wait_seconds[0],
        # Slides are authored in parallel child tasks, so the quality loop's
        # per-slide choice has no ambient way back to the measurement stage.
        # It travels with the slide's own result instead. E4: the same result
        # slot carries this slide's fired A2 normalisation rules (possibly
        # empty), merged in under "normalizations" so a slide that needed
        # normalisation is still recorded even when the quality loop did not
        # run for it.
        slide_quality_receipts={
            int(result[0]): {**dict(result[11]), "normalizations": list(result[12])}
            for result in completed_results
            if (isinstance(result[11], dict) and result[11]) or result[12]
        },
        slide_fit_reports=slide_fit_reports,
    )
