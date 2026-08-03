import asyncio
import io
from types import SimpleNamespace
import uuid

import pytest
from PIL import Image
from pptx import Presentation as PptxPresentation
from sqlalchemy import select
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
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from services import authored_checkpoint_service as checkpoints
from services import authored_presentation_service as authored
from services import presentation_generation_job_service as jobs


def _png(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (160, 90), color).save(output, format="PNG")
    return output.getvalue()


async def _database(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'authored-resume-flow.db'}"
    )
    async with engine.begin() as connection:
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
    return engine, maker


def test_render_failure_resumes_same_job_without_regenerating_completed_slide(
    tmp_path, monkeypatch
):
    from api.v1.ppt.endpoints import presentation_generate as endpoint
    from utils import llm_provider

    async def scenario():
        engine, maker = await _database(tmp_path, monkeypatch)
        try:
            app_data = tmp_path / "app-data"
            monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
            monkeypatch.setenv(
                "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "checkpoints")
            )
            monkeypatch.setenv("AUTHORED_TEST_FAILURES_ENABLED", "true")
            monkeypatch.setenv("ENVIRONMENT", "test")
            monkeypatch.setenv("AUTHORED_TEST_FAILURE_SLIDE", "2")
            monkeypatch.setenv("AUTHORED_TEST_FAILURE_STAGE", "render")

            # A single worker makes the failure boundary deterministic: slide 1
            # is fully durable, slide 2 fails at render, and slide 3 is untouched.
            monkeypatch.setattr(authored, "_AUTHORED_SLIDE_WORKERS", 1)
            monkeypatch.setattr(authored, "find_chrome", lambda: True)
            monkeypatch.setattr(
                llm_provider,
                "get_llm_provider",
                lambda: SimpleNamespace(value="checkpoint-test"),
            )
            monkeypatch.setattr(llm_provider, "get_model", lambda: "stub-model")

            outline = PresentationOutlineModel(
                slides=[
                    SlideOutlineModel(content=f"Slide {index + 1}")
                    for index in range(3)
                ]
            )
            plan = SimpleNamespace(
                contents=tuple(slide.content for slide in outline.slides),
                design_system="CHECKPOINT TEST DESIGN SYSTEM",
            )
            author_calls: list[int] = []
            render_calls: list[int] = []
            rendered_pngs = [_png((20 + index * 40, 80, 160)) for index in range(3)]

            async def fake_author(_selected_plan, index, **_options):
                author_calls.append(index)
                return (
                    "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
                    f"<body><h1>Slide {index + 1}</h1></body></html>"
                )

            async def fake_render(html):
                index = int(html.split("Slide ", 1)[1].split("<", 1)[0]) - 1
                render_calls.append(index)
                return rendered_pngs[index]

            monkeypatch.setattr(authored, "prepare_authored_deck", lambda *_: plan)
            monkeypatch.setattr(authored, "author_planned_slide", fake_author)
            monkeypatch.setattr(authored, "is_fallback_slide_html", lambda _: False)
            monkeypatch.setattr(authored, "render_html_to_png", fake_render)

            status = AsyncPresentationGenerationTaskModel(
                status="pending", message="Queued for generation"
            )
            presentation_id = uuid.uuid4()
            key_hash = "f" * 64
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=presentation_id,
                idempotency_key_hash=key_hash,
                request_id="resume-flow-request",
                request_sha256="e" * 64,
                request_payload={"template": "authored"},
                state="queued",
            )
            async with maker() as setup:
                setup.add_all((status, job))
                await setup.commit()

            request = GeneratePresentationRequest(
                content="Checkpoint recovery deck",
                template="authored",
                n_slides=3,
                vision_qa=False,
            )
            first_claim = await jobs.claim_generation_job(
                job.id, allow_auth_resume=False
            )
            assert first_claim is not None
            _, first_token = first_claim
            async with maker() as session:
                first_job = await session.get(PresentationGenerationJob, job.id)
            assert first_job.attempt_token == first_token

            with pytest.raises(checkpoints.AuthoredStageError) as first_failure:
                async with maker() as first_attempt_session:
                    await authored.generate_authored_presentation(
                        request,
                        presentation_id,
                        outline,
                        "English",
                        first_attempt_session,
                        first_job,
                    )
            assert first_failure.value.code == "authored_injected_failure"
            assert first_failure.value.slide_index == 1
            assert first_failure.value.stage == "render"
            assert author_calls == [0, 1]
            assert render_calls == [0]

            async with maker() as inspection:
                assert await inspection.get(PresentationModel, presentation_id) is None
                first_slide = await inspection.scalar(
                    select(AuthoredSlideCheckpoint).where(
                        AuthoredSlideCheckpoint.job_id == job.id,
                        AuthoredSlideCheckpoint.slide_index == 0,
                    )
                )
                failed_slide = await inspection.scalar(
                    select(AuthoredSlideCheckpoint).where(
                        AuthoredSlideCheckpoint.job_id == job.id,
                        AuthoredSlideCheckpoint.slide_index == 1,
                    )
                )
                untouched_slide = await inspection.scalar(
                    select(AuthoredSlideCheckpoint).where(
                        AuthoredSlideCheckpoint.job_id == job.id,
                        AuthoredSlideCheckpoint.slide_index == 2,
                    )
                )
            assert first_slide.persist_status == "completed"
            assert failed_slide.render_status == "failed"
            assert failed_slide.error["slide_number"] == 2
            assert failed_slide.error["stage"] == "render"
            assert untouched_slide.author_status == "pending"
            assert not list((app_data / "exports").glob("*.pptx"))

            assert await jobs.finalize_generation_job(
                job.id, first_token, succeeded=False
            )
            async with maker() as resume_session:
                failed_job = await resume_session.get(
                    PresentationGenerationJob, job.id
                )
                failed_status = await resume_session.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
                assert failed_job.state == "failed"
                assert failed_job.resume_allowed is True
                assert await endpoint._resume_failed_authored_job(
                    failed_job, failed_status, resume_session
                )

            second_claim = await jobs.claim_generation_job(
                job.id, allow_auth_resume=False
            )
            assert second_claim is not None
            _, second_token = second_claim
            assert second_token != first_token
            async with maker() as session:
                resumed_job = await session.get(PresentationGenerationJob, job.id)
            assert resumed_job.idempotency_key_hash == key_hash
            assert resumed_job.attempt_number == 2

            # Simulate the hardest final-publish ambiguity: the database commits
            # Presentation/Slides + task completed + job published, but the
            # connection reports an error to the caller immediately afterward.
            # The service may surface a persist error for this invocation, but
            # its fenced failure write must not overwrite the committed result.
            async with maker() as resumed_session:
                real_commit = resumed_session.commit
                commit_count = 0

                async def commit_then_disconnect():
                    nonlocal commit_count
                    commit_count += 1
                    await real_commit()
                    # The first commit only releases the admission read
                    # transaction. Inject the ambiguous ACK at the second,
                    # final publication commit.
                    if commit_count == 2:
                        raise ConnectionError("simulated disconnect after commit")

                monkeypatch.setattr(
                    resumed_session, "commit", commit_then_disconnect
                )
                with pytest.raises(checkpoints.AuthoredStageError) as ambiguity:
                    await authored.generate_authored_presentation(
                        request,
                        presentation_id,
                        outline,
                        "English",
                        resumed_session,
                        resumed_job,
                    )
            assert commit_count == 2
            assert ambiguity.value.stage == "persist"

            # A fresh read is authoritative. The deterministic export and the
            # published row let reconciliation return success without calling
            # any slide provider again.
            async with maker() as recovery_session:
                committed_job = await recovery_session.get(
                    PresentationGenerationJob, job.id
                )
                result = await authored.generate_authored_presentation(
                    request,
                    presentation_id,
                    outline,
                    "English",
                    recovery_session,
                    committed_job,
                )

            # Slide 1's durable author/render artifacts were reused. Slide 2 also
            # resumes at render, while previously untouched slide 3 runs normally.
            assert author_calls == [0, 1, 2]
            assert render_calls == [0, 1, 2]
            assert author_calls.count(0) == 1
            assert render_calls.count(0) == 1
            assert author_calls.count(1) == 1
            assert result.presentation_id == presentation_id
            assert result.path.endswith(".pptx")
            assert len(PptxPresentation(result.path).slides) == 3

            # The authored service publishes the job and task in the same DB
            # transaction as Presentation/Slide rows. The generic worker
            # finalizer is therefore an intentional fenced no-op afterward.
            assert not await jobs.finalize_generation_job(
                job.id, second_token, succeeded=True
            )
            async with maker() as verification:
                published = await verification.get(
                    PresentationModel, presentation_id
                )
                published_job = await verification.get(
                    PresentationGenerationJob, job.id
                )
                published_status = await verification.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
                slides = (
                    await verification.execute(
                        select(SlideModel)
                        .where(SlideModel.presentation == presentation_id)
                        .order_by(SlideModel.index)
                    )
                ).scalars().all()
                persisted_checkpoints = (
                    await verification.execute(
                        select(AuthoredSlideCheckpoint)
                        .where(AuthoredSlideCheckpoint.job_id == job.id)
                        .order_by(AuthoredSlideCheckpoint.slide_index)
                    )
                ).scalars().all()

            assert published.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
            assert published.mode == "authored"
            assert published.n_slides == 3
            assert published_job.state == "published"
            assert published_job.completed_slides == 3
            assert published_status.status == "completed"
            assert published_status.error is None
            assert len(slides) == 3
            assert all(slide.html_content for slide in slides)
            assert all(
                checkpoint.persist_status == "completed"
                for checkpoint in persisted_checkpoints
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
