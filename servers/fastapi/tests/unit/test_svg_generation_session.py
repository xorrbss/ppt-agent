"""Header commits must never lock the caller's job across page awaits."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import InvalidRequestError

from models.common_header_design import CommonHeaderDesignInput
from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import WorkspaceOperation
from services import svg_common_header_design_checkpoint as header
from services import svg_common_header_generation, svg_presentation_checkpoint
from services import svg_presentation_service as facade
from services.generation_core.drivers.svg import collect
from tests.unit.test_authored_checkpoint_service import (
    _checkpoint_database, _linked_operation, _running_authored_job,
)
from tests.unit.test_svg_checkpoint_recovery_contract import _seed_pages
from tests.unit.test_svg_generation_timing_receipt import _ctx, _install_harness, _request
from tests.unit.test_svg_header_capacity_policy import _request as _header_request
from services.authored_canvas import request_canvas_metadata
from services.svg_common_header_design_plan import reduced_header_outcome, seal_new_header_design_plan
from utils.hashing import canonical_json_digest


async def _header_inputs(maker, job, version):
    request = _header_request(common_header_design=CommonHeaderDesignInput(document_values={"name": "Exact"}))
    plan = seal_new_header_design_plan(request)
    base = {"common_header_design_plan": plan}
    outline = PresentationOutlineModel(slides=[
        SlideOutlineModel(content="# Title", title="Title", page_id="page-1")])
    outcome = reduced_header_outcome(plan, request, base, outline, request_canvas_metadata(request))
    if version == 2:
        plan = {key: value for key, value in plan.items()
                if key not in {"policy", "free_compatible", "plan_digest"}}
        plan["schema"] = "svg-common-header-design-plan/v2"
        plan["plan_digest"] = canonical_json_digest(plan)
        base["common_header_design_plan"] = plan
    # Only deterministic geometry is reused; publication is the ordinary
    # selected-candidate route, with real fenced document/candidate writes.
    contract = outcome["contract"]
    identity = {"candidate_revision": str(contract["revision"]),
                "candidate_id": contract["candidate_id"], "page_id": outline.slides[0].page_id}
    selection = {"version": "svg-common-header-selection/v2", "plan_digest": plan["plan_digest"],
                 "selected_candidate_id": contract["candidate_id"], "selected_revision": contract["revision"],
                 "representative_page_id": identity["page_id"],
                 "selection_input_digest": canonical_json_digest({"selected": identity})}
    document = {"status": "selected", "selection": selection,
                "selected_candidate_id": contract["candidate_id"],
                "selection_input_digest": selection["selection_input_digest"]}
    async with maker() as session:
        row = await session.get(PresentationGenerationJob, job.id)
        row.svg_style_snapshot = base
        row.authored_outline = outline.model_dump(mode="json")
        await session.commit()
    proposed = {**base, "common_header": contract, "common_header_selection": selection}
    proposed["digest"] = canonical_json_digest({k: v for k, v in proposed.items() if k != "digest"})
    outline.slides[0].common_header = outcome["bindings"][0]
    return base, proposed, outline, identity, document


async def _publish(maker, job, proposed, outline, identity, document):
    await header.read_update_header_design(job, payload=document, session_maker=maker)
    await header.candidate_page_checkpoint(job, 0, identity=identity,
        payload={"author_body_svg": '<svg xmlns="http://www.w3.org/2000/svg"/>'}, session_maker=maker)
    return await header.publish_header_design(job, snapshot=proposed, outline=outline, session_maker=maker)


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("site", ["document", "candidate", "publish"])
def test_private_header_writers_do_not_dirty_attached_owner(tmp_path, monkeypatch, version, site):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            await _seed_pages(maker, job, 1)
            _, proposed, outline, identity, document = await _header_inputs(maker, job, version)
            # Seed selection first, so each parameter isolates one mirror site.
            await _publish(maker, job, proposed, outline, identity, document)
            monkeypatch.setattr(svg_presentation_checkpoint, "async_session_maker", maker)
            async with maker() as outer:
                attached = await outer.get(PresentationGenerationJob, job.id)
                before = deepcopy(attached.model_dump())
                if site == "document":
                    await header.read_update_header_design(attached, payload=document, session_maker=maker)
                elif site == "candidate":
                    await header.candidate_page_checkpoint(attached, 0, identity=identity,
                        payload={"author_body_svg": "<svg/>", "observed": True}, session_maker=maker)
                else:
                    await header.publish_header_design(attached, snapshot=proposed,
                                                       outline=outline, session_maker=maker)
                assert not outer.dirty
                assert not inspect(attached).modified
                assert attached.model_dump() == before
                # The former autoflush trigger, followed by the actual fan-out
                # boundary and a separately fenced canonical checkpoint write.
                await outer.execute(select(WorkspaceOperation.deadline_at))
                from services.svg_generation_session import independent_generation
                async with independent_generation(outer, attached) as snapshot:
                    assert not outer.dirty and not outer.in_transaction()
                    await svg_presentation_checkpoint._checkpoint(snapshot, 0, payload={"attempts": []})
                    assert not outer.dirty and not outer.in_transaction()
        finally:
            await engine.dispose()
    asyncio.run(scenario())


@pytest.mark.parametrize("version", [2, 3])
def test_real_publish_then_collector_metadata_and_fenced_page_write(tmp_path, monkeypatch, version):
    _install_harness(monkeypatch)

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            await _linked_operation(maker, job)
            await _seed_pages(maker, job, 1)
            base, proposed, outline, identity, document = await _header_inputs(maker, job, version)
            monkeypatch.setattr(svg_presentation_checkpoint, "async_session_maker", maker)
            monkeypatch.setattr(facade, "async_session_maker", maker)
            monkeypatch.setattr(facade, "_checkpoint", svg_presentation_checkpoint._checkpoint)
            ctx = _ctx()
            ctx.count, ctx.snapshot = 1, base
            monkeypatch.setattr(collect, "_prepare_generation", AsyncMock(return_value=ctx))
            async with maker() as outer:
                attached = await outer.get(PresentationGenerationJob, job.id)
                attached._workspace_fencing_token = 9

                async def publish_header(active_ctx, request, presentation_id, active_outline,
                                         language, active_job, **_hooks):
                    await _publish(maker, active_job, proposed, active_outline, identity, document)
                    assert not outer.dirty and not outer.in_transaction()
                    assert active_job.svg_style_snapshot == proposed
                    assert active_job.authored_outline == active_outline.model_dump(mode="json")
                    published = active_job.svg_header_design_checkpoint["published_inputs"]
                    assert published["snapshot_digest"] == proposed["digest"]
                    return replace(active_ctx, snapshot=proposed)

                async def page(active_ctx, request, presentation_id, active_outline,
                               language, active_job, index, state, hooks):
                    # This is after the collector's real operation metadata SELECT.
                    assert not outer.dirty and not outer.in_transaction()
                    assert active_job.svg_style_snapshot == active_ctx.snapshot == proposed
                    await hooks.checkpoint(active_job, index, payload={"attempts": []})
                    with pytest.raises(InvalidRequestError, match="autobegin|Autobegin"):
                        await outer.execute(select(WorkspaceOperation.id))
                    assert not outer.in_transaction()
                    return ({"svg_content": "<svg/>", "png_sha256": "test",
                             "quality": {"passed": True, "verified": True, "failures": []}}, b"png")

                async def publish(*args, **_kwargs):
                    assert outer.sync_session.autobegin is True
                    assert args[5].svg_style_snapshot == args[6] == proposed
                    await outer.execute(select(WorkspaceOperation.id))
                    return "published"

                monkeypatch.setattr(svg_common_header_generation, "prepare_document_header", publish_header)
                monkeypatch.setattr(collect, "_page", page)
                monkeypatch.setattr(facade, "_publish", publish)
                assert await collect._generate(_request(), job.presentation_id, outline,
                                               "en", outer, attached) == "published"
        finally:
            await engine.dispose()
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["dirty", "flushed", "explicit_begin", "cancel"])
def test_generation_boundary_releases_locks_and_restores_session(tmp_path, monkeypatch, action):
    async def scenario():
        from services.svg_generation_session import independent_generation
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            async with maker() as outer:
                attached = await outer.get(PresentationGenerationJob, job.id)
                attached._workspace_fencing_token = 9
                if action in {"dirty", "flushed"}:
                    attached.completed_slides = 1
                if action == "flushed":
                    await outer.flush()
                expected = ("pending_writes" if action == "dirty" else "transaction_forbidden")
                try:
                    async with independent_generation(outer, attached) as snapshot:
                        assert not outer.in_transaction() and not outer.dirty
                        assert inspect(snapshot).session is None
                        assert snapshot._workspace_fencing_token == 9
                        if action == "explicit_begin":
                            await outer.begin()
                        elif action == "cancel":
                            raise asyncio.CancelledError()
                except RuntimeError as error:
                    assert action in {"dirty", "explicit_begin"}
                    assert expected in str(error)
                except asyncio.CancelledError:
                    assert action == "cancel"
                else:
                    assert action == "flushed"
                assert not outer.in_transaction() and not outer.dirty
                assert outer.sync_session.autobegin is True
                stored = await outer.get(PresentationGenerationJob, job.id)
                assert stored.completed_slides == 0
        finally:
            await engine.dispose()
    asyncio.run(scenario())
