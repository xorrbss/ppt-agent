import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from services import svg_presentation_checkpoint as svg_checkpoints
from services import authored_checkpoint_service as checkpoint_state
from services import authored_checkpoint_state
from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_generation_progress_poller import describe_authored_progress
from services.workspace_public_generation_lifecycle import (
    _PUBLIC_GENERATION_STAGE_STATES,
    _public_generation_transition_path,
)
from test_authored_checkpoint_service import (
    _checkpoint_database,
    _running_authored_job,
)


def test_svg_checkpoints_keep_public_stage_monotonic_and_record_page_timings(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            monkeypatch.setattr(svg_checkpoints, "async_session_maker", maker)
            _, job = await _running_authored_job(maker, total_slides=3)
            async with maker() as session:
                session.add_all(
                    AuthoredSlideCheckpoint(job_id=job.id, slide_index=index)
                    for index in range(3)
                )
                await session.commit()

            active_job = SimpleNamespace(id=job.id, attempt_token="attempt-a")

            async def write(index, payload):
                await svg_checkpoints._checkpoint(active_job, index, payload=payload)
                async with maker() as session:
                    return await session.scalar(
                        select(PresentationGenerationJob.current_stage).where(
                            PresentationGenerationJob.id == job.id
                        )
                    )

            stages = [
                await write(0, {"seed": "page-0"}),
                await write(1, {"seed": "page-1"}),
                await write(0, {"seed": "page-0", "svg_content": "<svg/>"}),
                await write(2, {"seed": "page-2"}),
                await write(0, {"seed": "page-0", "svg_content": "<svg/>", "png_sha256": "a" * 64}),
            ]
            assert stages == ["author", "author", "render", "render", "render"]

            public_states = []
            current = "outlining"
            for stage in stages:
                public_stage, _, _ = describe_authored_progress(0, 3, stage)
                target = _PUBLIC_GENERATION_STAGE_STATES[public_stage]
                path = _public_generation_transition_path(current, target)
                public_states.extend(path)
                current = target
            assert public_states == [
                "authoring", "authoring", "rendering", "rendering", "rendering"
            ]

            with pytest.raises(ValueError, match="public_generation_transition_invalid:rendering:authoring"):
                _public_generation_transition_path("rendering", "authoring")

            async with maker() as session:
                page = await session.scalar(
                    select(AuthoredSlideCheckpoint).where(
                        AuthoredSlideCheckpoint.job_id == job.id,
                        AuthoredSlideCheckpoint.slide_index == 0,
                    )
                )
            assert page.author_started_at is not None
            assert page.render_started_at is None
            assert page.author_completed_at is not None
            assert page.render_completed_at is not None
            assert page.author_started_at <= page.author_completed_at
            assert page.author_completed_at < page.render_completed_at
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("strategy", ["svg", "authored"])
@pytest.mark.parametrize("deck_stage", ["render", "persist", "repair", "publication"])
def test_page_failure_preserves_svg_deck_stage_and_authored_behavior(
    tmp_path, monkeypatch, strategy, deck_stage
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker, total_slides=2)
            async with maker() as session:
                stored = await session.get(PresentationGenerationJob, job.id)
                stored.request_payload = {"strategy": strategy}
                stored.current_stage = deck_stage
                session.add(AuthoredSlideCheckpoint(job_id=job.id, slide_index=0))
                await session.commit()

            error = AuthoredStageError(
                slide_index=0, stage="author", code="svg_author_failed", retryable=False
            )
            await checkpoint_state.fail_stage(
                job_id=job.id, slide_index=0, stage="author",
                attempt_token=job.attempt_token, error=error, request_id=None,
            )
            async with maker() as session:
                stored = await session.get(PresentationGenerationJob, job.id)
                page = await session.scalar(select(AuthoredSlideCheckpoint).where(
                    AuthoredSlideCheckpoint.job_id == job.id
                ))
                assert stored.current_stage == (deck_stage if strategy == "svg" else "author")
                assert stored.last_error["stage"] == "author"
                assert stored.resume_allowed is False
                assert page.author_status == "failed"
                assert page.failed_stage == "author"
            if strategy == "svg":
                public_stage, _, _ = describe_authored_progress(0, 2, stored.current_stage)
                target = _PUBLIC_GENERATION_STAGE_STATES[public_stage]
                assert _public_generation_transition_path("rendering", target)[-1] == target
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_svg_page_failure_refreshes_the_stage_under_the_job_fence(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            monkeypatch.setattr(svg_checkpoints, "async_session_maker", maker)
            _, job = await _running_authored_job(maker, total_slides=2)
            async with maker() as session:
                stored = await session.get(PresentationGenerationJob, job.id)
                stored.request_payload = {"strategy": "svg"}
                stored.current_stage = "author"
                session.add_all(AuthoredSlideCheckpoint(job_id=job.id, slide_index=index)
                                for index in range(2))
                await session.commit()
            async with maker() as stale_session:
                stale_job = await stale_session.get(PresentationGenerationJob, job.id)
                await svg_checkpoints._checkpoint(job, 1, payload={"svg_content": "<svg/>"})
                assert stale_job.current_stage == "author"
                await authored_checkpoint_state.fail_stage(
                    job_id=job.id, slide_index=0, stage="author", attempt_token=job.attempt_token,
                    error=AuthoredStageError(slide_index=0, stage="author", code="svg_author_failed"),
                    request_id=None, session=stale_session,
                )
                await stale_session.commit()
                assert stale_job.current_stage == "render"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
