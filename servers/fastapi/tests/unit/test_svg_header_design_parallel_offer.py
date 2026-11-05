"""The header design call runs beside the asset offer's plan and icon calls.

The design input of a v3-policy plan no longer carries the representative
page's asset manifest, so the offer (infographic plan, then icon selection) is
started first and settled only before the representative pages author with it.
Plans sealed earlier replay the design input they were sealed with.
"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services import svg_common_header_design_plan as policy
from services import svg_common_header_generation as generation
from services.svg_common_header_design_checkpoint import read_update_header_design
from services.svg_presentation_prepare import GenerationContext

from test_svg_header_capacity_policy import _inputs
from test_svg_header_flow_body_defects import _PNG, _candidate, _page
from test_svg_header_optional_stage_fallback import _selection

# A serial run would wait on these gates until the timeout, so the gates prove
# the overlap without depending on wall-clock speed.
GATE_TIMEOUT_SECONDS = 5
OFFERED = {"icon-0": {"kind": "icon", "policy": {"required": False, "allowOmit": True}}}


def _context(snapshot, pending=None):
    from services.svg_canvas import svg_canvas_for_aspect_ratio

    return GenerationContext(
        source_reflow=False, raster_reconstruction=False, raster_analysis=None, raster_pin=None,
        source_document=None, source_units=None, canonical_assets=False,
        canvas=svg_canvas_for_aspect_ratio("16:9"), physical_kwargs={}, snapshot=snapshot,
        source_font_profile=None, count=1, roles=["content"], manifests=[{}], planned=[{}],
        source_manifest=None, source_pointer=None, asset_context=None, asset_revision=None, service=None,
        semaphore=None, render_semaphore=None, pending_assets=pending, source_asset_counts=[0])


def _offer(events, gates, *, fail=False):
    """A fake offer: the plan call, then the icon call that needs the plan's labels."""
    async def run():
        events.append("plan:start")
        # The plan call only ends once the design call is running.
        await asyncio.wait_for(gates["design_started"].wait(), GATE_TIMEOUT_SECONDS)
        events.append("plan:end")
        events.append("icons:start")
        gates["icons_started"].set()
        await asyncio.sleep(0)
        events.append("icons:end")
        if fail:
            raise RuntimeError("fake offer failure")
        return dict(asset_offer=SimpleNamespace(receipt={"status": "resolved"}), manifests=[deepcopy(OFFERED)],
                    planned=[{"icon-0": object()}], source_manifest=None, source_pointer=None,
                    asset_context=None, asset_revision=None, service=None)
    return run


def _drive(tmp_path, monkeypatch, *, offer_fails=False, design_fails=False):
    from test_authored_checkpoint_service import _checkpoint_database, _running_authored_job
    from models.sql.presentation_generation_job import PresentationGenerationJob

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            request, _plan, snapshot, outline, _canvas = _inputs()
            async with maker() as session:
                row = await session.get(PresentationGenerationJob, job.id)
                row.svg_style_snapshot = deepcopy(snapshot)
                row.authored_outline = outline.model_dump(mode="json")
                session.add(row)
                await session.commit()
            events, seen = [], {}
            gates = {"design_started": asyncio.Event(), "icons_started": asyncio.Event()}
            ctx = _context(snapshot, asyncio.ensure_future(_offer(events, gates, fail=offer_fails)()))
            monkeypatch.setattr(generation, "preflight_header_selector", lambda: {"model": "fixture"})
            monkeypatch.setattr(generation, "_design_input", lambda *args: {"context": "bound"})
            monkeypatch.setattr(generation, "_design_reference_images", lambda *args: [])

            def build(payload, *, plan, stage, ordinal, **kwargs):
                async def execute():
                    events.append(f"{stage}:start")
                    if stage == "design":
                        gates["design_started"].set()
                        # The design call only ends once the icon call has begun.
                        await asyncio.wait_for(gates["icons_started"].wait(), GATE_TIMEOUT_SECONDS)
                        events.append("design:end")
                        if design_fails:
                            raise TimeoutError("fake provider timeout")
                        return {"result": {"candidates": [_candidate("first"), _candidate("second")]}}
                    return _selection(payload)
                return execute

            monkeypatch.setattr(generation, "build_header_call", build)

            async def page_runner(candidate_ctx, request_, presentation_id, outline_, language, job_, index,
                                  state, hooks):
                seen[candidate_ctx.header_author_attempt_identity["candidate_id"]] = (
                    deepcopy(candidate_ctx.manifests), candidate_ctx.asset_offer, candidate_ctx.pending_assets)
                result = _page(True, body_failures=[])
                result["input_digest"] = generation._page_input_digest(candidate_ctx, request_, outline_,
                                                                       language, index)
                await hooks.checkpoint(job_, index, payload=result)
                return result, _PNG

            arguments = dict(checkpoint=AsyncMock(return_value={}), async_session_maker=maker,
                read_png=lambda payload: _PNG, write_png=None, page_runner=page_runner,
                page_hooks_factory=lambda checkpoint, **kwargs: SimpleNamespace(checkpoint=checkpoint),
                state_factory=lambda: None)
            error = result = settled = None
            try:
                result = await generation.prepare_document_header(
                    ctx, request, job.presentation_id, outline, "en", job, **arguments)
                # What the deck does next, before any page authors.
                await result.settle_assets()
                settled = (result.manifests, result.asset_offer)
            except Exception as raised:  # what the deck would see
                error = raised
            document = await read_update_header_design(job, session_maker=maker)
            return SimpleNamespace(result=result, error=error, document=document, events=events,
                                   seen=seen, settled=settled)
        finally:
            await engine.dispose()
    return asyncio.run(scenario())


def test_plan_and_icon_calls_overlap_the_design_call_and_candidates_see_the_offer(tmp_path, monkeypatch):
    run = _drive(tmp_path, monkeypatch)
    events = run.events
    assert run.error is None
    # The design call starts before the plan call ends, and is still running
    # while the plan call has ended and the icon call has begun.
    assert events.index("design:start") < events.index("plan:end")
    assert events.index("plan:end") < events.index("design:end")
    assert events.index("icons:start") < events.index("design:end")
    # The icon call needs the plan's labels, so it never overlaps the plan call.
    assert events.index("plan:end") < events.index("icons:start")
    # Representative pages author with the settled offer, never a pending one.
    assert sorted(run.seen) == ["first", "second"]
    for manifests, offer, pending in run.seen.values():
        assert manifests == [OFFERED] and offer.receipt == {"status": "resolved"} and pending is None
    # Stage order and receipts stay valid: design, representative pages, selection.
    assert events.index("design:end") < events.index("selection:start")
    assert run.document["status"] == "published"
    assert run.document["steps"]["design-1"]["status"] == "succeeded"
    assert run.document["steps"]["selection-1"]["status"] == "succeeded"
    assert run.result.pending_assets is None and run.settled[0] == [OFFERED]


def test_a_failed_offer_does_not_change_the_header_stage(tmp_path, monkeypatch):
    run = _drive(tmp_path, monkeypatch, offer_fails=True)
    # The offer's own error reaches the deck after the paid design step settled;
    # it neither records a fallback nor leaves the design step unsettled.
    assert isinstance(run.error, RuntimeError) and str(run.error) == "fake offer failure"
    assert run.document["steps"]["design-1"]["status"] == "succeeded"
    assert "fallback" not in run.document and run.seen == {}
    assert "design:end" in run.events


def test_a_header_fallback_keeps_the_offer(tmp_path, monkeypatch):
    run = _drive(tmp_path, monkeypatch, design_fails=True)
    assert run.error is None
    assert run.result.snapshot["common_header_selection"]["mode"] == "reduced"
    assert run.document["fallback"]["reason"] == policy.PROVIDER_STEP_FAILED
    # The reduced header never waited for the offer; settling afterwards still delivers it.
    assert run.seen == {} and run.settled[0] == [OFFERED]
    assert run.settled[1].receipt == {"status": "resolved"}


def _design_context(fallback_policy):
    request, plan, snapshot, outline, _canvas = _inputs(fallback_policy=fallback_policy)
    ctx = _context(snapshot)
    ctx.manifests = [{"icon-0": {"kind": "icon", "data_url": "data:x", "name": "n"}}]
    return request, plan, outline, ctx


def test_new_plans_omit_the_manifest_and_earlier_plans_keep_their_original_input():
    for fallback_policy, carries in ((None, False), (policy._POLICY, True), (policy._POLICY_V1, True)):
        request, plan, outline, ctx = _design_context(fallback_policy)
        representative = generation._design_input(ctx, request, outline, plan, 0)["representative"]
        assert representative["source"] == outline.slides[0].content
        if carries:
            # The pre-v3 shape, key order included: the manifest sits between source_ref and source.
            assert list(representative) == ["page_id", "source_ref", "required_assets", "source"]
            assert representative["required_assets"] == {"icon-0": {"kind": "icon", "name": "n"}}
        else:
            assert list(representative) == ["page_id", "source_ref", "source"]


def test_a_decoupled_representative_ranks_by_pre_offer_source_images():
    _request, plan, outline, ctx = _design_context(None)
    outline.slides.append(outline.slides[0].model_copy(update={"page_id": "second"}))
    ctx.count, ctx.roles = 2, ["content", "content"]
    # The offer rewrites ``planned`` and ``manifests`` while the design runs.
    ctx.planned = [{"offer-icon": object()}, {"offer-icon": object(), "extra": object()}]
    ctx.manifests = [{}, {}]
    ctx.source_asset_counts = [2, 1]
    assert generation._representative_index(ctx, outline, False, None, plan) == 0
    _request, legacy, _outline, _ctx = _design_context(policy._POLICY)
    ctx.manifests = [{}, {"a": 1, "b": 2, "c": 3}]
    assert generation._representative_index(ctx, outline, False, None, legacy) == 1
