"""Background reconciliation for durable workspace assets.

Asset processors are allowed to disappear after an upload has been attached to
an operation.  This sweeper turns that otherwise-infinite wait into a durable,
retryable failure card and also performs the orphan expiry/physical cleanup
promised by ADR-005.  All state changes still flow through the same fenced asset
transition service used by trusted in-process workers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.workspace import WorkspaceAsset
from models.workspace import WorkspaceAssetTransitionRequest
from services.database import async_session_maker
from services.workspace_artifact_publication_service import (
    sweep_workspace_artifact_cleanup,
)
from services.workspace_asset_service import (
    cleanup_expired_workspace_assets,
    expire_orphan_workspace_assets,
    process_workspace_asset,
    transition_workspace_asset,
)
from services.workspace_service import WorkspaceServiceError
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import reset_current_owner_id, set_current_owner_id


logger = logging.getLogger(__name__)

DEFAULT_ASSET_PROCESSING_TIMEOUT_SECONDS = 30 * 60
DEFAULT_ASSET_MAINTENANCE_INTERVAL_SECONDS = 60
_PROCESSING_STATES = frozenset({"uploaded", "scanning", "processing"})

_maintenance_task: asyncio.Task[None] | None = None
_maintenance_stop: asyncio.Event | None = None


def _configured_positive_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value) if value > 0 else default


async def reconcile_timed_out_workspace_assets(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    now: datetime | None = None,
    timeout_seconds: int | None = None,
    limit: int = 100,
) -> int:
    """Fail attached assets that stopped making progress before their deadline.

    Discovery deliberately ignores request owner scope, then each transition is
    executed under the row's owner context.  This preserves tenant filtering for
    attachment/operation projection while allowing one process-wide sweeper.
    Version CAS makes a racing worker transition win safely.
    """

    current = now or get_current_utc_datetime()
    timeout = timeout_seconds or _configured_positive_int(
        "WORKSPACE_ASSET_PROCESSING_TIMEOUT_SECONDS",
        DEFAULT_ASSET_PROCESSING_TIMEOUT_SECONDS,
        minimum=30,
    )
    cutoff = current - timedelta(seconds=timeout)
    bounded_limit = max(1, min(limit, 1000))

    async with session_factory() as discovery:
        rows = list(
            (
                await discovery.execute(
                    select(
                        WorkspaceAsset.id,
                        WorkspaceAsset.owner_id,
                        WorkspaceAsset.version,
                    )
                    .where(
                        WorkspaceAsset.conversation_id.is_not(None),
                        WorkspaceAsset.state.in_(_PROCESSING_STATES),
                        WorkspaceAsset.updated_at <= cutoff,
                    )
                    .order_by(WorkspaceAsset.updated_at, WorkspaceAsset.id)
                    .limit(bounded_limit)
                    .execution_options(skip_owner_scope=True)
                )
            ).all()
        )

    transitioned = 0
    for asset_id, owner_id, version in rows:
        owner_token = set_current_owner_id(owner_id)
        try:
            async with session_factory() as session:
                try:
                    await transition_workspace_asset(
                        session,
                        asset_id=asset_id,
                        request=WorkspaceAssetTransitionRequest(
                            state="failed",
                            expected_version=version,
                            failure_code="asset_processing_timeout",
                        ),
                    )
                except WorkspaceServiceError as error:
                    await session.rollback()
                    if error.code not in {
                        "account_deletion_in_progress",
                        "workspace_asset_not_found",
                        "workspace_asset_version_conflict",
                        "workspace_asset_transition_invalid",
                    }:
                        raise
                else:
                    transitioned += 1
        finally:
            reset_current_owner_id(owner_token)
    return transitioned


async def process_pending_workspace_assets(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    storage_root: Path | None = None,
    limit: int = 100,
) -> int:
    """Drive persisted uploads to a terminal lifecycle state.

    Discovery is process-wide but work is re-entered under each asset owner.
    There is no in-memory task queue: ``uploaded``, ``scanning``, and
    ``processing`` are the durable recovery checkpoints.  If two maintenance
    processes discover the same row, transition CAS lets exactly one advance
    it and the loser simply retries on its next pass.
    """

    bounded_limit = max(1, min(limit, 1000))
    async with session_factory() as discovery:
        rows = list(
            (
                await discovery.execute(
                    select(
                        WorkspaceAsset.id,
                        WorkspaceAsset.owner_id,
                        WorkspaceAsset.version,
                    )
                    .where(WorkspaceAsset.state.in_(_PROCESSING_STATES))
                    .order_by(WorkspaceAsset.updated_at, WorkspaceAsset.id)
                    .limit(bounded_limit)
                    .execution_options(skip_owner_scope=True)
                )
            ).all()
        )

    completed = 0
    for asset_id, owner_id, version in rows:
        owner_token = set_current_owner_id(owner_id)
        try:
            async with session_factory() as session:
                try:
                    await process_workspace_asset(
                        session,
                        asset_id=asset_id,
                        expected_version=version,
                        storage_root=storage_root,
                    )
                except WorkspaceServiceError as error:
                    await session.rollback()
                    if error.code not in {
                        "workspace_asset_not_found",
                        "workspace_asset_version_conflict",
                        "workspace_asset_transition_invalid",
                    }:
                        raise
                else:
                    completed += 1
        finally:
            reset_current_owner_id(owner_token)
    return completed


async def run_workspace_asset_maintenance(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    now: datetime | None = None,
) -> dict[str, int]:
    """Run one bounded reconciliation/retention pass."""

    current = now or get_current_utc_datetime()
    timed_out = await reconcile_timed_out_workspace_assets(
        session_factory=session_factory,
        now=current,
    )
    processed = await process_pending_workspace_assets(
        session_factory=session_factory,
    )
    # The maintenance task is process-owned, not request-owned.  Explicitly
    # clear a context inherited by a custom task launcher before the global pass.
    owner_token = set_current_owner_id(None)
    try:
        async with session_factory() as session:
            expired = await expire_orphan_workspace_assets(session, now=current)
        async with session_factory() as session:
            deleted = await cleanup_expired_workspace_assets(session)
        artifact_cleanup = await sweep_workspace_artifact_cleanup(
            session_factory=session_factory,
            now=current,
        )
    finally:
        reset_current_owner_id(owner_token)
    return {
        "timed_out": timed_out,
        "processed": processed,
        "expired": expired,
        "deleted": deleted,
        "artifact_claimed": artifact_cleanup["claimed"],
        "artifact_deleted": artifact_cleanup["deleted"],
        "artifact_retry_pending": artifact_cleanup["retry_pending"],
        "artifact_dead_lettered": artifact_cleanup["dead_lettered"],
        "artifact_stale": artifact_cleanup["stale"],
    }


async def _workspace_asset_maintenance_loop() -> None:
    assert _maintenance_stop is not None
    while not _maintenance_stop.is_set():
        try:
            result = await run_workspace_asset_maintenance()
            if any(result.values()):
                logger.info(
                    "Workspace asset maintenance completed: "
                    "timed_out=%d processed=%d expired=%d deleted=%d "
                    "artifact_claimed=%d artifact_deleted=%d "
                    "artifact_retry_pending=%d artifact_dead_lettered=%d "
                    "artifact_stale=%d",
                    result["timed_out"],
                    result["processed"],
                    result["expired"],
                    result["deleted"],
                    result["artifact_claimed"],
                    result["artifact_deleted"],
                    result["artifact_retry_pending"],
                    result["artifact_dead_lettered"],
                    result["artifact_stale"],
                )
        except Exception:
            logger.exception("Workspace asset maintenance iteration failed")
        try:
            await asyncio.wait_for(
                _maintenance_stop.wait(),
                timeout=_configured_positive_int(
                    "WORKSPACE_ASSET_MAINTENANCE_INTERVAL_SECONDS",
                    DEFAULT_ASSET_MAINTENANCE_INTERVAL_SECONDS,
                ),
            )
        except TimeoutError:
            pass


async def start_workspace_asset_maintenance() -> None:
    """Reconcile once at startup and then poll until application shutdown."""

    global _maintenance_stop, _maintenance_task
    if _maintenance_task is not None and not _maintenance_task.done():
        return
    _maintenance_stop = asyncio.Event()
    try:
        await run_workspace_asset_maintenance()
    except Exception:
        # Startup remains available so operators can inspect/retry cards; the
        # durable rows are retried by the next interval rather than lost.
        logger.exception("Initial workspace asset maintenance failed")
    _maintenance_task = asyncio.create_task(_workspace_asset_maintenance_loop())


async def stop_workspace_asset_maintenance() -> None:
    global _maintenance_stop, _maintenance_task
    if _maintenance_stop is not None:
        _maintenance_stop.set()
    if _maintenance_task is not None:
        await _maintenance_task
    _maintenance_task = None
    _maintenance_stop = None
