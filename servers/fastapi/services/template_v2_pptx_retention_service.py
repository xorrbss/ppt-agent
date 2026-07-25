from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services.database import async_session_maker
from services.template_v2_pptx_storage import (
    PptxUploadRejected,
    cleanup_private_import,
    get_private_source_retention_ttl,
)
from utils.datetime_utils import get_current_utc_datetime


logger = logging.getLogger(__name__)
# Terminal states whose uploaded source -- and the runtime-extracted media relocated
# beside it in the same private directory -- may be reclaimed once its TTL expires.
# `confirmed` is deliberately absent: a materialized template keeps its source so the
# import can still be audited against the original deck.
SOURCE_CLEANUP_STATES = ("review_required", "failed", "cancelled")
SOURCE_CLEANUP_LEASE_DURATION = timedelta(minutes=5)
SOURCE_CLEANUP_BATCH_SIZE = 50
SOURCE_CLEANUP_INTERVAL_SECONDS = 60 * 60
SOURCE_RETENTION_POLICY_VERSION = 1


@dataclass(frozen=True)
class SourceCleanupSummary:
    initialized: int = 0
    claimed: int = 0
    deleted: int = 0
    already_missing: int = 0
    failed: int = 0


@dataclass(frozen=True)
class _ClaimedSource:
    import_id: uuid.UUID
    token: str
    storage_key: str
    manifest: dict
    retention_expires_at: datetime


_last_cleanup_monotonic: float | None = None
_cleanup_task: asyncio.Task | None = None
_cleanup_stop: asyncio.Event | None = None


def _now() -> datetime:
    return get_current_utc_datetime()


def _retention_audit(
    manifest: dict,
    *,
    ttl: timedelta | None,
    expires_at: datetime,
    cleanup: dict | None = None,
) -> dict:
    retention = {
        **deepcopy(manifest.get("private_source_retention") or {}),
        "policy_version": SOURCE_RETENTION_POLICY_VERSION,
        "expires_at": expires_at.isoformat(),
    }
    if ttl is not None:
        retention["ttl_days"] = ttl.days
    if cleanup is not None:
        retention["cleanup"] = cleanup
    return {
        **deepcopy(manifest),
        "private_source_retention": retention,
    }


def terminal_source_retention(
    manifest: dict,
    *,
    terminal_at: datetime,
) -> tuple[datetime, dict]:
    ttl = get_private_source_retention_ttl()
    expires_at = terminal_at + ttl
    return expires_at, _retention_audit(
        manifest,
        ttl=ttl,
        expires_at=expires_at,
    )


async def initialize_terminal_source_retention(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """Backfill explicit deadlines for terminal rows created before retention."""

    initialized_at = now or _now()
    ttl = get_private_source_retention_ttl()
    rows = (
        await session.execute(
            select(
                TemplateV2PptxImport.id,
                TemplateV2PptxImport.updated_at,
                TemplateV2PptxImport.manifest,
            )
            .where(
                TemplateV2PptxImport.state.in_(SOURCE_CLEANUP_STATES),
                TemplateV2PptxImport.attempt_token.is_(None),
                TemplateV2PptxImport.source_deleted_at.is_(None),
                TemplateV2PptxImport.source_retention_expires_at.is_(None),
            )
            .limit(SOURCE_CLEANUP_BATCH_SIZE)
        )
    ).all()
    initialized = 0
    for import_id, terminal_at, manifest in rows:
        terminal_at = terminal_at or initialized_at
        expires_at = terminal_at + ttl
        result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.state.in_(SOURCE_CLEANUP_STATES),
                TemplateV2PptxImport.attempt_token.is_(None),
                TemplateV2PptxImport.source_deleted_at.is_(None),
                TemplateV2PptxImport.source_retention_expires_at.is_(None),
            )
            .values(
                source_retention_expires_at=expires_at,
                manifest=_retention_audit(
                    manifest or {},
                    ttl=ttl,
                    expires_at=expires_at,
                ),
            )
            .execution_options(synchronize_session=False)
        )
        initialized += int(result.rowcount == 1)
    await session.commit()
    return initialized


async def _claim_source_for_cleanup(
    import_id: uuid.UUID,
    *,
    now: datetime,
) -> _ClaimedSource | None:
    token = uuid.uuid4().hex
    async with async_session_maker() as session:
        result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.state.in_(SOURCE_CLEANUP_STATES),
                TemplateV2PptxImport.attempt_token.is_(None),
                TemplateV2PptxImport.source_deleted_at.is_(None),
                TemplateV2PptxImport.source_retention_expires_at.is_not(None),
                TemplateV2PptxImport.source_retention_expires_at <= now,
                or_(
                    TemplateV2PptxImport.source_cleanup_token.is_(None),
                    TemplateV2PptxImport.source_cleanup_lease_expires_at.is_(None),
                    TemplateV2PptxImport.source_cleanup_lease_expires_at <= now,
                ),
            )
            .values(
                source_cleanup_token=token,
                source_cleanup_lease_expires_at=(
                    now + SOURCE_CLEANUP_LEASE_DURATION
                ),
                source_cleanup_attempted_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await session.rollback()
            return None
        row = (
            await session.execute(
                select(
                    TemplateV2PptxImport.source_storage_key,
                    TemplateV2PptxImport.manifest,
                    TemplateV2PptxImport.source_retention_expires_at,
                ).where(TemplateV2PptxImport.id == import_id)
            )
        ).one()
        await session.commit()
        return _ClaimedSource(
            import_id=import_id,
            token=token,
            storage_key=row.source_storage_key,
            manifest=deepcopy(row.manifest or {}),
            retention_expires_at=row.source_retention_expires_at,
        )


def _cleanup_error_code(error: Exception) -> str:
    if isinstance(error, PptxUploadRejected):
        return error.code
    if isinstance(error, OSError):
        return "private_source_cleanup_io_error"
    return "private_source_cleanup_failed"


async def _finish_source_cleanup(
    claim: _ClaimedSource,
    *,
    attempted_at: datetime,
    result_name: str,
    error_code: str | None = None,
) -> bool:
    cleanup_audit = {
        "attempted_at": attempted_at.isoformat(),
        "result": result_name,
    }
    if error_code is not None:
        cleanup_audit["error_code"] = error_code
    values = {
        "source_cleanup_token": None,
        "source_cleanup_lease_expires_at": None,
        "source_cleanup_attempted_at": attempted_at,
        "manifest": _retention_audit(
            claim.manifest,
            ttl=None,
            expires_at=claim.retention_expires_at,
            cleanup=cleanup_audit,
        ),
    }
    if error_code is None:
        values["source_deleted_at"] = attempted_at
    async with async_session_maker() as session:
        update_result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                TemplateV2PptxImport.id == claim.import_id,
                TemplateV2PptxImport.state.in_(SOURCE_CLEANUP_STATES),
                TemplateV2PptxImport.attempt_token.is_(None),
                TemplateV2PptxImport.source_cleanup_token == claim.token,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if update_result.rowcount != 1:
            await session.rollback()
            return False
        await session.commit()
        return True


async def cleanup_expired_private_sources(
    *,
    now: datetime | None = None,
) -> SourceCleanupSummary:
    """Best-effort cleanup with a durable per-row claim and audit trail."""

    cleanup_at = now or _now()
    get_private_source_retention_ttl()
    async with async_session_maker() as session:
        initialized = await initialize_terminal_source_retention(
            session,
            now=cleanup_at,
        )
        candidate_ids = (
            await session.execute(
                select(TemplateV2PptxImport.id)
                .where(
                    TemplateV2PptxImport.state.in_(SOURCE_CLEANUP_STATES),
                    TemplateV2PptxImport.attempt_token.is_(None),
                    TemplateV2PptxImport.source_deleted_at.is_(None),
                    TemplateV2PptxImport.source_retention_expires_at.is_not(None),
                    TemplateV2PptxImport.source_retention_expires_at <= cleanup_at,
                    or_(
                        TemplateV2PptxImport.source_cleanup_token.is_(None),
                        (
                            TemplateV2PptxImport.source_cleanup_lease_expires_at
                            .is_(None)
                        ),
                        (
                            TemplateV2PptxImport.source_cleanup_lease_expires_at
                            <= cleanup_at
                        ),
                    ),
                )
                .order_by(TemplateV2PptxImport.source_retention_expires_at)
                .limit(SOURCE_CLEANUP_BATCH_SIZE)
            )
        ).scalars().all()
    claimed = deleted = already_missing = failed = 0
    for import_id in candidate_ids:
        claim = await _claim_source_for_cleanup(import_id, now=cleanup_at)
        if claim is None:
            continue
        claimed += 1
        try:
            result_name = await asyncio.to_thread(
                cleanup_private_import,
                claim.storage_key,
            )
        except Exception as error:
            error_code = _cleanup_error_code(error)
            failed += 1
            logger.warning(
                "Private-source cleanup failed for %s: %s",
                import_id,
                error_code,
            )
            try:
                await _finish_source_cleanup(
                    claim,
                    attempted_at=cleanup_at,
                    result_name="failed",
                    error_code=error_code,
                )
            except Exception:
                logger.exception(
                    "Failed to persist private-source cleanup failure for %s",
                    import_id,
                )
            continue
        if await _finish_source_cleanup(
            claim,
            attempted_at=cleanup_at,
            result_name=result_name,
        ):
            if result_name == "deleted":
                deleted += 1
            else:
                already_missing += 1
        else:
            failed += 1
            logger.error(
                "Lost private-source cleanup ownership after deleting %s",
                import_id,
            )
    return SourceCleanupSummary(
        initialized=initialized,
        claimed=claimed,
        deleted=deleted,
        already_missing=already_missing,
        failed=failed,
    )


async def maybe_cleanup_expired_private_sources() -> SourceCleanupSummary | None:
    global _last_cleanup_monotonic
    current = time.monotonic()
    if (
        _last_cleanup_monotonic is not None
        and current - _last_cleanup_monotonic < SOURCE_CLEANUP_INTERVAL_SECONDS
    ):
        return None
    summary = await cleanup_expired_private_sources()
    _last_cleanup_monotonic = current
    return summary


async def _private_source_cleanup_loop() -> None:
    assert _cleanup_stop is not None
    while not _cleanup_stop.is_set():
        try:
            await maybe_cleanup_expired_private_sources()
        except Exception:
            logger.exception("Template V2 private-source cleanup iteration failed")
        try:
            await asyncio.wait_for(
                _cleanup_stop.wait(),
                timeout=SOURCE_CLEANUP_INTERVAL_SECONDS,
            )
        except TimeoutError:
            pass


async def start_template_v2_private_source_cleanup() -> None:
    """Start retention cleanup independently of the Template V2 feature flag."""

    global _cleanup_stop, _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        return
    _cleanup_stop = asyncio.Event()
    try:
        await maybe_cleanup_expired_private_sources()
    except Exception:
        logger.exception("Initial Template V2 private-source cleanup failed")
    _cleanup_task = asyncio.create_task(_private_source_cleanup_loop())


async def stop_template_v2_private_source_cleanup() -> None:
    global _cleanup_stop, _cleanup_task
    if _cleanup_stop is not None:
        _cleanup_stop.set()
    if _cleanup_task is not None:
        await _cleanup_task
    _cleanup_task = None
    _cleanup_stop = None
