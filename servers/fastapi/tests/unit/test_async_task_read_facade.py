from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import FastAPI
import httpx
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.async_tasks.router import API_V1_ASYNC_TASKS_ROUTER
from api.v1.auth.context import (
    reset_current_owner_id,
    set_current_owner_id,
)
from api.v1.ppt.router import API_V1_PPT_ROUTER
from enums.async_task_status import AsyncTaskStatus
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.async_task_read_service import (
    TASK_TYPE_PRESENTATION_GENERATION,
    _base_query,
    get_async_task,
    list_async_task_reads,
)
from services.database import get_async_session


OWNER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
OWNER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
BASE_TIME = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


async def _database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'async-task-read.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                AsyncPresentationGenerationTaskModel.__table__,
                PresentationGenerationJob.__table__,
            ],
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(
    maker,
    *,
    task_id: str,
    owner_id: uuid.UUID | None,
    state: str,
    minute: int,
    task_status: str = "legacy-status",
) -> tuple[AsyncPresentationGenerationTaskModel, PresentationGenerationJob]:
    created_at = BASE_TIME + timedelta(minutes=minute)
    task = AsyncPresentationGenerationTaskModel(
        id=task_id,
        owner_id=owner_id,
        status=task_status,
        message=f"{state} message",
        error={"code": "generation_failed"} if state == "failed" else None,
        data={"state": state},
        created_at=created_at,
        updated_at=created_at,
    )
    job = PresentationGenerationJob(
        task_id=task.id,
        presentation_id=uuid.uuid5(uuid.NAMESPACE_URL, task_id),
        request_sha256="a" * 64,
        request_payload={"task_id": task_id},
        state=state,
        created_at=created_at,
        updated_at=created_at + timedelta(seconds=30),
    )
    async with maker() as session:
        session.add(task)
        session.add(job)
        await session.commit()
    return task, job


def test_read_facade_maps_durable_states_and_is_strictly_owner_scoped(tmp_path):
    async def scenario():
        engine, maker = await _database(tmp_path)
        try:
            states = (
                "queued",
                "running",
                "staging",
                "awaiting_resume",
                "published",
                "failed",
                "future_recovering",
            )
            for index, state in enumerate(states):
                await _seed(
                    maker,
                    task_id=f"task-a-{state}",
                    owner_id=OWNER_A,
                    state=state,
                    minute=index,
                )
            await _seed(
                maker,
                task_id="task-b-published",
                owner_id=OWNER_B,
                state="published",
                minute=20,
            )
            await _seed(
                maker,
                task_id="task-anonymous",
                owner_id=None,
                state="published",
                minute=21,
            )
            orphan = AsyncPresentationGenerationTaskModel(
                id="task-a-orphan",
                owner_id=OWNER_A,
                status="completed",
            )
            async with maker() as session:
                session.add(orphan)
                await session.commit()

            owner_token = set_current_owner_id(OWNER_A)
            try:
                async with maker() as session:
                    reads = await list_async_task_reads(
                        sql_session=session,
                        order="asc",
                    )
                    pending = await list_async_task_reads(
                        sql_session=session,
                        status=AsyncTaskStatus.PENDING,
                        order="asc",
                    )
                    foreign = await get_async_task("task-b-published", session)
                    missing_job = await get_async_task("task-a-orphan", session)
            finally:
                reset_current_owner_id(owner_token)

            assert [item.job_state for item in reads] == list(states)
            assert foreign is None
            assert missing_job is None
            assert {
                item.job_state: (item.status, item.terminal)
                for item in reads
            } == {
                "queued": (AsyncTaskStatus.PENDING, False),
                "running": (AsyncTaskStatus.PENDING, False),
                "staging": (AsyncTaskStatus.PENDING, False),
                "awaiting_resume": (AsyncTaskStatus.PENDING, False),
                "published": (AsyncTaskStatus.COMPLETED, True),
                "failed": (AsyncTaskStatus.ERROR, True),
                "future_recovering": (AsyncTaskStatus.PENDING, False),
            }
            assert all(
                item.type == TASK_TYPE_PRESENTATION_GENERATION for item in reads
            )
            assert [item.job_state for item in pending] == [
                "queued",
                "running",
                "staging",
                "awaiting_resume",
                "future_recovering",
            ]
            assert reads[0].updated_at > reads[0].created_at

            anonymous_token = set_current_owner_id(None)
            try:
                async with maker() as session:
                    anonymous = await list_async_task_reads(sql_session=session)
            finally:
                reset_current_owner_id(anonymous_token)
            assert [item.id for item in anonymous] == ["task-anonymous"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_read_facade_filters_orders_and_paginates_the_durable_source(tmp_path):
    async def scenario():
        engine, maker = await _database(tmp_path)
        try:
            for index, state in enumerate(
                ("queued", "published", "failed", "published")
            ):
                await _seed(
                    maker,
                    task_id=f"task-filter-{index}",
                    owner_id=OWNER_A,
                    state=state,
                    minute=index,
                )
            token = set_current_owner_id(OWNER_A)
            try:
                async with maker() as session:
                    completed = await list_async_task_reads(
                        sql_session=session,
                        status=AsyncTaskStatus.COMPLETED,
                        created_at_from=BASE_TIME + timedelta(minutes=1),
                        created_at_to=BASE_TIME + timedelta(minutes=3),
                        order_by="updated_at",
                        order="desc",
                        limit=1,
                        offset=1,
                    )
                    alias_filter = await list_async_task_reads(
                        sql_session=session,
                        created_at=BASE_TIME + timedelta(minutes=2),
                        order="asc",
                    )
                    wrong_type = await list_async_task_reads(
                        sql_session=session,
                        task_type="unrelated.task",
                    )
            finally:
                reset_current_owner_id(token)

            assert [item.id for item in completed] == ["task-filter-1"]
            assert [item.id for item in alias_filter] == [
                "task-filter-2",
                "task-filter-3",
            ]
            assert wrong_type == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_http_contract_exposes_list_check_and_get_without_breaking_legacy(
    tmp_path,
):
    async def scenario():
        engine, maker = await _database(tmp_path)
        try:
            own_task, _ = await _seed(
                maker,
                task_id="task-http-own",
                owner_id=OWNER_A,
                state="published",
                minute=0,
            )
            await _seed(
                maker,
                task_id="task-http-foreign",
                owner_id=OWNER_B,
                state="failed",
                minute=1,
            )

            async def session_dependency():
                async with maker() as session:
                    yield session

            app = FastAPI()
            app.include_router(API_V1_PPT_ROUTER)
            app.include_router(API_V1_ASYNC_TASKS_ROUTER)
            app.dependency_overrides[get_async_session] = session_dependency

            @app.middleware("http")
            async def owner_scope(request, call_next):
                owner = request.headers.get("x-test-owner")
                token = set_current_owner_id(uuid.UUID(owner) if owner else None)
                try:
                    return await call_next(request)
                finally:
                    reset_current_owner_id(token)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                headers = {"x-test-owner": str(OWNER_A)}
                listed = await client.get(
                    "/api/v1/async-tasks",
                    params={
                        "type": TASK_TYPE_PRESENTATION_GENERATION,
                        "status": "completed",
                        "limit": 10,
                        "offset": 0,
                    },
                    headers=headers,
                )
                checked = await client.get(
                    f"/api/v1/async-tasks/status/{own_task.id}",
                    headers=headers,
                )
                fetched = await client.get(
                    f"/api/v1/async-tasks/{own_task.id}",
                    headers=headers,
                )
                hidden = await client.get(
                    "/api/v1/async-tasks/task-http-foreign",
                    headers=headers,
                )
                invalid_page = await client.get(
                    "/api/v1/async-tasks",
                    params={"limit": 201},
                    headers=headers,
                )

            assert listed.status_code == 200
            assert [item["id"] for item in listed.json()] == [own_task.id]
            assert checked.status_code == 200
            assert fetched.json() == checked.json()
            assert checked.json()["terminal"] is True
            assert hidden.status_code == 404
            assert hidden.json() == {"detail": "No async task found"}
            assert invalid_page.status_code == 422

            paths = app.openapi()["paths"]
            assert "/api/v1/async-tasks" in paths
            assert "/api/v1/async-tasks/status/{id}" in paths
            assert "/api/v1/async-tasks/{id}" in paths
            assert "/api/v1/ppt/presentation/status/{id}" in paths
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgresql_query_compiles_against_only_existing_durable_tables():
    owner_token = set_current_owner_id(OWNER_A)
    try:
        sql = str(
            _base_query().compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
    finally:
        reset_current_owner_id(owner_token)

    assert "async_presentation_generation_tasks" in sql
    assert "presentation_generation_jobs" in sql
    assert str(OWNER_A) in sql
    assert " FROM async_tasks " not in f" {sql} "


def test_main_app_mounts_generic_and_legacy_routes_without_openapi_collisions():
    from api.main import app

    document = app.openapi()
    paths = document["paths"]
    assert "/api/v1/async-tasks" in paths
    assert "/api/v1/async-tasks/status/{id}" in paths
    assert "/api/v1/async-tasks/{id}" in paths
    assert "/api/v1/ppt/presentation/status/{id}" in paths

    operation_ids = [
        operation["operationId"]
        for path_item in paths.values()
        for method, operation in path_item.items()
        if method
        in {
            "get",
            "put",
            "post",
            "delete",
            "options",
            "head",
            "patch",
            "trace",
        }
    ]
    assert len(operation_ids) == len(set(operation_ids))
