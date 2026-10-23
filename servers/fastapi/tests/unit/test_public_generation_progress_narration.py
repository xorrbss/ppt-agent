"""Stage narration is advisory; only authoritative transitions can terminalize."""

import asyncio
from dataclasses import replace

import pytest

from services import workspace_operation_runner as runner
from services.authored_generation_progress_poller import mark_authored_generation_stage
from services.workspace_public_generation_lifecycle import (
    _public_generation_transition_path,
    public_generation_narration_path,
)
from test_workspace_operation_runner import _database, _events, _operation, _seed_operations


async def _running_context(tmp_path):
    engine, maker = await _database(tmp_path / "narration.db")
    await _seed_operations(maker)
    claim = await runner.claim_next_workspace_operation(session_factory=maker)
    # Seed the active second attempt without invoking public admission policy.
    claim = replace(claim, kind="presentation.generate", attempt_number=2)
    async with maker() as session:
        operation = await session.get(runner.WorkspaceOperation, claim.operation_id)
        operation.kind = claim.kind
        operation.attempt_number = claim.attempt_number
        operation.progress = {"public_state": "authoring", "public_lifecycle_version": 3}
        await session.commit()
    return engine, maker, runner._ExecutionContext(claim, maker)


@pytest.mark.parametrize(
    "current,stage",
    [("authoring", "outline"), ("quality_gate", "authored_author"),
     ("quality_gate", "authored_render")],
)
def test_retry_and_svg_reports_preserve_public_stage_and_update_counters(tmp_path, current, stage):
    async def scenario():
        engine, maker, context = await _running_context(tmp_path)
        try:
            async with maker() as session:
                operation = await session.get(runner.WorkspaceOperation, context.claim.operation_id)
                operation.progress = {"public_state": current, "public_lifecycle_version": 5}
                await session.commit()
            assert await context.progress(stage=stage, current=2, total=6, percent=33)
            stored = await _operation(maker, context.claim.operation_id)
            assert stored.state == "running"
            assert stored.progress["public_state"] == current
            assert stored.progress["public_lifecycle_version"] == 5
            assert stored.progress["current"] == 2
            assert stored.progress["total"] == 6
            assert stored.progress["percent"] == 33
            assert stored.progress["attempt_number"] == 2
            assert (await _events(maker, stored.id))[-1].payload["public_state"] == current
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("current", ["failed", "cancelled", "completed", "dead_letter"])
def test_narration_cannot_publish_after_terminal_state(current):
    assert public_generation_narration_path(current, "authored_publication") == (current,)
    with pytest.raises(ValueError, match="public_generation_transition_invalid"):
        _public_generation_transition_path(current, "published")
    with pytest.raises(ValueError, match="public_generation_transition_invalid"):
        runner._public_generation_progress(
            {"public_state": current}, target="published", operation_version=4,
            attempt_number=2, fencing_token=1, stage="publication", reason="published",
        )


def test_forward_narration_uses_all_lifecycle_steps_and_waits_do_not_resume():
    assert public_generation_narration_path("outlining", "authored_checkpoint") == (
        "authoring", "rendering", "quality_gate",
    )
    for state in ("capacity_waiting", "retry_waiting"):
        assert public_generation_narration_path(state, "authored_author") == (state,)
    assert public_generation_narration_path("authoring", "unknown") == ("authoring",)
    with pytest.raises(ValueError, match="public_generation_transition_invalid"):
        _public_generation_transition_path("authoring", "outlining")


@pytest.mark.parametrize("boundary", ["public_generation_narration_path", "append_operation_event"])
def test_narration_projection_or_storage_error_does_not_fail_attempt(tmp_path, monkeypatch, boundary):
    async def scenario():
        engine, maker, context = await _running_context(tmp_path)
        try:
            def fail(*args, **kwargs):
                raise ValueError("telemetry failure")

            monkeypatch.setattr(runner, boundary, fail)
            assert await context.progress(stage="authored_render")
            stored = await _operation(maker, context.claim.operation_id)
            assert stored.state == "running"
            assert stored.progress["public_state"] == "authoring"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_narration_preserves_cancellation_and_attempt_fences(tmp_path, monkeypatch):
    async def scenario():
        engine, maker, context = await _running_context(tmp_path)
        try:
            def cancel(*args, **kwargs):
                raise asyncio.CancelledError

            monkeypatch.setattr(runner, "public_generation_narration_path", cancel)
            with pytest.raises(asyncio.CancelledError):
                await context.progress(stage="authored_author")
            context.claim = replace(context.claim, attempt_token="obsolete")
            assert not await context.progress(stage="authored_author")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_progress_version_race_does_not_claim_the_attempt_lost_its_lease(tmp_path, monkeypatch):
    async def scenario():
        engine, maker, context = await _running_context(tmp_path)
        try:
            async def lose_version_race(*args, **kwargs):
                return None

            monkeypatch.setattr(runner, "append_operation_event", lose_version_race)
            assert await context.progress(stage="authored_render")
            stored = await _operation(maker, context.claim.operation_id)
            assert stored.state == "running"
            assert stored.progress["public_state"] == "authoring"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_invalid_authored_stage_is_advisory():
    def forbidden_session():
        raise AssertionError("invalid narration must not open a session")

    assert asyncio.run(mark_authored_generation_stage(
        job_id=None, attempt_token="attempt", stage="unknown", session_factory=forbidden_session,
    )) is False
