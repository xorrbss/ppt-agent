from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from scripts import check_template_v2_operations as operations_script
from services import template_v2_pptx_operations as operations
from services import template_v2_pptx_retention_service as retention
from services.template_v2_pptx_storage import PrivateStorageHealth
from utils.datetime_utils import get_current_utc_datetime


def _import_job(*, state: str, now, lease_expires_at=None, retention_at=None):
    import_id = uuid.uuid4()
    return TemplateV2PptxImport(
        id=import_id,
        task_id=f"task-{import_id}",
        requested_template_id="content-free-template",
        state=state,
        lease_expires_at=lease_expires_at,
        source_retention_expires_at=retention_at,
        source_filename="private.pptx",
        source_media_type=(
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
        source_size_bytes=1,
        source_sha256="a" * 64,
        source_storage_key=f"private/{import_id}/source.pptx",
        created_at=now,
        updated_at=now,
    )


def test_managed_canary_requires_postgresql_but_local_sqlite_remains_valid():
    local = operations.template_v2_database_safety(
        {},
        configured_database_url="sqlite+aiosqlite:///local.db",
    )
    production_sqlite = operations.template_v2_database_safety(
        {"TEMPLATE_V2_DEPLOYMENT_TIER": "production"},
        configured_database_url="sqlite+aiosqlite:///production.db",
    )
    staging_postgres = operations.template_v2_database_safety(
        {"TEMPLATE_V2_DEPLOYMENT_TIER": "staging"},
        configured_database_url="postgresql+asyncpg://db/presenton",
    )
    invalid_url = operations.template_v2_database_safety(
        {},
        configured_database_url="not a database url",
    )

    assert local.safe is True
    assert production_sqlite.as_dict() == {
        "safe": False,
        "code": "template_v2_managed_canary_requires_postgresql",
        "deployment_tier": "production",
        "database_backend": "sqlite",
    }
    assert staging_postgres.safe is True
    assert invalid_url.code == "template_v2_database_url_invalid"
    assert invalid_url.database_backend == "invalid"


def test_enabled_managed_canary_fails_closed_on_sqlite():
    try:
        operations.require_template_v2_database_safety(
            feature_enabled=True,
            environ={"TEMPLATE_V2_DEPLOYMENT_TIER": "production"},
            configured_database_url="sqlite+aiosqlite:///production.db",
        )
    except RuntimeError as error:
        assert str(error) == "template_v2_managed_canary_requires_postgresql"
    else:
        raise AssertionError("enabled production canary must reject SQLite")


def test_operational_status_blocks_rollback_and_reports_warning_aggregates(
    tmp_path,
    monkeypatch,
):
    async def scenario() -> None:
        now = get_current_utc_datetime()
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ops.db'}")
        maker = async_sessionmaker(
            engine,
            class_=SQLModelAsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(operations, "_get_session_maker", lambda: maker)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    TemplateV2PptxImport.metadata.create_all,
                )
            async with maker() as session:
                session.add_all(
                    [
                        _import_job(state="queued", now=now),
                        _import_job(
                            state="processing",
                            now=now,
                            lease_expires_at=now - timedelta(seconds=1),
                        ),
                        _import_job(state="review_required", now=now),
                        _import_job(
                            state="failed",
                            now=now,
                            retention_at=now - timedelta(seconds=1),
                        ),
                        _import_job(
                            state="confirmed",
                            now=now,
                            retention_at=now - timedelta(seconds=1),
                        ),
                    ]
                )
                await session.commit()

            status = await operations.get_template_v2_operational_status(now=now)

            assert status.rollback_safe is False
            assert status.rollback_blocking_count == 4
            assert status.active_count == 1
            assert status.stale_active_count == 1
            assert status.failed_count == 1
            assert status.review_required_count == 1
            assert status.overdue_cleanup_count == 1
            assert status.health_code == "template_v2_stale_imports_detected"

            logger = Mock()
            await operations.log_template_v2_operational_health(logger=logger)
            logger.warning.assert_called_once()
            payload = logger.warning.call_args.args[1]
            assert "private.pptx" not in payload
            event = json.loads(payload)
            assert event["stale_active_count"] == 1
            assert event["rollback_safe"] is False
            assert event["rollback_code"] == "template_v2_rollback_drain_required"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_review_required_import_is_degraded_when_no_higher_priority_issue(
    tmp_path,
    monkeypatch,
):
    async def scenario() -> None:
        now = get_current_utc_datetime()
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'review-required.db'}"
        )
        maker = async_sessionmaker(
            engine,
            class_=SQLModelAsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(operations, "_get_session_maker", lambda: maker)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    TemplateV2PptxImport.metadata.create_all,
                )
            async with maker() as session:
                session.add(_import_job(state="review_required", now=now))
                await session.commit()

            status = await operations.get_template_v2_operational_status(now=now)

            assert status.healthy is False
            assert status.review_required_count == 1
            assert (
                status.health_code
                == "template_v2_review_required_imports_require_attention"
            )
            logger = Mock()
            await operations.log_template_v2_operational_health(logger=logger)
            logger.warning.assert_called_once()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_private_source_cleanup_loop_is_not_gated_by_rollout_policy(
    monkeypatch,
):
    async def scenario() -> None:
        calls = 0

        async def cleanup():
            nonlocal calls
            calls += 1
            return retention.SourceCleanupSummary()

        monkeypatch.delenv("ENABLE_TEMPLATE_V2", raising=False)
        monkeypatch.setattr(
            retention,
            "maybe_cleanup_expired_private_sources",
            cleanup,
        )
        retention._cleanup_task = None
        retention._cleanup_stop = None

        await retention.start_template_v2_private_source_cleanup()
        await retention.stop_template_v2_private_source_cleanup()

        assert calls >= 1
        assert retention._cleanup_task is None
        assert retention._cleanup_stop is None

    asyncio.run(scenario())


def test_operations_cli_uses_exit_two_for_unsafe_rollback(monkeypatch, capsys):
    async def fake_run(mode):
        return (
            {
                "mode": mode,
                "rollback_safe": False,
                "rollback_blocking_count": 2,
            },
            False,
        )

    monkeypatch.setattr(operations_script, "_run", fake_run)

    assert operations_script.main(["--mode", "rollback"]) == 2
    output = capsys.readouterr().out
    assert '"rollback_blocking_count": 2' in output
    assert "private.pptx" not in output


def test_operations_health_is_read_only_and_checks_database_safety_first(
    monkeypatch,
):
    events = []
    status = operations.TemplateV2OperationalStatus(
        healthy=True,
        health_code="template_v2_healthy",
        rollback_safe=True,
        rollback_code="template_v2_rollback_safe",
        rollback_blocking_count=0,
        active_count=0,
        stale_active_count=0,
        failed_count=0,
        review_required_count=0,
        overdue_cleanup_count=0,
    )

    def require_safety(*, feature_enabled):
        assert feature_enabled is True
        events.append("safety")

    async def get_status():
        events.append("status")
        return status

    async def dispose():
        events.append("dispose")

    make_directory = Mock()
    monkeypatch.setattr(
        operations_script,
        "require_template_v2_database_safety",
        require_safety,
    )
    monkeypatch.setattr(
        operations_script,
        "get_template_v2_operational_status",
        get_status,
    )
    monkeypatch.setattr(
        operations_script,
        "get_private_storage_health",
        lambda: PrivateStorageHealth(
            ready=True,
            code="template_v2_private_storage_ready",
        ),
    )
    monkeypatch.setattr(operations_script, "dispose_engines", dispose)
    monkeypatch.setattr(operations_script.os, "makedirs", make_directory)

    payload, ready = asyncio.run(operations_script._run("health"))

    assert ready is True
    assert payload["health_code"] == "template_v2_healthy"
    assert events == ["safety", "status", "dispose"]
    make_directory.assert_not_called()


def test_operations_health_fails_closed_when_private_volume_is_missing(
    monkeypatch,
):
    events = []

    def require_safety(*, feature_enabled):
        assert feature_enabled is True
        events.append("safety")

    async def get_status():
        events.append("status")

    async def dispose():
        events.append("dispose")

    monkeypatch.setattr(
        operations_script,
        "require_template_v2_database_safety",
        require_safety,
    )
    monkeypatch.setattr(
        operations_script,
        "get_template_v2_operational_status",
        get_status,
    )
    monkeypatch.setattr(
        operations_script,
        "get_private_storage_health",
        lambda: PrivateStorageHealth(
            ready=False,
            code="template_v2_private_storage_missing",
        ),
    )
    monkeypatch.setattr(operations_script, "dispose_engines", dispose)

    payload, ready = asyncio.run(operations_script._run("health"))

    assert ready is False
    assert payload == {
        "mode": "health",
        "private_storage_ready": False,
        "private_storage_code": "template_v2_private_storage_missing",
    }
    assert events == ["safety", "dispose"]


def test_operations_health_rejects_unsafe_database_before_query(monkeypatch):
    events = []

    def require_safety(*, feature_enabled):
        assert feature_enabled is True
        events.append("safety")
        raise RuntimeError("template_v2_managed_canary_requires_postgresql")

    async def get_status():
        events.append("status")

    async def dispose():
        events.append("dispose")

    make_directory = Mock()
    monkeypatch.setattr(
        operations_script,
        "require_template_v2_database_safety",
        require_safety,
    )
    monkeypatch.setattr(
        operations_script,
        "get_template_v2_operational_status",
        get_status,
    )
    monkeypatch.setattr(operations_script, "dispose_engines", dispose)
    monkeypatch.setattr(operations_script.os, "makedirs", make_directory)

    with pytest.raises(
        RuntimeError,
        match="template_v2_managed_canary_requires_postgresql",
    ):
        asyncio.run(operations_script._run("health"))

    assert events == ["safety", "dispose"]
    make_directory.assert_not_called()


def test_operations_cli_redacts_unexpected_failure(monkeypatch, capsys):
    async def fake_run(_mode):
        raise RuntimeError("postgresql://operator:secret@database/presenton")

    monkeypatch.setattr(operations_script, "_run", fake_run)

    assert operations_script.main(["--mode", "health"]) == 2
    output = capsys.readouterr().out
    assert "template_v2_operations_check_failed" in output
    assert "secret" not in output
