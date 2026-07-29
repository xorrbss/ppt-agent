"""Disposable PostgreSQL coverage for Template V2 canary preflight and rollback.

This test resets the PostgreSQL ``public`` schema. It must only run against a
disposable database whose name ends in ``test`` or ``tests``; it is never a
managed/shared staging-database rehearsal.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from api.v1.ppt.endpoints.structured_templates import (
    delete_structured_template,
    get_structured_template,
    list_structured_templates,
)
from utils.db_utils import to_sync_sqlalchemy_url


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
CHECKER = FASTAPI_ROOT / "scripts" / "check_template_v2_canary.py"
TEMPLATE_ID = "postgres-canary-template"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(FASTAPI_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _reset_public_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def postgres_canary_database(tmp_path):
    require_postgres = os.getenv("PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION") == "1"
    configured_url = os.getenv("PPT_AGENT_POSTGRES_TEST_URL")
    if not configured_url:
        if require_postgres:
            pytest.fail(
                "PPT_AGENT_POSTGRES_TEST_URL is required by the PostgreSQL "
                "integration gate"
            )
        pytest.skip("set PPT_AGENT_POSTGRES_TEST_URL to run the canary rollback gate")

    sync_url = to_sync_sqlalchemy_url(configured_url)
    parsed_url = make_url(sync_url)
    if parsed_url.get_backend_name() != "postgresql":
        pytest.fail("PPT_AGENT_POSTGRES_TEST_URL must use PostgreSQL")
    database_name = parsed_url.database or ""
    if not re.search(r"(?:^|[_-])tests?$", database_name, re.IGNORECASE):
        pytest.fail(
            "refusing destructive canary rollback test against a database whose "
            "name does not end in test or tests"
        )

    engine = create_engine(sync_url, poolclass=NullPool)
    app_data = tmp_path / "app-data"
    private_imports = (
        app_data.parent / f"{app_data.name}-private" / "template-v2-imports"
    )
    app_data.mkdir()
    private_imports.mkdir(parents=True)
    runtime_url = parsed_url.set(
        drivername="postgresql+asyncpg"
    ).render_as_string(hide_password=False)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        _reset_public_schema(engine)
        command.upgrade(_alembic_config(sync_url), "head")
        yield engine, runtime_url, app_data
    finally:
        _reset_public_schema(engine)
        engine.dispose()


def _seed_template(engine: Engine) -> uuid.UUID:
    presentation_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO presentations
                    (
                        id, content, n_slides, language, title, created_at,
                        updated_at, mode, share_token, version
                    )
                VALUES
                    (
                        :id, 'PostgreSQL canary deck', 0, 'en', 'Canary',
                        :created_at, :updated_at, 'template', :share_token,
                        'v2-standard'
                    )
                """
            ),
            {
                "id": presentation_id,
                "created_at": now,
                "updated_at": now,
                "share_token": f"canary-{presentation_id.hex}",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO template_v2
                    (id, presentation_id, name, revision)
                VALUES
                    (:id, :presentation_id, 'PostgreSQL canary', 1)
                """
            ),
            {"id": TEMPLATE_ID, "presentation_id": presentation_id},
        )
    return presentation_id


def _state_snapshot(engine: Engine, presentation_id: uuid.UUID) -> tuple:
    with engine.connect() as connection:
        presentation = connection.execute(
            text(
                """
                SELECT id, content, title, version
                FROM presentations
                WHERE id = :id
                """
            ),
            {"id": presentation_id},
        ).one()
        template = connection.execute(
            text(
                """
                SELECT id, presentation_id, name, revision
                FROM template_v2
                WHERE id = :id
                """
            ),
            {"id": TEMPLATE_ID},
        ).one()
        revision = connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
    return presentation, template, revision


def _run_checker(runtime_url: str, app_data: Path, *, enabled: bool):
    environment = {
        **os.environ,
        "DATABASE_URL": runtime_url,
        "APP_DATA_DIRECTORY": str(app_data),
        "TEMPLATE_V2_DEPLOYMENT_TIER": "production",
        "ENABLE_TEMPLATE_V2": "true" if enabled else "false",
        "TEMPLATE_V2_TEMPLATE_ALLOWLIST": TEMPLATE_ID,
    }
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=FASTAPI_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.stderr == ""
    return result, json.loads(result.stdout)


async def _exercise_api_contract(runtime_url: str, monkeypatch) -> None:
    engine = create_async_engine(runtime_url, poolclass=NullPool)
    session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        async with session_maker() as session:
            monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
            listed = await list_structured_templates(sql_session=session)
            assert [template.id for template in listed] == [TEMPLATE_ID]
            assert (
                await get_structured_template(TEMPLATE_ID, sql_session=session)
            ).id == TEMPLATE_ID

            monkeypatch.setenv("ENABLE_TEMPLATE_V2", "false")
            assert await list_structured_templates(sql_session=session) == []
            assert (
                await get_structured_template(TEMPLATE_ID, sql_session=session)
            ).id == TEMPLATE_ID
            with pytest.raises(HTTPException) as blocked:
                await delete_structured_template(TEMPLATE_ID, sql_session=session)
            assert blocked.value.status_code == 403
            assert blocked.value.detail == "template_v2_creation_disabled"
    finally:
        await engine.dispose()


def test_postgresql_canary_preflight_and_flag_rollback_preserve_state(
    postgres_canary_database,
    monkeypatch,
):
    engine, runtime_url, app_data = postgres_canary_database
    presentation_id = _seed_template(engine)
    before = _state_snapshot(engine, presentation_id)

    enabled_result, enabled_payload = _run_checker(
        runtime_url,
        app_data,
        enabled=True,
    )
    assert enabled_result.returncode == 0
    assert enabled_payload["ready"] is True
    assert enabled_payload["database_reachable"] is True
    assert enabled_payload["schema_at_head"] is True
    assert enabled_payload["private_storage_ready"] is True
    assert enabled_payload["health_code"] == "template_v2_operations_healthy"

    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", TEMPLATE_ID)
    asyncio.run(_exercise_api_contract(runtime_url, monkeypatch))

    disabled_result, disabled_payload = _run_checker(
        runtime_url,
        app_data,
        enabled=False,
    )
    assert disabled_result.returncode == 2
    assert disabled_payload["ready"] is False
    assert disabled_payload["code"] == "template_v2_feature_disabled"
    assert _state_snapshot(engine, presentation_id) == before
