"""Disposable PostgreSQL coverage for Template V2 canary preflight and rollback.

This test resets the PostgreSQL ``public`` schema. It must only run against a
disposable database whose name ends in ``test`` or ``tests``; it is never a
managed/shared staging-database rehearsal.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from alembic import command
from api.v1.ppt.endpoints.structured_templates import (
    delete_structured_template,
    get_structured_template,
    list_structured_templates,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services import template_v2_pptx_ingestion_service as ingestion
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
    runtime_url = parsed_url.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
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
    now = datetime.now(UTC)
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
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    return presentation, template, revision


def _run_checker(
    runtime_url: str,
    app_data: Path,
    *,
    enabled: bool,
    extra_environment: dict[str, str] | None = None,
):
    environment = {
        **os.environ,
        "DATABASE_URL": runtime_url,
        "APP_DATA_DIRECTORY": str(app_data),
        "TEMPLATE_V2_DEPLOYMENT_TIER": "production",
        "ENABLE_TEMPLATE_V2": "true" if enabled else "false",
        "TEMPLATE_V2_TEMPLATE_ALLOWLIST": TEMPLATE_ID,
        "TEMPLATE_V2_PPTX_MALWARE_SCAN_MODE": "disabled",
        **(extra_environment or {}),
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


def _async_node(runtime_url: str):
    engine = create_async_engine(runtime_url, poolclass=NullPool)
    maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine, maker


async def _seed_import_jobs(session_maker, *, count: int):
    now = datetime.now(UTC)
    jobs = []
    tasks = []
    imports = []
    for index in range(count):
        import_id = uuid.uuid4()
        task_id = f"postgres-multinode-{import_id.hex}"
        jobs.append((import_id, task_id))
        tasks.append(
            AsyncPresentationGenerationTaskModel(
                id=task_id,
                status="pending",
                message="Queued Template V2 PPTX import",
                created_at=now.replace(tzinfo=None),
                updated_at=now.replace(tzinfo=None),
                data={
                    "kind": ingestion.IMPORT_TASK_KIND,
                    "import_id": str(import_id),
                    "state": "queued",
                    "attempt_number": 0,
                },
            )
        )
        imports.append(
            TemplateV2PptxImport(
                id=import_id,
                task_id=task_id,
                requested_template_id=f"postgres-load-{index}",
                state="queued",
                source_filename="content-free.pptx",
                source_media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "presentationml.presentation"
                ),
                source_size_bytes=1,
                source_sha256=f"{index:064x}",
                source_storage_key=f"private/{import_id}/source.pptx",
                manifest={"fixture": "postgres-multinode-content-free"},
                created_at=now,
                updated_at=now,
            )
        )
    async with session_maker() as session:
        session.add_all(tasks)
        await session.flush()
        session.add_all(imports)
        await session.commit()
    return jobs


def test_postgresql_multinode_claim_load_has_exactly_one_owner_per_job(
    postgres_canary_database,
):
    _, runtime_url, _ = postgres_canary_database

    async def scenario() -> None:
        nodes = [_async_node(runtime_url) for _ in range(4)]
        engines = [engine for engine, _ in nodes]
        makers = [maker for _, maker in nodes]
        try:
            jobs = await _seed_import_jobs(makers[0], count=12)
            claimed_at = datetime.now(UTC)

            async def claim_from_node(node_index, maker):
                wins = []
                for import_id, task_id in jobs:
                    token = f"node-{node_index}-{import_id.hex}"
                    async with maker() as session:
                        claimed = await ingestion.claim_template_v2_pptx_import(
                            session,
                            import_id,
                            task_id,
                            token=token,
                            now=claimed_at,
                        )
                    if claimed is not None:
                        wins.append((import_id, claimed))
                return wins

            node_wins = await asyncio.gather(
                *(
                    claim_from_node(node_index, maker)
                    for node_index, maker in enumerate(makers)
                )
            )
            wins = [win for node in node_wins for win in node]
            assert len(wins) == len(jobs)
            assert {import_id for import_id, _ in wins} == {
                import_id for import_id, _ in jobs
            }

            async with makers[0]() as session:
                rows = (
                    await session.execute(
                        select(TemplateV2PptxImport).where(
                            TemplateV2PptxImport.id.in_(
                                [import_id for import_id, _ in jobs]
                            )
                        )
                    )
                ).scalars()
                persisted = list(rows)
            assert len(persisted) == len(jobs)
            assert all(row.state == "processing" for row in persisted)
            assert all(row.attempt_number == 1 for row in persisted)
            assert len({row.attempt_token for row in persisted}) == len(jobs)
        finally:
            await asyncio.gather(*(engine.dispose() for engine in engines))

    asyncio.run(scenario())


def test_postgresql_multinode_lease_recovery_is_single_winner_and_fenced(
    postgres_canary_database,
    monkeypatch,
):
    _, runtime_url, _ = postgres_canary_database

    async def scenario() -> None:
        nodes = [_async_node(runtime_url) for _ in range(4)]
        engines = [engine for engine, _ in nodes]
        makers = [maker for _, maker in nodes]
        try:
            ((import_id, task_id),) = await _seed_import_jobs(
                makers[0],
                count=1,
            )
            claimed_at = datetime.now(UTC)
            old_token = "node-0-old-attempt"
            async with makers[0]() as session:
                assert (
                    await ingestion.claim_template_v2_pptx_import(
                        session,
                        import_id,
                        task_id,
                        token=old_token,
                        now=claimed_at,
                    )
                    == old_token
                )

            monkeypatch.setattr(ingestion, "async_session_maker", makers[1])
            heartbeat_at = claimed_at + timedelta(seconds=1)
            assert await ingestion.heartbeat_template_v2_pptx_import(
                import_id,
                task_id,
                old_token,
                now=heartbeat_at,
            )

            async def recover(maker, recovery_time):
                async with maker() as session:
                    return await ingestion.recover_stalled_template_v2_pptx_imports(
                        session,
                        now=recovery_time,
                    )

            before_expiry = claimed_at + ingestion.IMPORT_LEASE_DURATION
            assert (
                sum(
                    await asyncio.gather(
                        recover(makers[0], before_expiry),
                        recover(makers[1], before_expiry),
                    )
                )
                == 0
            )

            after_expiry = heartbeat_at + ingestion.IMPORT_LEASE_DURATION
            assert (
                sum(
                    await asyncio.gather(
                        recover(makers[0], after_expiry),
                        recover(makers[1], after_expiry),
                    )
                )
                == 1
            )

            async def reclaim(node_index, maker):
                token = f"node-{node_index}-replacement"
                async with maker() as session:
                    return await ingestion.claim_template_v2_pptx_import(
                        session,
                        import_id,
                        task_id,
                        token=token,
                        now=after_expiry,
                    )

            reclaimed = [
                token
                for token in await asyncio.gather(
                    *(
                        reclaim(node_index, maker)
                        for node_index, maker in enumerate(makers)
                    )
                )
                if token is not None
            ]
            assert len(reclaimed) == 1
            new_token = reclaimed[0]

            monkeypatch.setattr(ingestion, "async_session_maker", makers[2])
            assert (
                await ingestion.fail_template_v2_pptx_import(
                    import_id,
                    task_id,
                    old_token,
                    RuntimeError("stale owner must be fenced"),
                )
                is False
            )
            assert await ingestion.fail_template_v2_pptx_import(
                import_id,
                task_id,
                new_token,
                RuntimeError("content-free terminal fixture"),
            )

            async with makers[3]() as session:
                import_job = await session.get(
                    TemplateV2PptxImport,
                    import_id,
                )
                task = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    task_id,
                )
            assert import_job is not None
            assert import_job.state == "failed"
            assert import_job.attempt_number == 2
            assert import_job.attempt_token is None
            assert task is not None
            assert task.status == "error"
            assert task.data["attempt_number"] == 2
        finally:
            await asyncio.gather(*(engine.dispose() for engine in engines))

    asyncio.run(scenario())


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
    assert enabled_payload["malware_scan_ready"] is True
    assert enabled_payload["malware_scan_mode"] == "disabled"
    assert enabled_payload["health_code"] == "template_v2_operations_healthy"

    scanner_result, scanner_payload = _run_checker(
        runtime_url,
        app_data,
        enabled=True,
        extra_environment={
            "TEMPLATE_V2_PPTX_MALWARE_SCAN_MODE": "required",
            "TEMPLATE_V2_PPTX_MALWARE_SCANNER": (
                "definitely-missing-template-v2-scanner"
            ),
        },
    )
    assert scanner_result.returncode == 2
    assert scanner_payload["ready"] is False
    assert scanner_payload["malware_scan_ready"] is False
    assert scanner_payload["code"] == "template_v2_pptx_malware_scanner_unavailable"

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
