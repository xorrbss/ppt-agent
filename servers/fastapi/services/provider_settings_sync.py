import json
import logging
import time
from typing import Mapping

from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from models.sql.provider_settings import ProviderSettings
from services.provider_settings_legacy import (
    mirror_provider_settings_to_legacy_file,
)
from services.provider_settings_retry import (
    PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS,
    provider_settings_retry_delay_seconds,
)
from services.provider_settings_values import (
    CODEX_MANAGED_FIELDS,
    REDACTED_SECRET_PLACEHOLDER,
    sanitize_provider_settings_for_storage,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.db_utils import get_database_url_and_connect_args, to_sync_sqlalchemy_url
from utils.env_settings.provider_credentials import get_provider_credential_settings
from utils.get_env import get_user_config_path_env
from utils.user_config_store import read_user_config_file


logger = logging.getLogger(__name__)


class ProviderSettingsImportError(RuntimeError):
    """Raised when an import would silently discard an on-disk config."""


def _raw_file_config_for_divergence_check(path: str) -> dict | None:
    """Parse ``path`` directly, bypassing ACL hardening and the ``.bak`` sibling.

    This exists only to sanity-check ``read_user_config_file``'s result
    against what is plainly on disk. It is not a substitute source of truth
    (it never consults the backup file), so it must only be used to detect
    divergence, never to source the config that gets imported.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read().strip()
    except OSError:
        return None
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def sync_legacy_file_to_provider_settings(
    *,
    managed_updates: Mapping[str, object] | None = None,
    expected_revision: int | None = None,
):
    """Persist changes made by remaining synchronous userConfig writers."""
    # Imported lazily so the public service can re-export this bridge without a
    # module cycle while retaining one shared record/error contract.
    from services.provider_settings import (
        ProviderSettingsConcurrencyError,
        ProviderSettingsRecord,
    )

    # A release-validation launcher may import an existing read-only provider
    # config into an isolated database while keeping USER_CONFIG_PATH pointed at
    # the RC-local compatibility mirror. The import path is scoped to the
    # one-shot sync process and is never inherited by API or worker processes.
    path = get_provider_credential_settings().config_import_path or get_user_config_path_env()
    if not path:
        return None
    # A credential refresh must commit against the revision that supplied its
    # refresh token. It must never stage stale tokens into the legacy file.
    if expected_revision is not None and managed_updates is None:
        raise ValueError("conditional settings writes require managed updates")
    raw_legacy_config = read_user_config_file(path) if expected_revision is None else {}
    if not raw_legacy_config and expected_revision is None:
        # A genuinely missing/empty config is a normal bootstrap state. But a
        # file that plainly has content on disk yet read back empty means a
        # read failure was silently swallowed somewhere upstream (this is
        # exactly how a readable userConfig.json was previously imported as
        # nothing while this function still reported success). Refuse to
        # proceed rather than committing an empty import.
        on_disk = _raw_file_config_for_divergence_check(path)
        if on_disk:
            raise ProviderSettingsImportError(
                f"Refusing to import provider settings from {path}: the file "
                "has content on disk but read_user_config_file returned an "
                "empty config. This indicates a read failure was silently "
                "swallowed rather than the config genuinely being empty; fix "
                "the underlying read failure (e.g. ACL hardening) and retry "
                "instead of importing nothing."
            )
    config = sanitize_provider_settings_for_storage(raw_legacy_config)
    database_url, connect_args = get_database_url_and_connect_args()
    engine = create_engine(
        to_sync_sqlalchemy_url(database_url),
        connect_args=connect_args if "sqlite" in database_url else {},
    )
    try:
        table = ProviderSettings.__table__
        for attempt in range(PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS):
            committed = None
            try:
                with engine.begin() as connection:
                    row = connection.execute(
                        select(table.c.revision, table.c.config).where(
                            table.c.id == 1
                        )
                    ).one_or_none()
                    if expected_revision is not None and (
                        row is None or int(row.revision) != expected_revision
                    ):
                        raise ProviderSettingsConcurrencyError() from None
                    if row is None:
                        connection.execute(
                            insert(table).values(
                                id=1,
                                config=config,
                                revision=0,
                                updated_at=get_current_utc_datetime(),
                            )
                        )
                        committed = ProviderSettingsRecord(0, config)
                    else:
                        raw_existing = dict(row.config or {})
                        existing = sanitize_provider_settings_for_storage(raw_existing)
                        if managed_updates is None:
                            merged = {**existing, **config}
                        else:
                            merged = dict(existing)
                            for key, value in managed_updates.items():
                                if key not in CODEX_MANAGED_FIELDS:
                                    continue
                                if value is None or value == "":
                                    merged.pop(key, None)
                                elif value != REDACTED_SECRET_PLACEHOLDER:
                                    merged[key] = value
                        merged = sanitize_provider_settings_for_storage(merged)
                        if merged == existing and raw_existing == existing:
                            result = connection.execute(
                                update(table)
                                .where(
                                    table.c.id == 1,
                                    table.c.revision == int(row.revision),
                                )
                                .values(revision=table.c.revision)
                            )
                            if result.rowcount == 1:
                                committed = ProviderSettingsRecord(
                                    int(row.revision), existing
                                )
                        else:
                            result = connection.execute(
                                update(table)
                                .where(
                                    table.c.id == 1,
                                    table.c.revision == int(row.revision),
                                )
                                .values(
                                    config=merged,
                                    revision=table.c.revision + 1,
                                    updated_at=get_current_utc_datetime(),
                                )
                            )
                            if result.rowcount == 1:
                                committed = ProviderSettingsRecord(
                                    int(row.revision) + 1, merged
                                )

                if committed is not None:
                    try:
                        mirror_provider_settings_to_legacy_file(
                            committed.config, committed.revision
                        )
                    except Exception as error:
                        logger.warning(
                            "provider settings mirror outcome=failed "
                            "revision=%d error=%s",
                            committed.revision,
                            type(error).__name__,
                        )
                    return committed
                if expected_revision is not None:
                    raise ProviderSettingsConcurrencyError() from None
            except (IntegrityError, OperationalError) as error:
                if attempt + 1 == PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS:
                    logger.warning(
                        "provider settings write outcome=exhausted "
                        "attempt=%d error=%s",
                        attempt + 1,
                        type(error).__name__,
                    )
                    raise ProviderSettingsConcurrencyError() from None
            time.sleep(provider_settings_retry_delay_seconds(attempt))

        logger.warning(
            "provider settings write outcome=exhausted attempt=%d error=%s",
            PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS,
            ProviderSettingsConcurrencyError.__name__,
        )
        raise ProviderSettingsConcurrencyError() from None
    finally:
        engine.dispose()
