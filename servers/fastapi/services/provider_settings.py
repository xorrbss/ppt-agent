import asyncio
from dataclasses import dataclass
import logging
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.provider_settings import ProviderSettings
from services.provider_settings_values import (
    ProviderSettingsValidationError as ProviderSettingsValidationError,
    REDACTED_SECRET_PLACEHOLDER as REDACTED_SECRET_PLACEHOLDER,
    merge_provider_settings as merge_provider_settings,
    redact_provider_settings as redact_provider_settings,
    sanitize_provider_settings as sanitize_provider_settings,
    sanitize_provider_settings_for_storage,
)
from services.provider_settings_retry import (
    PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS,
    provider_settings_retry_pause as _retry_pause,
)
from services.provider_settings_legacy import (
    LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY as LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY,
    LEGACY_PROVIDER_SETTINGS_REVISION_KEY as LEGACY_PROVIDER_SETTINGS_REVISION_KEY,
    legacy_pending_refresh_token as _legacy_pending_refresh_token,
    legacy_provider_settings_revision as _legacy_revision,
    mirror_provider_settings_to_legacy_file as _mirror_to_legacy_file,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.get_env import get_user_config_path_env
from utils.user_config_store import read_user_config_file


logger = logging.getLogger(__name__)

PROVIDER_SETTINGS_ID = 1
_UPDATE_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    WeakKeyDictionary()
)


class ProviderSettingsConcurrencyError(RuntimeError):
    def __init__(self):
        super().__init__("provider settings write could not be completed")


def _raise_concurrency_exhausted(attempt: int, error_class: str) -> None:
    logger.warning(
        "provider settings write outcome=exhausted attempt=%d error=%s",
        attempt + 1,
        error_class,
    )
    raise ProviderSettingsConcurrencyError() from None


@dataclass(frozen=True, slots=True)
class ProviderSettingsRecord:
    revision: int
    config: dict[str, Any]


def get_provider_settings_update_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _UPDATE_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _UPDATE_LOCKS[loop] = lock
    return lock


def _sanitize_for_storage(config: dict[str, Any]) -> dict[str, Any]:
    return sanitize_provider_settings_for_storage(config)


async def _read_provider_settings_record(
    session: AsyncSession,
) -> ProviderSettingsRecord | None:
    table = ProviderSettings.__table__
    result = await session.execute(
        select(table.c.revision, table.c.config).where(
            table.c.id == PROVIDER_SETTINGS_ID
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    return ProviderSettingsRecord(
        revision=int(row.revision),
        config=dict(row.config or {}),
    )


async def _mirror_to_legacy_file_best_effort(
    record: ProviderSettingsRecord,
    *,
    force: bool = False,
    expected_pending_token: str | None = None,
    fence_pending_token: bool = False,
) -> None:
    try:
        await asyncio.to_thread(
            _mirror_to_legacy_file,
            record.config,
            record.revision,
            force=force,
            expected_pending_token=expected_pending_token,
            fence_pending_token=fence_pending_token,
        )
    except Exception as error:
        logger.warning(
            "provider settings mirror outcome=failed revision=%d error=%s",
            record.revision,
            type(error).__name__,
        )


async def _recover_pending_startup_write(
    session: AsyncSession,
    legacy_config: dict[str, Any],
    record: ProviderSettingsRecord,
) -> ProviderSettingsRecord:
    pending_token = _legacy_pending_refresh_token(legacy_config)
    base_revision = _legacy_revision(legacy_config)
    if pending_token is None or base_revision is None:
        return record

    if base_revision == record.revision:
        try:
            committed = await save_provider_settings_record(
                session,
                legacy_config,
                expected_revision=base_revision,
            )
        except ProviderSettingsConcurrencyError:
            latest = await _read_provider_settings_record(session)
            if latest is None or latest.revision == base_revision:
                logger.warning(
                    "provider settings pending startup outcome=failed revision=%d",
                    base_revision,
                )
                raise ProviderSettingsConcurrencyError() from None
            record = latest
        else:
            await _mirror_to_legacy_file_best_effort(
                committed,
                force=True,
                expected_pending_token=pending_token,
            )
            logger.info(
                "provider settings pending startup outcome=applied revision=%d",
                committed.revision,
            )
            return committed

    await _mirror_to_legacy_file_best_effort(
        record,
        force=True,
        expected_pending_token=pending_token,
    )
    logger.info(
        "provider settings pending startup outcome=recovered revision=%d",
        record.revision,
    )
    return record


async def migrate_provider_settings_from_file_record(
    session: AsyncSession,
) -> ProviderSettingsRecord:
    """One-time startup import, followed by DB-to-file compatibility syncing."""
    path = get_user_config_path_env()
    legacy_config = (
        await asyncio.to_thread(read_user_config_file, path) if path else {}
    )
    initial_config = _sanitize_for_storage(legacy_config)
    initial_pending_token = _legacy_pending_refresh_token(legacy_config)
    table = ProviderSettings.__table__
    created = False

    for attempt in range(PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS):
        try:
            record = await _read_provider_settings_record(session)
            if record is None:
                try:
                    await session.execute(
                        insert(table).values(
                            id=PROVIDER_SETTINGS_ID,
                            config=initial_config,
                            revision=0,
                            updated_at=get_current_utc_datetime(),
                        )
                    )
                    await session.commit()
                    record = ProviderSettingsRecord(0, initial_config)
                    created = True
                    logger.info(
                        "provider settings initialization outcome=created revision=0"
                    )
                except IntegrityError:
                    await session.rollback()
                    if attempt + 1 == PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS:
                        _raise_concurrency_exhausted(attempt, "IntegrityError")
                    await _retry_pause(attempt)
                    continue
            else:
                record = await _recover_pending_startup_write(
                    session,
                    legacy_config,
                    record,
                )
                cleaned = _sanitize_for_storage(record.config)
                if cleaned != record.config:
                    record = await save_provider_settings_record(
                        session,
                        cleaned,
                        replace=True,
                    )
            await _mirror_to_legacy_file_best_effort(
                record,
                force=created,
                expected_pending_token=initial_pending_token,
                fence_pending_token=created,
            )
            return record
        except OperationalError:
            await session.rollback()
            if attempt + 1 == PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS:
                _raise_concurrency_exhausted(attempt, "OperationalError")
            await _retry_pause(attempt)
    _raise_concurrency_exhausted(
        PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS - 1,
        ProviderSettingsConcurrencyError.__name__,
    )


async def migrate_provider_settings_from_file(
    session: AsyncSession,
) -> dict[str, Any]:
    return (await migrate_provider_settings_from_file_record(session)).config


async def get_provider_settings(session: AsyncSession) -> dict[str, Any]:
    return (await get_provider_settings_record(session)).config


async def get_provider_settings_record(
    session: AsyncSession,
) -> ProviderSettingsRecord:
    record = await _read_provider_settings_record(session)
    if record is None:
        return await migrate_provider_settings_from_file_record(session)
    cleaned = _sanitize_for_storage(record.config)
    if cleaned != record.config:
        return await save_provider_settings_record(
            session,
            cleaned,
            replace=True,
        )
    return ProviderSettingsRecord(record.revision, cleaned)


async def get_provider_settings_revision(session: AsyncSession) -> int | None:
    table = ProviderSettings.__table__
    result = await session.execute(
        select(table.c.revision).where(table.c.id == PROVIDER_SETTINGS_ID)
    )
    revision = result.scalar_one_or_none()
    return int(revision) if revision is not None else None


async def save_provider_settings(
    session: AsyncSession, incoming: dict[str, Any]
) -> dict[str, Any]:
    return (await save_provider_settings_record(session, incoming)).config


async def save_provider_settings_record(
    session: AsyncSession,
    incoming: dict[str, Any],
    *,
    replace: bool = False,
    expected_revision: int | None = None,
) -> ProviderSettingsRecord:
    """Merge and commit provider settings with bounded optimistic CAS retries."""
    sanitized_incoming = _sanitize_for_storage(incoming)
    table = ProviderSettings.__table__

    for attempt in range(PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS):
        retry_error_class = ProviderSettingsConcurrencyError.__name__
        try:
            seen = await _read_provider_settings_record(session)
            if expected_revision is not None and (
                seen is None or seen.revision != expected_revision
            ):
                await session.rollback()
                raise ProviderSettingsConcurrencyError() from None
            if seen is None:
                try:
                    await session.execute(
                        insert(table).values(
                            id=PROVIDER_SETTINGS_ID,
                            config={},
                            revision=0,
                            updated_at=get_current_utc_datetime(),
                        )
                    )
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                if attempt + 1 == PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS:
                    _raise_concurrency_exhausted(
                        attempt,
                        ProviderSettingsConcurrencyError.__name__,
                    )
                await _retry_pause(attempt)
                continue

            existing = _sanitize_for_storage(seen.config)
            merged = (
                sanitized_incoming
                if replace
                else merge_provider_settings(existing, incoming)
            )
            merged = _sanitize_for_storage(merged)
            if merged == existing and seen.config == existing:
                result = await session.execute(
                    update(table)
                    .where(
                        table.c.id == PROVIDER_SETTINGS_ID,
                        table.c.revision == seen.revision,
                    )
                    .values(revision=table.c.revision)
                )
                if result.rowcount == 1:
                    await session.commit()
                    committed = ProviderSettingsRecord(seen.revision, existing)
                    await _mirror_to_legacy_file_best_effort(committed)
                    return committed
                await session.rollback()
                if expected_revision is not None:
                    raise ProviderSettingsConcurrencyError() from None
                if attempt + 1 == PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS:
                    _raise_concurrency_exhausted(
                        attempt,
                        ProviderSettingsConcurrencyError.__name__,
                    )
                await _retry_pause(attempt)
                continue

            result = await session.execute(
                update(table)
                .where(
                    table.c.id == PROVIDER_SETTINGS_ID,
                    table.c.revision == seen.revision,
                )
                .values(
                    config=merged,
                    revision=table.c.revision + 1,
                    updated_at=get_current_utc_datetime(),
                )
            )
            if result.rowcount == 1:
                await session.commit()
                committed = ProviderSettingsRecord(seen.revision + 1, merged)
                await _mirror_to_legacy_file_best_effort(committed)
                return committed
            await session.rollback()
            if expected_revision is not None:
                raise ProviderSettingsConcurrencyError() from None
        except OperationalError:
            await session.rollback()
            retry_error_class = "OperationalError"

        if attempt + 1 == PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS:
            _raise_concurrency_exhausted(
                attempt,
                retry_error_class,
            )
        await _retry_pause(attempt)
    _raise_concurrency_exhausted(
        PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS - 1,
        ProviderSettingsConcurrencyError.__name__,
    )


async def import_legacy_file_to_provider_settings(
    session: AsyncSession,
) -> ProviderSettingsRecord:
    """Import Electron's already-durable compatibility write through DB CAS."""
    path = get_user_config_path_env()
    if not path:
        raise RuntimeError("provider settings compatibility file unavailable")
    config = await asyncio.to_thread(read_user_config_file, path)
    expected_revision = _legacy_revision(config)
    pending_token = _legacy_pending_refresh_token(config)
    if expected_revision is None or pending_token is None:
        raise ProviderSettingsConcurrencyError() from None
    try:
        committed = await save_provider_settings_record(
            session,
            config,
            expected_revision=expected_revision,
        )
    except ProviderSettingsConcurrencyError:
        try:
            latest = await get_provider_settings_record(session)
            await _mirror_to_legacy_file_best_effort(
                latest,
                force=True,
                expected_pending_token=pending_token,
            )
        except Exception as error:
            logger.warning(
                "provider settings pending recovery outcome=failed error=%s",
                type(error).__name__,
            )
        raise ProviderSettingsConcurrencyError() from None

    try:
        await asyncio.to_thread(
            _mirror_to_legacy_file,
            committed.config,
            committed.revision,
            force=True,
            expected_pending_token=pending_token,
        )
    except Exception as error:
        logger.warning(
            "provider settings pending completion outcome=failed error=%s",
            type(error).__name__,
        )
        raise ProviderSettingsConcurrencyError() from None
    return committed


def sync_legacy_file_to_provider_settings(*, managed_updates=None):
    from services.provider_settings_sync import (
        sync_legacy_file_to_provider_settings as sync_bridge,
    )

    return sync_bridge(managed_updates=managed_updates)
