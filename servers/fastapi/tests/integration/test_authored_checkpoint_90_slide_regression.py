import asyncio
import io
from collections import Counter
from types import SimpleNamespace
import uuid

import pytest
from PIL import Image
from pptx import Presentation as PptxPresentation
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from services import authored_checkpoint_service as checkpoints
from services import authored_presentation_service as authored
from services import presentation_generation_job_service as jobs


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (160, 90), (32, 96, 180)).save(output, format="PNG")
    return output.getvalue()


def test_90_slide_bounded_workers_resume_without_repeating_successful_work(
    tmp_path, monkeypatch
):
    from api.v1.ppt.endpoints import presentation_generate as endpoint
    from utils import llm_provider

    async def scenario():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'authored-90-slide.db'}",
            connect_args={"timeout": 30.0},
        )
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.exec_driver_sql("PRAGMA busy_timeout=30000")
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    AsyncPresentationGenerationTaskModel.__table__,
                    PresentationGenerationJob.__table__,
                    AuthoredSlideCheckpoint.__table__,
                    PresentationModel.__table__,
                    SlideModel.__table__,
                ],
            )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(checkpoints, "async_session_maker", maker)
        monkeypatch.setattr(jobs, "async_session_maker", maker)
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
        monkeypatch.setenv(
            "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "checkpoints")
        )
        monkeypatch.setenv("AUTHORED_TEST_FAILURES_ENABLED", "true")
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("AUTHORED_TEST_FAILURE_SLIDE", "45")
        monkeypatch.setenv("AUTHORED_TEST_FAILURE_STAGE", "render")
        monkeypatch.setattr(authored, "_AUTHORED_SLIDE_WORKERS", 4)
        monkeypatch.setattr(authored, "find_chrome", lambda: True)
        monkeypatch.setattr(
            llm_provider,
            "get_llm_provider",
            lambda: SimpleNamespace(value="90-slide-test"),
        )
        monkeypatch.setattr(llm_provider, "get_model", lambda: "stub-model")

        outline = PresentationOutlineModel(
            slides=[
                SlideOutlineModel(content=f"Slide {index + 1}")
                for index in range(90)
            ]
        )
        plan = SimpleNamespace(
            contents=tuple(slide.content for slide in outline.slides),
            design_system="90 SLIDE CHECKPOINT TEST",
        )
        author_successes: Counter[int] = Counter()
        render_successes: Counter[int] = Counter()
        active_author = 0
        active_render = 0
        max_active_author = 0
        max_active_render = 0
        rendered_png = _png()

        async def fake_author(_selected_plan, index, **_options):
            nonlocal active_author, max_active_author
            active_author += 1
            max_active_author = max(max_active_author, active_author)
            try:
                await asyncio.sleep(0.001)
                author_successes[index] += 1
                return (
                    "<!DOCTYPE html><html><body>"
                    f"<h1 data-index='{index}'>Slide {index + 1}</h1>"
                    "</body></html>"
                )
            finally:
                active_author -= 1

        async def fake_render(html):
            nonlocal active_render, max_active_render
            index = int(html.split("data-index='", 1)[1].split("'", 1)[0])
            active_render += 1
            max_active_render = max(max_active_render, active_render)
            try:
                await asyncio.sleep(0.001)
                render_successes[index] += 1
                return rendered_png
            finally:
                active_render -= 1

        monkeypatch.setattr(authored, "prepare_authored_deck", lambda *_: plan)
        monkeypatch.setattr(authored, "author_planned_slide", fake_author)
        monkeypatch.setattr(authored, "is_fallback_slide_html", lambda _: False)
        monkeypatch.setattr(authored, "render_html_to_png", fake_render)

        # Make the paid-call/checkpoint race deterministic. While slide 39's
        # successful author result is waiting to become durable, the other
        # workers advance to the injected slide-45 failure. A cancellation-safe
        # checkpoint write must finish before the sibling cancellation escapes.
        real_complete_stage = authored.complete_stage

        async def delayed_complete_stage(**options):
            if options["stage"] == "author" and options["slide_index"] == 38:
                await asyncio.sleep(0.3)
            return await real_complete_stage(**options)

        monkeypatch.setattr(authored, "complete_stage", delayed_complete_stage)

        status = AsyncPresentationGenerationTaskModel(
            status="pending", message="Queued for generation"
        )
        presentation_id = uuid.uuid4()
        job = PresentationGenerationJob(
            task_id=status.id,
            presentation_id=presentation_id,
            idempotency_key_hash="9" * 64,
            request_id="90-slide-regression",
            request_sha256="8" * 64,
            request_payload={"template": "authored", "strategy": "authored"},
            state="queued",
        )
        async with maker() as setup:
            setup.add_all((status, job))
            await setup.commit()

        request = GeneratePresentationRequest(
            content="90 slide checkpoint recovery deck",
            template="authored",
            strategy="authored",
            n_slides=90,
            include_title_slide=False,
            vision_qa=False,
        )
        try:
            first_claim = await jobs.claim_generation_job(
                job.id, allow_auth_resume=False
            )
            assert first_claim is not None
            _, first_token = first_claim
            async with maker() as session:
                first_job = await session.get(PresentationGenerationJob, job.id)
                with pytest.raises(checkpoints.AuthoredStageError) as failure:
                    await authored.generate_authored_presentation(
                        request,
                        presentation_id,
                        outline,
                        "English",
                        session,
                        first_job,
                    )
            assert failure.value.slide_index == 44
            assert failure.value.stage == "render"
            assert max_active_author <= 4
            assert max_active_render <= 4
            assert await jobs.finalize_generation_job(
                job.id, first_token, succeeded=False
            )

            async with maker() as session:
                failed_job = await session.get(PresentationGenerationJob, job.id)
                failed_status = await session.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
                completed_before_resume = await session.scalar(
                    select(func.count())
                    .select_from(AuthoredSlideCheckpoint)
                    .where(
                        AuthoredSlideCheckpoint.job_id == job.id,
                        AuthoredSlideCheckpoint.persist_status == "completed",
                    )
                )
                assert 1 <= completed_before_resume < 90
                assert await endpoint._resume_failed_authored_job(
                    failed_job, failed_status, session
                )

            second_claim = await jobs.claim_generation_job(
                job.id, allow_auth_resume=False
            )
            assert second_claim is not None
            _, second_token = second_claim
            async with maker() as session:
                resumed_job = await session.get(PresentationGenerationJob, job.id)
                result = await authored.generate_authored_presentation(
                    request,
                    presentation_id,
                    outline,
                    "English",
                    session,
                    resumed_job,
                )

            assert max_active_author <= 4
            assert max_active_render <= 4
            assert author_successes == Counter({index: 1 for index in range(90)})
            assert render_successes == Counter({index: 1 for index in range(90)})
            assert len(PptxPresentation(result.path).slides) == 90
            # Authored persistence atomically publishes and consumes the attempt
            # token. The generic worker finalizer is therefore an idempotent no-op.
            assert not await jobs.finalize_generation_job(
                job.id, second_token, succeeded=True
            )

            async with maker() as session:
                published_job = await session.get(PresentationGenerationJob, job.id)
                slide_count = await session.scalar(
                    select(func.count())
                    .select_from(SlideModel)
                    .where(SlideModel.presentation == presentation_id)
                )
                checkpoint_count = await session.scalar(
                    select(func.count())
                    .select_from(AuthoredSlideCheckpoint)
                    .where(
                        AuthoredSlideCheckpoint.job_id == job.id,
                        AuthoredSlideCheckpoint.persist_status == "completed",
                    )
                )
            assert published_job.state == "published"
            assert published_job.attempt_number == 2
            assert published_job.completed_slides == 90
            assert slide_count == 90
            assert checkpoint_count == 90
        finally:
            await engine.dispose()

    asyncio.run(scenario())
