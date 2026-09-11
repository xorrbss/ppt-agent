from __future__ import annotations

from services.authored_presentation_pipeline_deps import *
from services import authored_presentation_author_batch as author_batch_impl
from services import authored_presentation_pipeline_helpers as _pipeline_helpers
from services.authored_local_fit_policy import local_fit_applies

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


def _resolve_authored_render_workers(
    author_workers: int, environment: Mapping[str, str] = os.environ,
) -> int:
    """Resolve the authored render queue against the managed Chromium ceiling."""
    raw = environment.get("AUTHORED_SLIDE_RENDER_CONCURRENCY")
    if raw is None:
        raw = environment.get("PUBLIC_RENDER_MAX_CONCURRENCY", str(author_workers))
    return max(1, int(raw))


_AUTHORED_RENDER_WORKERS = _resolve_authored_render_workers(_AUTHORED_AUTHOR_WORKERS)
_AUTHORED_SLIDE_WORKERS = _AUTHORED_AUTHOR_WORKERS + _AUTHORED_RENDER_WORKERS
_DEFAULT_AUTHOR_PLANNED_SLIDE = author_planned_slide


def _call_pipeline_helper(name: str, *args, **kwargs):
    _pipeline_helpers.bind_runtime(sys.modules[__name__])
    return getattr(_pipeline_helpers, name)(*args, **kwargs)


def _evaluate_slide_quality(*args, **kwargs):
    return _call_pipeline_helper("_evaluate_slide_quality", *args, **kwargs)


def _local_fit_quality_flag(*args, **kwargs):
    return _call_pipeline_helper("_local_fit_quality_flag", *args, **kwargs)


def _local_fit_unresolved_flag(*args, **kwargs):
    return _call_pipeline_helper("_local_fit_unresolved_flag", *args, **kwargs)


def _reusable_source_repair_scene(
    *, source_reflow: bool, durable_evidence: Any,
    original_html: str, scene_html: str, slide_index: int,
) -> bool:
    """Reuse an admitted source repair only through its validated durable tuple.

    The caller has already obtained the checkpoint under attempt fencing and
    verified its immutable artifact revision. Admission additionally binds the
    reconstructed original, exact repaired scene, PNG, metrics, and slide index.
    """
    if not source_reflow or durable_evidence is None or durable_evidence.html != scene_html:
        return False
    return validate_structural_checkpoint(
        html=scene_html,
        png=durable_evidence.png,
        metrics=durable_evidence.metrics,
        expected_slide_index=slide_index,
        baseline_html_digest=html_digest(original_html),
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
    local_fit_policy_revision: str | None = None,
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
        source_block_bindings is not None
        or source_image_block_bindings is not None
        or source_images_locked_to_source_slide
    )
    local_fit_enabled = local_fit_applies(
        reference_mode=plan_reference_mode, source_reflow=source_reflow
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
    preauthored_normalization_receipts: dict[int, list[dict]] = {}
    author_rescue_deferrals: dict[int, DeferredAuthorBudgetExhausted] = {}
    # G0: the deck's style anchor, once one slide has been authored and gated.
    # ``None`` keeps every later prompt byte-identical to today's.
    style_anchor: Any | None = None
    # F3: cooldown-wait seconds; the nested stages update this one-element list.
    authoring_cooldown_wait_seconds = [0.0]
    schedule = AuthoredWaveSchedule(new_authored_batch_policy_state())
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
    batch_context = {
        "generation_job": generation_job, "source_block_bindings": source_block_bindings,
        "preauthored_htmls": preauthored_htmls, "attempt_token": attempt_token,
        "fencing_token": fencing_token, "preauthored_attempt_counts": preauthored_attempt_counts,
        "slide_source_images": slide_source_images, "plan": plan,
        "require_all_source_images": require_all_source_images,
        "preauthored_author_work": preauthored_author_work,
        "preauthored_normalization_receipts": preauthored_normalization_receipts,
    }
    async def _persist_batch_success(index: int, html: str) -> str:
        return await author_batch_impl.persist_batch_success(
            index, html, context=batch_context, runtime=sys.modules[__name__],
        )
    async def _run_author_batch(indexes, *, recovery, prior_failures, style_anchor=None):
        return await author_batch_impl.run_author_batch(
            indexes, recovery=recovery, prior_failures=prior_failures, style_anchor=style_anchor,
            context=batch_context, runtime=sys.modules[__name__],
            persist_batch_success=_persist_batch_success,
        )
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
        dict[str, Any],
        list[dict],
        Mapping[str, Any] | None,
    ]:
        author_work = preauthored_author_work.get(index, 0.0)
        illustration_work = 0.0
        render_work = 0.0
        quality_work = 0.0
        browser_metrics = None
        quality_report = None
        quality_failure = None
        # Local-fit slides bypass the optional best-of-N quality loop. Keep the
        # result contract total on that path: an empty receipt means the loop
        # did not run, just as apply_slide_quality_loop documents.
        quality_loop_receipt: dict[str, Any] = {}
        local_fit_receipt: Mapping[str, Any] | None = None
        local_fit_source_html: str | None = None
        local_fit_policy_value: Mapping[str, Any] | None = None
        local_fit_checkpoint_reused = False
        accepted_fit = None
        rescue = None
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
            normalization_receipt = list(preauthored_normalization_receipts.get(index, ()))
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
                                    raise spent_budget
                                html = await author_planned_slide(
                                    plan, index, **author_kwargs
                                )
                            except Exception as author_error:
                                rescue = await rescue_author_stage(
                                    plan, index, author_error,
                                    author_kwargs=author_kwargs,
                                    profile=composition_profile,
                                    reference_mode=plan_reference_mode,
                                    source_reflow=source_reflow, schedule=schedule,
                                )
                                authoring_cooldown_wait_seconds[0] = schedule.cooldown_wait_seconds
                                if rescue is None:
                                    raise
                                html = rescue.html
                                author_provider_attempt_count = max(
                                    author_provider_attempt_count, rescue.provider_attempt_count,
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
                    if (
                        source_block_bindings is not None
                        and index < len(source_block_bindings)
                    ):
                        html = stamp_source_blocks(
                            html, source_block_bindings[index]
                        ).html
                except RendererPolicyError as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_slide_author_failed",
                        cause=exc,
                        retryable=False,
                    ) from exc
                except BrowserSourceReadbackError as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code=exc.code,
                        cause=exc,
                        retryable=author_provider_attempt_count < 3,
                        message=exc.detail,
                        provider_attempt_count=author_provider_attempt_count,
                        prior_failure=getattr(rescue, "prior_failure", None),
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
            # Normalize explicit CSS before Chromium establishes the baseline.
            # Local fit uses its physical output floor; the shared capacity path
            # keeps its frozen per-slide floor. Exact reconstruction bypasses
            # both transformations because source fidelity is its contract.
            if local_fit_enabled:
                local_conversion = pt_per_css_px(width_pt=960, height_pt=540)
                illustrated_html = enforce_authored_font_floor(
                    illustrated_html,
                    minimum_px=required_font_px(
                        FONT_FLOOR_PT,
                        1,
                        local_conversion,
                    ),
                    minimum_pt=FONT_FLOOR_PT,
                )
            elif (
                capacity_plan is not None
                and plan_reference_mode != EXACT_RECONSTRUCTION
            ):
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
            if not local_fit_enabled:
                illustrated_html, quality_loop_receipt = await apply_slide_quality_loop(
                    illustrated_html,
                    slide_index=index,
                    checkpoint=checkpoint,
                    reference_mode=plan_reference_mode,
                    composition_profile=composition_profile,
                    slide_content=(
                        plan.contents[index]
                        if index < len(plan.contents)
                        else ""
                    ),
                    infographic=(
                        infographic_briefs[index]
                        if infographic_briefs
                        else None
                    ),
                )
            # Capture the authored baseline before any local fit. A resumed
            # fitted checkpoint must not become a new baseline and acquire a
            # second ten-percent shrink allowance.
            if local_fit_enabled:
                local_fit_source_html = illustrated_html
                original_digest = html_digest(local_fit_source_html)
                local_fit_policy_value = local_fit_policy(
                    author_revision=original_digest,
                    original_html_digest=original_digest,
                    export_width_pt=960,
                    export_height_pt=540,
                    slide_index=index,
                    policy_revision=(local_fit_policy_revision or POLICY_REVISION),
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
                            reusable_source_repair = _reusable_source_repair_scene(
                                source_reflow=source_reflow,
                                durable_evidence=durable_evidence,
                                original_html=illustrated_html,
                                scene_html=scene_state.envelope.html_projection,
                                slide_index=index,
                            )
                            reusable_fit = (
                                reusable_checkpoint_receipt(
                                    durable_evidence.metrics,
                                    original_html=illustrated_html,
                                    final_html=scene_state.envelope.html_projection,
                                    slide_index=index,
                                    policy=local_fit_policy_value,
                                    final_png=durable_evidence.png,
                                )
                                if durable_evidence is not None
                                and local_fit_policy_value is not None
                                else None
                            )
                            if reusable_source_repair:
                                # Source reflow intentionally disables local fit.
                                # Its admission receipt independently proves the
                                # exact repaired scene/render checkpoint, so it
                                # must survive reconstruction of the author input.
                                scene_envelope = scene_state.envelope
                            elif reusable_fit is not None:
                                # The checkpoint contains an independently
                                # reconstructed acceptance for this exact
                                # original baseline and final scene.
                                scene_envelope = scene_state.envelope
                                local_fit_receipt = dict(reusable_fit)
                                local_fit_receipt["telemetry"] = {
                                    **dict(
                                        local_fit_receipt.get("telemetry") or {}
                                    ),
                                    "evidence_cache_hit": True,
                                }
                                local_fit_checkpoint_reused = True
                            else:
                                # The slide was authored again -- a source-binding
                                # rejection is a slide-scoped failure now, so its
                                # HTML legitimately changes between attempts.
                                LOGGER.info(
                                    "resealing scene envelope after re-author slide=%d",
                                    index,
                                )
                                scene_envelope = seal_slide_scene_envelope(
                                    illustrated_html,
                                    slide_index=index,
                                )
                                persist_scene_envelope(
                                    generation_job.id, scene_envelope
                                )
                        else:
                            scene_envelope = scene_state.envelope
                            reusable_fit = (
                                reusable_checkpoint_receipt(
                                    durable_evidence.metrics,
                                    original_html=illustrated_html,
                                    final_html=scene_envelope.html_projection,
                                    slide_index=index,
                                    policy=local_fit_policy_value,
                                    final_png=durable_evidence.png,
                                )
                                if durable_evidence is not None
                                and local_fit_policy_value is not None
                                else None
                            )
                            if reusable_fit is not None:
                                local_fit_receipt = dict(reusable_fit)
                                local_fit_receipt["telemetry"] = {
                                    **dict(
                                        local_fit_receipt.get("telemetry") or {}
                                    ),
                                    "evidence_cache_hit": True,
                                }
                                local_fit_checkpoint_reused = True
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
            if (
                local_fit_checkpoint_reused
                and isinstance(local_fit_receipt, Mapping)
                and receipt_is_preserved_original(local_fit_receipt)
            ):
                author_flagged_slides[index] = _local_fit_unresolved_flag(
                    index, local_fit_receipt.get("unresolvedReasons") or ()
                )
            png = await read_valid_render_artifact(checkpoint) if checkpoint else None
            html_changed = bool(
                checkpoint is not None
                and checkpoint_stage_html(checkpoint)
                and checkpoint_stage_html(checkpoint) != illustrated_html
            )
            if local_fit_policy_value is not None and not local_fit_checkpoint_reused:
                # A legacy PNG/checkpoint has no baseline-bound acceptance and
                # cannot be promoted to the active policy.
                png = None
                browser_metrics = None
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
                remaining_operation_seconds = (
                    remaining_authored_local_fit_seconds(cap=120)
                    if local_fit_policy_value is not None
                    else 120.0
                )
                if (
                    local_fit_policy_value is not None
                    and remaining_operation_seconds <= 0
                ):
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="render",
                        code="authored_local_fit_deadline_exceeded",
                        retryable=False,
                    )
                local_budget = (
                    slide_budget(60, remaining_operation_seconds)
                    if local_fit_policy_value is not None
                    else None
                )
                slide_deadline = (
                    render_started + local_budget["total_seconds"]
                    if local_budget is not None
                    else None
                )
                fitted_capture = None
                try:
                    # Chromium is independently bounded.  The author gate is
                    # released before this point, allowing the next queued
                    # slide to call the provider while this exact HTML is
                    # rendered, checked, and checkpointed.
                    first_slot_acquired = False
                    if local_budget is not None:
                        await asyncio.wait_for(
                            render_gate.acquire(),
                            timeout=local_budget["first_seconds"],
                        )
                    else:
                        await render_gate.acquire()
                    first_slot_acquired = True
                    try:
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
                            if local_fit_policy_value is None:
                                png = await render_html_to_png(illustrated_html)
                                browser_metrics = get_cached_browser_metrics(
                                    illustrated_html
                                )
                            else:
                                first_remaining = max(
                                    0.001,
                                    (
                                        render_started
                                        + local_budget["first_seconds"]
                                        - time.monotonic()
                                    ),
                                )
                                async with asyncio.timeout(first_remaining):
                                    fitted_capture = await render_html_to_png_with_metrics(
                                        local_fit_source_html or illustrated_html,
                                        timeout=max(
                                            1,
                                            min(60, int(first_remaining)),
                                        ),
                                        fit=True,
                                        fit_policy=local_fit_policy_value,
                                        deck_scope=render_deck_scope,
                                    )
                                png = fitted_capture.png
                                browser_metrics = fitted_capture.browser_metrics
                            if png == _placeholder_png():
                                raise RuntimeError("renderer returned a placeholder")
                    finally:
                        if first_slot_acquired:
                            render_gate.release()
                    if fitted_capture is not None:
                        fit_source = local_fit_source_html or illustrated_html
                        accepted = await admit_local_fit_capture(
                            fitted_capture,
                            original_html=fit_source,
                            policy=local_fit_policy_value,
                            evaluate=partial(
                                _evaluate_slide_quality,
                                index=index,
                                capacity_plan=capacity_plan,
                                composition_profile=composition_profile,
                                reference_mode=plan_reference_mode,
                            ),
                            render=render_html_to_png_with_metrics,
                            render_gate=render_gate,
                            slide_deadline=float(slide_deadline),
                            verify_seconds=local_budget["verify_seconds"],
                            deck_scope=render_deck_scope,
                        )
                        illustrated_html = accepted.html
                        png = accepted.png
                        browser_metrics = accepted.metrics
                        local_fit_receipt = accepted.receipt
                        accepted_fit = accepted
                        unresolved = accepted.receipt.get("unresolvedReasons")
                        if isinstance(unresolved, (list, tuple)) and unresolved:
                            author_flagged_slides[index] = _local_fit_unresolved_flag(
                                index, unresolved
                            )
                        if illustrated_html != scene_envelope.html_projection:
                            scene_envelope = seal_slide_scene_envelope(
                                illustrated_html, slide_index=index
                            )
                            if generation_job is not None:
                                persist_scene_envelope(
                                    generation_job.id, scene_envelope
                                )
                except Exception as exc:
                    if (
                        local_fit_policy_value is not None
                        and not (
                            isinstance(exc, AuthoredLocalFitRejected)
                            and exc.reason == "original_output_unavailable"
                        )
                    ):
                        reason = local_fit_failure_reason(exc)
                        try:
                            accepted = await preserve_original_capture(
                                original_html=local_fit_source_html or illustrated_html,
                                policy=local_fit_policy_value,
                                reason=reason,
                                rejected_report=(
                                    fitted_capture.fit_report
                                    if fitted_capture is not None
                                    else None
                                ),
                                render=render_html_to_png_with_metrics,
                                render_gate=render_gate,
                                slide_deadline=float(slide_deadline),
                                verify_seconds=local_budget["verify_seconds"],
                                deck_scope=render_deck_scope,
                            )
                        except AuthoredLocalFitRejected as recovery_error:
                            raise AuthoredStageError(
                                slide_index=index,
                                stage="render",
                                code=f"authored_local_fit_{recovery_error.reason}",
                                cause=recovery_error,
                                retryable=False,
                            ) from recovery_error
                        illustrated_html = accepted.html
                        png = accepted.png
                        browser_metrics = accepted.metrics
                        local_fit_receipt = accepted.receipt
                        accepted_fit = accepted
                        author_flagged_slides[index] = _local_fit_unresolved_flag(
                            index, accepted.receipt.get("unresolvedReasons") or ()
                        )
                    else:
                        code = (
                            f"authored_local_fit_{exc.reason}"
                            if isinstance(exc, AuthoredLocalFitRejected)
                            else "authored_slide_render_failed"
                        )
                        raise AuthoredStageError(
                            slide_index=index,
                            stage="render",
                            code=code,
                            cause=exc,
                            retryable=(
                                False
                                if isinstance(exc, AuthoredLocalFitRejected)
                                else is_retryable_exception(exc)
                            ),
                        ) from exc
                finally:
                    render_work = time.monotonic() - render_started
                    await record_local_stage(latency_ms=render_work * 1000)
            if source_reflow and rescue is not None and source_block_bindings is not None:
                flag = source_reflow_rescue_readback(
                    index=index, html=illustrated_html, metrics=browser_metrics,
                    bindings=source_block_bindings[index], profile=composition_profile,
                    reference_mode=plan_reference_mode, prior_failure=rescue.prior_failure,
                    provider_attempt_count=author_provider_attempt_count,
                )
                if flag is not None:
                    author_flagged_slides[index] = flag
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
                if local_fit_policy_value is not None and quality_failure is not None:
                    if accepted_fit is not None or (
                        isinstance(local_fit_receipt, Mapping)
                        and receipt_is_preserved_original(local_fit_receipt)
                    ):
                        # The local-fit boundary already accepted one exact
                        # html/png/metrics tuple; changed HTML also passed its
                        # independent fit-disabled reconstruction. A later
                        # whole-page visual finding is retained as publication
                        # evidence without reopening authoring or invalidating
                        # that accepted tuple.
                        flag = _local_fit_quality_flag(index, quality_failure)
                        existing = author_flagged_slides.get(index)
                        if existing is None:
                            author_flagged_slides[index] = flag
                        else:
                            merged_reasons = list(existing.get("reasons") or ())
                            primary = existing.get("reason")
                            if (
                                isinstance(primary, str)
                                and primary not in merged_reasons
                            ):
                                merged_reasons.insert(0, primary)
                            for reason in flag["reasons"]:
                                if reason not in merged_reasons:
                                    merged_reasons.append(reason)
                            author_flagged_slides[index] = {
                                **existing,
                                "reasons": merged_reasons[:8],
                            }
                        quality_failure = None
                    else:
                        # Preserve the pre-existing fail-closed behavior for a
                        # local-fit path that has no newly accepted tuple.
                        raise AuthoredStageError(
                            slide_index=index,
                            stage="render",
                            code="authored_local_fit_geometry_unresolved",
                            retryable=False,
                            message=(
                                "Local fit left blocking geometry or font issues"
                            ),
                        )
                if accepted_fit is not None:
                    # This tuple has passed the local-fit evidence boundary;
                    # any later visual advisory is carried by the slide flag.
                    cache_accepted_capture(accepted_fit, timeout=60)
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
                local_fit_receipt,
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
    return _call_pipeline_helper(
        "build_pipeline_result", completed_results, plan=plan, started=started,
        evidence_ledger=evidence_ledger, evidence_attempt_id=evidence_attempt_id,
        author_flagged_slides=author_flagged_slides,
        authoring_cooldown_wait_seconds=authoring_cooldown_wait_seconds,
    )
