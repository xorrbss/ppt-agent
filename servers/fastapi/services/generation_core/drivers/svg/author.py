"""Everything that produces an SVG string for one SVG-driver page: the
pre-loop raster-reconstruction synthesis, the local template-layout attempt,
the ``author_source_svg_layout`` / ``author_slide_svg`` dispatch, source-image
and AssetOffer slots (planned illustrations/icons, see ``asset_slots.py``), and
inflight/ordinal bookkeeping around a single author attempt.

Split out of ``services/svg_presentation_generate.py`` (P0 split, see
``docs/generation-core-architecture-20260925.md``); ``collect.py`` owns the
attempt ``while`` loop and calls :func:`attempt_author` once per author
attempt, and :func:`raster_reconstruct_svg` once before that loop when the
deck is a raster-reconstruction deck.
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from contextvars import copy_context
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re

from services.svg_templates.chapters import page_chapter
from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg.attempt_cause import cause, dispatch_cause
from services.generation_core.drivers.svg.native_declarations import bind_native_declarations
from services.provider_usage_instrumentation import provider_usage_scope
from services.svg_presentation_checkpoint import _provider_attempt_count
from utils.ppt_writing_prompt import WRITING_POLICY_ID
from services.authored_batch_policy import OVERLOAD_COOLDOWN_MAX_SECONDS
from utils.provider_overload import ProviderOutcomeError, is_page_recoverable_overload

_LOCAL_ESCALATION_REASONS = {
    "layout_candidate_unavailable", "layout_overflow_unresolved",
    "layout_collision_unresolved", "layout_search_budget_exhausted",
}
_LOGGER = logging.getLogger(__name__)


async def raster_reconstruct_svg(raster_analysis, raster_pin, request, index):
    """Pre-loop SVG synthesis for the raster-reconstruction branch."""
    from services.raster_redesign_generation import (
        raster_render_advisories_for_page, render_raster_reconstruction_page_svg,
    )

    svg = render_raster_reconstruction_page_svg(
        raster_analysis, raster_pin, index + 1,
        style_id=getattr(request, "authored_style", None) or "screenshot-blue",
        instructions=request.instructions,
    )
    advisories = raster_render_advisories_for_page(raster_analysis, index + 1, svg)
    return svg, advisories


def free_raster_redesign(ctx, index):
    """New snapshots compose approved redesign pages; historical replay stays local."""
    return bool(ctx.raster_reconstruction and ctx.snapshot.get("composition_mode") == "free-svg"
                and ctx.raster_analysis.pages[index].render_mode == "redesign")


def optional_vector_kinds(request, ctx) -> frozenset:
    """Offered vector slot kinds a page may leave out without failing quality.

    A model-drawn picture (``illustration``, ``spot``) is always optional page
    support. An icon is optional on the same terms exactly when the author's
    rules say so: the new-copy writing policy calls every illustration, spot
    and icon slot optional generated support to omit when unsupported or
    distracting. A deck without that policy (or a source page) was told to
    place every offered slot, so an unused icon still fails its page there.
    """

    if getattr(request, "writing_policy", None) == WRITING_POLICY_ID and not getattr(ctx, "source_reflow", False):
        return frozenset({"illustration", "spot", "icon"})
    return frozenset({"illustration", "spot"})


@dataclass
class AuthorAttempt:
    """Result of one author attempt; ``payload`` is mutated in place."""
    svg: str | None
    retry: bool = False
    #: Overload cooldown the caller must wait out after releasing its provider
    #: permit (0 = retry immediately).
    cooldown_seconds: float = 0.0


_DISPATCH_STOPPED = {"streamStage": "before_stream", "failureKind": "dispatch_stopped"}

# Deck-level cap on page re-dispatches triggered by a transport timeout. A
# timeout after the request was sent may already have been billed, so each
# replay is a possible duplicate purchase; past the cap the timeout is terminal.
MAX_TIMEOUT_RETRIES_PER_RUN = 3
_CALL_TIMEOUT_CODE = "public_provider_call_timeout"


def _reserve_timeout_retry(state) -> int | None:
    """Take one run-wide timeout retry slot; ``None`` when the cap is spent.

    Synchronous on purpose: the cap check and the increment share one region,
    so concurrent pages can never both take the last slot. A run state without
    the counter starts at zero.
    """
    used = getattr(state, "timeout_retries", 0)
    if used >= MAX_TIMEOUT_RETRIES_PER_RUN:
        return None
    state.timeout_retries = used + 1
    return used + 1


class PageDispatchFrozen(ProviderOutcomeError):
    """A sibling's page-local failure froze dispatch before this page's first approval.

    Nothing was dispatched for it and no failure record is written; the page
    stays unstarted. Stopped before admission it takes no reservation and
    writes nothing. Stopped at ``before_dispatch`` (inside admission, after the
    reservation) it is the existing ``public_provider_dispatch_stopped``
    before-stream outcome with its sealed ``dispatch.stopped`` witness, the
    same proof the terminal-drain stop writes. ``collect.tracked_page`` turns
    it into a marker either way.

    ``pre_sdk_dispatch_stop`` tells provider admission that no SDK request was
    sent, so the stop leaves provider health alone. Only stops raised from
    ``before_dispatch`` carry it; stops raised inside the SDK call do not.
    """

    def __init__(self, index):
        super().__init__("public_provider_dispatch_stopped", recovery_allowed=False,
                         diagnostics=_DISPATCH_STOPPED)
        self.slide_index = index
        self.pre_sdk_dispatch_stop = True


def _approve_dispatch(state, index) -> bool:
    """Approve one provider dispatch of ``index`` unless dispatch is frozen.

    Synchronous on purpose: the freeze check and the approval record share one
    region, so a freeze set by a sibling can never fall between them. A page
    approved before the freeze keeps its approval for its remaining attempts.
    """
    approved = getattr(state, "dispatch_approved", None)
    if approved is None:
        return True
    if getattr(state, "dispatch_frozen", False):
        return index in approved
    approved.add(index)
    return True


async def attempt_author(*, ctx, request, index, content, role, count, payload, ordinal,
                         provider_ordinal, job, state, checkpoint, async_session_maker,
                         receipt_attempt_ordinal=None) -> AuthorAttempt:
    # ``provider_ordinal`` keys this call's sealed terminal record, inflight
    # witness and diagnostics. ``receipt_attempt_ordinal`` (default: the same)
    # is the attempt index the author sends to admission (retry class,
    # semantic slot, receipt); a page's sealed ``dispatch.stopped`` keys can
    # move the first past the second, never the reverse.
    if receipt_attempt_ordinal is None:
        receipt_attempt_ordinal = provider_ordinal
    if payload.get("cached_author_render"):
        raise AuthoredStageError(slide_index=index, stage="author",
                                 code="svg_cached_author_render_provider_forbidden", retryable=False)
    if (payload.get("partial_recovery") or {}).get("category") == "known":
        from services.generation_core.drivers.svg.partial_quality_recovery import current_quality_repair_authorized
        decision = (payload.get("quality_recovery") or {}).get("decision") or {}
        if ((payload.get("quality_recovery") or {}).get("status") != "repair_pending"
                or payload.get("correction_mode") != decision.get("repair_scope")
                or not await current_quality_repair_authorized(ctx, payload, index, job, checkpoint)):
            raise AuthoredStageError(slide_index=index, stage="author",
                                     code="svg_partial_recovery_cached_provider_forbidden", retryable=False)
    from services.document_canvas_generation import source_restyle_font_floor
    from services.generation_core.authoring.author_slide_svg import author_slide_svg
    from utils.provider_overload import ProviderOutcomeError, safe_provider_diagnostics

    def raise_if_fatal():
        if state.fatal_error is not None:
            raise state.fatal_error

    offered_images = ctx.manifests[index]
    source_floor = source_restyle_font_floor(request.instructions, getattr(request, "_document_canvas", None))
    if ctx.snapshot.get("readability_policy") is not None:
        from services.document_canvas_generation import svg_quality_font_floor

        source_floor = max(source_floor, svg_quality_font_floor(ctx.snapshot["readability_policy"]))
    if ctx.canonical_assets:
        from services.svg_generation_assets import author_images
        async with async_session_maker() as asset_session:
            offered_images = await author_images(asset_session, ctx.service, context=ctx.asset_context,
                manifest=ctx.source_manifest, page_index=index)
    offer = getattr(ctx, "asset_offer", None)
    vector_icons = offer.vector[index] if offer is not None else {}
    local_authored = None
    hint = payload.get("repair_hint")
    from services.svg_authoring_profile import read_quality_recovery_policy
    recovery_policy = read_quality_recovery_policy(ctx.snapshot)
    correction_mode = payload.get("correction_mode") if recovery_policy else None
    recovery_kwargs = {}
    if correction_mode:
        recovery_kwargs["correction_mode"] = correction_mode
        previous_render = getattr(ctx, "quality_recovery_previous_renders", {}).get(index)
        if correction_mode == "validated_svg_adaptation":
            from services.generation_core.drivers.svg.quality_recovery import (
                reserved_recovery_candidate, ensure_recovery_authority, refresh_recovery_source_authority,
            )
            from utils.authored_style_svg_recovery import read_validated_recovery_png
            from types import SimpleNamespace
            from utils.hashing import sha256_hex
            authority_hooks = SimpleNamespace(checkpoint=checkpoint,
                async_session_maker=async_session_maker, recovery_snapshot=ctx.snapshot)
            await ensure_recovery_authority(job, authority_hooks)
            await refresh_recovery_source_authority(ctx, authority_hooks)
            candidate = reserved_recovery_candidate(ctx, index, content, payload)
            png = read_validated_recovery_png(candidate,
                allowed_source_evidence=ctx.quality_recovery_source_evidence)
            if (not png or len(png) > 8 * 1024 * 1024 or not png.startswith(b"\x89PNG\r\n\x1a\n")
                    or "sha256:" + sha256_hex(png) != candidate["png_digest"]
                    or payload.get("last_valid_svg") != candidate["svg_content"]):
                raise ValueError("svg_validated_recovery_render_binding_invalid")
            previous_render = (png, candidate["png_digest"].removeprefix("sha256:"),
                candidate["svg_digest"].removeprefix("sha256:"))
        if previous_render is None and payload.get("quality_recovery", {}).get("prior_candidate"):
            from services.svg_presentation_checkpoint import _read_png
            from services.generation_core.drivers.svg.quality_recovery import ensure_recovery_authority
            from types import SimpleNamespace
            prior = payload["quality_recovery"]["prior_candidate"]
            await ensure_recovery_authority(job, SimpleNamespace(checkpoint=checkpoint,
                async_session_maker=async_session_maker, recovery_snapshot=ctx.snapshot))
            previous_render = (_read_png(prior["evidence"]),
                prior["evidence"].get("png_sha256"), prior.get("svg_digest"))
        if previous_render:
            png, png_digest, svg_digest = previous_render
            from utils.hashing import sha256_hex
            previous_svg = payload.get("last_valid_svg")
            if (not png or sha256_hex(png) != png_digest or not previous_svg
                    or sha256_hex(previous_svg.encode()) != svg_digest):
                raise ValueError("svg_correction_previous_render_binding_invalid")
            recovery_kwargs.update(previous_render_png=png, previous_render_png_digest="sha256:" + png_digest)
    intent_kwargs = {}
    author_identity = getattr(ctx, "header_author_attempt_identity", None)
    if author_identity is not None:
        intent_kwargs["author_attempt_identity"] = author_identity
    intents = getattr(ctx, "visual_intents", None)
    if isinstance(intents, list) and index < len(intents):
        from services.generation_core.drivers.svg.visual_intent import VISUAL_INTENT_VERSION

        intent = intents[index]
        if isinstance(intent, dict) and intent.get("contract_version") == VISUAL_INTENT_VERSION:
            intent_kwargs["visual_intent"] = intent
            payload["visual_intent"] = intent
    if not (ctx.source_reflow or ctx.raster_reconstruction):
        from services.generation_core.drivers.svg.native_requirements import (
            requested_native_data, requirements_revision)

        requirements = requested_native_data(content, requirements_revision(ctx.snapshot))
        if requirements:
            payload["nativeRequirements"] = requirements
        else:
            payload.pop("nativeRequirements", None)
    whole_page_source = ctx.snapshot.get("source_authoring_contract") == {"version": "svg-source-whole-page/v1"}
    free_source_composition = whole_page_source or (ctx.snapshot.get("source_template") or {}).get("composition_mode") == "free-svg"
    if ctx.source_reflow and not free_source_composition and not payload.get("local_layout_attempted"):
        from services.svg_source_template_layout import try_template_source_layout
        page_task = asyncio.current_task()

        def check_local_cancelled(page_task=page_task):
            if state.fatal_error is not None or (page_task is not None and page_task.cancelling()):
                raise asyncio.CancelledError()

        image_slots = {candidate.occurrence_id: slot for slot, candidate in ctx.planned[index].items()}
        min_font_size_px = source_floor
        try:
            local_result = await asyncio.to_thread(
                try_template_source_layout, ctx.source_document, index, ctx.snapshot,
                source_images=offered_images,
                image_slots=image_slots,
                min_font_size_px=min_font_size_px,
                width=ctx.canvas.width_px, height=ctx.canvas.height_px,
                check_cancelled=check_local_cancelled,
                _validated_pages=ctx.source_units,
            )
        except ValueError as error:
            code = str(error).split(":", 1)[0]
            if not re.fullmatch(r"svg_[a-z_]+", code):
                code = "svg_source_local_layout_failed"
            raise AuthoredStageError(slide_index=index, stage="author",
                code=code, retryable=False) from error
        raise_if_fatal()
        payload["local_layout_attempted"] = True
        if isinstance(local_result, dict):
            if local_result.get("escalation_reason") not in _LOCAL_ESCALATION_REASONS:
                raise ValueError("svg_source_local_escalation_invalid")
            if local_result.get("model_layout_allowed") is not True:
                raise AuthoredStageError(slide_index=index, stage="author",
                    code="svg_source_template_layout_unavailable", retryable=False)
            payload["local_layout"] = local_result
            hint = json.dumps(local_result, ensure_ascii=False)[:3000]
            payload["repair_hint"] = hint
        else:
            local_authored = local_result
            payload["local_layout"] = {"model_calls": 0, "status": "resolved"}
        await checkpoint(job, index, payload=payload)

    # A frozen, never-approved page stops before provider admission, so it
    # takes no reservation; ``before_dispatch`` below stays the authoritative
    # check for a freeze that lands while admission is awaited.
    if (local_authored is None and getattr(state, "dispatch_frozen", False)
            and index not in getattr(state, "dispatch_approved", ())):
        raise PageDispatchFrozen(index)
    observer_ready = False
    pending_attempt = None
    attempt_cause = None

    async def before_dispatch(provider_ordinal=provider_ordinal):
        nonlocal observer_ready, pending_attempt, attempt_cause
        # Dispatch freeze first, after the permit wait and before any inflight
        # witness or the drain check: no dispatch is approved after a freeze
        # unless this page was approved before it.
        if not _approve_dispatch(state, index):
            stopped = PageDispatchFrozen(index)
            if job is not None:
                # Admission already reserved this call. Seal the existing
                # before-stream witness (inflight, then ``dispatch.stopped``)
                # so the settled reservation is not an unproven UNKNOWN; with
                # no job there is no usage scope and so no reservation.
                payload["inflight"] = provider_ordinal
                await checkpoint(job, index, payload=payload)
                await save_event({"terminal_event": "dispatch.stopped",
                                  "diagnostics": dict(_DISPATCH_STOPPED)})
            raise stopped
        # A checkpoint is a recovery witness, not a separate
        # purchase ledger. Admission still owns reservation.
        if getattr(state, "terminal_drain_enabled", False) and state.fatal_error is not None:
            if job is not None:
                payload["inflight"] = provider_ordinal
                await checkpoint(job, index, payload=payload)
                await save_event({"terminal_event": "dispatch.stopped", "diagnostics": {
                    "streamStage": "before_stream", "failureKind": "dispatch_stopped"}})
            stopped = ProviderOutcomeError("public_provider_dispatch_stopped", recovery_allowed=False,
                diagnostics={"streamStage": "before_stream", "failureKind": "dispatch_stopped"})
            stopped.pre_sdk_dispatch_stop = True
            raise stopped
        raise_if_fatal()
        # This is the one dispatch boundary for plain, source and raster SVG.
        # Append only after prompt construction (which reads attempts[-1]) and
        # dispatch approval; a stopped admission is not a provider attempt.
        attempt_cause = dispatch_cause(payload, ordinal=ordinal)
        if attempt_cause["code"] == "unclassified":
            _LOGGER.warning("SVG attempt cause unclassified stage=%s page_index=%s",
                            "author" if ordinal == 0 else "repair", index)
        pending_attempt = {"kind": "provider", "status": "pending",
            "attempt_cause": dict(attempt_cause), "receipt": {
                "attempt_ordinal": receipt_attempt_ordinal,
                "terminal_ordinal": provider_ordinal, "attempt_cause": dict(attempt_cause)}}
        payload.setdefault("attempts", []).append(pending_attempt)
        payload.pop("next_attempt_cause", None)
        payload["inflight"] = provider_ordinal
        await checkpoint(job, index, payload=payload)
        observer_ready = True

    observer_scope = nullcontext()
    if job is not None:
        # The sealed terminal record is written with or without terminal drain:
        # a dispatch-freeze stop needs it too.
        from services.svg_presentation_checkpoint import record_svg_provider_event

        async def save_event(event):
            record = {**event, "schema": "svg-provider-terminal/v1", "ordinal": provider_ordinal,
                "job_id": str(job.id), "operation_id": (str(job.workspace_operation_id)
                    if getattr(job, "workspace_operation_id", None) is not None else None),
                "attempt_token": job.attempt_token,
                "fencing_token": getattr(job, "_workspace_fencing_token", None),
                "input_digest": payload.get("input_digest")}
            sealed = await record_svg_provider_event(job, index, record)
            payload.setdefault("provider_terminal_responses", {})[str(provider_ordinal)] = sealed
            if sealed.get("terminal_event") in {"response.completed", "dispatch.stopped"}:
                payload["inflight"] = None

    if getattr(state, "terminal_drain_enabled", False) and job is not None:
        from utils.codex_stream_adapter import observe_codex_provider_events
        loop, owner_context = asyncio.get_running_loop(), copy_context()

        async def dispatch_preflight():
            from types import SimpleNamespace
            from services.generation_core.drivers.svg.quality_recovery import ensure_recovery_authority
            await ensure_recovery_authority(job, SimpleNamespace(checkpoint=checkpoint,
                async_session_maker=async_session_maker, recovery_snapshot=ctx.snapshot))
            if state.fatal_error is not None:
                await save_event({"terminal_event": "dispatch.stopped", "diagnostics": {
                    "streamStage": "before_stream", "failureKind": "dispatch_stopped"}})
                raise ProviderOutcomeError("public_provider_dispatch_stopped", recovery_allowed=False,
                    diagnostics={"streamStage": "before_stream", "failureKind": "dispatch_stopped"})

        def observe_event(event):
            if not observer_ready:
                return
            deadline = getattr(state, "hard_deadline_at", None)
            remaining = 30.0 if deadline is None else max(0.0,
                (deadline - datetime.now(timezone.utc)).total_seconds())
            if remaining <= 0:
                raise AuthoredStageError(slide_index=index, stage="author", retryable=False,
                    code="svg_provider_completed_checkpoint_authority_expired"
                        if event.get("terminal_event") == "response.completed" else "svg_provider_checkpoint_authority_expired")
            preflight = event.get("terminal_event") == "dispatch_ready"
            future = owner_context.run(lambda: asyncio.run_coroutine_threadsafe(
                dispatch_preflight() if preflight else save_event(event), loop))
            try:
                from utils.provider_overload import current_provider_call_remaining_seconds
                provider_remaining = current_provider_call_remaining_seconds()
                wait_remaining = 30.0 if provider_remaining is None else provider_remaining
                if deadline is not None:
                    wait_remaining = min(wait_remaining, remaining)
                future.result(timeout=wait_remaining)
            except BaseException as error:
                future.cancel()
                from services.generation_core.drivers.svg.partial_quality_recovery import is_current_provider_control_error
                if is_current_provider_control_error(error):
                    raise
                if preflight and isinstance(error, Exception):
                    raise ProviderOutcomeError("public_provider_dispatch_stopped", recovery_allowed=False,
                        diagnostics=safe_provider_diagnostics(error, diagnostics={
                            "streamStage": "before_stream", "failureKind": "dispatch_stopped"})) from error
                raise

        from services.generation_core.drivers.svg.partial_quality_recovery import is_current_provider_control_error

        observer_scope = observe_codex_provider_events(
            observe_event, is_control_error=is_current_provider_control_error,
        )

    with provider_usage_scope(job_id=job.id if job else None,
                              operation="authored_generation",
                              stage="author" if ordinal == 0 else "repair",
                              slide_index=index,
                              attempt_number=job.attempt_number if job else 0,
                              public_only=bool(getattr(ctx, "header_operation_public_only", False)),
                              quality_repair_context=(ctx if
                                  index in (getattr(ctx, "partial_recovery_manifest", None) or {}).get("known_indices", [])
                                  and (payload.get("quality_recovery") or {}).get("status") == "repair_pending" else None),
                              **({"workspace_operation_id": job.workspace_operation_id}
                                 if job is not None and (getattr(job, "request_payload", None) or {}).get("_svg_partial_recovery")
                                 else {})), observer_scope:
        try:
            if local_authored is not None:
                authored = local_authored
            elif free_raster_redesign(ctx, index):
                from services.generation_core.authoring.author_raster_svg_layout import author_raster_svg_layout

                authored = await author_raster_svg_layout(ctx.raster_analysis, ctx.raster_pin, index, ctx.snapshot,
                    instructions=request.instructions, correction_hint=hint,
                    receipt_attempt_ordinal=receipt_attempt_ordinal, before_dispatch=before_dispatch,
                    header_page=(getattr(ctx, "header_pages", None) or [None] * count)[index],
                    **intent_kwargs)
            elif ctx.source_reflow:
                from services.generation_core.authoring.author_source_svg_layout import (
                    author_source_svg_layout, source_binding_correction_hint,
                )
                image_slots = {candidate.occurrence_id: slot
                               for slot, candidate in ctx.planned[index].items()}
                source_hint = hint
                if whole_page_source and hint and payload.get("attempts"):
                    from services.svg_author_output_view import snapshot_output_view
                    source_hint = source_binding_correction_hint(ctx.source_units[index], image_slots,
                        payload["attempts"][-1].get("raw_response"), hint,
                        output_view=snapshot_output_view(ctx.snapshot))
                authored = await author_source_svg_layout(
                    ctx.source_document, index, ctx.snapshot,
                    source_images=offered_images,
                    image_slots=image_slots,
                    min_font_size_px=source_floor,
                    width=ctx.canvas.width_px, height=ctx.canvas.height_px,
                    instructions=request.instructions, correction_hint=source_hint,
                    writing_policy=getattr(request, "writing_policy", None),
                    receipt_attempt_ordinal=receipt_attempt_ordinal,
                    before_dispatch=before_dispatch,
                    _validated_pages=ctx.source_units,
                    header_page=(getattr(ctx, "header_pages", None) or [None] * count)[index],
                    **({"previous_svg": payload.get("last_valid_svg")} if whole_page_source and hint else {}),
                    **recovery_kwargs,
                    **intent_kwargs,
                )
            else:
                repair_kwargs = {}
                repair_kwargs.update(recovery_kwargs)
                if hint:
                    from services.svg_authoring_profile import behaviour_profile, read_authoring_profile

                    profile = behaviour_profile(read_authoring_profile(ctx.snapshot))
                    if profile and profile["version"] == 8:
                        previous_svg = payload.get("last_valid_svg") or payload.get("svg_content")
                        if previous_svg:
                            repair_kwargs["previous_svg"] = previous_svg
                authored = await author_slide_svg(
                    content=content, style_snapshot=ctx.snapshot, role=role,
                    index=index, n=count, instructions=request.instructions,
                    header_page=(getattr(ctx, "header_pages", None) or [None] * count)[index],
                    writing_policy=getattr(request, "writing_policy", None),
                    language=getattr(request, "language", None),
                    correction_hint=hint,
                    template_chapter=page_chapter(ctx, index),
                    receipt_attempt_ordinal=receipt_attempt_ordinal,
                    source_images={**offered_images, **vector_icons},
                    before_dispatch=before_dispatch,
                    canonical_source_images=ctx.canonical_assets,
                    **ctx.physical_kwargs,
                    **intent_kwargs,
                    **repair_kwargs,
                )
        except ProviderOutcomeError as error:
            # Transport failures already have their own purchase-count record.
            # Keep its cause in diagnostics without counting it twice.
            if pending_attempt is not None:
                payload["attempts"].remove(pending_attempt)
            diagnostics = safe_provider_diagnostics(error)
            payload.setdefault("provider_diagnostics", []).append({
                "ordinal": provider_ordinal, "code": error.code,
                "diagnostics": diagnostics,
                **({"attempt_cause": dict(attempt_cause),
                    "receipt": dict(pending_attempt["receipt"])} if pending_attempt is not None else {}),
            })
            if not is_page_recoverable_overload(error):
                await checkpoint(job, index, payload=payload)
                raise
            failure = {"ordinal": provider_ordinal, "code": error.code}
            # Only a re-dispatch spends the run's timeout cap: restoring the last
            # valid SVG buys nothing, and the page's second failure raises anyway.
            # Past the cap the timeout stays terminal, exactly as before.
            if (error.code == _CALL_TIMEOUT_CODE and not payload.get("last_valid_svg")
                    and len(payload.get("provider_failures") or ()) + 1 < 2):
                run_timeout_retry = _reserve_timeout_retry(state)
                if run_timeout_retry is None:
                    await checkpoint(job, index, payload=payload)
                    raise
                failure["run_timeout_retry"] = run_timeout_retry
            failures = payload.setdefault("provider_failures", [])
            failures.append(failure)
            payload["next_attempt_cause"] = cause("provider_retry:" + error.code)
            payload["inflight"] = None
            if payload.get("last_valid_svg"):
                payload["svg_content"] = payload["last_valid_svg"]
                payload["repair_unavailable"] = True
                await checkpoint(job, index, payload=payload)
                return AuthorAttempt(svg=None, retry=True)
            await checkpoint(job, index, payload=payload)
            if len(failures) >= 2:
                raise
            # The caller sleeps this outside its provider permit (collect.py).
            # A provider Retry-After is honoured up to the overload ceiling;
            # otherwise the default backoff keeps its 30-60 s window.
            if getattr(error, "cooldown_from_retry_after", False) and error.cooldown_seconds is not None:
                cooldown = min(OVERLOAD_COOLDOWN_MAX_SECONDS, max(0., error.cooldown_seconds))
            else:
                cooldown = min(60., max(30., error.cooldown_seconds or 30.))
            deck_cooldown = getattr(ctx, "overload_cooldown", None)
            if deck_cooldown is not None:
                deck_cooldown.register(cooldown)
            return AuthorAttempt(svg=None, retry=True, cooldown_seconds=cooldown)
        except Exception as error:
            from services.generation_core.drivers.svg.partial_quality_recovery import is_current_provider_control_error
            if getattr(state, "terminal_drain_enabled", False) and is_current_provider_control_error(error):
                raise
            terminal = (payload.get("provider_terminal_responses") or {}).get(str(provider_ordinal)) or {}
            if terminal.get("terminal_event") != "response.completed" or not terminal.get("receipt_digest"):
                raise
            known = ProviderOutcomeError("public_provider_postresponse_failed", recovery_allowed=False,
                diagnostics=safe_provider_diagnostics(error, diagnostics={
                    "terminalEvent": "response.completed", "streamStage": "post_response",
                    "statusOrigin": "local_stream", "failureKind": "local_postresponse",
                    "responseId": terminal.get("response_id")}))
            payload.setdefault("provider_diagnostics", []).append({"ordinal": provider_ordinal,
                "code": known.code, "diagnostics": known.diagnostics})
            await checkpoint(job, index, payload=payload)
            raise known from error
    if not getattr(state, "terminal_drain_enabled", False):
        raise_if_fatal()
    terminal = (payload.get("provider_terminal_responses") or {}).get(str(provider_ordinal)) or {}
    if terminal.get("terminal_event") == "response.completed":
        from utils.hashing import sha256_hex
        if (not terminal.get("receipt_digest")
                or terminal.get("output_digest") != sha256_hex(authored.raw_response.encode("utf-8"))):
            raise AuthoredStageError(slide_index=index, stage="author",
                code="svg_provider_terminal_output_mismatch", retryable=False)
    svg = authored.svg_content
    template_redesign = (ctx.snapshot.get("svg_template") or {}).get("mode") == "template-redesign"
    if not (ctx.source_reflow or ctx.raster_reconstruction or template_redesign):
        from services.svg_default_fonts import normalize_model_font_families

        svg, font_advisories = normalize_model_font_families(svg)
        if font_advisories:
            payload["fontAdvisories"] = font_advisories
        else:
            payload.pop("fontAdvisories", None)
    elif template_redesign:
        # The admitted frame owns exact font bytes/faces; composition verifies them.
        payload.pop("fontAdvisories", None)
    receipt = authored.receipt
    if local_authored is None:
        if pending_attempt is None:
            # Defensive compatibility for an adapter that omitted its hook;
            # cause instrumentation itself must never fail a generation.
            attempt_cause = cause("unclassified")
            _LOGGER.warning("SVG attempt cause unclassified stage=%s page_index=%s",
                            "author" if ordinal == 0 else "repair", index)
        receipt = {**receipt, "attempt_cause": dict(attempt_cause)}
    if local_authored is None and provider_ordinal != receipt_attempt_ordinal:
        # Sealed ``dispatch.stopped`` records moved this call's terminal
        # ordinal past its attempt index; recovery proves the response by it.
        receipt = {**receipt, "terminal_ordinal": provider_ordinal}
    record = {"raw_response": authored.raw_response, "receipt": receipt,
              "kind": "local" if local_authored is not None else "provider"}
    if pending_attempt is not None:
        pending_attempt.clear()
        pending_attempt.update(record, attempt_cause=dict(attempt_cause))
    else:
        if local_authored is None:
            record["attempt_cause"] = dict(attempt_cause)
        payload["attempts"].append(record)
    payload["model_attempt_count"] = _provider_attempt_count(payload)
    payload["inflight"] = None
    payload["svg_content"] = svg
    if getattr(ctx, "header_author_attempt_identity", None) is not None:
        payload["author_body_svg"] = svg
        payload["header_author_attempt_identity"] = dict(ctx.header_author_attempt_identity)
    # Declarations are bound to the digest of this exact drawing; any other SVG
    # this page ends with (correction, restore, fallback) does not match it.
    bind_native_declarations(payload, svg, authored.native_declarations)
    if offer is not None and offer.slots[index]:
        # Per-page AssetOffer receipt; raster slots are also accounted for by
        # the render step's sourceImages check, vector icons only here.
        optional_kinds = optional_vector_kinds(request, ctx)
        drawings = sorted(slot for slot, meta in offer.slots[index].items()
                          if slot in vector_icons and meta.get("kind") in optional_kinds)
        payload["assetOffer"] = {"slots": offer.slots[index], "vectorIcons": {
            "offered": sorted(vector_icons), "used": authored.receipt.get("vector_icons_used", []),
            **({"optional": drawings} if drawings else {})}}
    if ctx.source_reflow:
        payload["source_preservation"] = authored.receipt.get("source_preservation")
        if payload["source_preservation"] is not None:
            payload["source_preservation"]["font_profile"] = ctx.source_font_profile
    if free_raster_redesign(ctx, index):
        payload["rasterCompositionMode"] = "free-svg"
        payload["rasterContentLedger"] = authored.receipt.get("raster_content_ledger")
        payload["rasterRegionBindings"] = authored.receipt.get("raster_region_bindings")
    await checkpoint(job, index, payload=payload)
    if getattr(state, "terminal_drain_enabled", False):
        raise_if_fatal()
    return AuthorAttempt(svg=svg, retry=False)
