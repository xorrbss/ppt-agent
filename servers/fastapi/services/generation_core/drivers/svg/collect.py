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
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone

INTERNAL_SVG_STAGE_PLAN_KEY = "_internal_svg_stage_plan"


def validate_svg_stage_plan(value):
    """Validate trusted scheduling data without adding public request fields."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("schema") != "svg-stage-plan/v1":
        raise ValueError("svg_stage_plan_invalid")
    if set(value) - {"schema", "partitions", "visual_intents", "source_lineage", "illustration_budget"}:
        raise ValueError("svg_stage_plan_invalid")
    partitions = value.get("partitions")
    if not isinstance(partitions, list) or not partitions or any(not isinstance(p, list) or not p for p in partitions):
        raise ValueError("svg_stage_plan_invalid")
    indices = [i for partition in partitions for i in partition]
    if any(type(i) is not int or i < 0 for i in indices) or sorted(indices) != list(range(len(indices))):
        raise ValueError("svg_stage_plan_invalid")
    intents = value.get("visual_intents")
    if intents is not None and (not isinstance(intents, list) or len(intents) != len(indices) or any(not isinstance(i, dict) for i in intents)):
        raise ValueError("svg_stage_plan_invalid")
    if "source_lineage" in value and not isinstance(value["source_lineage"], dict):
        raise ValueError("svg_stage_plan_invalid")
    if "illustration_budget" in value and (type(value["illustration_budget"]) is not int or value["illustration_budget"] < 0):
        raise ValueError("svg_stage_plan_invalid")
    return deepcopy(value)


def stage_runtime_profile(request, profile):
    plan = validate_svg_stage_plan(getattr(request, "_svg_stage_plan", None))
    if plan is None or "illustration_budget" not in plan:
        return profile
    if plan["illustration_budget"] > profile.illustration_budget:
        raise ValueError("svg_stage_asset_budget_exceeds_runtime")
    from dataclasses import replace
    return replace(profile, illustration_budget=plan["illustration_budget"])


def select_svg_stage_plan(request, value):
    plan = validate_svg_stage_plan(value)
    if plan is not None and getattr(request, "strategy", None) != "svg":
        raise ValueError("svg_stage_plan_requires_svg")
    request._svg_stage_plan = plan


def validate_stage_authority(plan, *, owner_id, tenant_id, request_digest=None):
    authority = plan.get("source_lineage", {}).get("execution_authority") or {}
    if not owner_id or not tenant_id or str(authority.get("owner_id")) != str(owner_id) or str(authority.get("tenant_id")) != str(tenant_id):
        raise ValueError("svg_stage_authority_mismatch")
    if not all(isinstance(authority.get(key), str) and authority[key].startswith("sha256:") and len(authority[key]) == 71 for key in ["manifest_digest", "request_digest"]):
        raise ValueError("svg_stage_authority_missing")
    if request_digest is not None and authority["request_digest"] != request_digest:
        raise ValueError("svg_stage_request_binding_mismatch")
    return authority


def _stage_error(code):
    return AuthoredStageError(slide_index=None, stage="author", code=code, retryable=False)


def _unknown_provider_checkpoint(value):
    if isinstance(value, dict):
        if value.get("inflight") is not None:
            return True
        return any(_unknown_provider_checkpoint(v) for v in value.values())
    if isinstance(value, list):
        return any(_unknown_provider_checkpoint(v) for v in value)
    return value in {"public_provider_result_unknown", "public_provider_timeout", "provider_outcome:public_provider_result_unknown"} if isinstance(value, str) else False


async def _stage_preflight(plan, job, checkpoint, count):
    if sum(map(len, plan["partitions"])) != count:
        raise _stage_error("svg_stage_plan_count_mismatch")
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    validate_stage_authority(plan, owner_id=get_current_owner_id(), tenant_id=get_current_tenant_id())
    operation_id = getattr(job, "workspace_operation_id", None)
    if operation_id is None or (getattr(job, "request_payload", None) or {}).get(INTERNAL_SVG_STAGE_PLAN_KEY) != plan:
        raise _stage_error("svg_stage_operation_binding_mismatch")
    saved_pages = []
    missing = False
    for i in range(count):
        try:
            saved = await checkpoint(job, i)
        except RuntimeError as error:
            if str(error) != "svg_checkpoint_missing":
                raise
            missing = True
            saved = None
        saved_pages.append(saved or {})
    if missing:
        if any(saved_pages) or job is None or getattr(job, "attempt_number", 0) != 1 or getattr(job, "resume_count", 0) or getattr(job, "completed_slides", 0):
            raise RuntimeError("svg_checkpoint_missing")
        from sqlalchemy import func, select
        from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall
        from models.sql.provider_usage_receipt import ProviderUsageReceipt
        from models.sql.slide import SlideModel
        from models.sql.workspace import WorkspaceOperation
        from services.database import async_session_maker

        async with async_session_maker() as session:
            calls = await session.scalar(select(func.count()).select_from(PublicProviderLogicalCall).join(PublicOperationAdmission, PublicOperationAdmission.id == PublicProviderLogicalCall.admission_id).where(PublicOperationAdmission.operation_id == operation_id))
            usage = await session.scalar(select(func.count()).select_from(ProviderUsageReceipt).where(ProviderUsageReceipt.job_id == job.id))
            published_svg = await session.scalar(select(func.count()).select_from(SlideModel).where(SlideModel.presentation == job.presentation_id, SlideModel.svg_content.is_not(None), SlideModel.svg_content != ""))
            operation = await session.get(WorkspaceOperation, operation_id)
        source_id = (plan.get("source_lineage") or {}).get("source_presentation_id")
        if calls or usage or published_svg or operation is None or operation.result_revision is not None or (source_id and str(job.presentation_id) == str(source_id)):
            raise RuntimeError("svg_checkpoint_missing")
    if any(_unknown_provider_checkpoint(payload) for payload in saved_pages):
        raise _stage_error("public_provider_result_unknown")
    progress = saved_pages[0].get("trusted_stage_execution") or {}
    if progress and progress.get("plan_digest") != _digest(plan):
        raise _stage_error("svg_stage_checkpoint_conflict")
    if progress and progress.get("operation_id") != str(operation_id):
        raise _stage_error("svg_stage_operation_binding_mismatch")
    if any(stage.get("status") in {"review_inflight", "review_unknown"} for stage in progress.get("stages", [])):
        raise _stage_error("public_provider_result_unknown")
    return progress


async def _stage_checkpoint(job, checkpoint, plan, stages):
    seed = deepcopy(await checkpoint(job, 0) or {})
    progress = {"schema": "svg-stage-execution/v1", "plan_digest": _digest(plan),
                "operation_id": str(job.workspace_operation_id), "authority": deepcopy(plan["source_lineage"]["execution_authority"]),
                "stages": deepcopy(stages)}
    seed["trusted_stage_execution"] = progress
    await checkpoint(job, 0, payload=seed)
    return progress


async def _collect_stages(ctx, plan, tracked_page, job, checkpoint, read_png, run_advisory, review_stage=None):
    """Only the current partition is scheduled; accepted reviews survive resume."""
    progress = await _stage_preflight(plan, job, checkpoint, ctx.count)
    stages = deepcopy(progress.get("stages", []))
    pages_by_index = {}
    for ordinal, indices in enumerate(plan["partitions"]):
        # Creating tasks happens inside this loop, after the preceding review.
        stopped = asyncio.Event()
        original_semaphore = ctx.semaphore

        class StageAdmissionSemaphore:
            async def __aenter__(self):
                await original_semaphore.acquire()
                if stopped.is_set():
                    original_semaphore.release()
                    raise _stage_error("svg_stage_stopped_after_page_failure")
                return self

            async def __aexit__(self, *exc):
                original_semaphore.release()

        async def settle_page(index):
            try:
                return await tracked_page(index)
            except BaseException:
                stopped.set()
                raise

        ctx.semaphore = StageAdmissionSemaphore()
        try:
            lineage = (getattr(job, "request_payload", None) or {}).get("_svg_recovery_lineage")
            if lineage:
                replacements = set(lineage.get("replacement_page_indexes", []))
                settled = {}
                for i in sorted(indices, key=lambda i: (i not in replacements, i)):
                    settled[i] = await settle_page(i)
                results = [settled[i] for i in indices]
            else:
                results = await asyncio.gather(
                    *[asyncio.create_task(settle_page(i)) for i in indices],
                    return_exceptions=True,
                )
        finally:
            ctx.semaphore = original_semaphore
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise errors[0]
        pages_by_index.update(zip(indices, results, strict=True))
        page_digests = [_digest({"svg": p.get("svg_content"), "png": p.get("png_sha256")}) for p, _ in results]
        if any(p.get("quality", {}).get("passed") is not True for p, _ in results):
            raise _stage_error("svg_stage_quality_failed")
        prior = stages[ordinal] if ordinal < len(stages) else None
        if prior and prior.get("status") == "accepted":
            if prior.get("indices") != indices or prior.get("page_digests") != page_digests:
                raise _stage_error("svg_stage_checkpoint_conflict")
            continue
        if prior and (prior.get("indices") != indices or prior.get("page_digests") != page_digests):
            raise _stage_error("svg_stage_checkpoint_conflict")
        marker = {"indices": list(indices), "page_digests": page_digests, "status": "review_pending"}
        if prior and isinstance(prior.get("advisory"), dict):
            marker["advisory"] = prior["advisory"]
        if ordinal < len(stages):
            stages[ordinal] = marker
        else:
            stages.append(marker)
        await _stage_checkpoint(job, checkpoint, plan, stages)
        if any(p.get("quality", {}).get("passed") is not True for p, _ in results):
            raise _stage_error("svg_stage_quality_failed")
        advisory = marker.get("advisory")
        if advisory is None:
            marker["status"] = "review_inflight"
            await _stage_checkpoint(job, checkpoint, plan, stages)
            try:
                advisory = await deck_quality.deck_advisory(ctx, results, job=job, read_png=read_png, run_advisory=run_advisory, round_ordinal=ordinal)
                vision = advisory.get("vision") if isinstance(advisory, dict) else None
                if not isinstance(vision, dict) or vision.get("skipped"):
                    raise _stage_error("svg_stage_review_unavailable")
            except BaseException:
                marker["status"] = "review_unknown"
                await _stage_checkpoint(job, checkpoint, plan, stages)
                raise
            marker.update(status="review_pending", advisory=advisory)
            await _stage_checkpoint(job, checkpoint, plan, stages)
        from services.generation_core.quality.advisory_record import advisory_flagged_pages
        if advisory_flagged_pages(advisory):
            raise _stage_error("svg_stage_review_flagged")
        if review_stage is not None:
            decision = await review_stage(ordinal, tuple(indices), tuple(results), advisory)
            if decision is not True:
                raise _stage_error("svg_stage_review_pending")
        marker.update(status="accepted", advisory=advisory)
        await _stage_checkpoint(job, checkpoint, plan, stages)
    pages = [pages_by_index[i] for i in range(ctx.count)]
    # Rebind stage-local advisory indices to the final document without another purchase.
    vision_slides = []
    for stage in stages:
        for slide in stage["advisory"]["vision"].get("slides", []):
            slide = deepcopy(slide)
            slide["index"] = stage["indices"][slide["index"]]
            vision_slides.append(slide)
    from services.generation_core.quality.advisory_record import advisory_record
    from services.svg_export_visual_review import split_page_advisories
    from types import SimpleNamespace
    from utils.hashing import sha256_hex
    vision = {"slides": sorted(vision_slides, key=lambda s: s["index"]), "slideCount": ctx.count,
              "stageReviewDigests": [_digest(s["advisory"]) for s in stages]}
    record = advisory_record(vision, [sha256_hex((p.get("svg_content") or "").encode()) for p, _ in pages])
    slides = [SimpleNamespace(svg_content=p.get("svg_content") or "", content={"svgReceipt": p}) for p, _ in pages]
    record["pageAdvisories"] = split_page_advisories(vision, slides, range(ctx.count))
    return pages, record

from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg import author, render
from services.generation_core.drivers.svg import quality as deck_quality
from services.generation_core.drivers.svg import timing
from services import svg_outline_spans
from services.generation_core.drivers.svg.terminal import rescue_terminal_page
from services.svg_presentation_checkpoint import _collect_pages_or_fail, _digest, _provider_attempt_count as _legacy_provider_attempt_count, _provider_attempt_limit as _legacy_provider_attempt_limit
from services.svg_presentation_prepare import _prepare_generation


@dataclass
class _RunState:
    """Mutable state shared across one deck's concurrent page attempts."""
    fatal_error: BaseException | None = None
    cache_only_approval: dict | None = None
    cached_author_render_manifest_hash: str | None = None
    terminal_drain_enabled: bool = False
    hard_deadline_at: datetime | None = None


async def _drain_leaf_tasks(tasks, state):
    """Stop issuing work, but retain terminal results of already issued calls."""
    from utils.provider_idempotency import PROVIDER_CALL_TIMEOUT_SECONDS
    timeout = PROVIDER_CALL_TIMEOUT_SECONDS
    if state.hard_deadline_at is not None:
        timeout = min(timeout, max(0.0,
            (state.hard_deadline_at - datetime.now(timezone.utc)).total_seconds()))
    pending = {task for task in tasks if not task.done()}
    if pending and timeout > 0:
        _, pending = await asyncio.wait(pending, timeout=timeout)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _generate(request, presentation_id, outline, language, session, job, *, stage_plan=None, review_stage=None):
    from services import svg_presentation_service as _facade
    async_session_maker = _facade.async_session_maker
    checkpoint = _facade._checkpoint
    read_png = _facade._read_png
    write_png = _facade._write_png
    publish = _facade._publish
    restored_plan = validate_svg_stage_plan(getattr(request, "_svg_stage_plan", None))
    stage_plan = validate_svg_stage_plan(stage_plan) if stage_plan is not None else restored_plan
    if restored_plan is not None and stage_plan != restored_plan:
        raise _stage_error("svg_stage_plan_conflict")
    if stage_plan is not None:
        select_svg_stage_plan(request, stage_plan)
        if job is None:
            raise _stage_error("svg_stage_job_required")
        await _stage_preflight(stage_plan, job, checkpoint, len(outline.slides))
    spans = svg_outline_spans.current() or svg_outline_spans.begin()
    with spans.span("prepare_total"):
        ctx = await _prepare_generation(request, presentation_id, outline, session, job)
    if (ctx.snapshot.get("common_header_design_plan") or {}).get("schema") == "svg-common-header-design-plan/v2":
        from services.svg_common_header_generation import prepare_document_header
        ctx = await prepare_document_header(ctx, request, presentation_id, outline, language, job,
            checkpoint=checkpoint, async_session_maker=async_session_maker,
            read_png=read_png, write_png=write_png, page_runner=_page,
            page_hooks_factory=_PageHooks, state_factory=_RunState)
    from services.svg_authoring_profile import read_quality_recovery_policy
    recovery_policy = read_quality_recovery_policy(ctx.snapshot)
    if recovery_policy:
        ctx.quality_review_contents = [slide.content for slide in outline.slides]
        if ctx.source_reflow:
            import json
            ctx.quality_review_contents = [json.dumps(unit.model_dump(mode="json"), ensure_ascii=False)
                                          for unit in ctx.source_units]
        ctx.quality_recovery_previous_renders = {}
        from services.generation_core.drivers.svg.quality_recovery import recovery_binding
        ctx.quality_recovery_input_binding = recovery_binding(ctx, request, job)
    if stage_plan is None and job is not None and (await checkpoint(job, 0) or {}).get("trusted_stage_execution"):
        raise _stage_error("svg_stage_plan_missing")
    timing.record_outline(request)
    spans.emit(presentation_id)
    timing.enter_deck_stage(request, "author")
    state = _RunState(terminal_drain_enabled=bool(stage_plan is None and (
        recovery_policy or getattr(ctx, "partial_recovery_manifest", None))))
    if job is not None and job.workspace_operation_id is not None:
        from models.sql.workspace import WorkspaceOperation
        from services.svg_cached_author_render_recovery import REVISIONS

        operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
        if state.terminal_drain_enabled and operation is not None:
            from utils.datetime_utils import as_utc
            state.hard_deadline_at = as_utc(operation.deadline_at)
        operation_payload = (operation.request_payload or {}) if operation is not None else {}
        if operation_payload.get("recovery_revision") in REVISIONS:
            if stage_plan is not None:
                raise _stage_error("svg_cached_author_render_stage_plan_forbidden")
            state.cached_author_render_manifest_hash = operation_payload.get(
                "svg_cached_author_render_manifest_hash")
            if (not ctx.source_reflow
                    or not state.cached_author_render_manifest_hash
                    or job.total_slides != ctx.count or ctx.count < 1):
                raise _stage_error("svg_cached_author_render_marker_missing")
            for page_index in range(ctx.count):
                saved_page = await checkpoint(job, page_index) or {}
                archive = saved_page.get("cached_author_render_archive") or {}
                if (not saved_page.get("cached_author_render")
                        or archive.get("approval_manifest_hash")
                        != state.cached_author_render_manifest_hash):
                    raise _stage_error("svg_cached_author_render_marker_missing")
    hooks = _PageHooks(checkpoint=checkpoint, async_session_maker=async_session_maker,
                       read_png=read_png, write_png=write_png)
    partial_manifest = getattr(ctx, "partial_recovery_manifest", None)
    if partial_manifest:
        original_checkpoint = checkpoint

        async def partial_checkpoint(active_job, page_index, *, payload=None):
            if payload is not None and page_index in partial_manifest["known_indices"]:
                from services.svg_partial_recovery import verify_cached_page
                if (payload.get("partial_recovery") or {}).get("operation_id") != str(active_job.workspace_operation_id):
                    raise ValueError("svg_partial_recovery_page_unbound")
                witness = partial_manifest["pages"][page_index]
                if witness.get("phase") == "author-only":
                    durable = await original_checkpoint(active_job, page_index) or {}
                    prior_phase = (durable.get("partial_recovery") or {}).get("phase")
                    next_phase = (payload.get("partial_recovery") or {}).get("phase")
                    if prior_phase == "finalized" and next_phase != "finalized":
                        raise ValueError("svg_partial_recovery_cached_phase_rollback")
                from services.generation_core.drivers.svg.partial_quality_recovery import current_quality_repair_authorized
                if not await current_quality_repair_authorized(ctx, payload, page_index, active_job, original_checkpoint):
                    verify_cached_page(payload, partial_manifest, page_index, witness["input_digest"])
            return await original_checkpoint(active_job, page_index, payload=payload)

        checkpoint = hooks.checkpoint = partial_checkpoint
    if recovery_policy:
        hooks.recovery_snapshot = ctx.snapshot
    from services.svg_authoring_profile import read_authoring_profile
    if job is not None and ctx.count in (2, 4) and read_authoring_profile(ctx.snapshot) == {"id": "art-direction-v1", "version": 7}:
        saved_target = await checkpoint(job, 1) or {}
        if saved_target.get("cache_only_remeasurement"):
            state.cache_only_approval = _cache_remeasurement_approval(ctx, job, saved_target)

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

    cached_first = getattr(ctx, 'recovery_cached_first', None) or []
    task_order = cached_first + [i for i in range(ctx.count) if i not in cached_first]
    stage_advisory = None
    tasks_by_index = {} if stage_plan is not None else {i: asyncio.create_task(tracked_page(i)) for i in task_order}
    tasks = [tasks_by_index[i] for i in range(ctx.count)] if stage_plan is None else []
    try:
        if stage_plan is None:
            pages = await asyncio.gather(*tasks) if state.terminal_drain_enabled else await _collect_pages_or_fail(tasks)
            if recovery_policy:
                from services.generation_core.drivers.svg.quality_recovery import recover_screen_pages
                pages = await recover_screen_pages(ctx, request, presentation_id, outline, job,
                    pages, state, hooks, _attempt_loop)
        else:
            pages, stage_advisory = await _collect_stages(ctx, stage_plan, tracked_page, job, checkpoint, read_png, _facade.run_vision_advisory, review_stage)
        if state.fatal_error is not None:
            raise state.fatal_error
    except BaseException as error:
        if state.terminal_drain_enabled and isinstance(error, Exception):
            state.fatal_error = state.fatal_error or error
            await _drain_leaf_tasks(tasks, state)
        else:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        raise
    timing.settle_page_loop(request)
    timing.enter_deck_stage(request, "quality")
    with timing.observe_stage(request, "quality"):
        summary = await deck_quality.classify_pages(pages, job=job, checkpoint=checkpoint,
            **({"snapshot": ctx.snapshot} if recovery_policy else {}))
        if recovery_policy:
            from services.generation_core.drivers.svg.quality_recovery import recover_deck
            pages, advisory = await recover_deck(ctx, request, presentation_id, outline, job,
                pages, state, hooks, _facade.run_vision_advisory, _attempt_loop, deck_quality)
            summary = await deck_quality.classify_pages(pages, job=job, checkpoint=checkpoint,
                snapshot=ctx.snapshot)
        if not recovery_policy:
            advisory = stage_advisory if stage_plan is not None else await deck_quality.deck_advisory(ctx, pages, job=job, read_png=read_png, run_advisory=_facade.run_vision_advisory)
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


def _cache_remeasurement_approval(ctx, job, saved):
    from services.svg_classification_remeasurement import REMEASUREMENT_SCHEMA, SVG_CLASSIFICATION_REMEASUREMENT
    from services.svg_authoring_profile import read_authoring_profile

    marker = saved.get("cache_only_remeasurement") or {}
    archive = saved.get("classification_remeasurement_archive") or {}
    manifest = archive.get("approval_manifest") or {}
    if not (job is not None and ctx.count in (2, 4)
            and read_authoring_profile(ctx.snapshot) == {"id": "art-direction-v1", "version": 7}
            and marker.get("schema") == REMEASUREMENT_SCHEMA
            and marker.get("revision") == SVG_CLASSIFICATION_REMEASUREMENT
            and marker.get("slide_index") == 1
            and marker.get("operation_id") == str(job.workspace_operation_id)
            and marker.get("source_operation_id") == manifest.get("operation_id")
            and marker.get("approval_manifest_hash") == archive.get("approval_manifest_hash") == _digest(manifest)
            and marker.get("style_digest") == manifest.get("style_digest") == _digest(ctx.snapshot)
            and marker.get("reference_digest") == manifest.get("reference_digest") == _digest(ctx.snapshot.get("references"))
            and manifest.get("target_indices") == [1]
            and marker.get("expected_page_count") == manifest.get("expected_page_count") == ctx.count
            and marker.get("recovery_reason") == manifest.get("recovery_reason")
            and marker.get("recovery_reason") in {"classification_legend_paint", "native_single_series_title_unit"}
            and ctx.count == {"classification_legend_paint": 4, "native_single_series_title_unit": 2}[marker["recovery_reason"]]
            and manifest.get("max_additional_author_calls") == 0
            and manifest.get("source_and_png_must_remain_identical") is True
            and sorted(page.get("slide_index") for page in manifest.get("pages", [])) == list(range(ctx.count))):
        raise AuthoredStageError(slide_index=1, stage="render", code="svg_cache_remeasurement_invalid", retryable=False)
    return manifest


def _verify_protected_cache(saved, png, witness, input_digest, index, *, metrics_digest=None):
    from services.svg_classification_remeasurement import author_witness_digest
    from utils.hashing import sha256_hex

    quality = saved.get("quality") or {}
    if not (png is not None and isinstance(saved.get("svg_content"), str)
            and sha256_hex(saved["svg_content"].encode()) == witness.get("source_sha256")
            and sha256_hex(png) == saved.get("png_sha256") == witness.get("png_sha256")
            and saved.get("input_digest") == witness.get("input_digest") == input_digest
            and author_witness_digest(saved) == witness.get("author_receipt_digest")
            and str(quality.get("source_digest", "")).removeprefix("sha256:") == witness.get("source_sha256")
            and str(quality.get("png_digest", "")).removeprefix("sha256:") == witness.get("png_sha256")
            and quality.get("measurement_complete") is True and quality.get("geometry_passed") is True
            and _digest(quality.get("browser_metrics")) == (metrics_digest or witness.get("browser_metrics_digest"))
            and not saved.get("inflight")):
        raise AuthoredStageError(slide_index=index, stage="render", code="svg_cache_remeasurement_invalid", retryable=False)


async def _cached_author_render_page(ctx, request, presentation_id, index, content,
                                     saved, input_digest, job, state, hooks):
    """Render a locked author witness once; never enter the provider loop."""
    from services.svg_cached_author_render_recovery import (
        APPROVED_CONVERTER_CODE_DIGEST, REVISIONS, marker_schema,
        author_witness, converter_code_digest, derived_child_request_valid,
        digest, source_receipt_witness,
    )
    from services.svg_source_layout import verify_source_receipt
    from services.svg_presentation_checkpoint import _provider_attempt_count
    from utils.hashing import sha256_hex

    def invalid():
        raise AuthoredStageError(slide_index=index, stage="render",
                                 code="svg_cached_author_render_invalid", retryable=False)

    marker = saved.get("cached_author_render") or {}
    archive = saved.get("cached_author_render_archive") or {}
    manifest = archive.get("approval_manifest") or {}
    original_row = archive.get("checkpoint_row") or {}
    original = original_row.get("svg_checkpoint") or {}
    pages = manifest.get("pages") or []
    witness = next((page for page in pages if page.get("slide_index") == index), None)
    revision = manifest.get("recovery_revision")
    if (revision not in REVISIONS or witness is None
            or marker.get("schema") != marker_schema(revision)
            or marker.get("revision") != revision
            or marker.get("operation_id") != str(job.workspace_operation_id)
            or marker.get("source_operation_id") != manifest.get("operation_id")
            or marker.get("slide_index") != index
            or marker.get("status") not in {"pending", "completed"}
            or marker.get("approval_manifest_hash") != archive.get("approval_manifest_hash")
            or archive.get("approval_manifest_hash") != digest(manifest)
            or state.cached_author_render_manifest_hash != digest(manifest)
            or not APPROVED_CONVERTER_CODE_DIGEST
            or converter_code_digest() != manifest.get("converter_code_digest")
            or converter_code_digest() != APPROVED_CONVERTER_CODE_DIGEST
            or manifest.get("max_additional_provider_calls") != 0
            or manifest.get("expected_page_count") != ctx.count
            or ctx.count != job.total_slides
            or [page.get("slide_index") for page in pages] != list(range(ctx.count))
            or not derived_child_request_valid(
                job, manifest, operation_id=job.workspace_operation_id,
                presentation_id=presentation_id)
            or digest(ctx.snapshot) != manifest.get("style_digest")
            or getattr(ctx.source_document, "manifest_digest", None)
            != manifest.get("source_manifest_digest")
            or digest(original_row) != witness.get("row_digest")
            or not (saved.get("input_digest") == marker.get("input_digest") == input_digest)
            or saved.get("inflight") is not None
            or _provider_attempt_count(saved) != 1
            or author_witness(saved) != marker.get("author_receipt_digest")
            or author_witness(original) != witness.get("author_receipt_digest")
            or any(marker.get(key) != witness.get(key) for key in (
                "raw_sha256", "svg_sha256", "input_digest", "author_receipt_digest",
                "source_receipt_digest"))):
        invalid()
    svg = original.get("svg_content") or original.get("last_valid_svg")
    if (not isinstance(svg, str) or not svg
            or (marker["status"] == "pending"
                and not (saved.get("svg_content") or saved.get("last_valid_svg")))
            or any(value not in (None, "", svg)
                   for value in (saved.get("svg_content"), saved.get("last_valid_svg")))
            or sha256_hex(svg.encode()) != witness["svg_sha256"]
            or source_receipt_witness(saved) != witness["source_receipt_digest"]
            or source_receipt_witness(saved) != source_receipt_witness(original)
            or saved.get("nativeDeclarations") != original.get("nativeDeclarations")
            or saved.get("source_manifest_ref") != original.get("source_manifest_ref")
            or saved.get("source_manifest_digest") != original.get("source_manifest_digest")
            or marker.get("verified_fixed_axis_migrations")
            != witness.get("verified_fixed_axis_migrations")):
        invalid()
    try:
        verify_source_receipt(svg, saved.get("source_preservation"), ctx.source_document, index)
    except Exception as error:
        raise AuthoredStageError(slide_index=index, stage="render",
                                 code="svg_cached_author_render_source_mismatch",
                                 retryable=False) from error
    if marker["status"] == "completed":
        png = hooks.read_png(saved)
        if (png is None or saved.get("svg_content") != svg
                or saved.get("quality", {}).get("passed") is not True
                or saved.get("quality", {}).get("verified") is not True
                or sha256_hex(png) != saved.get("png_sha256")):
            invalid()
        return saved, png
    payload = deepcopy(saved)
    payload["svg_content"] = svg
    payload["cached_author_render"]["verified_fixed_axis_migrations"] = deepcopy(
        witness["verified_fixed_axis_migrations"])
    from services.authored_canvas import request_canvas_metadata
    from services.document_canvas_generation import svg_readability_policy
    recovery_policy = svg_readability_policy(
        request_canvas_metadata(request), source_restyle=ctx.source_reflow,
        instructions=request.instructions)
    if (recovery_policy.get("quality_target_font_pt", 0) < 9.5
            or recovery_policy != manifest.get("recovery_readability_policy")):
        invalid()

    async def guarded_checkpoint(active_job, page_index, *, payload=None):
        if active_job.id != job.id or page_index != index:
            invalid()
        if payload is not None and (
                payload.get("svg_content") != svg
                or payload.get("input_digest") != input_digest
                or author_witness(payload) != witness["author_receipt_digest"]
                or payload.get("cached_author_render_archive") != archive
                or source_receipt_witness(payload) != witness["source_receipt_digest"]
                or payload.get("nativeDeclarations") != original.get("nativeDeclarations")
                or payload.get("cached_author_render", {}).get("verified_fixed_axis_migrations")
                != witness["verified_fixed_axis_migrations"]
                or payload.get("cached_author_render", {}).get("status") not in {"pending", "completed"}):
            invalid()
        return await hooks.checkpoint(active_job, page_index, payload=payload)

    with timing.observe_stage(request, "render"):
        result = await render.attempt_render(
            ctx=ctx, request=request, presentation_id=presentation_id,
            index=index, content=content, payload=payload, svg=svg, ordinal=0,
            job=job, checkpoint=guarded_checkpoint,
            async_session_maker=hooks.async_session_maker, write_png=hooks.write_png,
            recovery_readability_policy=recovery_policy,
        )
    if result is render.RETRY or payload.get("quality", {}).get("passed") is not True:
        invalid()
    payload["cached_author_render"]["status"] = "completed"
    await guarded_checkpoint(job, index, payload=payload)
    return result


async def _remeasure_partial_cached_page(ctx, request, presentation_id, index, content,
                                       saved, input_digest, job, hooks):
    """Measure a bound prior SVG without purchasing or replacing its author bytes."""
    from services.svg_partial_recovery import verify_cached_page
    manifest = ctx.partial_recovery_manifest
    from utils.hashing import sha256_hex
    if (saved.get("partial_recovery") or {}).get("operation_id") != str(job.workspace_operation_id):
        raise ValueError("svg_partial_recovery_page_unbound")
    if saved.get("final_selected_render_evidence") is not None:
        from services.generation_core.drivers.svg.quality_recovery import ensure_recovery_authority
        await ensure_recovery_authority(job, hooks)
    witness = verify_cached_page(saved, manifest, index, input_digest)
    payload = deepcopy(saved)
    if payload.get("final_selected_render_evidence") is not None:
        from services.svg_final_selected_continuation import verify_cached_render
        png = verify_cached_render(payload, manifest, index)
        await ensure_recovery_authority(job, hooks)
        await hooks.checkpoint(job, index, payload=payload)
        return payload, png
    current_phase = (saved.get("partial_recovery") or {}).get("phase", "finalized")

    async def guarded_checkpoint(active_job, page_index, *, payload=None):
        nonlocal current_phase
        if payload is not None:
            marker = payload.get("partial_recovery") or {}
            if witness.get("phase") == "author-only":
                if current_phase == "finalized" and marker.get("phase") != "finalized":
                    raise ValueError("svg_partial_recovery_cached_phase_rollback")
                if current_phase == "author-only":
                    if marker.get("phase") != "author-only":
                        raise ValueError("svg_partial_recovery_cached_phase_invalid")
                    # The existing renderer calls this after its deterministic
                    # finalizer. Only the already-sealed first canonical hash
                    # may replace the active author-only drawing.
                    if sha256_hex(str(payload.get("svg_content", "")).encode()) == witness["final_render_svg_sha256"]:
                        marker["phase"] = "finalized"
            verify_cached_page(payload, manifest, index, input_digest)
            current_phase = marker.get("phase", "finalized")
        return await hooks.checkpoint(active_job, page_index, payload=payload)

    with timing.observe_stage(request, "render"):
        result = await render.attempt_render(ctx=ctx, request=request, presentation_id=presentation_id,
            index=index, content=content, payload=payload, svg=payload["svg_content"],
            ordinal=max(0, _provider_attempt_count(payload) - 1), job=job,
            checkpoint=guarded_checkpoint, async_session_maker=hooks.async_session_maker,
            write_png=hooks.write_png, allow_existing_common_header=True)
    verify_cached_page(payload, manifest, index, input_digest)
    if result is render.RETRY or payload.get("quality", {}).get("passed") is not True:
        raise AuthoredStageError(slide_index=index, stage="render",
                                 code="svg_partial_recovery_cached_quality_failed", retryable=False)
    if getattr(ctx, "quality_recovery_input_binding", None):
        from services.generation_core.drivers.svg.quality_recovery import local_evidence_binding
        payload["quality_recovery_local_evidence"] = local_evidence_binding(
            ctx.quality_recovery_input_binding, payload)
        await guarded_checkpoint(job, index, payload=payload)
    return result


async def _page(ctx, request, presentation_id, outline, language, job, index, state, hooks):
    from services.svg_source_layout import verify_source_receipt

    async with ctx.semaphore:
        if state.fatal_error is not None:
            raise state.fatal_error
        content = outline.slides[index].content
        from services.svg_generation_inputs import svg_page_input_digest
        header_pages = getattr(ctx, "header_pages", None)
        input_digest = svg_page_input_digest(content=content, style=ctx.snapshot,
            index=index, count=ctx.count, instructions=request.instructions,
            language=language, preset=request.codex_generation_preset,
            assets=ctx.manifests[index], writing_policy=getattr(request, "writing_policy", None),
            header_page=header_pages[index] if header_pages is not None else None)
        if ctx.source_reflow:
            input_digest = _digest({"page": input_digest,
                "source_manifest": ctx.source_document.manifest_digest,
                "font_profile": ctx.source_font_profile,
                "source_page": ctx.source_units[index].model_dump(mode="json"),
                "source_layout_version": "svg-source-layout/v2"})
        if ctx.raster_reconstruction:
            input_digest = _digest({"page": input_digest, "rasterApproval": ctx.raster_pin.approval_digest})
        saved = await hooks.checkpoint(job, index) or {}
        partial_manifest = getattr(ctx, "partial_recovery_manifest", None)
        partial_quality_repair = False
        if saved.get("partial_recovery") or partial_manifest:
            from services.svg_partial_recovery import verify_cached_page
            if (not partial_manifest or job is None
                    or (saved.get("partial_recovery") or {}).get("operation_id") != str(job.workspace_operation_id)):
                raise ValueError("svg_partial_recovery_page_unbound")
            from services.generation_core.drivers.svg.partial_quality_recovery import current_quality_repair_authorized
            partial_quality_repair = await current_quality_repair_authorized(ctx, saved, index, job, hooks.checkpoint)
            partial_page = (partial_manifest["pages"][index] if partial_quality_repair
                else verify_cached_page(saved, partial_manifest, index, input_digest))
        else:
            partial_page = None
        if getattr(ctx, "quality_recovery_input_binding", None):
            from services.generation_core.drivers.svg.quality_recovery import ensure_recovery_authority
            await ensure_recovery_authority(job, hooks)
            if saved.get("inflight") is not None:
                raise AuthoredStageError(slide_index=index, stage="author",
                    code="public_provider_result_unknown", retryable=False)
        if saved.get("input_digest") and saved.get("input_digest") != input_digest:
            raise RuntimeError("svg_checkpoint_input_conflict")
        if partial_page and partial_page["category"] == "known" and not partial_quality_repair:
            return await _remeasure_partial_cached_page(ctx, request, presentation_id, index,
                content, saved, input_digest, job, hooks)
        if saved.get("cached_author_render"):
            if state.cached_author_render_manifest_hash is None:
                raise AuthoredStageError(slide_index=index, stage="render",
                                         code="svg_cached_author_render_operation_missing", retryable=False)
            return await _cached_author_render_page(ctx, request, presentation_id, index,
                                                    content, saved, input_digest, job, state, hooks)
        if state.cached_author_render_manifest_hash is not None:
            raise AuthoredStageError(slide_index=index, stage="author",
                                     code="svg_cached_author_render_marker_missing", retryable=False)
        if saved.get("append_only_cache"):
            try:
                return await _remeasure_append_only_cache(ctx, request, presentation_id, index,
                    content, saved, input_digest, job, hooks)
            except _KnownCachedRepairRequired as repair:
                saved = repair.payload
        png = hooks.read_png(saved)
        if saved.get("cache_only_remeasurement") or state.cache_only_approval is not None:
            if state.cache_only_approval is None:
                state.cache_only_approval = _cache_remeasurement_approval(ctx, job, saved)
            manifest = state.cache_only_approval
            witness = next(page for page in manifest["pages"] if page["slide_index"] == index)
            marker = saved.get("cache_only_remeasurement") or {}
            _verify_protected_cache(saved, png, witness, input_digest, index,
                metrics_digest=marker.get("remeasured_metrics_digest") if marker.get("status") == "completed" else None)
            if index != 1:
                if saved["quality"].get("passed") is not True:
                    raise AuthoredStageError(slide_index=index, stage="render", code="svg_cache_remeasurement_invalid", retryable=False)
                return saved, png
            _cache_remeasurement_approval(ctx, job, saved)
            if marker.get("status") == "completed" and saved["quality"].get("passed") is True:
                return saved, png
            archived = (saved.get("classification_remeasurement_archive") or {}).get("checkpoint_row", {}).get("svg_checkpoint") or {}
            failure_kind = {"classification_legend_paint": "source_classification_unproven",
                            "native_single_series_title_unit": "native_requested_chart_missing"}[marker["recovery_reason"]]
            if not (marker.get("status") == "pending" and _provider_attempt_count(saved) == 2
                    and archived.get("svg_content") == saved["svg_content"]
                    and archived.get("quality") == saved["quality"]
                    and all(marker.get(key) == witness.get(key) for key in (
                        "source_sha256", "png_sha256", "input_digest", "author_receipt_digest", "browser_metrics_digest"))
                    and saved["quality"].get("passed") is False and saved["quality"].get("failures")
                    and all(failure.get("kind") == failure_kind for failure in saved["quality"]["failures"])
                    and not saved["attempts"][-1].get("receipt", {}).get("validation_error")):
                raise AuthoredStageError(slide_index=index, stage="render", code="svg_cache_remeasurement_invalid", retryable=False)
            payload = deepcopy(saved)

            async def guarded_checkpoint(active_job, page_index, *, payload=None):
                if payload is not None:
                    from services.svg_classification_remeasurement import author_witness_digest
                    if (payload.get("svg_content") != saved["svg_content"]
                            or payload.get("input_digest") != input_digest
                            or author_witness_digest(payload) != witness["author_receipt_digest"]
                            or payload.get("png_sha256") != witness["png_sha256"]
                            or payload.get("classification_remeasurement_archive") != saved["classification_remeasurement_archive"]):
                        raise AuthoredStageError(slide_index=index, stage="render", code="svg_cache_remeasurement_changed", retryable=False)
                return await hooks.checkpoint(active_job, page_index, payload=payload)

            try:
                with timing.observe_stage(request, "render"):
                    result = await render.attempt_render(ctx=ctx, request=request, presentation_id=presentation_id,
                        index=index, content=content, payload=payload, svg=saved["svg_content"], ordinal=1,
                        job=job, checkpoint=guarded_checkpoint,
                        async_session_maker=hooks.async_session_maker, write_png=hooks.write_png)
                if result is render.RETRY:
                    raise AuthoredStageError(slide_index=index, stage="render", code="svg_cache_remeasurement_retry_forbidden", retryable=False)
                if payload.get("quality", {}).get("passed") is not True or payload["cache_only_remeasurement"].get("status") != "completed":
                    raise AuthoredStageError(slide_index=index, stage="render", code="svg_cache_remeasurement_invalid", retryable=False)
                return result
            except Exception as error:
                if getattr(error, "code", None) != "authored_attempt_lost":
                    from services.svg_classification_remeasurement import author_witness_digest
                    unchanged = (payload.get("svg_content") == saved["svg_content"]
                        and payload.get("input_digest") == input_digest
                        and author_witness_digest(payload) == witness["author_receipt_digest"]
                        and payload.get("png_sha256") == witness["png_sha256"]
                        and payload.get("classification_remeasurement_archive") == saved["classification_remeasurement_archive"])
                    failed = deepcopy(payload if unchanged else saved)
                    failed["cache_only_remeasurement"].update(status="failed", error_code=getattr(error, "code", type(error).__name__))
                    await hooks.checkpoint(job, index, payload=failed)
                raise
        if png is not None and saved.get("svg_content") and saved.get("quality"):
            if getattr(ctx, "quality_recovery_input_binding", None):
                from services.generation_core.drivers.svg.quality_recovery import local_evidence_binding
                if (getattr(ctx, "quality_recovery_checkpoint_reuse_eligible", False) is not True
                        or saved.get("quality_recovery_local_evidence") != local_evidence_binding(
                        ctx.quality_recovery_input_binding, saved)):
                    raise ValueError("svg_quality_recovery_local_evidence_stale")
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
            if getattr(ctx, "quality_recovery_input_binding", None) and not (ctx.source_reflow or ctx.raster_reconstruction):
                from services.svg_authoring_profile import read_quality_recovery_plan
                from services.generation_core.drivers.svg.quality_recovery import ScreenRecoveryPending, actual_provider_attempt_count
                if (read_quality_recovery_plan(ctx.snapshot)["page_attempt_limit"] == 3
                        and actual_provider_attempt_count(payload) == 2
                        and not payload.get("provider_failures")
                        and error.code in {"svg_validation_failed", "svg_source_quality_failed", "svg_repair_budget_exhausted"}
                        and not payload.get("quality_recovery_fallback")):
                    return ScreenRecoveryPending(payload, error)
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
    attempt_limit = _provider_attempt_limit(payload, index=index, job=job)
    if getattr(ctx, "header_author_attempt_identity", None) is not None:
        attempt_limit = min(attempt_limit, ctx.snapshot["common_header_design_plan"]["representative_body_attempt_limit"])
    from services.generation_core.drivers.svg.partial_quality_recovery import current_episode_attempt_count
    partial_manifest = getattr(ctx, "partial_recovery_manifest", None)
    partial_known = bool(partial_manifest and index in partial_manifest["known_indices"])
    if getattr(ctx, "quality_recovery_input_binding", None) and payload.get("quality_recovery_fallback", {}).get("status") == "reserved":
        from services.generation_core.drivers.svg.quality_recovery import reserved_recovery_candidate
        reserved_recovery_candidate(ctx, index, content, payload)
        attempt_limit = 3
    while payload.get("svg_content") is not None or (
            current_episode_attempt_count(ctx, payload, index) if partial_known
            else _provider_attempt_count(payload)) < attempt_limit:
        if state.fatal_error is not None:
            raise state.fatal_error
        provider_count = _provider_attempt_count(payload)
        svg = payload.get("svg_content")
        ordinal = provider_count if svg is None else max(0, provider_count - 1)
        if svg is None:
            if getattr(ctx, "quality_recovery_input_binding", None):
                from services.generation_core.drivers.svg.quality_recovery import ensure_recovery_authority
                if current_episode_attempt_count(ctx, payload, index) >= attempt_limit:
                    raise AuthoredStageError(slide_index=index, stage="author",
                        code="svg_repair_budget_exhausted", retryable=False)
                await ensure_recovery_authority(job, hooks)
            provider_ordinal = ordinal + len(payload.get("provider_failures", [])) + 1
            with timing.observe_stage(request, "repair" if ordinal > 0 else "author"):
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
        if getattr(ctx, "quality_recovery_input_binding", None):
            from services.generation_core.drivers.svg.quality_recovery import local_evidence_binding
            payload["quality_recovery_local_evidence"] = local_evidence_binding(
                ctx.quality_recovery_input_binding, payload)
            await hooks.checkpoint(job, index, payload=payload)
            if payload.get("quality_recovery", {}).get("status") == "repair_pending":
                payload["quality_recovery"]["status"] = "candidate_ready"
                await hooks.checkpoint(job, index, payload=payload)
        return render_result
    raise AuthoredStageError(slide_index=index, stage="author",
                            code="svg_repair_budget_exhausted", retryable=False)


def _append_only_checkpoint_marker(payload):
    markers = [payload[k] for k in ("append_only_cache", "append_only_replacement", "append_only_unstarted") if k in payload]
    if len(markers) > 1:
        raise ValueError("svg_timeout_repurchase_attempt_fence_invalid")
    return markers[0] if markers else None


def _provider_attempt_count(payload):
    marker = _append_only_checkpoint_marker(payload)
    if marker is None:
        return _legacy_provider_attempt_count(payload)
    consumed = marker["witness"]["consumed_attempt_count"] if payload.get("append_only_replacement") else 0
    return consumed + sum(a.get("kind") != "local" for a in payload.get("attempts", [])) + len(payload.get("provider_failures", []))


def _provider_attempt_limit(payload, *, index=None, job=None):
    cached_repair = payload.get("append_only_cached_repair")
    if cached_repair:
        lineage = (job.request_payload or {}).get("_svg_recovery_lineage") if job else None
        if cached_repair.get("lineage") != lineage or lineage.get("child_job_id") != str(job.id):
            raise ValueError("svg_recovery_lineage_conflict")
        witness = cached_repair["witness"]
        if witness.get("slide_index") != index or not witness.get("known_quality_repair_approved"):
            raise ValueError("svg_recovery_lineage_conflict")
        return witness["consumed_attempt_count"] + witness["max_additional_author_calls"]
    marker = _append_only_checkpoint_marker(payload)
    if marker is None:
        return _legacy_provider_attempt_limit(payload, index=index, job=job)
    witness, lineage = marker.get("witness", {}), marker.get("lineage", {})
    bound = (getattr(job, "request_payload", None) or {}).get("_svg_recovery_lineage")
    if (job is None or lineage != bound or str(job.id) != lineage.get("child_job_id")
        or str(job.workspace_operation_id) != lineage.get("child_operation_id")
        or witness.get("slide_index") != index or type(witness.get("consumed_attempt_count")) is not int):
        raise ValueError("svg_timeout_repurchase_attempt_fence_invalid")
    consumed = witness["consumed_attempt_count"] if payload.get("append_only_replacement") else 0
    return consumed + witness["max_additional_author_calls"]


async def _remeasure_append_only_cache(ctx, request, presentation_id, index, content, saved, input_digest, job, hooks):
    from utils.hashing import sha256_hex
    marker = saved["append_only_cache"]
    witness = marker["witness"]
    _provider_attempt_limit(saved, index=index, job=job)
    svg = saved.get("svg_content")
    if (not svg or sha256_hex(svg.encode("utf-8")) != witness["source_digest"]
        or _digest(saved.get("attempts", [])) != witness["author_digest"]
        or input_digest != witness["input_digest"]
        or (marker.get("status") != "completed" and _digest(saved.get("nativeDeclarations")) != witness["native_digest"])):
        raise ValueError("svg_recovery_cache_changed")
    if marker.get("status") == "completed" and saved.get("quality", {}).get("passed") is True:
        png = hooks.read_png(saved)
        if png is not None:
            return saved, None if ctx.source_reflow else png
    payload = deepcopy(saved)
    async def guarded_checkpoint(active_job, page_index, *, payload=None):
        if payload is not None:
            original_attempts = saved.get("attempts", [])
            measured_attempts = payload.get("attempts", [])
            def provider_history(attempts):
                return [{k: v for k, v in attempt.items() if k != "quality"} for attempt in attempts]
            if _digest(provider_history(measured_attempts)) != _digest(provider_history(original_attempts)):
                raise ValueError("svg_recovery_cache_changed")
            # The renderer owns current quality; ancestor provider receipts stay frozen.
            payload["attempts"] = deepcopy(original_attempts)
            if (page_index != index or payload.get("input_digest") != input_digest
                or _digest(payload.get("attempts", [])) != witness["author_digest"]
                or payload.get("append_only_cache", {}).get("lineage") != marker["lineage"]):
                raise ValueError("svg_recovery_cache_changed")
            if payload.get("svg_content") != svg:
                if payload.get("svg_content") is None and payload.get("last_valid_svg") == svg:
                    payload["svg_content"] = svg
                else:
                    raise ValueError("svg_recovery_cache_changed")
        return await hooks.checkpoint(active_job, page_index, payload=payload)
    result = await render.attempt_render(ctx=ctx, request=request, presentation_id=presentation_id,
        index=index, content=content, payload=payload, svg=svg, ordinal=max(0, len(saved.get("attempts", []))-1),
        job=job, checkpoint=guarded_checkpoint, async_session_maker=hooks.async_session_maker,
        write_png=hooks.write_png, allow_existing_common_header=True)
    if result is render.RETRY or payload.get("quality", {}).get("passed") is not True:
        payload["append_only_cache"]["status"] = "failed"
        await guarded_checkpoint(job, index, payload=payload)
        if witness.get("known_quality_repair_approved") and witness.get("max_additional_author_calls") == 1:
            payload["append_only_cached_repair"] = payload.pop("append_only_cache")
            await hooks.checkpoint(job, index, payload=payload)
            raise _KnownCachedRepairRequired(payload)
        raise ValueError("svg_source_quality_failed")
    payload["append_only_cache"]["status"] = "completed"
    await guarded_checkpoint(job, index, payload=payload)
    return payload, result[1]


class _KnownCachedRepairRequired(Exception):
    def __init__(self, payload):
        self.payload = payload
        super().__init__("verified_cached_quality_requires_approved_repair")
