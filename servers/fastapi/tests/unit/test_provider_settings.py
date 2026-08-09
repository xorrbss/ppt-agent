import asyncio
import threading

import pytest
from fastapi import HTTPException
from api.v1.admin import router as admin_router
from models.sql.provider_settings import ProviderSettings
from models.sql.user import User
from services.provider_settings import (
    ProviderSettingsValidationError,
    REDACTED_SECRET_PLACEHOLDER,
    merge_provider_settings,
    migrate_provider_settings_from_file,
    redact_provider_settings,
    save_provider_settings,
    sanitize_provider_settings,
)
from utils.user_config_store import read_user_config_file, update_user_config_file


class ProviderSettingsSession:
    def __init__(self):
        self.row = None
        self.commit_count = 0

    async def get(self, _model, _key):
        return self.row

    def add(self, row):
        self.row = row

    async def commit(self):
        self.commit_count += 1
        return None

    async def refresh(self, _row):
        return None


def test_user_table_has_username_and_no_email_column():
    columns = set(User.__table__.columns.keys())

    assert "username" in columns
    assert "email" not in columns


def test_provider_settings_exclude_all_legacy_auth_fields():
    assert sanitize_provider_settings(
        {
            "LLM": "openai",
            "AUTH_USERNAME": "admin",
            "AUTH_PASSWORD": "plain",
            "AUTH_PASSWORD_HASH": "hash",
            "AUTH_SECRET_KEY": "jwt-secret",
        }
    ) == {"LLM": "openai"}


def test_provider_settings_api_redacts_registry_secrets_and_preserves_placeholders():
    stored = {
        "LLM": "openai",
        "OPENAI_API_KEY": "provider-key",
        "OPENAI_MODEL": "gpt-test",
    }

    public = redact_provider_settings(stored)

    assert public == {
        "LLM": "openai",
        "OPENAI_API_KEY": REDACTED_SECRET_PLACEHOLDER,
        "OPENAI_MODEL": "gpt-test",
    }
    assert "provider-key" not in repr(public)
    assert merge_provider_settings(stored, public) == stored


def test_secret_placeholder_is_never_persisted_without_an_existing_secret():
    assert merge_provider_settings(
        {"LLM": "openai"},
        {"OPENAI_API_KEY": REDACTED_SECRET_PLACEHOLDER},
    ) == {"LLM": "openai"}


def test_custom_secret_survives_redacted_and_partial_updates():
    stored = {
        "LLM": "custom",
        "CUSTOM_LLM_URL": "https://custom.example.test/v1",
        "CUSTOM_LLM_API_KEY": "stored-custom-key",
        "CUSTOM_MODEL": "private-model",
    }

    redacted = redact_provider_settings(stored)
    assert redacted["CUSTOM_LLM_API_KEY"] == REDACTED_SECRET_PLACEHOLDER
    assert "stored-custom-key" not in repr(redacted)

    assert merge_provider_settings(stored, redacted) == stored
    assert merge_provider_settings(
        stored, {"CUSTOM_MODEL": "replacement-model"}
    ) == {
        **stored,
        "CUSTOM_MODEL": "replacement-model",
    }


def test_partial_update_removes_a_previously_persisted_secret_placeholder():
    assert merge_provider_settings(
        {
            "LLM": "custom",
            "CUSTOM_LLM_API_KEY": REDACTED_SECRET_PLACEHOLDER,
        },
        {"CUSTOM_MODEL": "private-model"},
    ) == {
        "LLM": "custom",
        "CUSTOM_MODEL": "private-model",
    }


def test_save_partial_custom_update_preserves_secret_in_db_and_file_mirror(
    monkeypatch,
):
    stored = {
        "LLM": "custom",
        "CUSTOM_LLM_URL": "https://custom.example.test/v1",
        "CUSTOM_LLM_API_KEY": "stored-custom-key",
        "CUSTOM_MODEL": "private-model",
    }
    session = ProviderSettingsSession()
    session.row = ProviderSettings(id=1, config=stored)
    mirrored = []
    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file",
        lambda config: mirrored.append(dict(config)),
    )

    saved = asyncio.run(
        save_provider_settings(session, {"CUSTOM_MODEL": "replacement-model"})
    )

    expected = {**stored, "CUSTOM_MODEL": "replacement-model"}
    assert saved == expected
    assert session.row.config == expected
    assert mirrored == [expected]


def test_save_never_writes_a_placeholder_when_no_custom_secret_exists(monkeypatch):
    session = ProviderSettingsSession()
    mirrored = []
    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file",
        lambda config: mirrored.append(dict(config)),
    )

    saved = asyncio.run(
        save_provider_settings(
            session,
            {
                "LLM": "custom",
                "CUSTOM_LLM_URL": "https://custom.example.test/v1",
                "CUSTOM_LLM_API_KEY": REDACTED_SECRET_PLACEHOLDER,
                "CUSTOM_MODEL": "private-model",
            },
        )
    )

    assert "CUSTOM_LLM_API_KEY" not in saved
    assert "CUSTOM_LLM_API_KEY" not in session.row.config
    assert REDACTED_SECRET_PLACEHOLDER not in repr(mirrored)


def test_admin_get_redacts_secret_values(monkeypatch):
    async def get_settings(_session):
        return {"LLM": "openai", "OPENAI_API_KEY": "provider-key"}

    monkeypatch.setattr(admin_router, "_ensure_settings_are_mutable", lambda: None)
    monkeypatch.setattr(admin_router, "get_provider_settings", get_settings)

    response = asyncio.run(admin_router.read_provider_settings(None, object()))

    assert response == {
        "LLM": "openai",
        "OPENAI_API_KEY": REDACTED_SECRET_PLACEHOLDER,
    }
    assert "provider-key" not in repr(response)


def test_admin_custom_model_check_uses_stored_key_without_exposing_it(monkeypatch):
    stored_key = "stored-custom-key"
    provider_call = {}

    async def get_settings(_session):
        return {
            "LLM": "custom",
            "CUSTOM_LLM_URL": "https://custom.example.test/v1",
            "CUSTOM_LLM_API_KEY": stored_key,
            "CUSTOM_MODEL": "private-model",
        }

    async def list_models(url, api_key):
        provider_call.update(url=url, api_key=api_key)
        return ["other-model", "private-model"]

    monkeypatch.setattr(admin_router, "_ensure_settings_are_mutable", lambda: None)
    monkeypatch.setattr(admin_router, "get_provider_settings", get_settings)
    monkeypatch.setattr(
        admin_router, "list_available_openai_compatible_models", list_models
    )

    response = asyncio.run(
        admin_router.check_stored_custom_model_available(None, object())
    )

    assert provider_call == {
        "url": "https://custom.example.test/v1",
        "api_key": stored_key,
    }
    assert response == {"available": True}
    assert stored_key not in repr(response)
    assert REDACTED_SECRET_PLACEHOLDER not in provider_call.values()


def test_admin_custom_model_check_works_when_provider_mutations_are_disabled(
    monkeypatch,
):
    stored = {
        "LLM": "custom",
        "CUSTOM_LLM_URL": "https://custom.example.test/v1",
        "CUSTOM_LLM_API_KEY": "stored-custom-key",
        "CUSTOM_MODEL": "private-model",
    }
    assert (
        redact_provider_settings(stored)["CUSTOM_LLM_API_KEY"]
        == REDACTED_SECRET_PLACEHOLDER
    )

    async def get_settings(_session):
        return stored

    async def list_models(_url, _api_key):
        return ["private-model"]

    monkeypatch.setattr(admin_router, "get_can_change_keys_env", lambda: "false")
    monkeypatch.setattr(admin_router, "get_provider_settings", get_settings)
    monkeypatch.setattr(
        admin_router, "list_available_openai_compatible_models", list_models
    )

    response = asyncio.run(
        admin_router.check_stored_custom_model_available(None, object())
    )

    assert response == {"available": True}


def test_admin_custom_model_check_reports_model_absence(monkeypatch):
    async def get_settings(_session):
        return {
            "CUSTOM_LLM_URL": "https://custom.example.test/v1",
            "CUSTOM_LLM_API_KEY": "stored-custom-key",
            "CUSTOM_MODEL": "missing-model",
        }

    async def list_models(_url, _api_key):
        return ["other-model"]

    monkeypatch.setattr(admin_router, "_ensure_settings_are_mutable", lambda: None)
    monkeypatch.setattr(admin_router, "get_provider_settings", get_settings)
    monkeypatch.setattr(
        admin_router, "list_available_openai_compatible_models", list_models
    )

    response = asyncio.run(
        admin_router.check_stored_custom_model_available(None, object())
    )

    assert response == {"available": False}


def test_admin_custom_model_check_never_sends_a_stored_placeholder(monkeypatch):
    async def get_settings(_session):
        return {
            "CUSTOM_LLM_URL": "https://custom.example.test/v1",
            "CUSTOM_LLM_API_KEY": REDACTED_SECRET_PLACEHOLDER,
            "CUSTOM_MODEL": "private-model",
        }

    async def list_models(_url, _api_key):
        pytest.fail("the redaction placeholder must never reach a provider")

    monkeypatch.setattr(admin_router, "_ensure_settings_are_mutable", lambda: None)
    monkeypatch.setattr(admin_router, "get_provider_settings", get_settings)
    monkeypatch.setattr(
        admin_router, "list_available_openai_compatible_models", list_models
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            admin_router.check_stored_custom_model_available(None, object())
        )

    assert error.value.status_code == 422
    assert REDACTED_SECRET_PLACEHOLDER not in str(error.value.detail)


def test_admin_custom_model_check_redacts_provider_error_details(monkeypatch):
    stored_key = "stored-custom-key"

    async def get_settings(_session):
        return {
            "CUSTOM_LLM_URL": "https://custom.example.test/v1",
            "CUSTOM_LLM_API_KEY": stored_key,
            "CUSTOM_MODEL": "private-model",
        }

    async def list_models(_url, _api_key):
        raise RuntimeError(f"upstream accidentally included {stored_key}")

    monkeypatch.setattr(admin_router, "_ensure_settings_are_mutable", lambda: None)
    monkeypatch.setattr(admin_router, "get_provider_settings", get_settings)
    monkeypatch.setattr(
        admin_router, "list_available_openai_compatible_models", list_models
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            admin_router.check_stored_custom_model_available(None, object())
        )

    assert error.value.status_code == 502
    assert stored_key not in str(error.value.detail)
    assert "custom.example.test" not in str(error.value.detail)


def test_admin_put_preserves_placeholder_but_publishes_raw_secret(monkeypatch):
    existing = {"LLM": "openai", "OPENAI_API_KEY": "provider-key"}
    published = []

    async def save_settings(_session, incoming):
        return merge_provider_settings(existing, incoming)

    monkeypatch.setattr(admin_router, "_ensure_settings_are_mutable", lambda: None)
    monkeypatch.setattr(admin_router, "save_provider_settings", save_settings)
    monkeypatch.setattr(
        admin_router,
        "refresh_process_runtime_settings",
        lambda *, persisted_config: published.append(persisted_config),
    )

    response = asyncio.run(
        admin_router.update_provider_settings(
            {"LLM": "openai", "OPENAI_API_KEY": REDACTED_SECRET_PLACEHOLDER},
            None,
            object(),
        )
    )

    assert response["OPENAI_API_KEY"] == REDACTED_SECRET_PLACEHOLDER
    assert published == [existing]


@pytest.mark.parametrize(
    "invalid",
    [
        {"LLM": "not-a-provider"},
        {"COMFYUI_WORKFLOW": "{broken"},
    ],
)
def test_invalid_registry_values_never_commit_or_mirror(monkeypatch, invalid):
    session = ProviderSettingsSession()
    session.row = ProviderSettings(id=1, config={"LLM": "openai"})
    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file",
        lambda _config: pytest.fail("invalid settings must not reach the file mirror"),
    )

    with pytest.raises(ProviderSettingsValidationError):
        asyncio.run(save_provider_settings(session, invalid))

    assert session.row.config == {"LLM": "openai"}
    assert session.commit_count == 0


def test_admin_put_projects_invalid_registry_values_as_422(monkeypatch):
    async def reject(_session, _incoming):
        raise ProviderSettingsValidationError("LLM")

    monkeypatch.setattr(admin_router, "_ensure_settings_are_mutable", lambda: None)
    monkeypatch.setattr(admin_router, "save_provider_settings", reject)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            admin_router.update_provider_settings(
                {"LLM": "not-a-provider"}, None, object()
            )
        )

    assert error.value.status_code == 422
    assert "not-a-provider" not in str(error.value.detail)


def test_provider_settings_file_mirror_does_not_block_the_event_loop(monkeypatch):
    mirror_started = threading.Event()
    release_mirror = threading.Event()
    ticker_ran = threading.Event()
    event_loop_was_blocked = threading.Event()

    def blocking_mirror(_config):
        mirror_started.set()
        release_mirror.wait(timeout=2)

    def observe_ticker():
        mirror_started.wait(timeout=2)
        if not ticker_ran.wait(timeout=0.5):
            event_loop_was_blocked.set()
        release_mirror.set()

    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file", blocking_mirror
    )
    session = ProviderSettingsSession()

    async def exercise():
        observer = threading.Thread(target=observe_ticker)
        observer.start()

        async def tick():
            await asyncio.sleep(0)
            ticker_ran.set()

        await asyncio.gather(
            save_provider_settings(session, {"LLM": "openai"}),
            tick(),
        )
        observer.join(timeout=2)

    asyncio.run(exercise())

    assert ticker_ran.is_set()
    assert not event_loop_was_blocked.is_set()


def test_concurrent_admin_puts_publish_in_serialized_order(monkeypatch):
    stored = {"LLM": "openai"}
    entered_first = asyncio.Event()
    release_first = asyncio.Event()
    calls = []
    published = []

    async def save_settings(_session, incoming):
        calls.append(dict(incoming))
        if "OPENAI_MODEL" in incoming:
            entered_first.set()
            await release_first.wait()
        stored.update(incoming)
        return dict(stored)

    monkeypatch.setattr(admin_router, "_ensure_settings_are_mutable", lambda: None)
    monkeypatch.setattr(admin_router, "save_provider_settings", save_settings)
    monkeypatch.setattr(
        admin_router,
        "refresh_process_runtime_settings",
        lambda *, persisted_config: published.append(dict(persisted_config)),
    )

    async def exercise():
        first = asyncio.create_task(
            admin_router.update_provider_settings(
                {"OPENAI_MODEL": "model-a"}, None, object()
            )
        )
        await entered_first.wait()
        second = asyncio.create_task(
            admin_router.update_provider_settings(
                {"GOOGLE_MODEL": "model-b"}, None, object()
            )
        )
        await asyncio.sleep(0)
        assert calls == [{"OPENAI_MODEL": "model-a"}]
        release_first.set()
        await asyncio.gather(first, second)

    asyncio.run(exercise())

    assert published[-1] == {
        "LLM": "openai",
        "OPENAI_MODEL": "model-a",
        "GOOGLE_MODEL": "model-b",
    }


def test_startup_migrates_user_config_and_rewrites_compatibility_file(
    monkeypatch, tmp_path
):
    path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(path))
    update_user_config_file(
        str(path),
        lambda _: {
            "AUTH_USERNAME": "admin",
            "AUTH_PASSWORD_HASH": "legacy-hash",
            "AUTH_SECRET_KEY": "jwt-secret",
            "LLM": "openai",
            "OPENAI_API_KEY": "provider-key",
        },
    )
    session = ProviderSettingsSession()

    migrated = asyncio.run(migrate_provider_settings_from_file(session))

    assert migrated == {
        "LLM": "openai",
        "OPENAI_API_KEY": "provider-key",
    }
    assert session.row.config == migrated
    assert read_user_config_file(str(path)) == {
        "LLM": "openai",
        "OPENAI_API_KEY": "provider-key",
        "AUTH_USERNAME": "admin",
        "AUTH_PASSWORD_HASH": "legacy-hash",
        "AUTH_SECRET_KEY": "jwt-secret",
    }
