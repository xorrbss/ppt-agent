"""Live PostgreSQL coverage for the durable generic async-task read facade.

The test uses and removes an isolated schema. A dedicated test database is
still required so a configuration mistake cannot target production.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import re
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from api.v1.auth.context import (
    reset_current_owner_id,
    set_current_owner_id,
)
from enums.async_task_status import AsyncTaskStatus
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.async_task_read_service import (
    get_async_task,
    list_async_task_reads,
)
from utils.db_utils import to_sync_sqlalchemy_url


def _postgres_urls() -> tuple[str, str]:
    require_postgres = os.getenv("PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION") == "1"
    configured_url = os.getenv("PPT_AGENT_POSTGRES_TEST_URL")
    if not configured_url:
        if require_postgres:
            pytest.fail(
                "PPT_AGENT_POSTGRES_TEST_URL is required by the PostgreSQL "
                "integration gate"
            )
        pytest.skip("set PPT_AGENT_POSTGRES_TEST_URL to run live PostgreSQL tests")

    sync_url = to_sync_sqlalchemy_url(configured_url)
    parsed = make_url(sync_url)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("PPT_AGENT_POSTGRES_TEST_URL must use PostgreSQL")
    if not re.search(r"(?:^|[_-])tests?$", parsed.database or "", re.IGNORECASE):
        pytest.fail(
            "refusing PostgreSQL integration test against a database whose "
            "name does not end in test or tests"
        )

    query = dict(parsed.query)
    if "sslmode" in query and "ssl" not in query:
        query["ssl"] = query.pop("sslmode")
    async_url = parsed.set(
        drivername="postgresql+asyncpg",
        query=query,
    ).render_as_string(hide_password=False)
    return sync_url, async_url


def test_postgresql_owner_status_filter_and_terminal_semantics():
    sync_url, async_url = _postgres_urls()
    schema = f"async_read_{uuid.uuid4().hex}"
    sync_engine = create_engine(sync_url, poolclass=NullPool)
    with sync_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    async def scenario():
        engine = create_async_engine(
            async_url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": schema}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid.uuid4()
        other_owner = uuid.uuid4()
        now = datetime.now(timezone.utc)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    SQLModel.metadata.create_all,
                    tables=[
                        AsyncPresentationGenerationTaskModel.__table__,
                        PresentationGenerationJob.__table__,
                    ],
                )

            rows = (
                ("pg-own-published", owner, "published", 0),
                ("pg-own-failed", owner, "failed", 1),
                ("pg-other-published", other_owner, "published", 2),
            )
            async with maker() as session:
                for task_id, owner_id, state, minute in rows:
                    timestamp = now + timedelta(minutes=minute)
                    task = AsyncPresentationGenerationTaskModel(
                        id=task_id,
                        owner_id=owner_id,
                        status="source-state",
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                    job = PresentationGenerationJob(
                        task_id=task_id,
                        presentation_id=uuid.uuid4(),
                        request_sha256="b" * 64,
                        request_payload={"source": "postgresql"},
                        state=state,
                        created_at=timestamp,
                        updated_at=timestamp + timedelta(seconds=5),
                    )
                    session.add(task)
                    session.add(job)
                await session.commit()

            token = set_current_owner_id(owner)
            try:
                async with maker() as session:
                    completed = await list_async_task_reads(
                        sql_session=session,
                        status=AsyncTaskStatus.COMPLETED,
                    )
                    failed = await get_async_task("pg-own-failed", session)
                    hidden = await get_async_task("pg-other-published", session)
            finally:
                reset_current_owner_id(token)

            assert [item.id for item in completed] == ["pg-own-published"]
            assert completed[0].terminal is True
            assert completed[0].status is AsyncTaskStatus.COMPLETED
            assert failed is not None
            assert failed.status is AsyncTaskStatus.ERROR
            assert failed.terminal is True
            assert hidden is None
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        with sync_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        sync_engine.dispose()
