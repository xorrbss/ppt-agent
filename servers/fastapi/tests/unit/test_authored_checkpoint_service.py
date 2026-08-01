import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from services import authored_checkpoint_service as checkpoints


async def _checkpoint_database(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'authored-checkpoints.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                AsyncPresentationGenerationTaskModel.__table__,
                PresentationGenerationJob.__table__,
                AuthoredSlideCheckpoint.__table__,
            ],
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoints, "async_session_maker", maker)
    monkeypatch.setenv(
        "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "render-artifacts")
    )
    return engine, maker


async def _running_authored_job(maker, *, total_slides=0):
    status = AsyncPresentationGenerationTaskModel(
        status="running",
        message="Generating authored presentation",
    )
    job = PresentationGenerationJob(
        task_id=status.id,
        presentation_id=uuid.uuid4(),
        request_sha256="a" * 64,
        request_payload={"template": "authored"},
        state="running",
        attempt_number=3,
        attempt_token="attempt-a",
        total_slides=total_slides,
    )
    async with maker() as session:
        session.add_all((status, job))
        await session.commit()
    return status, job


def test_exception_metadata_is_bounded_and_redacts_secrets_paths_and_html():
    unsafe = RuntimeError(
        "provider failed api_key=top-secret-value "
        "at https://provider.example/v1/images?token=also-secret "
        r"from C:\private\customer\payload.json"
    )
    sanitized = checkpoints.sanitize_exception_message(unsafe)

    assert "top-secret-value" not in sanitized
    assert "also-secret" not in sanitized
    assert "customer" not in sanitized
    assert sanitized.count("[redacted]") >= 3
    assert len(sanitized) <= 512
    assert checkpoints.sanitize_exception_message(
        RuntimeError("upstream returned <!DOCTYPE html><html>private body</html>")
    ) == "[authored content redacted]"
    standalone = checkpoints.sanitize_exception_message(
        RuntimeError("Bearer bearer-secret sk-standalonesecret")
    )
    assert "bearer-secret" not in standalone
    assert "standalonesecret" not in standalone

    error = checkpoints.AuthoredStageError(
        slide_index=6,
        stage="render",
        code="authored_slide_render_failed",
        cause=unsafe,
        retryable=True,
    )
    safe = error.as_safe_dict(request_id="request-123", attempt_number=3)

    assert safe["slide_index"] == 6
    assert safe["slide_number"] == 7
    assert safe["stage"] == "render"
    assert safe["code"] == "authored_slide_render_failed"
    assert safe["retryable"] is True
    assert safe["request_id"] == "request-123"
    assert safe["attempt_number"] == 3

    custom = checkpoints.AuthoredStageError(
        slide_index=0,
        stage="persist",
        code="custom",
        message="publish failed token=do-not-store",
    ).as_safe_dict()
    assert "do-not-store" not in custom["detail"]
    assert safe["exception_type"] == "RuntimeError"
    assert safe["cause"] == sanitized
    assert len(safe["fingerprint"]) == 16


def test_final_publish_is_attempt_fenced_and_terminal_in_one_transaction(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker)
            async with maker() as session:
                with pytest.raises(checkpoints.AuthoredStageError) as raised:
                    await checkpoints.begin_publish(
                        session,
                        job_id=job.id,
                        attempt_token="stale-attempt",
                    )
                assert raised.value.code == "authored_attempt_lost"

            async with maker() as session:
                await checkpoints.begin_publish(
                    session,
                    job_id=job.id,
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_publish(
                    session,
                    job_id=job.id,
                    attempt_token="attempt-a",
                )
                await session.commit()

            async with maker() as session:
                stored = await session.get(PresentationGenerationJob, job.id)
                assert stored.state == "published"
                assert stored.attempt_token is None
                assert stored.current_stage == "completed"
                assert stored.resume_allowed is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_checkpoint_transitions_preserve_outputs_and_report_actual_progress(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            status, job = await _running_authored_job(maker)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title", "content"],
            )

            progress, error = await checkpoints.progress_for_task(status.id)
            assert error is None
            assert progress == {
                "completed_slides": 0,
                "total_slides": 2,
                "percent": 0,
                "current_slide": None,
                "current_stage": "author",
                "stage_counts": {
                    "author": 0,
                    "illustrate": 0,
                    "render": 0,
                    "persist": 0,
                },
                "finalizing": False,
            }

            for slide_index in range(2):
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="author",
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="author",
                    attempt_token="attempt-a",
                    html=f"<section>slide {slide_index + 1}</section>",
                )
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="illustrate",
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="illustrate",
                    attempt_token="attempt-a",
                    skipped=True,
                )
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="render",
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="render",
                    attempt_token="attempt-a",
                    png=f"png-{slide_index}".encode(),
                )
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="persist",
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="persist",
                    attempt_token="attempt-a",
                )

                progress, _ = await checkpoints.progress_for_task(status.id)
                assert progress["completed_slides"] == slide_index + 1
                assert progress["percent"] == (50 if slide_index == 0 else 99)

            stored = await checkpoints.get_checkpoint(job.id, 0)
            assert stored.author_html == "<section>slide 1</section>"
            assert stored.illustrate_status == "skipped"
            assert checkpoints.read_valid_render(stored) == b"png-0"

            progress, error = await checkpoints.progress_for_task(status.id)
            assert error is None
            assert progress["stage_counts"] == {
                "author": 2,
                "illustrate": 2,
                "render": 2,
                "persist": 2,
            }
            assert progress["finalizing"] is True

            # Re-running an upstream stage invalidates downstream persistence and
            # therefore immediately lowers the durable completion count.
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="render",
                attempt_token="attempt-a",
            )
            invalidated = await checkpoints.get_checkpoint(job.id, 0)
            assert invalidated.render_status == "running"
            assert invalidated.persist_status == "pending"
            progress, _ = await checkpoints.progress_for_task(status.id)
            assert progress["completed_slides"] == 1
            assert progress["percent"] == 50

            await checkpoints.complete_stage(
                job_id=job.id,
                slide_index=0,
                stage="render",
                attempt_token="attempt-a",
                png=b"png-0-rebuilt",
            )
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="persist",
                attempt_token="attempt-a",
            )
            await checkpoints.complete_stage(
                job_id=job.id,
                slide_index=0,
                stage="persist",
                attempt_token="attempt-a",
            )

            async with maker() as session:
                published = await session.get(PresentationGenerationJob, job.id)
                published.state = "published"
                session.add(published)
                await session.commit()
            progress, _ = await checkpoints.progress_for_task(status.id)
            assert progress["completed_slides"] == 2
            assert progress["percent"] == 100
            assert progress["finalizing"] is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_stage_is_durable_sanitized_and_injected_only_once(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            status, job = await _running_authored_job(maker)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title", "content"],
            )
            monkeypatch.setenv("AUTHORED_TEST_FAILURES_ENABLED", "true")
            monkeypatch.setenv("ENVIRONMENT", "test")
            monkeypatch.setenv("AUTHORED_TEST_FAILURE_SLIDE", "2")
            monkeypatch.setenv("AUTHORED_TEST_FAILURE_STAGE", "author")

            checkpoint = await checkpoints.get_checkpoint(job.id, 1)
            assert checkpoints.should_inject_failure(
                checkpoint, slide_index=1, stage="author"
            )
            error = checkpoints.AuthoredStageError(
                slide_index=1,
                stage="author",
                code="authored_injected_failure",
                cause=RuntimeError("api_key=must-not-leak"),
            )
            safe = await checkpoints.fail_stage(
                job_id=job.id,
                slide_index=1,
                stage="author",
                attempt_token="attempt-a",
                error=error,
                request_id="request-456",
            )

            assert safe["slide_number"] == 2
            assert safe["stage"] == "author"
            assert "must-not-leak" not in safe["cause"]
            failed = await checkpoints.get_checkpoint(job.id, 1)
            assert failed.author_status == "failed"
            assert failed.failed_stage == "author"
            assert not checkpoints.should_inject_failure(
                failed, slide_index=1, stage="author"
            )

            progress, api_error = await checkpoints.progress_for_task(status.id)
            assert progress["current_slide"] == 2
            assert progress["current_stage"] == "author"
            assert api_error == safe
            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                assert stored_job.resume_allowed is True
                assert stored_job.last_error == safe

            # The error marker remains while the failed stage is restarted, so
            # the test-only injection cannot charge/fail the resumed attempt twice.
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=1,
                stage="author",
                attempt_token="attempt-a",
            )
            restarting = await checkpoints.get_checkpoint(job.id, 1)
            assert restarting.author_status == "running"
            assert not checkpoints.should_inject_failure(
                restarting, slide_index=1, stage="author"
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_checkpoint_root_defaults_under_app_data_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTHORED_CHECKPOINT_DIRECTORY", raising=False)
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))

    root = checkpoints.checkpoint_root()

    assert root == (tmp_path / "app-data" / "authored-checkpoints").resolve()
    assert root.is_dir()


def test_clear_checkpoint_html_keeps_stage_history_and_render_pointer(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title"],
            )
            for stage, kwargs in (
                ("author", {"html": "<section>authored</section>"}),
                ("illustrate", {"html": "<section>illustrated</section>"}),
                ("render", {"png": b"png-0"}),
                ("persist", {}),
            ):
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=0,
                    stage=stage,
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=0,
                    stage=stage,
                    attempt_token="attempt-a",
                    **kwargs,
                )

            await checkpoints.clear_checkpoint_html(job.id)

            cleared = await checkpoints.get_checkpoint(job.id, 0)
            assert cleared.author_html is None
            assert cleared.illustrated_html is None
            assert cleared.author_status == "completed"
            assert cleared.persist_status == "completed"
            assert cleared.author_completed_at is not None
            assert cleared.render_path is not None
            assert checkpoints.read_valid_render(cleared) == b"png-0"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_attempt_token_fences_stale_checkpoint_writes(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title"],
            )
            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                stored_job.attempt_token = "attempt-b"
                session.add(stored_job)
                await session.commit()

            with pytest.raises(checkpoints.AuthoredStageError) as raised:
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=0,
                    stage="author",
                    attempt_token="attempt-a",
                )
            assert raised.value.code == "authored_attempt_lost"
            checkpoint = await checkpoints.get_checkpoint(job.id, 0)
            assert checkpoint.author_status == "pending"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
