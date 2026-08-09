import asyncio
import io
import json
import socket
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from PIL import Image
from pptx import Presentation as PptxPresentation
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
import uvicorn

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
from models.sql.workspace import (
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services import authored_checkpoint_service as checkpoints
from services import authored_presentation_service as authored
from services import database
from services import workspace_operation_runner as workspace_runner
from services.presentation_generation_operation_handler import (
    register_presentation_generation_operation_handler,
)
from services import presentation_generation_job_service as jobs


def _png(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (160, 90), color).save(output, format="PNG")
    return output.getvalue()


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_until(predicate, *, timeout: float = 10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        result = await predicate()
        if result:
            return result
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Timed out waiting for authored job state")
        await asyncio.sleep(0.02)


async def _start_server(app: FastAPI):
    port = _free_tcp_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="critical",
            lifespan="off",
        )
    )
    task = asyncio.create_task(server.serve())

    async def started():
        return server.started

    await _wait_until(started, timeout=5)
    return server, task, f"http://127.0.0.1:{port}"


def test_tcp_api_failure_status_same_key_resume_and_pptx(
    tmp_path, monkeypatch
):
    """Exercise the product router over a real loopback uvicorn socket."""

    from api.v1.ppt.endpoints import presentation_generate as endpoint
    from utils import llm_provider

    async def scenario():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'authored-http-flow.db'}"
        )
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    AsyncPresentationGenerationTaskModel.__table__,
                    PresentationGenerationJob.__table__,
                    AuthoredSlideCheckpoint.__table__,
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    # A bare generate (no presentation_id) now bootstraps its own
                    # deck and workspace anchor, so this schema must carry the
                    # workspace tables the real app creates.
                    WorkspaceConversation.__table__,
                    WorkspaceMessage.__table__,
                    WorkspaceAsset.__table__,
                    WorkspaceMessageAttachment.__table__,
                    WorkspaceOperation.__table__,
                    WorkspaceOperationEvent.__table__,
                    WorkspaceOutbox.__table__,
                ],
            )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(checkpoints, "async_session_maker", maker)
        monkeypatch.setattr(jobs, "async_session_maker", maker)
        monkeypatch.setattr(endpoint, "async_session_maker", maker)

        async def session_override():
            async with maker() as session:
                yield session

        def build_app():
            app = FastAPI()
            app.dependency_overrides[
                database.get_async_session
            ] = session_override
            app.include_router(
                endpoint.PRESENTATION_GENERATE_ROUTER,
                prefix="/api/v1/ppt",
            )
            return app

        app_data = tmp_path / "app-data"
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        monkeypatch.setenv(
            "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "checkpoints")
        )
        monkeypatch.setenv("AUTHORED_TEST_FAILURES_ENABLED", "true")
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("AUTHORED_TEST_FAILURE_SLIDE", "2")
        monkeypatch.setenv("AUTHORED_TEST_FAILURE_STAGE", "render")
        monkeypatch.setattr(authored, "_AUTHORED_SLIDE_WORKERS", 1)
        monkeypatch.setattr(authored, "find_chrome", lambda: True)
        monkeypatch.setattr(
            llm_provider,
            "get_llm_provider",
            lambda: SimpleNamespace(value="http-checkpoint-test"),
        )
        monkeypatch.setattr(llm_provider, "get_model", lambda: "stub-model")

        plan = SimpleNamespace(
            contents=("Slide 1", "Slide 2", "Slide 3"),
            design_system="HTTP CHECKPOINT TEST DESIGN SYSTEM",
        )
        author_calls: list[int] = []
        render_calls: list[int] = []
        brief_calls = 0
        outline_calls = 0
        rendered_pngs = [
            _png((20 + index * 40, 80, 160)) for index in range(3)
        ]

        async def fake_brief(*_args, **_kwargs):
            nonlocal brief_calls
            brief_calls += 1
            return SimpleNamespace(
                to_prompt_context=lambda: "deterministic content brief"
            )

        async def fake_outline(*_args, **_kwargs):
            nonlocal outline_calls
            outline_calls += 1
            yield json.dumps(
                {
                    "slides": [
                        {"content": f"Slide {index + 1}"}
                        for index in range(3)
                    ]
                }
            )

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

        async def no_op(*_args, **_kwargs):
            return None

        monkeypatch.setattr(endpoint, "generate_content_brief", fake_brief)
        monkeypatch.setattr(endpoint, "generate_ppt_outline", fake_outline)
        monkeypatch.setattr(
            endpoint.MEM0_PRESENTATION_MEMORY_SERVICE,
            "store_generation_context",
            no_op,
        )
        monkeypatch.setattr(
            endpoint.MEM0_PRESENTATION_MEMORY_SERVICE,
            "store_generated_outlines",
            no_op,
        )
        monkeypatch.setattr(authored, "prepare_authored_deck", lambda *_: plan)
        monkeypatch.setattr(authored, "author_planned_slide", fake_author)
        monkeypatch.setattr(authored, "is_fallback_slide_html", lambda _: False)
        monkeypatch.setattr(authored, "render_html_to_png", fake_render)

        # Chat-first generation is executed by the workspace operation runner
        # rather than by a per-request background task, and this loopback app
        # runs with `lifespan="off"`, so start one here and point both it and the
        # generation handler at the test engine.
        generation_handler = register_presentation_generation_operation_handler(
            replace=True
        )
        monkeypatch.setattr(generation_handler, "_session_factory", maker)
        runner = workspace_runner.WorkspaceOperationRunner(
            session_factory=maker, poll_seconds=0.02
        )
        monkeypatch.setattr(
            workspace_runner, "_workspace_operation_runner", runner
        )
        await runner.start()

        server = None
        server_task = None
        try:
            server, server_task, base_url = await _start_server(build_app())
            payload = {
                "content": "TCP checkpoint recovery deck",
                "template": "authored",
                "strategy": "authored",
                "n_slides": 3,
                "include_title_slide": False,
                "vision_qa": False,
                "export_as": "pptx",
            }
            headers = {"Idempotency-Key": "tcp-authored-resume-key"}
            async with httpx.AsyncClient(base_url=base_url) as client:
                first = await client.post(
                    "/api/v1/ppt/presentation/generate/async",
                    json=payload,
                    headers=headers,
                )
                assert first.status_code == 200, first.text
                task_id = first.json()["id"]

                async def failed_status():
                    response = await client.get(
                        f"/api/v1/ppt/presentation/status/{task_id}"
                    )
                    assert response.status_code == 200
                    body = response.json()
                    return body if body["status"] == "error" else None

                failure = await _wait_until(failed_status)
                assert {
                    key: failure["progress"][key]
                    for key in (
                        "completed_slides",
                        "total_slides",
                        "percent",
                        "current_slide",
                        "current_stage",
                    )
                } == {
                    "completed_slides": 1,
                    "total_slides": 3,
                    "percent": 33,
                    "current_slide": 2,
                    "current_stage": "render",
                }
                assert failure["progress"]["finalizing"] is False
                assert failure["progress"]["stage_counts"] == {
                    "author": 2,
                    "illustrate": 2,
                    "render": 1,
                    "persist": 1,
                }
                assert failure["failure"]["code"] == "authored_injected_failure"
                assert failure["failure"]["slide_number"] == 2
                assert failure["failure"]["stage"] == "render"

                async def finalized_failure():
                    async with maker() as session:
                        job = await session.scalar(
                            select(PresentationGenerationJob).where(
                                PresentationGenerationJob.task_id == task_id
                            )
                        )
                        if job is not None and job.state == "failed":
                            return job
                        return None

                failed_job = await _wait_until(finalized_failure)
                assert failed_job.authored_outline is not None
                assert len(failed_job.authored_outline["slides"]) == 3
                assert brief_calls == 1
                assert outline_calls == 1
                assert author_calls == [0, 1]
                assert render_calls == [0]

                async with maker() as session:
                    presentation = await session.get(
                        PresentationModel, failed_job.presentation_id
                    )
                assert presentation is None
                assert not list((app_data / "exports").glob("*.pptx"))

                # Prove that recovery does not depend on in-process task state:
                # stop the real TCP server, discard its process-local registry,
                # and retry against a freshly started FastAPI/Uvicorn instance.
                server.should_exit = True
                await asyncio.wait_for(server_task, timeout=5)
                server = None
                server_task = None
                jobs._active_jobs.clear()
                server, server_task, restarted_url = await _start_server(build_app())

                async with httpx.AsyncClient(
                    base_url=restarted_url
                ) as restarted_client:
                    resumed = await restarted_client.post(
                        "/api/v1/ppt/presentation/generate/async",
                        json=payload,
                        headers=headers,
                    )
                    assert resumed.status_code == 200, resumed.text
                    assert resumed.json()["id"] == task_id
                    assert resumed.json()["status"] in {"pending", "running"}

                    async def completed_status():
                        response = await restarted_client.get(
                            f"/api/v1/ppt/presentation/status/{task_id}"
                        )
                        assert response.status_code == 200
                        body = response.json()
                        if (
                            body["status"] == "completed"
                            and body["progress"]["percent"] == 100
                        ):
                            return body
                        return None

                    completed = await _wait_until(completed_status, timeout=15)

            assert {
                key: completed["progress"][key]
                for key in (
                    "completed_slides",
                    "total_slides",
                    "percent",
                    "current_slide",
                    "current_stage",
                )
            } == {
                "completed_slides": 3,
                "total_slides": 3,
                "percent": 100,
                "current_slide": None,
                "current_stage": "completed",
            }
            assert completed["progress"]["finalizing"] is False
            assert completed["progress"]["stage_counts"] == {
                "author": 3,
                "illustrate": 3,
                "render": 3,
                "persist": 3,
            }
            assert completed["failure"] is None
            assert brief_calls == 1
            assert outline_calls == 1
            assert author_calls == [0, 1, 2]
            assert render_calls == [0, 1, 2]

            output_path = Path(completed["data"]["path"])
            assert output_path.exists()
            assert len(PptxPresentation(output_path).slides) == 3

            async with maker() as session:
                job = await session.scalar(
                    select(PresentationGenerationJob).where(
                        PresentationGenerationJob.task_id == task_id
                    )
                )
                presentation = await session.get(
                    PresentationModel, job.presentation_id
                )
                slide_count = await session.scalar(
                    select(func.count())
                    .select_from(SlideModel)
                    .where(SlideModel.presentation == job.presentation_id)
                )
                durable_checkpoints = (
                    await session.execute(
                        select(AuthoredSlideCheckpoint)
                        .where(AuthoredSlideCheckpoint.job_id == job.id)
                        .order_by(AuthoredSlideCheckpoint.slide_index)
                    )
                ).scalars().all()

            assert job.state == "published"
            assert job.attempt_number == 2
            assert job.completed_slides == 3
            assert job.authored_outline is not None
            assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
            assert presentation.n_slides == 3
            assert slide_count == 3
            assert len(durable_checkpoints) == 3
            assert all(
                checkpoint.persist_status == "completed"
                for checkpoint in durable_checkpoints
            )
        finally:
            await runner.stop()
            for active in list(jobs._active_jobs.values()):
                if not active.done():
                    active.cancel()
            if jobs._active_jobs:
                await asyncio.gather(
                    *list(jobs._active_jobs.values()), return_exceptions=True
                )
            jobs._active_jobs.clear()
            if server is not None:
                server.should_exit = True
            if server_task is not None:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(server_task, timeout=5)
            await engine.dispose()

    asyncio.run(scenario())
