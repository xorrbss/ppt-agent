from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select

from models.sql.asset_gateway_idempotency_receipt import (
    AssetGatewayIdempotencyReceipt,
)
from services.asset_gateway_idempotency_service import (
    ASSET_GATEWAY_RECEIPT_CLEANUP_BATCH_SIZE,
    cleanup_expired_asset_gateway_idempotency_receipts,
)
from services import workspace_asset_maintenance


async def _receipt_database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: AssetGatewayIdempotencyReceipt.__table__.create(
                sync_connection,
                checkfirst=True,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_global_receipt_cleanup_is_cross_owner_and_bounded(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)

    async def scenario() -> None:
        engine, sessions = await _receipt_database(tmp_path / "global-cleanup.db")
        try:
            async with sessions() as session:
                for index in range(ASSET_GATEWAY_RECEIPT_CLEANUP_BATCH_SIZE + 2):
                    owner_scope = str(uuid.uuid4())
                    session.add(
                        AssetGatewayIdempotencyReceipt(
                            owner_scope=owner_scope,
                            operation="document.upload",
                            key_hash=f"{index:064x}",
                            request_sha256="a" * 64,
                            expires_at=now - timedelta(minutes=1),
                        )
                    )
                await session.commit()

            async with sessions() as session:
                removed = (
                    await cleanup_expired_asset_gateway_idempotency_receipts(
                        session,
                        now=now,
                        limit=10_000,
                    )
                )
                assert removed == ASSET_GATEWAY_RECEIPT_CLEANUP_BATCH_SIZE

            async with sessions() as session:
                remaining = await session.scalar(
                    select(func.count()).select_from(
                        AssetGatewayIdempotencyReceipt
                    )
                )
                assert remaining == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_workspace_asset_maintenance_runs_global_receipt_cleanup(monkeypatch) -> None:
    calls: list[datetime] = []
    now = datetime.now(timezone.utc)

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    def session_factory():
        return SessionContext()

    async def reconcile(**_kwargs):
        return 0

    async def process(**_kwargs):
        return 0

    async def count_zero(*_args, **_kwargs):
        return 0

    async def cleanup_receipts(_session, *, now):
        calls.append(now)
        return 3

    async def cleanup_artifacts(**_kwargs):
        return {
            "claimed": 0,
            "deleted": 0,
            "retry_pending": 0,
            "dead_lettered": 0,
            "stale": 0,
        }

    monkeypatch.setattr(
        workspace_asset_maintenance,
        "reconcile_timed_out_workspace_assets",
        reconcile,
    )
    monkeypatch.setattr(
        workspace_asset_maintenance,
        "process_pending_workspace_assets",
        process,
    )
    monkeypatch.setattr(
        workspace_asset_maintenance,
        "expire_orphan_workspace_assets",
        count_zero,
    )
    monkeypatch.setattr(
        workspace_asset_maintenance,
        "cleanup_expired_workspace_assets",
        count_zero,
    )
    monkeypatch.setattr(
        workspace_asset_maintenance,
        "cleanup_expired_reference_snapshots",
        count_zero,
    )
    monkeypatch.setattr(
        workspace_asset_maintenance,
        "cleanup_expired_asset_gateway_idempotency_receipts",
        cleanup_receipts,
    )
    monkeypatch.setattr(
        workspace_asset_maintenance,
        "sweep_workspace_artifact_cleanup",
        cleanup_artifacts,
    )

    result = asyncio.run(
        workspace_asset_maintenance.run_workspace_asset_maintenance(
            session_factory=session_factory,  # type: ignore[arg-type]
            now=now,
        )
    )
    assert calls == [now]
    assert result["idempotency_receipts_deleted"] == 3
