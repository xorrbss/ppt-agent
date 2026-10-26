"""Dispatch diagnostics preserve isolated render checkpoint contracts."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from services import svg_common_header_generation as header, svg_presentation_checkpoint as checkpoints
from services.generation_core.drivers.svg import collect, render
from tests.unit.test_svg_generation_timing_receipt import _ctx, _install_harness, _request
from tests.unit.test_authored_checkpoint_service import _checkpoint_database, _running_authored_job
from tests.unit.test_svg_checkpoint_recovery_contract import _pages, _seed_pages


@pytest.mark.parametrize("version", [2, 3])
def test_real_header_representatives_keep_payload_only_checkpoint(monkeypatch, version):
    _install_harness(monkeypatch)
    ctx = _ctx()
    ctx.count = 1
    plan = {"schema": f"svg-common-header-design-plan/v{version}",
            "plan_digest": "plan", "input_digest": "input", "max_render_seconds": 5,
            "representative_body_attempt_limit": 2}
    ctx.snapshot = {"common_header_design_plan": plan}
    outline = SimpleNamespace(slides=[SimpleNamespace(page_id="page-1", content="page")])
    request = _request()
    candidate_writes = []

    async def candidate_storage(active_job, page_index, *, identity, payload=None, **storage):
        if payload is not None:
            candidate_writes.append((deepcopy(identity), deepcopy(payload)))
        return {}

    monkeypatch.setattr(header, "candidate_page_checkpoint", candidate_storage)
    monkeypatch.setattr(header, "read_update_header_design", AsyncMock(return_value={}))
    monkeypatch.setattr(header, "preflight_header_selector", lambda: {"fixture": True})
    monkeypatch.setattr(header, "_header_values", lambda *_: [])
    monkeypatch.setattr(header, "_design_input", lambda *_: {})
    monkeypatch.setattr(header, "_design_reference_images", lambda *_: [])
    monkeypatch.setattr(header, "_paid_step", AsyncMock(return_value={}))
    monkeypatch.setattr(header, "_checked_candidates", lambda *_: [
        {"candidate_id": "a"}, {"candidate_id": "b"}])
    monkeypatch.setattr(header, "header_snapshot_base", lambda snapshot: snapshot)
    monkeypatch.setattr(header, "prepare_common_header_v2", lambda *args, **kwargs: (
        {"digest": "header"}, None))
    monkeypatch.setattr(header, "_snapshot", lambda base, contract: {**base, "common_header": contract})
    monkeypatch.setattr("services.svg_master_furniture.master_furniture_receipt", lambda *args, **kwargs: None)

    class RepresentativesMeasured(Exception):
        pass

    def selected_input(_ctx, _request, _outline, _plan, _document, _candidates, pages, *_args):
        assert set(pages) == {"a", "b"}
        assert all(page["eligible"] for page in pages.values())
        raise RepresentativesMeasured

    monkeypatch.setattr(header, "_selection_input", selected_input)

    async def page_runner(candidate_ctx, active_request, presentation_id, active_outline,
                          language, job, index, state, hooks):
        # The real representative callback and hooks feed the ordinary page loop.
        payload = {"svg_content": "<svg/>", "attempts": [{"kind": "local", "receipt": {}}]}
        return await collect._attempt_loop(candidate_ctx, active_request, presentation_id,
            index, active_outline.slides[index].content, payload, job, state, hooks)

    with pytest.raises(RepresentativesMeasured):
        asyncio.run(header._prepare_designed_document_header(ctx, request, uuid.uuid4(),
            outline, "en", SimpleNamespace(), checkpoint=AsyncMock(), async_session_maker=None,
            read_png=lambda _: b"png", write_png=lambda _: {"png_path": "path", "png_sha256": "png"},
            page_runner=page_runner, page_hooks_factory=collect._PageHooks, state_factory=collect._RunState))
    assert {identity["candidate_id"] for identity, _ in candidate_writes} == {"a", "b"}
    assert all("render_started_at" not in payload for _, payload in candidate_writes)
    recorder = request._authored_generation_timing_recorder
    assert len(recorder.render_attempts()) == 2
    assert {"render.wait", "render.work", "render.checkpoint"} <= set(recorder.observations())


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, asyncio.CancelledError])
def test_dispatch_hook_failure_is_optional_but_cancellation_propagates(monkeypatch, caplog, error_type):
    _install_harness(monkeypatch)
    rendered = AsyncMock(return_value=SimpleNamespace(
        png=b"png", quality={"passed": True, "verified": True, "failures": []}))
    monkeypatch.setattr("services.svg_renderer.render_svg_with_metrics", rendered)
    hook = AsyncMock(side_effect=error_type("dispatch hook failed"))
    checkpoint = AsyncMock()

    async def scenario():
        return await render.attempt_render(ctx=_ctx(), request=_request(), presentation_id=uuid.uuid4(),
            index=0, content="page", payload={"attempts": [{"kind": "local", "receipt": {}}]},
            svg="<svg/>", ordinal=0, job=object(), checkpoint=checkpoint, dispatch_hook=hook,
            async_session_maker=None, write_png=lambda _: {"png_path": "path", "png_sha256": "png"})

    if error_type is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(scenario())
        rendered.assert_not_awaited()
    else:
        assert asyncio.run(scenario())[0]["quality"]["passed"] is True
        rendered.assert_awaited_once()
        assert "dispatch" in caplog.text
    hook.assert_awaited_once()
    assert checkpoint.await_count >= (0 if error_type is asyncio.CancelledError else 1)
    assert all("render_started_at" not in call.kwargs for call in checkpoint.await_args_list)


def test_canonical_collector_wires_dispatch_to_the_real_page_row(tmp_path, monkeypatch):
    from services import svg_presentation_service as facade

    _install_harness(monkeypatch)

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            monkeypatch.setattr(checkpoints, "async_session_maker", maker)
            monkeypatch.setattr(facade, "_checkpoint", checkpoints._checkpoint)
            _, job = await _running_authored_job(maker, total_slides=1)
            await _seed_pages(maker, job, 1)
            # Normal authoring already persists this attempt before dispatch.
            await checkpoints._checkpoint(job, 0, payload={"attempts": []})
            ctx = _ctx()
            ctx.count = 1
            monkeypatch.setattr(collect, "_prepare_generation", AsyncMock(return_value=ctx))

            async def local_page(active_ctx, request, presentation_id, outline, language,
                                 active_job, index, state, hooks):
                payload = {"svg_content": "<svg/>", "attempts": [{"kind": "local", "receipt": {}}]}
                return await collect._attempt_loop(active_ctx, request, presentation_id,
                    index, outline.slides[index].content, payload, active_job, state, hooks)

            monkeypatch.setattr(collect, "_page", local_page)
            outline = SimpleNamespace(slides=[SimpleNamespace(content="page")])
            assert await collect._generate(_request(), job.presentation_id, outline,
                "en", None, job) == "published"
            row, = await _pages(maker, job)
            assert row.render_started_at is not None
            assert "render_started_at" not in row.svg_checkpoint
        finally:
            await engine.dispose()

    asyncio.run(scenario())
