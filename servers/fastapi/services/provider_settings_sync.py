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
from utils.get_env import get_user_config_path_env
from utils.user_config_store import read_user_config_file


logger = logging.getLogger(__name__)


def sync_legacy_file_to_provider_settings(
    *,
    managed_updates: Mapping[str, object] | None = None,
):
    """Persist changes made by remaining synchronous userConfig writers."""
    # Imported lazily so the public service can re-export this bridge without a
    # module cycle while retaining one shared record/error contract.
    from services.provider_settings import (
        ProviderSettingsConcurrencyError,
        ProviderSettingsRecord,
    )

    path = get_user_config_path_env()
    if not path:
        return None
    config = sanitize_provider_settings_for_storage(read_user_config_file(path))
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
