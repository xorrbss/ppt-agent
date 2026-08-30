"""Bounded product scheduler for public artifact expiry and orphan recovery."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import os

from services.artifact_lifecycle import (
    configured_artifact_lifecycle_service,
    public_artifact_lifecycle_enabled,
)
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime


LOGGER = logging.getLogger(__name__)
_runtime: "PublicArtifactLifecycleRuntime | None" = None


def _positive_int(name: str, default: int, *, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


class PublicArtifactLifecycleRuntime:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not public_artifact_lifecycle_enabled():
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="public-artifact-lifecycle",
        )

    async def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await self._task
        self._task = None

    async def run_once(self) -> tuple[int, int, int]:
        now = get_current_utc_datetime()
        limit = _positive_int("PUBLIC_ARTIFACT_GC_BATCH_LIMIT", 100, minimum=1)
        provisional_seconds = _positive_int(
            "PUBLIC_ARTIFACT_PROVISIONAL_RETENTION_SECONDS", 3600, minimum=60
        )
        delete_grace_seconds = _positive_int(
            "PUBLIC_ARTIFACT_DELETE_GRACE_SECONDS", 3600, minimum=60
        )
        async with async_session_maker() as session:
            service = configured_artifact_lifecycle_service(session)
            expired = await service.expire_once(now=now, limit=limit)
            deleted = await service.collect_once(
                older_than=now - timedelta(seconds=delete_grace_seconds),
                limit=limit,
            )
            orphans = await service.sweep_provisional_orphans(
                older_than=now - timedelta(seconds=provisional_seconds),
                limit=limit,
            )
            await session.commit()
        return expired, deleted, orphans

    async def _run(self) -> None:
        interval = _positive_int(
            "PUBLIC_ARTIFACT_LIFECYCLE_INTERVAL_SECONDS", 60, minimum=5
        )
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                LOGGER.exception("Public artifact lifecycle iteration failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except TimeoutError:
                pass


async def start_public_artifact_lifecycle_runtime() -> PublicArtifactLifecycleRuntime:
    global _runtime
    if _runtime is None:
        _runtime = PublicArtifactLifecycleRuntime()
    await _runtime.start()
    return _runtime


async def stop_public_artifact_lifecycle_runtime() -> None:
    global _runtime
    if _runtime is None:
        return
    await _runtime.close()
    _runtime = None


__all__ = [
    "PublicArtifactLifecycleRuntime",
    "start_public_artifact_lifecycle_runtime",
    "stop_public_artifact_lifecycle_runtime",
]
