import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.sql.provider_settings import ProviderSettings
from services.provider_settings import (
    LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY,
    LEGACY_PROVIDER_SETTINGS_REVISION_KEY,
    ProviderSettingsConcurrencyError,
    ProviderSettingsRecord,
    _mirror_to_legacy_file,
    REDACTED_SECRET_PLACEHOLDER,
    get_provider_settings_record,
    import_legacy_file_to_provider_settings,
    migrate_provider_settings_from_file_record,
    save_provider_settings_record,
    sync_legacy_file_to_provider_settings,
)
from services.provider_settings_retry import (
    PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS,
    PROVIDER_SETTINGS_CAS_MAX_DELAY_SECONDS,
    provider_settings_retry_delay_seconds,
    provider_settings_retry_pause,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.user_config_store import read_user_config_file, update_user_config_file


async def _create_database(path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        connect_args={"timeout": 30.0},
    )
    async with engine.begin() as connection:
        await connection.run_sync(ProviderSettings.__table__.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_cas_conflict_reloads_and_merges_before_retry(monkeypatch):
    seen = iter(
        (
            ProviderSettingsRecord(3, {"LLM": "openai"}),
            ProviderSettingsRecord(
                4,
                {"LLM": "openai", "GOOGLE_MODEL": "concurrent-model"},
            ),
        )
    )
    writes = []

    async def read(_session):
        return next(seen)

    class Session:
        async def execute(self, statement):
            values = {
                getattr(column, "key", column): getattr(value, "value", value)
                for column, value in statement._values.items()
            }
            writes.append(values["config"])
            return SimpleNamespace(rowcount=1 if len(writes) == 2 else 0)

        async def rollback(self):
            return None

        async def commit(self):
            return None

    monkeypatch.setattr(
        "services.provider_settings._read_provider_settings_record",
        read,
    )
    monkeypatch.setattr(
        "services.provider_settings._retry_pause",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file",
        lambda _config, _revision, **_kwargs: None,
    )

    committed = asyncio.run(
        save_provider_settings_record(
            Session(),
            {"OPENAI_MODEL": "requested-model"},
        )
    )

    assert committed.revision == 5
    assert committed.config == {
        "LLM": "openai",
        "GOOGLE_MODEL": "concurrent-model",
        "OPENAI_MODEL": "requested-model",
    }
    assert writes[-1] == committed.config


def test_cas_retry_delay_has_injectable_bounded_jitter():
    no_jitter = provider_settings_retry_delay_seconds(
        2,
        jitter_source=lambda: 0.0,
    )
    full_jitter = provider_settings_retry_delay_seconds(
        2,
        jitter_source=lambda: 1.0,
    )
    capped = provider_settings_retry_delay_seconds(
        100,
        jitter_source=lambda: 1.0,
    )

    assert no_jitter == pytest.approx(0.02)
    assert full_jitter == pytest.approx(0.025)
    assert capped == pytest.approx(PROVIDER_SETTINGS_CAS_MAX_DELAY_SECONDS)


def test_cas_retry_pause_accepts_test_sleep_and_jitter_injection():
    sleeps = []

    async def sleeper(delay):
        sleeps.append(delay)

    asyncio.run(
        provider_settings_retry_pause(
            1,
            jitter_source=lambda: 0.5,
            sleeper=sleeper,
        )
    )

    assert sleeps == [pytest.approx(0.01125)]


def test_cas_exhaustion_logs_only_safe_metadata(monkeypatch, caplog):
    secret = "database-url-secret-must-not-leak"

    async def fail_read(_session):
        raise OperationalError("statement", {}, RuntimeError(secret))

    class Session:
        async def rollback(self):
            return None

    pauses = AsyncMock()
    monkeypatch.setattr(
        "services.provider_settings._read_provider_settings_record",
        fail_read,
    )
    monkeypatch.setattr("services.provider_settings._retry_pause", pauses)

    with caplog.at_level(logging.WARNING, logger="services.provider_settings"):
        with pytest.raises(ProviderSettingsConcurrencyError):
            asyncio.run(save_provider_settings_record(Session(), {"LLM": "openai"}))

    assert pauses.await_count == PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS - 1
    assert (
        f"outcome=exhausted attempt={PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS} "
        "error=OperationalError"
    ) in caplog.text
    assert secret not in caplog.text


def test_semantic_noop_uses_revision_guard_and_retries_interleaved_commit(
    monkeypatch,
):
    seen = iter(
        (
            ProviderSettingsRecord(
                3,
                {"LLM": "openai", "OPENAI_MODEL": "model-a"},
            ),
            ProviderSettingsRecord(
                4,
                {
                    "LLM": "openai",
                    "OPENAI_MODEL": "model-a",
                    "GOOGLE_MODEL": "concurrent-model",
                },
            ),
        )
    )
    guards = 0

    async def read(_session):
        return next(seen)

    class Session:
        async def execute(self, _statement):
            nonlocal guards
            guards += 1
            return SimpleNamespace(rowcount=1 if guards == 2 else 0)

        async def rollback(self):
            return None

        async def commit(self):
            return None

    monkeypatch.setattr(
        "services.provider_settings._read_provider_settings_record",
        read,
    )
    monkeypatch.setattr(
        "services.provider_settings._retry_pause",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file",
        lambda _config, _revision, **_kwargs: None,
    )

    committed = asyncio.run(
        save_provider_settings_record(
            Session(),
            {"LLM": "openai", "OPENAI_MODEL": "model-a"},
        )
    )

    assert guards == 2
    assert committed.revision == 4
    assert committed.config["GOOGLE_MODEL"] == "concurrent-model"


def test_semantic_noop_does_not_bump_revision_and_never_stores_sentinel(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file",
        lambda _config, _revision, **_kwargs: None,
    )

    async def exercise():
        engine, maker = await _create_database(tmp_path / "noop.db")
        try:
            async with maker() as session:
                first = await save_provider_settings_record(
                    session,
                    {
                        "LLM": "openai",
                        "OPENAI_API_KEY": "stored-secret",
                        "OPENAI_MODEL": "model-a",
                    },
                )
            async with maker() as session:
                duplicate = await save_provider_settings_record(
                    session,
                    {
                        "LLM": "openai",
                        "OPENAI_API_KEY": REDACTED_SECRET_PLACEHOLDER,
                        "OPENAI_MODEL": "model-a",
                    },
                )
                stored = await get_provider_settings_record(session)
            return first, duplicate, stored
        finally:
            await engine.dispose()

    first, duplicate, stored = asyncio.run(exercise())

    assert duplicate.revision == first.revision
    assert duplicate.config["OPENAI_API_KEY"] == "stored-secret"
    assert REDACTED_SECRET_PLACEHOLDER not in repr(stored.config)


def test_concurrent_cas_writes_merge_without_lost_update(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file",
        lambda _config, _revision, **_kwargs: None,
    )

    async def exercise():
        engine, maker = await _create_database(tmp_path / "concurrent.db")
        try:
            async with maker() as session:
                initial = await save_provider_settings_record(
                    session,
                    {"LLM": "openai"},
                )

            async def save(patch):
                async with maker() as session:
                    return await save_provider_settings_record(session, patch)

            left, right = await asyncio.gather(
                save({"OPENAI_MODEL": "model-a"}),
                save({"GOOGLE_MODEL": "model-b"}),
            )
            async with maker() as session:
                final = await get_provider_settings_record(session)
            return initial, left, right, final
        finally:
            await engine.dispose()

    initial, left, right, final = asyncio.run(exercise())

    assert final.config == {
        "LLM": "openai",
        "OPENAI_MODEL": "model-a",
        "GOOGLE_MODEL": "model-b",
    }
    assert final.revision == initial.revision + 2
    assert {left.revision, right.revision} == {
        initial.revision + 1,
        initial.revision + 2,
    }


def test_persisted_redaction_sentinel_is_cleaned_with_revision_bump(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file",
        lambda _config, _revision, **_kwargs: None,
    )

    async def exercise():
        engine, maker = await _create_database(tmp_path / "cleanup.db")
        try:
            async with maker() as session:
                session.add(
                    ProviderSettings(
                        id=1,
                        revision=7,
                        config={
                            "LLM": "openai",
                            "OPENAI_API_KEY": REDACTED_SECRET_PLACEHOLDER,
                        },
                    )
                )
                await session.commit()
            async with maker() as session:
                return await get_provider_settings_record(session)
        finally:
            await engine.dispose()

    cleaned = asyncio.run(exercise())

    assert cleaned.revision == 8
    assert cleaned.config == {"LLM": "openai"}


def test_committed_write_survives_mirror_failure_without_secret_logging(
    monkeypatch,
    tmp_path,
    caplog,
):
    secret = "mirror-secret-must-not-leak"

    def fail_mirror(_config, _revision, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file",
        fail_mirror,
    )

    async def exercise():
        engine, maker = await _create_database(tmp_path / "mirror.db")
        try:
            async with maker() as session:
                return await save_provider_settings_record(
                    session,
                    {"LLM": "openai", "OPENAI_API_KEY": secret},
                )
        finally:
            await engine.dispose()

    with caplog.at_level(logging.WARNING, logger="services.provider_settings"):
        committed = asyncio.run(exercise())

    assert committed.config["OPENAI_API_KEY"] == secret
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text


def test_sync_codex_bridge_applies_managed_deletions_without_stale_clobber(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "sync.db"
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    engine = create_engine(f"sqlite:///{database_path}")
    ProviderSettings.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(
            ProviderSettings.__table__.insert().values(
                id=1,
                revision=5,
                updated_at=get_current_utc_datetime(),
                config={
                    "LLM": "openai",
                    "OPENAI_MODEL": "database-newer-model",
                    "CODEX_ACCESS_TOKEN": "old-token",
                },
            )
        )
    update_user_config_file(
        str(config_path),
        lambda _existing: {
            "LLM": "openai",
            "OPENAI_MODEL": "stale-file-model",
        },
    )

    committed = sync_legacy_file_to_provider_settings(
        managed_updates={"CODEX_ACCESS_TOKEN": None},
    )

    with engine.connect() as connection:
        stored = connection.execute(
            select(ProviderSettings.__table__.c.config)
        ).scalar_one()
    engine.dispose()
    assert committed is not None
    assert committed.revision == 6
    assert stored["OPENAI_MODEL"] == "database-newer-model"
    assert "CODEX_ACCESS_TOKEN" not in stored


def test_sync_import_path_is_read_only_and_mirrors_to_runtime_config(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "sync-import.db"
    import_path = tmp_path / "existing-provider.json"
    runtime_path = tmp_path / "rc-runtime" / "userConfig.json"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("USER_CONFIG_PATH", str(runtime_path))
    monkeypatch.setenv("PRESENTON_PROVIDER_CONFIG_IMPORT_PATH", str(import_path))
    engine = create_engine(f"sqlite:///{database_path}")
    ProviderSettings.__table__.create(engine)
    update_user_config_file(
        str(import_path),
        lambda _existing: {"LLM": "codex", "CODEX_ACCESS_TOKEN": "import-only"},
    )
    original_bytes = import_path.read_bytes()

    committed = sync_legacy_file_to_provider_settings(managed_updates=None)

    with engine.connect() as connection:
        stored = connection.execute(
            select(ProviderSettings.__table__.c.config)
        ).scalar_one()
    engine.dispose()
    assert committed is not None
    assert stored["CODEX_ACCESS_TOKEN"] == "import-only"
    assert import_path.read_bytes() == original_bytes
    assert runtime_path.is_file()
    assert read_user_config_file(str(runtime_path))["CODEX_ACCESS_TOKEN"] == "import-only"


def test_sync_semantic_noop_is_revision_guarded(monkeypatch, tmp_path):
    database_path = tmp_path / "sync-noop.db"
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    engine = create_engine(f"sqlite:///{database_path}")
    ProviderSettings.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(
            ProviderSettings.__table__.insert().values(
                id=1,
                revision=5,
                updated_at=get_current_utc_datetime(),
                config={"LLM": "openai", "OPENAI_MODEL": "model-a"},
            )
        )
    update_user_config_file(
        str(config_path),
        lambda _existing: {
            "LLM": "openai",
            "OPENAI_MODEL": "model-a",
            LEGACY_PROVIDER_SETTINGS_REVISION_KEY: 5,
        },
    )
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    monkeypatch.setattr(
        "services.provider_settings_sync.create_engine",
        lambda *_args, **_kwargs: engine,
    )
    committed = sync_legacy_file_to_provider_settings(managed_updates={})

    assert committed is not None
    assert committed.revision == 5
    assert any(
        "UPDATE provider_settings SET revision=provider_settings.revision" in sql
        for sql in statements
    )


def test_sync_cas_exhaustion_uses_bounded_retry_and_safe_logging(
    monkeypatch,
    tmp_path,
    caplog,
):
    config_path = tmp_path / "userConfig.json"
    secret = "sync-database-url-secret-must-not-leak"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    update_user_config_file(str(config_path), lambda _existing: {"LLM": "openai"})

    class FailingEngine:
        disposed = False

        def begin(self):
            raise OperationalError("statement", {}, RuntimeError(secret))

        def dispose(self):
            self.disposed = True

    engine = FailingEngine()
    retry_attempts = []
    monkeypatch.setattr(
        "services.provider_settings_sync.create_engine",
        lambda *_args, **_kwargs: engine,
    )
    monkeypatch.setattr(
        "services.provider_settings_sync.provider_settings_retry_delay_seconds",
        lambda attempt: retry_attempts.append(attempt) or 0.0,
    )
    monkeypatch.setattr("services.provider_settings_sync.time.sleep", lambda _delay: None)

    with caplog.at_level(
        logging.WARNING,
        logger="services.provider_settings_sync",
    ):
        with pytest.raises(ProviderSettingsConcurrencyError):
            sync_legacy_file_to_provider_settings(managed_updates={})

    assert retry_attempts == list(range(PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS - 1))
    assert engine.disposed is True
    assert (
        f"outcome=exhausted attempt={PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS} "
        "error=OperationalError"
    ) in caplog.text
    assert secret not in caplog.text


def test_delayed_older_mirror_cannot_overwrite_newer_revision(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))

    _mirror_to_legacy_file(
        {"LLM": "openai", "OPENAI_MODEL": "newer-model"},
        8,
    )
    _mirror_to_legacy_file(
        {"LLM": "openai", "OPENAI_MODEL": "older-model"},
        7,
    )

    stored = read_user_config_file(str(config_path))
    assert stored["OPENAI_MODEL"] == "newer-model"
    assert stored[LEGACY_PROVIDER_SETTINGS_REVISION_KEY] == 8


def test_equal_revision_mirror_preserves_pending_electron_candidate(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    _mirror_to_legacy_file(
        {"LLM": "openai", "OPENAI_MODEL": "database-model"},
        8,
        force=True,
    )
    update_user_config_file(
        str(config_path),
        lambda existing: {
            **existing,
            "OPENAI_MODEL": "pending-electron-model",
            LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: "pending-token-a",
        },
    )

    _mirror_to_legacy_file(
        {"LLM": "openai", "OPENAI_MODEL": "database-model"},
        8,
    )

    stored = read_user_config_file(str(config_path))
    assert stored["OPENAI_MODEL"] == "pending-electron-model"
    assert stored[LEGACY_PROVIDER_SETTINGS_REVISION_KEY] == 8
    assert (
        stored[LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY]
        == "pending-token-a"
    )


def test_pending_candidate_blocks_even_higher_revision_normal_mirror(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    update_user_config_file(
        str(config_path),
        lambda _existing: {
            "LLM": "openai",
            "OPENAI_MODEL": "pending-model",
            LEGACY_PROVIDER_SETTINGS_REVISION_KEY: 4,
            LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: "pending-token-a",
        },
    )

    _mirror_to_legacy_file(
        {"LLM": "openai", "OPENAI_MODEL": "database-model"},
        9,
    )

    stored = read_user_config_file(str(config_path))
    assert stored["OPENAI_MODEL"] == "pending-model"
    assert stored[LEGACY_PROVIDER_SETTINGS_REVISION_KEY] == 4
    assert (
        stored[LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY]
        == "pending-token-a"
    )


def test_electron_import_commits_and_clears_matching_pending_token(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    update_user_config_file(
        str(config_path),
        lambda _existing: {
            "LLM": "openai",
            "OPENAI_MODEL": "candidate-model",
            LEGACY_PROVIDER_SETTINGS_REVISION_KEY: 5,
            LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: "pending-token-a",
        },
    )

    async def exercise():
        engine, maker = await _create_database(tmp_path / "pending-success.db")
        try:
            async with maker() as session:
                session.add(
                    ProviderSettings(
                        id=1,
                        revision=5,
                        config={"LLM": "openai", "OPENAI_MODEL": "old-model"},
                    )
                )
                await session.commit()
            async with maker() as session:
                return await import_legacy_file_to_provider_settings(session)
        finally:
            await engine.dispose()

    committed = asyncio.run(exercise())
    stored = read_user_config_file(str(config_path))

    assert committed.revision == 6
    assert committed.config["OPENAI_MODEL"] == "candidate-model"
    assert stored["OPENAI_MODEL"] == "candidate-model"
    assert stored[LEGACY_PROVIDER_SETTINGS_REVISION_KEY] == 6
    assert LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY not in stored


def test_stale_electron_file_import_is_rejected_without_db_mutation(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    update_user_config_file(
        str(config_path),
        lambda _existing: {
            "LLM": "openai",
            "OPENAI_MODEL": "stale-model",
            "OPENAI_API_KEY": "stale-secret",
            LEGACY_PROVIDER_SETTINGS_REVISION_KEY: 4,
            LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: "pending-token-a",
        },
    )

    async def exercise():
        engine, maker = await _create_database(tmp_path / "stale-import.db")
        try:
            async with maker() as session:
                session.add(
                    ProviderSettings(
                        id=1,
                        revision=5,
                        config={
                            "LLM": "openai",
                            "OPENAI_MODEL": "database-model",
                            "OPENAI_API_KEY": "database-secret",
                        },
                    )
                )
                await session.commit()
            async with maker() as session:
                with pytest.raises(ProviderSettingsConcurrencyError) as error:
                    await import_legacy_file_to_provider_settings(session)
                assert "stale-secret" not in str(error.value)
            async with maker() as session:
                return await get_provider_settings_record(session)
        finally:
            await engine.dispose()

    stored = asyncio.run(exercise())

    assert stored.revision == 5
    assert stored.config["OPENAI_MODEL"] == "database-model"
    assert stored.config["OPENAI_API_KEY"] == "database-secret"
    recovered = read_user_config_file(str(config_path))
    assert recovered["OPENAI_MODEL"] == "database-model"
    assert recovered[LEGACY_PROVIDER_SETTINGS_REVISION_KEY] == 5
    assert LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY not in recovered


def test_completion_does_not_clear_or_overwrite_new_pending_token(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    update_user_config_file(
        str(config_path),
        lambda _existing: {
            "LLM": "openai",
            "OPENAI_MODEL": "candidate-a",
            LEGACY_PROVIDER_SETTINGS_REVISION_KEY: 5,
            LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: "pending-token-a",
        },
    )

    async def save(_session, _config, **_kwargs):
        update_user_config_file(
            str(config_path),
            lambda existing: {
                **existing,
                "OPENAI_MODEL": "candidate-b",
                LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: "pending-token-b",
            },
        )
        return ProviderSettingsRecord(
            6,
            {"LLM": "openai", "OPENAI_MODEL": "candidate-a"},
        )

    monkeypatch.setattr(
        "services.provider_settings.save_provider_settings_record",
        save,
    )

    committed = asyncio.run(import_legacy_file_to_provider_settings(object()))
    stored = read_user_config_file(str(config_path))

    assert committed.revision == 6
    assert stored["OPENAI_MODEL"] == "candidate-b"
    assert (
        stored[LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY]
        == "pending-token-b"
    )


def test_stale_recovery_does_not_overwrite_new_pending_token(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    update_user_config_file(
        str(config_path),
        lambda _existing: {
            "LLM": "openai",
            "OPENAI_MODEL": "candidate-a",
            LEGACY_PROVIDER_SETTINGS_REVISION_KEY: 5,
            LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: "pending-token-a",
        },
    )

    async def conflict(_session, _config, **_kwargs):
        update_user_config_file(
            str(config_path),
            lambda existing: {
                **existing,
                "OPENAI_MODEL": "candidate-b",
                LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: "pending-token-b",
            },
        )
        raise ProviderSettingsConcurrencyError()

    async def latest(_session):
        return ProviderSettingsRecord(
            6,
            {"LLM": "openai", "OPENAI_MODEL": "database-model"},
        )

    monkeypatch.setattr(
        "services.provider_settings.save_provider_settings_record",
        conflict,
    )
    monkeypatch.setattr(
        "services.provider_settings.get_provider_settings_record",
        latest,
    )

    with pytest.raises(ProviderSettingsConcurrencyError):
        asyncio.run(import_legacy_file_to_provider_settings(object()))
    stored = read_user_config_file(str(config_path))
    assert stored["OPENAI_MODEL"] == "candidate-b"
    assert (
        stored[LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY]
        == "pending-token-b"
    )


def test_startup_applies_one_pending_candidate_across_concurrent_workers(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    update_user_config_file(
        str(config_path),
        lambda _existing: {
            "LLM": "openai",
            "OPENAI_MODEL": "startup-candidate",
            LEGACY_PROVIDER_SETTINGS_REVISION_KEY: 5,
            LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: "startup-token-a",
        },
    )

    async def exercise():
        engine, maker = await _create_database(tmp_path / "startup-pending.db")
        try:
            async with maker() as session:
                session.add(
                    ProviderSettings(
                        id=1,
                        revision=5,
                        config={"LLM": "openai", "OPENAI_MODEL": "old-model"},
                    )
                )
                await session.commit()

            async def initialize():
                async with maker() as session:
                    return await migrate_provider_settings_from_file_record(
                        session
                    )

            records = await asyncio.gather(initialize(), initialize())
            async with maker() as session:
                final = await get_provider_settings_record(session)
            return records, final
        finally:
            await engine.dispose()

    records, final = asyncio.run(exercise())
    stored = read_user_config_file(str(config_path))

    assert all(record.revision == 6 for record in records)
    assert final.revision == 6
    assert final.config["OPENAI_MODEL"] == "startup-candidate"
    assert stored[LEGACY_PROVIDER_SETTINGS_REVISION_KEY] == 6
    assert LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY not in stored


def test_initial_row_mirror_preserves_pending_candidate_created_after_read(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    update_user_config_file(
        str(config_path),
        lambda _existing: {"LLM": "openai", "OPENAI_MODEL": "initial-model"},
    )
    real_mirror = _mirror_to_legacy_file

    def mirror_after_electron_write(config, revision, **kwargs):
        update_user_config_file(
            str(config_path),
            lambda existing: {
                **existing,
                "OPENAI_MODEL": "pending-model",
                LEGACY_PROVIDER_SETTINGS_REVISION_KEY: 0,
                LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: "new-token",
            },
        )
        real_mirror(config, revision, **kwargs)

    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file",
        mirror_after_electron_write,
    )

    async def exercise():
        engine, maker = await _create_database(tmp_path / "startup-new-row.db")
        try:
            async with maker() as session:
                return await migrate_provider_settings_from_file_record(session)
        finally:
            await engine.dispose()

    record = asyncio.run(exercise())
    stored = read_user_config_file(str(config_path))

    assert record.revision == 0
    assert record.config["OPENAI_MODEL"] == "initial-model"
    assert stored["OPENAI_MODEL"] == "pending-model"
    assert stored[LEGACY_PROVIDER_SETTINGS_REVISION_KEY] == 0
    assert stored[LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY] == "new-token"


def test_startup_recovers_stale_pending_candidate_without_db_mutation(
    monkeypatch,
    tmp_path,
    caplog,
):
    config_path = tmp_path / "userConfig.json"
    pending_token = "startup-token-must-not-be-logged"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    update_user_config_file(
        str(config_path),
        lambda _existing: {
            "LLM": "openai",
            "OPENAI_MODEL": "stale-candidate",
            LEGACY_PROVIDER_SETTINGS_REVISION_KEY: 5,
            LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: pending_token,
        },
    )

    async def exercise():
        engine, maker = await _create_database(tmp_path / "startup-stale.db")
        try:
            async with maker() as session:
                session.add(
                    ProviderSettings(
                        id=1,
                        revision=6,
                        config={"LLM": "openai", "OPENAI_MODEL": "latest-model"},
                    )
                )
                await session.commit()
            async with maker() as session:
                return await migrate_provider_settings_from_file_record(session)
        finally:
            await engine.dispose()

    with caplog.at_level(logging.INFO, logger="services.provider_settings"):
        record = asyncio.run(exercise())
    stored = read_user_config_file(str(config_path))

    assert record.revision == 6
    assert record.config["OPENAI_MODEL"] == "latest-model"
    assert stored["OPENAI_MODEL"] == "latest-model"
    assert LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY not in stored
    assert "outcome=recovered revision=6" in caplog.text
    assert pending_token not in caplog.text


def test_stale_recovery_failure_logs_only_error_class(
    monkeypatch,
    tmp_path,
    caplog,
):
    config_path = tmp_path / "userConfig.json"
    pending_token = "recovery-token-must-not-be-logged"
    secret = "recovery-error-secret-must-not-be-logged"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    update_user_config_file(
        str(config_path),
        lambda _existing: {
            "LLM": "openai",
            LEGACY_PROVIDER_SETTINGS_REVISION_KEY: 5,
            LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY: pending_token,
        },
    )

    async def conflict(*_args, **_kwargs):
        raise ProviderSettingsConcurrencyError()

    async def fail_recovery(_session):
        raise RuntimeError(secret)

    monkeypatch.setattr(
        "services.provider_settings.save_provider_settings_record",
        conflict,
    )
    monkeypatch.setattr(
        "services.provider_settings.get_provider_settings_record",
        fail_recovery,
    )

    with caplog.at_level(logging.WARNING, logger="services.provider_settings"):
        with pytest.raises(ProviderSettingsConcurrencyError):
            asyncio.run(import_legacy_file_to_provider_settings(object()))

    assert "outcome=failed error=RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert pending_token not in caplog.text
