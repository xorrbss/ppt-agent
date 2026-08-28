import asyncio
import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from services import authored_checkpoint_service as checkpoints
from services import authored_presentation_pipeline as pipeline
from utils.llm_calls.author_slide import Brand
from utils.renderer_isolation import RendererPolicyError


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


def _one_slide_outline():
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content="checkpoint resume")]
    )


def _pipeline_brand():
    return Brand(
        topic="checkpoint",
        language="English",
        primary="#2563EB",
        fonts="Inter",
    )


def _install_one_slide_pipeline_fakes(monkeypatch, *, rendered_html):
    plan = SimpleNamespace(
        contents=("checkpoint resume",),
        design_system="TEST DESIGN SYSTEM",
        reference_mode="style_reference",
    )

    async def unexpected_author(*_args, **_kwargs):
        raise AssertionError("a completed author checkpoint must be resumed")

    async def fake_render(html):
        rendered_html.append(html)
        return b"rendered-png"

    async def no_recording(**_kwargs):
        return None

    monkeypatch.setattr(pipeline, "prepare_authored_deck", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(pipeline, "author_planned_slide", unexpected_author)
    monkeypatch.setattr(pipeline, "render_html_to_png", fake_render)
    monkeypatch.setattr(pipeline, "record_local_stage", no_recording)


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


def test_retry_classifier_keeps_renderer_policy_failures_non_retryable():
    class HttpError(RuntimeError):
        def __init__(self, status_code):
            super().__init__(f"HTTP {status_code}")
            self.status_code = status_code

    assert (
        checkpoints.is_retryable_exception(RendererPolicyError("unsafe HTML"))
        is False
    )
    assert checkpoints.is_retryable_exception(HttpError(429)) is True
    assert checkpoints.is_retryable_exception(HttpError(500)) is True
    assert checkpoints.is_retryable_exception(HttpError(503)) is True
    assert checkpoints.is_retryable_exception(TimeoutError("provider timeout")) is True


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


def test_filesystem_evidence_recovers_db_pointer_and_clears_render_failure(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["content"],
            )
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="render",
                attempt_token="attempt-a",
            )
            async with maker() as session:
                stored = (
                    await session.execute(
                        select(AuthoredSlideCheckpoint).where(
                            AuthoredSlideCheckpoint.job_id == job.id,
                            AuthoredSlideCheckpoint.slide_index == 0,
                        )
                    )
                ).scalar_one()
                stored.render_status = "failed"
                stored.failed_stage = "render"
                stored.error = {"code": "render_pointer_commit_interrupted"}
                session.add(stored)
                await session.commit()

            attempt_id = checkpoints.evidence_attempt_id("attempt-a")
            base_path = checkpoints.render_artifact_path(job.id, 0, "attempt-a")
            render_path = base_path.with_name(
                f"{base_path.stem}-r0-crash-window.png"
            )
            html = "<section>durable html</section>"
            png = b"durable-png"
            metrics = {"revision": 0, "visible": True}
            checkpoints.atomic_write(render_path, png)
            checkpoints.write_valid_evidence(
                render_path,
                attempt_id=attempt_id,
                slide_index=0,
                revision=0,
                html=html,
                png=png,
                metrics=metrics,
            )

            recovered = await checkpoints.get_checkpoint(job.id, 0)
            assert recovered.render_status == "completed"
            assert recovered.render_completed_at is not None
            assert recovered.failed_stage is None
            assert recovered.error is None
            bundle = checkpoints.read_valid_evidence(recovered)
            assert (bundle.html, bundle.png, bundle.metrics, bundle.revision) == (
                html,
                png,
                metrics,
                0,
            )

            # DB -> filesystem mismatch blocks the recovered pointer.
            recovered.render_sha256 = "0" * 64
            with pytest.raises(checkpoints.SlideCheckpointCorrupt):
                checkpoints.read_valid_evidence(recovered)
            recovered.render_sha256 = hashlib.sha256(png).hexdigest()

            # Filesystem current -> immutable revision mismatch also blocks.
            sidecar = render_path.with_name(f".{render_path.name}.evidence.json")
            current_payload = json.loads(sidecar.read_text("utf-8"))
            current_payload["metrics"]["visible"] = False
            current_payload["digest"] = checkpoints.SlideCheckpointBundle._digest_for_payload(
                current_payload["html"],
                png,
                current_payload["metrics"],
            )
            sidecar.write_text(
                json.dumps(current_payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with pytest.raises(checkpoints.SlideCheckpointCorrupt):
                checkpoints.read_valid_evidence(recovered)

            store = checkpoints.AtomicSlideCheckpointStore(tmp_path / "bundle-store")
            orphan_dir = tmp_path / "bundle-store" / "attempt-a" / "slide-0"
            orphan_dir.mkdir(parents=True)
            orphan = orphan_dir / ".prepared-r1-deadbeef.json"
            orphan.write_text(
                '{"revision":1,"digest":"deadbeef"}',
                encoding="utf-8",
            )
            assert store.sweep_orphans(max_age_seconds=0, max_files=1) == 1
            assert not orphan.exists()
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


def test_invalid_resumed_html_fails_before_render_and_is_durably_non_retryable(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        rendered_html = []
        _install_one_slide_pipeline_fakes(
            monkeypatch,
            rendered_html=rendered_html,
        )
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title"],
            )
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="author",
                attempt_token="attempt-a",
            )
            unsafe = (
                "<!doctype html><html><head>"
                '<meta http-equiv="refresh" content="0;url=https://attacker.test">'
                "</head><body><main>unsafe checkpoint</main></body></html>"
            )
            await checkpoints.complete_stage(
                job_id=job.id,
                slide_index=0,
                stage="author",
                attempt_token="attempt-a",
                html=unsafe,
            )

            with pytest.raises(checkpoints.AuthoredStageError) as raised:
                await pipeline._author_illustrate_render_pipeline(
                    _one_slide_outline(),
                    _pipeline_brand(),
                    SimpleNamespace(
                        reference_mode="style_reference",
                        illustration_prompt=None,
                    ),
                    generation_job=job,
                )

            assert raised.value.stage == "author"
            assert raised.value.code == "authored_slide_author_failed"
            assert raised.value.retryable is False
            assert rendered_html == []
            failed = await checkpoints.get_checkpoint(job.id, 0)
            assert failed.author_status == "failed"
            assert failed.render_status == "pending"
            assert failed.failed_stage == "author"
            assert failed.error["retryable"] is False
            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                assert stored_job.resume_allowed is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resumed_completed_html_is_canonicalized_then_rendered_normally(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        rendered_html = []
        _install_one_slide_pipeline_fakes(
            monkeypatch,
            rendered_html=rendered_html,
        )
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title"],
            )
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="author",
                attempt_token="attempt-a",
            )
            resumable = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<META CONTENT='width=device-width' NAME='VIEWPORT'>"
                "</head><body><main>safe checkpoint</main></body></html>"
            )
            await checkpoints.complete_stage(
                job_id=job.id,
                slide_index=0,
                stage="author",
                attempt_token="attempt-a",
                html=resumable,
            )

            result = await pipeline._author_illustrate_render_pipeline(
                _one_slide_outline(),
                _pipeline_brand(),
                SimpleNamespace(
                    reference_mode="style_reference",
                    illustration_prompt=None,
                ),
                generation_job=job,
            )

            assert len(rendered_html) == 1
            assert "viewport" not in rendered_html[0].lower()
            assert '<meta charset="utf-8">' in rendered_html[0]
            assert result.deck.htmls == rendered_html
            resumed = await checkpoints.get_checkpoint(job.id, 0)
            assert resumed.author_status == "completed"
            assert resumed.illustrate_status == "skipped"
            assert resumed.render_status == "completed"
            assert resumed.persist_status == "completed"
            assert resumed.failed_stage is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
