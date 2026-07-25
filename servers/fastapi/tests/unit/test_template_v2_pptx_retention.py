from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services import template_v2_pptx_ingestion_service as ingestion
from services import template_v2_pptx_retention_service as retention
from services.template_v2_pptx_storage import (
    get_private_source_retention_ttl,
    resolve_private_source,
)


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[
                    AsyncPresentationGenerationTaskModel.__table__,
                    TemplateV2PptxImport.__table__,
                ],
            )
        )
    return engine, maker


async def _insert_import(
    maker,
    *,
    state: str,
    updated_at: datetime,
    retention_expires_at: datetime | None,
    storage_key: str | None = None,
    task_status: str | None = None,
    cleanup_token: str | None = None,
) -> tuple[uuid.UUID, str, str]:
    import_id = uuid.uuid4()
    task_id = f"task-{uuid.uuid4().hex}"
    key = storage_key or f"{import_id}/source.pptx"
    async with maker() as session:
        session.add(
            AsyncPresentationGenerationTaskModel(
                id=task_id,
                status=task_status or ("error" if state == "failed" else "pending"),
                data={},
                created_at=updated_at,
                updated_at=updated_at,
            )
        )
        session.add(
            TemplateV2PptxImport(
                id=import_id,
                task_id=task_id,
                requested_template_id=f"template-{uuid.uuid4().hex}",
                state=state,
                attempt_number=1,
                source_retention_expires_at=retention_expires_at,
                source_cleanup_token=cleanup_token,
                source_cleanup_lease_expires_at=(
                    updated_at + timedelta(minutes=5)
                    if cleanup_token
                    else None
                ),
                source_filename="source.pptx",
                source_media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "presentationml.presentation"
                ),
                source_size_bytes=4,
                source_sha256="a" * 64,
                source_storage_key=key,
                manifest={"schema_version": 1},
                created_at=updated_at,
                updated_at=updated_at,
            )
        )
        await session.commit()
    return import_id, task_id, key


def _write_source(storage_key: str) -> Path:
    source = resolve_private_source(storage_key)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"pptx")
    return source


def test_private_source_ttl_has_bounded_explicit_override(monkeypatch) -> None:
    monkeypatch.delenv("TEMPLATE_V2_PPTX_SOURCE_TTL_DAYS", raising=False)
    assert get_private_source_retention_ttl() == timedelta(days=7)
    monkeypatch.setenv("TEMPLATE_V2_PPTX_SOURCE_TTL_DAYS", "30")
    assert get_private_source_retention_ttl() == timedelta(days=30)
    for value, code in [
        ("0", "template_v2_pptx_source_ttl_days_out_of_range"),
        ("91", "template_v2_pptx_source_ttl_days_out_of_range"),
        ("1.5", "invalid_template_v2_pptx_source_ttl_days"),
        ("-1", "invalid_template_v2_pptx_source_ttl_days"),
    ]:
        monkeypatch.setenv("TEMPLATE_V2_PPTX_SOURCE_TTL_DAYS", value)
        with pytest.raises(RuntimeError, match=code):
            get_private_source_retention_ttl()


def test_cleanup_deletes_at_expiry_and_persists_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "expiry.sqlite")
        monkeypatch.setattr(retention, "async_session_maker", maker)
        now = datetime.now(timezone.utc)
        try:
            import_id, _, key = await _insert_import(
                maker,
                state="review_required",
                updated_at=now - timedelta(days=7),
                retention_expires_at=now,
            )
            source = _write_source(key)
            summary = await retention.cleanup_expired_private_sources(now=now)
            assert summary.claimed == summary.deleted == 1
            assert not source.exists()
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                assert job is not None
                assert job.source_deleted_at is not None
                assert job.source_cleanup_attempted_at is not None
                cleanup = job.manifest["private_source_retention"]["cleanup"]
                assert cleanup["result"] == "deleted"
                assert cleanup["attempted_at"] == now.isoformat()
        finally:
            await engine.dispose()

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("state", "expect_deleted"),
    [("cancelled", True), ("confirmed", False)],
)
def test_cancelled_sources_are_reclaimed_but_confirmed_ones_are_retained(
    tmp_path: Path,
    monkeypatch,
    state: str,
    expect_deleted: bool,
) -> None:
    """Cancelling abandons the upload; confirming keeps it for audit against the deck.

    `cancel_template_v2_pptx_import` always writes a retention deadline, so leaving
    `cancelled` out of the cleanup states retained the private source forever.
    """

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / f"{state}.sqlite")
        monkeypatch.setattr(retention, "async_session_maker", maker)
        now = datetime.now(timezone.utc)
        try:
            import_id, _, key = await _insert_import(
                maker,
                state=state,
                updated_at=now - timedelta(days=7),
                retention_expires_at=now,
                task_status="completed",
            )
            source = _write_source(key)

            summary = await retention.cleanup_expired_private_sources(now=now)

            assert source.exists() is not expect_deleted
            assert summary.deleted == int(expect_deleted)
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                assert job is not None
                assert (job.source_deleted_at is not None) is expect_deleted
        finally:
            await engine.dispose()

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    asyncio.run(scenario())


def test_restart_cleanup_initializes_legacy_terminal_deadline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "legacy.sqlite")
        monkeypatch.setattr(retention, "async_session_maker", maker)
        now = datetime.now(timezone.utc)
        try:
            import_id, _, key = await _insert_import(
                maker,
                state="failed",
                updated_at=now - timedelta(days=8),
                retention_expires_at=None,
            )
            source = _write_source(key)
            summary = await retention.cleanup_expired_private_sources(now=now)
            assert summary.initialized == 1
            assert summary.claimed == summary.deleted == 1
            assert not source.exists()
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                assert job is not None
                assert job.source_retention_expires_at is not None
                assert job.source_deleted_at is not None
        finally:
            await engine.dispose()

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    asyncio.run(scenario())


def test_cleanup_preserves_active_recent_and_claimed_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "preserve.sqlite")
        monkeypatch.setattr(retention, "async_session_maker", maker)
        now = datetime.now(timezone.utc)
        paths: list[Path] = []
        try:
            for state, expiry, token in [
                ("queued", now - timedelta(days=1), None),
                ("processing", now - timedelta(days=1), None),
                ("failed", now + timedelta(seconds=1), None),
                ("failed", now - timedelta(days=1), "cleanup-owner"),
            ]:
                _, _, key = await _insert_import(
                    maker,
                    state=state,
                    updated_at=now,
                    retention_expires_at=expiry,
                    cleanup_token=token,
                )
                paths.append(_write_source(key))
            summary = await retention.cleanup_expired_private_sources(now=now)
            assert summary.claimed == 0
            assert all(path.exists() for path in paths)
        finally:
            await engine.dispose()

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    asyncio.run(scenario())


def test_cleanup_revalidates_unsafe_storage_key_and_records_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "unsafe.sqlite")
        monkeypatch.setattr(retention, "async_session_maker", maker)
        now = datetime.now(timezone.utc)
        outside = tmp_path / "outside.pptx"
        outside.write_bytes(b"keep")
        try:
            import_id, _, _ = await _insert_import(
                maker,
                state="failed",
                updated_at=now - timedelta(days=8),
                retention_expires_at=now - timedelta(days=1),
                storage_key="../outside.pptx",
            )
            summary = await retention.cleanup_expired_private_sources(now=now)
            assert summary.claimed == summary.failed == 1
            assert outside.read_bytes() == b"keep"
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                assert job is not None
                assert job.source_deleted_at is None
                assert job.source_cleanup_token is None
                cleanup = job.manifest["private_source_retention"]["cleanup"]
                assert cleanup == {
                    "attempted_at": now.isoformat(),
                    "result": "failed",
                    "error_code": "invalid_private_storage_key",
                }
        finally:
            await engine.dispose()

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    asyncio.run(scenario())


def test_cleanup_io_failure_is_best_effort_and_audited(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "failure.sqlite")
        monkeypatch.setattr(retention, "async_session_maker", maker)
        monkeypatch.setattr(
            retention,
            "cleanup_private_source",
            lambda _key: (_ for _ in ()).throw(PermissionError("denied")),
        )
        now = datetime.now(timezone.utc)
        try:
            import_id, _, _ = await _insert_import(
                maker,
                state="review_required",
                updated_at=now - timedelta(days=8),
                retention_expires_at=now - timedelta(days=1),
            )
            summary = await retention.cleanup_expired_private_sources(now=now)
            assert summary.claimed == summary.failed == 1
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                assert job is not None
                assert job.source_deleted_at is None
                cleanup = job.manifest["private_source_retention"]["cleanup"]
                assert cleanup["error_code"] == "private_source_cleanup_io_error"
        finally:
            await engine.dispose()

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    asyncio.run(scenario())


def test_retry_cannot_race_expiry_or_cleanup_claim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "retry.sqlite")
        monkeypatch.setattr(ingestion, "async_session_maker", maker)
        now = datetime.now(timezone.utc)
        try:
            expired_id, expired_task, _ = await _insert_import(
                maker,
                state="failed",
                task_status="error",
                updated_at=now - timedelta(days=7),
                retention_expires_at=now,
            )
            claimed_id, claimed_task, _ = await _insert_import(
                maker,
                state="failed",
                task_status="error",
                updated_at=now,
                retention_expires_at=now + timedelta(days=1),
                cleanup_token="cleanup-owner",
            )
            recent_id, recent_task, _ = await _insert_import(
                maker,
                state="failed",
                task_status="error",
                updated_at=now,
                retention_expires_at=now + timedelta(days=1),
            )
            outcomes = []
            for import_id, task_id in [
                (expired_id, expired_task),
                (claimed_id, claimed_task),
                (recent_id, recent_task),
            ]:
                async with maker() as session:
                    outcomes.append(
                        await ingestion.requeue_failed_template_v2_pptx_import(
                            session,
                            import_id,
                            task_id,
                            {"schema_version": 1},
                            now=now,
                        )
                    )
            assert outcomes == [False, False, True]
            async with maker() as session:
                recent = await session.get(TemplateV2PptxImport, recent_id)
                assert recent is not None
                assert recent.state == "queued"
                assert recent.source_retention_expires_at is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_dispatcher_runs_cleanup_before_dispatch(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
        monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "safe-template")
        events: list[str] = []

        async def cleanup():
            events.append("cleanup")

        async def dispatch():
            events.append("dispatch")
            return 0

        monkeypatch.setattr(
            ingestion,
            "maybe_cleanup_expired_private_sources",
            cleanup,
        )
        monkeypatch.setattr(
            ingestion,
            "dispatch_template_v2_pptx_imports_once",
            dispatch,
        )
        ingestion._dispatcher_task = None
        await ingestion.start_template_v2_pptx_dispatcher()
        await ingestion.stop_template_v2_pptx_dispatcher()
        assert events[:2] == ["cleanup", "dispatch"]

    asyncio.run(scenario())


def test_dispatcher_stays_stopped_when_template_v2_is_disabled(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.delenv("ENABLE_TEMPLATE_V2", raising=False)
        monkeypatch.delenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", raising=False)
        ingestion._dispatcher_task = None
        ingestion._dispatcher_stop = None
        ingestion._dispatcher_wake = None

        await ingestion.start_template_v2_pptx_dispatcher()

        assert ingestion._dispatcher_task is None
        assert ingestion._dispatcher_stop is None
        assert ingestion._dispatcher_wake is None

    asyncio.run(scenario())
