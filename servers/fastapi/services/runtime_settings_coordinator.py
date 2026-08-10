from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from typing import Any

from services.database import async_session_maker
from services.provider_settings import (
    ProviderSettingsRecord,
    get_provider_settings_record,
    get_provider_settings_revision,
    migrate_provider_settings_from_file_record,
)
from utils.runtime_settings import (
    RuntimeSettings,
    RuntimeSettingsPublishOutcome,
    get_process_runtime_settings_envelope,
    publish_process_runtime_settings,
)
from utils.user_config import load_runtime_settings


logger = logging.getLogger(__name__)
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
MAX_RETRY_SECONDS = 30.0


class RuntimeSettingsCoordinator:
    """Converge one process to the database-backed provider settings revision."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] = async_session_maker,
        poll_interval_seconds: float | None = None,
        retry_jitter: Callable[[float], float] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval = (
            DEFAULT_POLL_INTERVAL_SECONDS
            if poll_interval_seconds is None
            else poll_interval_seconds
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._retry_jitter = retry_jitter or (
            lambda base: random.uniform(0.0, min(base * 0.2, 1.0))
        )

    @staticmethod
    def _settings(record: ProviderSettingsRecord) -> RuntimeSettings:
        return load_runtime_settings(
            persisted_config=record.config,
        )

    async def initialize(self) -> ProviderSettingsRecord:
        async with self._session_factory() as session:
            record = await migrate_provider_settings_from_file_record(session)
        outcome = publish_process_runtime_settings(
            record.revision,
            self._settings(record),
        )
        logger.info(
            "runtime settings sync outcome=%s revision=%d attempt=0",
            outcome,
            record.revision,
        )
        return record

    async def poll_once(self) -> str:
        current = get_process_runtime_settings_envelope()
        current_revision = current.revision if current is not None else -1
        async with self._session_factory() as session:
            revision = await get_provider_settings_revision(session)
            if revision is None or revision <= current_revision:
                return RuntimeSettingsPublishOutcome.NO_CHANGE
            record = await get_provider_settings_record(session)
        return publish_process_runtime_settings(
            record.revision,
            self._settings(record),
        )

    async def run(self) -> None:
        attempt = 0
        delay = self._poll_interval
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                continue
            except TimeoutError:
                pass

            try:
                outcome = await self.poll_once()
                attempt = 0
                delay = self._poll_interval
                if outcome == RuntimeSettingsPublishOutcome.APPLIED:
                    envelope = get_process_runtime_settings_envelope()
                    logger.info(
                        "runtime settings sync outcome=applied revision=%d attempt=0",
                        envelope.revision if envelope is not None else -1,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                attempt += 1
                envelope = get_process_runtime_settings_envelope()
                logger.warning(
                    "runtime settings sync outcome=failed revision=%d attempt=%d error=%s",
                    envelope.revision if envelope is not None else -1,
                    attempt,
                    type(error).__name__,
                )
                base_delay = min(
                    self._poll_interval * (2 ** min(attempt, 10)),
                    MAX_RETRY_SECONDS,
                )
                delay = min(
                    base_delay + max(0.0, self._retry_jitter(base_delay)),
                    MAX_RETRY_SECONDS,
                )

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self.run(),
            name="runtime-settings-coordinator",
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            await task


_coordinator: RuntimeSettingsCoordinator | None = None


async def start_runtime_settings_coordinator() -> RuntimeSettingsCoordinator:
    global _coordinator
    if _coordinator is not None:
        return _coordinator
    coordinator = RuntimeSettingsCoordinator()
    await coordinator.initialize()
    coordinator.start()
    _coordinator = coordinator
    return coordinator


async def stop_runtime_settings_coordinator() -> None:
    global _coordinator
    coordinator = _coordinator
    _coordinator = None
    if coordinator is not None:
        await coordinator.stop()
