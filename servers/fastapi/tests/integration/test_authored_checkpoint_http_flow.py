import asyncio
import socket
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import uvicorn

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import WorkspaceOperation
from services import authored_checkpoint_service as checkpoints
from services import database
from services import presentation_generation_job_service as jobs
from tests.support.authored_checkpoint_public_fixture import (
    checkpoint_environment,
    create_checkpoint_schema,
    install_checkpoint_principal,
    seed_checkpoint_owner,
)


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
            raise AssertionError("Timed out waiting for the loopback server")
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


def test_tcp_api_refuses_a_new_authored_generation(tmp_path, monkeypatch):
    """The product router over a real loopback socket refuses authored generation.

    This used to drive an authored job through an injected render failure, a
    server restart and a same-key resume. Authored generation was removed on
    2026-11-09, so the same request (and its idempotent replay) is now a 422
    with ``authored_strategy_removed`` that leaves no durable row and makes no
    model call.
    """

    from api.v1.ppt.endpoints import presentation_generate as endpoint
    from services import presentation_generation_pipeline as generation_pipeline

    async def scenario():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'authored-http-flow.db'}",
            connect_args={"timeout": 30},
        )
        await create_checkpoint_schema(engine)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        await seed_checkpoint_owner(maker)
        monkeypatch.setattr(checkpoints, "async_session_maker", maker)
        monkeypatch.setattr(jobs, "async_session_maker", maker)
        monkeypatch.setattr(generation_pipeline, "async_session_maker", maker)
        monkeypatch.setattr(database, "async_session_maker", maker)
        monkeypatch.setattr(database, "sql_engine", engine)

        async def session_override():
            async with maker() as session:
                yield session

        app = FastAPI()
        app.dependency_overrides[database.get_async_session] = session_override
        app.include_router(endpoint.PRESENTATION_GENERATE_ROUTER, prefix="/api/v1/ppt")
        install_checkpoint_principal(app)

        for name, value in checkpoint_environment(tmp_path).items():
            monkeypatch.setenv(name, value)
        model_calls: list[str] = []

        async def refuse_model_call(*_args, **_kwargs):
            model_calls.append("called")
            raise AssertionError("an authored request must not reach a model call")

        monkeypatch.setattr(generation_pipeline, "generate_content_brief", refuse_model_call)
        monkeypatch.setattr(generation_pipeline, "generate_ppt_outline", refuse_model_call)
        monkeypatch.setattr(
            generation_pipeline,
            "wake_workspace_operation_runner",
            lambda: SimpleNamespace(),
        )

        server, server_task, base_url = await _start_server(app)
        try:
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
                for _attempt in range(2):
                    response = await client.post(
                        "/api/v1/ppt/presentation/generate/async",
                        json=payload,
                        headers=headers,
                    )
                    assert response.status_code == 422, response.text
                    assert "authored_strategy_removed" in response.text

            async with maker() as session:
                for model in (
                    PresentationModel, WorkspaceOperation, PresentationGenerationJob,
                    AuthoredSlideCheckpoint,
                ):
                    assert await session.scalar(select(func.count()).select_from(model)) == 0
            assert model_calls == []
        finally:
            server.should_exit = True
            await asyncio.wait_for(server_task, timeout=5)
            await engine.dispose()

    asyncio.run(scenario())
