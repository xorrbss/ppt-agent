import asyncio
import logging

from services.provider_settings import ProviderSettingsRecord
from services.runtime_settings_coordinator import RuntimeSettingsCoordinator
from utils.runtime_settings import (
    RuntimeSettings,
    get_process_runtime_settings_envelope,
    publish_process_runtime_settings,
    replace_process_runtime_settings,
)


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


def test_poll_fetches_configuration_only_after_revision_advances(monkeypatch):
    coordinator = RuntimeSettingsCoordinator(
        session_factory=_SessionContext,
        poll_interval_seconds=0.01,
    )
    previous = replace_process_runtime_settings(None)
    base = RuntimeSettings.from_sources(
        environ={},
        user_config={"LLM": "openai", "OPENAI_MODEL": "old-model"},
    )
    publish_process_runtime_settings(10, base)
    revisions = iter((10, 11))
    fetches = []

    async def revision(_session):
        return next(revisions)

    async def record(_session):
        fetches.append(True)
        return ProviderSettingsRecord(
            11,
            {"LLM": "openai", "OPENAI_MODEL": "new-model"},
        )

    monkeypatch.setattr(
        "services.runtime_settings_coordinator.get_provider_settings_revision",
        revision,
    )
    monkeypatch.setattr(
        "services.runtime_settings_coordinator.get_provider_settings_record",
        record,
    )
    try:
        assert asyncio.run(coordinator.poll_once()) == "no_change"
        assert fetches == []
        assert asyncio.run(coordinator.poll_once()) == "applied"
        assert fetches == [True]
        envelope = get_process_runtime_settings_envelope()
        assert envelope is not None
        assert envelope.revision == 11
        assert envelope.settings.get("OPENAI_MODEL") == "new-model"
    finally:
        replace_process_runtime_settings(previous)


def test_poll_failure_keeps_last_good_and_logs_only_safe_metadata(caplog):
    secret = "secret-that-must-not-be-logged"
    jitter_bases = []
    coordinator = RuntimeSettingsCoordinator(
        session_factory=_SessionContext,
        poll_interval_seconds=0.001,
        retry_jitter=lambda base: jitter_bases.append(base) or 0.0,
    )
    previous = replace_process_runtime_settings(None)
    settings = RuntimeSettings.from_sources(
        environ={},
        user_config={"LLM": "openai", "OPENAI_API_KEY": secret},
    )
    publish_process_runtime_settings(20, settings)
    calls = 0

    async def fail():
        nonlocal calls
        calls += 1
        if calls >= 2:
            coordinator._stop.set()
        raise RuntimeError(secret)

    coordinator.poll_once = fail  # type: ignore[method-assign]
    with caplog.at_level(
        logging.WARNING,
        logger="services.runtime_settings_coordinator",
    ):
        asyncio.run(coordinator.run())

    try:
        envelope = get_process_runtime_settings_envelope()
        assert envelope is not None
        assert envelope.revision == 20
        assert envelope.settings is settings
        assert calls == 2
        assert jitter_bases == [0.002, 0.004]
        assert "RuntimeError" in caplog.text
        assert "attempt=2" in caplog.text
        assert secret not in caplog.text
    finally:
        replace_process_runtime_settings(previous)


def test_coordinator_snapshot_consumes_database_authority_when_changes_are_locked(
    monkeypatch,
):
    monkeypatch.setenv("CAN_CHANGE_KEYS", "false")
    monkeypatch.setenv("LLM", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    record = ProviderSettingsRecord(
        1,
        {"LLM": "google", "GOOGLE_MODEL": "database-model"},
    )

    settings = RuntimeSettingsCoordinator._settings(record)

    assert settings.get("LLM") == "google"
    assert settings.get("OPENAI_MODEL") == "environment-model"
    assert settings.get("GOOGLE_MODEL") == "database-model"
