import asyncio
import logging
from typing import Any
from weakref import WeakKeyDictionary

from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.provider_settings import ProviderSettings
from models.user_config import UserConfig
from constants.settings_registry import get_settings_registry
from utils.datetime_utils import get_current_utc_datetime
from utils.db_utils import get_database_url_and_connect_args, to_sync_sqlalchemy_url
from utils.get_env import get_user_config_path_env
from utils.runtime_settings import coerce_runtime_setting
from utils.user_config_store import read_user_config_file, update_user_config_file


logger = logging.getLogger(__name__)

PROVIDER_SETTINGS_ID = 1
CODEX_MANAGED_FIELDS = {
    "CODEX_ACCESS_TOKEN",
    "CODEX_REFRESH_TOKEN",
    "CODEX_TOKEN_EXPIRES",
    "CODEX_ACCOUNT_ID",
    "CODEX_USERNAME",
    "CODEX_EMAIL",
    "CODEX_IS_PRO",
}
EMPTY_VALUE_PRESERVED_FIELDS = {
    "OPEN_WEBUI_IMAGE_URL",
    "OPEN_WEBUI_IMAGE_API_KEY",
    "CODEX_MODEL",
}
PROVIDER_SETTING_KEYS = frozenset(
    entry["key"]
    for entry in get_settings_registry()
    if {
        "start.user_config",
        "user_config.managed",
        "user_config.ui",
    }.intersection(entry["targets"])
)
SECRET_SETTING_KEYS = frozenset(
    entry["key"] for entry in get_settings_registry() if entry["secret"]
)
REDACTED_SECRET_PLACEHOLDER = "__configured__"
_UPDATE_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    WeakKeyDictionary()
)


class ProviderSettingsValidationError(ValueError):
    def __init__(self, key: str):
        super().__init__(f"Invalid provider setting: {key}")


def get_provider_settings_update_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _UPDATE_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _UPDATE_LOCKS[loop] = lock
    return lock


def sanitize_provider_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Allow only registry-declared provider settings and validate their types."""
    filtered = {
        key: value
        for key, value in config.items()
        if key in PROVIDER_SETTING_KEYS
    }
    try:
        validated = UserConfig(**filtered).model_dump(exclude_none=True)
    except ValidationError as error:
        location = error.errors()[0].get("loc", ("unknown",))
        raise ProviderSettingsValidationError(str(location[0])) from None

    result: dict[str, Any] = {}
    for key, value in validated.items():
        try:
            coerced = coerce_runtime_setting(key, value)
        except (TypeError, ValueError):
            raise ProviderSettingsValidationError(key) from None
        result[key] = value if value == "" else coerced
    return result


def redact_provider_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Return API-safe settings with configured secrets represented write-only."""
    sanitized = sanitize_provider_settings(config)
    return {
        key: REDACTED_SECRET_PLACEHOLDER
        if key in SECRET_SETTING_KEYS and value
        else value
        for key, value in sanitized.items()
    }


def merge_provider_settings(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """Preserve the previous settings API's patch and managed-token behavior."""
    sanitized = sanitize_provider_settings(incoming)
    merged = {**sanitize_provider_settings(existing), **sanitized}

    for key in SECRET_SETTING_KEYS:
        if sanitized.get(key) == REDACTED_SECRET_PLACEHOLDER:
            if existing.get(key):
                merged[key] = existing[key]
            else:
                merged.pop(key, None)

    for key in CODEX_MANAGED_FIELDS:
        if key in existing:
            merged[key] = existing[key]
        else:
            merged.pop(key, None)

    for key in EMPTY_VALUE_PRESERVED_FIELDS:
        if not sanitized.get(key) and key in existing:
            merged[key] = existing[key]

    return merged


def _mirror_to_legacy_file(config: dict[str, Any]) -> None:
    """Mirror DB settings for code paths that still consume userConfig.json."""
    path = get_user_config_path_env()
    if not path:
        return

    def replace_provider_config(existing: dict[str, Any]) -> dict[str, Any]:
        mirrored = dict(config)
        # Authentication is database-backed now, but the compatibility file is
        # also the rollback/recovery copy. Never discard its credential fields.
        for key, value in existing.items():
            if key.upper().startswith("AUTH_"):
                mirrored[key] = value
        return mirrored

    update_user_config_file(path, replace_provider_config)


async def migrate_provider_settings_from_file(session: AsyncSession) -> dict[str, Any]:
    """One-time startup import, followed by DB-to-file compatibility syncing."""
    row = await session.get(ProviderSettings, PROVIDER_SETTINGS_ID)
    path = get_user_config_path_env()

    if row is None:
        legacy_config = await asyncio.to_thread(read_user_config_file, path) if path else {}
        row = ProviderSettings(
            id=PROVIDER_SETTINGS_ID,
            config=sanitize_provider_settings(legacy_config),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        logger.info("Migrated provider settings from userConfig.json into the database.")
    else:
        sanitized = sanitize_provider_settings(dict(row.config or {}))
        if sanitized != row.config:
            row.config = sanitized
            row.updated_at = get_current_utc_datetime()
            await session.commit()

    config = dict(row.config or {})
    await asyncio.to_thread(_mirror_to_legacy_file, config)
    return config


async def get_provider_settings(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(ProviderSettings, PROVIDER_SETTINGS_ID)
    if row is None:
        return await migrate_provider_settings_from_file(session)
    return sanitize_provider_settings(dict(row.config or {}))


async def save_provider_settings(
    session: AsyncSession, incoming: dict[str, Any]
) -> dict[str, Any]:
    row = await session.get(ProviderSettings, PROVIDER_SETTINGS_ID)
    if row is None:
        existing: dict[str, Any] = {}
        row = ProviderSettings(id=PROVIDER_SETTINGS_ID, config={})
        session.add(row)
    else:
        existing = dict(row.config or {})

    row.config = merge_provider_settings(existing, incoming)
    row.updated_at = get_current_utc_datetime()
    await session.commit()
    await session.refresh(row)

    config = dict(row.config or {})
    await asyncio.to_thread(_mirror_to_legacy_file, config)
    return config


def sync_legacy_file_to_provider_settings() -> None:
    """Persist changes made by remaining synchronous userConfig writers.

    Codex token refresh currently occurs in synchronous provider code, so this
    narrow compatibility bridge keeps those rare writes in the DB as well.
    """
    path = get_user_config_path_env()
    if not path:
        return
    config = sanitize_provider_settings(read_user_config_file(path))
    database_url, _ = get_database_url_and_connect_args()
    engine = create_engine(to_sync_sqlalchemy_url(database_url))
    try:
        with engine.begin() as connection:
            table = ProviderSettings.__table__
            exists = connection.execute(
                select(table.c.id).where(table.c.id == PROVIDER_SETTINGS_ID)
            ).scalar_one_or_none()
            values = {
                "config": config,
                "updated_at": get_current_utc_datetime(),
            }
            if exists is None:
                connection.execute(
                    table.insert().values(id=PROVIDER_SETTINGS_ID, **values)
                )
            else:
                connection.execute(
                    table.update()
                    .where(table.c.id == PROVIDER_SETTINGS_ID)
                    .values(**values)
                )
    finally:
        engine.dispose()
