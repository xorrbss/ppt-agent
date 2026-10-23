import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from services import svg_presentation_checkpoint as svg_checkpoints
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
            assert page.render_started_at is not None
            assert page.author_completed_at is not None
            assert page.render_completed_at is not None
            assert page.author_started_at < page.render_started_at
            assert page.author_completed_at < page.render_completed_at
        finally:
            await engine.dispose()

    asyncio.run(scenario())
