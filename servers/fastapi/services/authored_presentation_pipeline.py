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
from services.authored_checkpoint_artifacts import (
    read_valid_evidence_artifact,
    read_valid_render_artifact,
)
from services.authored_presentation_artifacts import (
    _complete_stage_after_external_success,
)
from services.authored_illustration_stage import illustrate_slide
from services.authored_infographic_assets import (
    build_infographic_asset_context,
    deck_has_picture_source,
)
from services.authored_quality_loop_stage import apply_slide_quality_loop
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
from services.authored_generation_performance import authored_provider_concurrency
from services.authored_slide_worker_pool import run_slide_worker_pool
from services.authored_batch_policy import (
    advance_authored_batch_policy,
    new_authored_batch_policy_state,
    plan_authored_batch_wave,
    plan_authored_recovery_batches,
)
from services.source_images.models import SourceImageCandidate
from services.source_content.browser_readback import BrowserSourceReadbackError
from services.source_content.header_pages import header_absent_pages
from services.source_content.text_stamping import stamp_source_blocks
from utils.asset_directory_utils import get_images_directory
from utils.authored_illustrations import (
    enforce_strict_source_image_size_floor,
    validate_bound_source_images,
    validate_visible_source_images,
)
from utils.authored_font_policy import enforce_authored_font_floor
from utils.authored_dom_quality import ensure_authored_qids, inspect_authored_dom
from utils.llm_calls.author_deck import (
    author_correction_from_stored_failure,
    AuthoredBatchFailure,
    AuthoredProviderBudgetExhausted,
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
from utils.llm_provider import get_llm_provider
from utils.slide_capture import (
    _placeholder_png,
    ensure_browser_metrics,
    get_cached_browser_metrics,
    render_html_to_png,
)

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


def _author_failure_retryable(error: BaseException) -> bool:
    """Keep deterministic slide-budget exhaustion out of operation retries."""

    if isinstance(error, AuthoredProviderBudgetExhausted):
        return False
    if _terminal_provider_code(error) is not None:
        return False
    explicit_retryable = getattr(error, "retryable", None)
    if isinstance(explicit_retryable, bool):
        return explicit_retryable
    return is_retryable_exception(error)


def _terminal_provider_code(error: BaseException) -> str | None:
    """Recover a non-retryable public admission cause through safe wrappers."""

    current: BaseException | None = error
    visited: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in visited:
            break
        visited.add(id(current))
        code = getattr(current, "code", None)
        if (
            isinstance(code, str)
            and code.startswith("public_provider_")
            and getattr(current, "retryable", None) is False
        ):
            return code
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return None


def _author_stage_failure(
    slide_index: int,
    error: BaseException,
    *,
    provider_attempt_count: int | None = None,
) -> AuthoredStageError:
    """Build the exact author-stage envelope consumed by operation retry policy."""

    code = _terminal_provider_code(error) or "authored_slide_author_failed"
    return AuthoredStageError(
        slide_index=slide_index,
        stage="author",
        code=code,
        cause=error,
        retryable=_author_failure_retryable(error),
        provider_attempt_count=provider_attempt_count,
    )


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
        initial: list[int] = []
        resumed_recovery: list[int] = []
        failures: dict[int, AuthoredBatchFailure] = {}
        successful: set[int] = set()
        # Why an earlier attempt of this operation rejected a slide. A retry
        # exists to redo exactly those slides, and re-sending the prompt that
        # already failed reproduces the same rejection, so the reason travels
        # with the slide as its correction.
        carried_failures: dict[int, AuthoredBatchFailure] = {}
        for index in range(len(plan.contents)):
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
            if (
                checkpoint is not None
                and checkpoint.author_status == "completed"
                and checkpoint.author_html
            ):
                successful.add(index)
                continue
            prior = _checkpoint_provider_attempt_count(checkpoint)
            preauthored_attempt_counts[index] = prior
            carried_hint = author_correction_from_stored_failure(
                getattr(checkpoint, "error", None)
            )
            if carried_hint:
                carried_failures[index] = AuthoredBatchFailure(
                    RuntimeError("authored_prior_attempt_failed"), carried_hint
                )
            if prior >= 2:
                prior_error = (
                    checkpoint.error
                    if checkpoint is not None
                    and isinstance(checkpoint.error, Mapping)
                    else None
                )
                prior_code = str((prior_error or {}).get("code") or "")
                error = AuthoredStageError(
                    slide_index=index,
                    stage="author",
                    code=(
                        prior_code
                        if prior_code.startswith("public_provider_")
                        else "authored_provider_attempt_budget_exhausted"
                    ),
                    retryable=False,
                    provider_attempt_count=prior,
                    # Without this the retry records only that the slide ran out
                    # of calls, replacing the first attempt's record of why it
                    # failed -- the only thing that says where to look.
                    prior_failure=prior_error,
                )
                failures[index] = AuthoredBatchFailure(error, str(error))
            elif prior == 1:
                resumed_recovery.append(index)
                failures[index] = carried_failures.get(
                    index,
                    AuthoredBatchFailure(
                        RuntimeError("authored_prior_attempt_failed"),
                        "Regenerate this slide while obeying every original contract.",
                    ),
                )
            else:
                initial.append(index)

        policy = new_authored_batch_policy_state()
        pending = list(initial)
        while pending:
            wave = plan_authored_batch_wave(pending, policy)
            outcomes = await asyncio.gather(
                *(
                    _run_author_batch(
                        batch.slide_indices,
                        recovery=False,
                        prior_failures=carried_failures,
                    )
                    for batch in wave.batches
                ),
                return_exceptions=True,
            )
            consumed: set[int] = set()
            for batch, outcome in zip(wave.batches, outcomes, strict=True):
                consumed.update(batch.slide_indices)
                if isinstance(outcome, BaseException):
                    policy = advance_authored_batch_policy(
                        policy, succeeded=False, error=outcome
                    )
                    failures.update(
                        {
                            index: AuthoredBatchFailure(outcome, str(outcome))
                            for index in batch.slide_indices
                        }
                    )
                    continue
                policy = advance_authored_batch_policy(
                    policy, succeeded=bool(outcome.htmls)
                )
                successful.update(outcome.htmls)
                failures.update(outcome.failures)
            pending = [index for index in pending if index not in consumed]

        failed_for_recovery = [
            index
            for index in [*initial, *resumed_recovery]
            if index in failures and preauthored_attempt_counts.get(index, 0) < 2
        ]
        recovery_batches = plan_authored_recovery_batches(
            failed_for_recovery,
            successful_slide_indices=successful,
            profile=policy.profile,
        )
        for offset in range(0, len(recovery_batches), policy.concurrency):
            window = recovery_batches[offset : offset + policy.concurrency]
            outcomes = await asyncio.gather(
                *(
                    _run_author_batch(
                        batch.slide_indices,
                        recovery=True,
                        prior_failures=failures,
                    )
                    for batch in window
                ),
                return_exceptions=True,
            )
            for batch, outcome in zip(window, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    policy = advance_authored_batch_policy(
                        policy, succeeded=False, error=outcome
                    )
                    for index in batch.slide_indices:
                        failures[index] = AuthoredBatchFailure(outcome, str(outcome))
                    continue
                policy = advance_authored_batch_policy(
                    policy, succeeded=bool(outcome.htmls)
                )
                successful.update(outcome.htmls)
                for index in outcome.htmls:
                    failures.pop(index, None)
                failures.update(outcome.failures)

        unresolved = [
            index for index in range(len(plan.contents))
            if index not in successful and index in failures
        ]
        if unresolved:
            for index in unresolved:
                error = failures[index].error
                stage_error = (
                    error
                    if isinstance(error, AuthoredStageError)
                    else _author_stage_failure(
                        index,
                        error,
                        provider_attempt_count=preauthored_attempt_counts.get(index),
                    )
                )
                if generation_job is not None:
                    await fail_stage(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="author",
                        attempt_token=attempt_token,
                        fencing_token=fencing_token,
                        error=stage_error,
                        request_id=generation_job.request_id,
                    )
            first = unresolved[0]
            error = failures[first].error
            if isinstance(error, AuthoredStageError):
                raise error
            raise _author_stage_failure(
                first,
                error,
                provider_attempt_count=preauthored_attempt_counts.get(first),
            ) from error

    await _preauthor_missing_slides()

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
            if not author_completed:
                if author_provider_attempt_count >= 2:
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

            # Provider output sometimes declares a smaller font even though the
            # frozen capacity brief forbids it. Clamp that explicit CSS before
            # the first Chromium render so the same 15px floor governs authoring,
            # preview, quality admission, and export instead of failing only at
            # the terminal post-render gate.
            if capacity_plan is not None and plan_reference_mode != EXACT_RECONSTRUCTION:
                illustrated_html = enforce_authored_font_floor(
                    illustrated_html,
                    minimum_px=capacity_plan.at(index).min_font_px,
                )

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

            quality_started = time.monotonic()
            try:
                quality_report, quality_failure = _evaluate_slide_quality(
                    index=index,
                    html=illustrated_html,
                    browser_metrics=browser_metrics,
                    capacity_plan=capacity_plan,
                    composition_profile=composition_profile,
                    reference_mode=plan_reference_mode,
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

    completed_results = await run_slide_worker_pool(
        process_slide,
        slide_count=len(plan.contents),
        worker_count=_AUTHORED_SLIDE_WORKERS,
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
    )
