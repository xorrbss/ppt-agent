import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from dataclasses import FrozenInstanceError

import pytest

from constants.settings_registry import (
    get_setting_choices,
    get_settings_registry,
)
from enums.image_provider import ImageProvider
from enums.llm_provider import LLMProvider
from models.user_config import UserConfig
from utils.llm_config import get_llm_config
from utils.llm_provider import get_model
from utils.runtime_settings import (
    RuntimeSettings,
    RuntimeSettingsInvariantError,
    RuntimeSettingsPublishOutcome,
    create_runtime_settings_task,
    get_process_runtime_settings_envelope,
    publish_process_runtime_settings,
    replace_process_runtime_settings,
    resolve_runtime_settings,
    runtime_settings_context,
)


def _openai_settings(api_key: str, model: str) -> RuntimeSettings:
    return RuntimeSettings.from_sources(
        environ={},
        user_config={
            "LLM": "openai",
            "OPENAI_API_KEY": api_key,
            "OPENAI_MODEL": model,
        },
    )


def test_registry_is_the_typed_provider_authority():
    registry = get_settings_registry()
    keys = [entry["key"] for entry in registry]

    assert len(keys) == len(set(keys))
    assert set(get_setting_choices("LLM")) == {provider.value for provider in LLMProvider}
    assert set(get_setting_choices("IMAGE_PROVIDER")) == {
        provider.value for provider in ImageProvider
    }
    assert all(
        {"key", "type", "default", "secret", "targets"}.issubset(entry)
        for entry in registry
    )
    assert all(entry["default"] is None for entry in registry if entry["secret"])
    persisted_targets = {"start.user_config", "user_config.managed", "user_config.ui"}
    assert set(UserConfig.model_fields) == {
        entry["key"]
        for entry in registry
        if persisted_targets.intersection(entry["targets"])
    }


def test_runtime_settings_are_deeply_immutable():
    settings = _openai_settings("key-a", "model-a")

    with pytest.raises(FrozenInstanceError):
        settings._values = {}  # type: ignore[misc]
    with pytest.raises(TypeError):
        settings._values["OPENAI_API_KEY"] = "changed"  # type: ignore[index]


def test_revisioned_publication_is_monotonic_idempotent_and_secret_safe():
    previous = replace_process_runtime_settings(None)
    current = get_process_runtime_settings_envelope()
    base = (current.revision if current is not None else 0) + 10
    settings_a = _openai_settings("secret-a", "model-a")
    settings_b = _openai_settings("secret-b", "model-b")
    try:
        assert (
            publish_process_runtime_settings(base, settings_a)
            == RuntimeSettingsPublishOutcome.APPLIED
        )
        assert (
            publish_process_runtime_settings(base, settings_a)
            == RuntimeSettingsPublishOutcome.DUPLICATE
        )
        assert (
            publish_process_runtime_settings(base - 1, settings_b)
            == RuntimeSettingsPublishOutcome.OUT_OF_ORDER
        )
        with pytest.raises(RuntimeSettingsInvariantError) as error:
            publish_process_runtime_settings(base, settings_b)
        assert "secret-a" not in str(error.value)
        assert "secret-b" not in str(error.value)
        envelope = get_process_runtime_settings_envelope()
        assert envelope is not None
        assert envelope.revision == base
        assert envelope.settings is settings_a
    finally:
        replace_process_runtime_settings(previous)


def test_revision_envelope_compare_and_publish_is_atomic_across_threads():
    previous = replace_process_runtime_settings(None)
    settings = [
        _openai_settings(f"key-{revision}", f"model-{revision}")
        for revision in range(101, 121)
    ]
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(
                executor.map(
                    lambda item: publish_process_runtime_settings(*item),
                    zip(range(101, 121), settings, strict=True),
                )
            )
        envelope = get_process_runtime_settings_envelope()
        assert envelope is not None
        assert envelope.revision == 120
        assert envelope.settings == settings[-1]
        assert RuntimeSettingsPublishOutcome.APPLIED in outcomes
        assert set(outcomes).issubset(
            {
                RuntimeSettingsPublishOutcome.APPLIED,
                RuntimeSettingsPublishOutcome.OUT_OF_ORDER,
            }
        )
    finally:
        replace_process_runtime_settings(previous)


def test_concurrent_runtime_snapshots_do_not_cross_contaminate_llm_resolution():
    before = dict(os.environ)
    settings_a = _openai_settings("key-a", "model-a")
    settings_b = _openai_settings("key-b", "model-b")

    async def exercise():
        release_a = asyncio.Event()
        b_finished = asyncio.Event()

        async def resolve_a():
            await release_a.wait()
            config = get_llm_config(settings_a)
            return config.api_key, get_model(settings_a)

        async def resolve_b():
            config = get_llm_config(settings_b)
            b_finished.set()
            return config.api_key, get_model(settings_b)

        task_a = asyncio.create_task(resolve_a())
        task_b = asyncio.create_task(resolve_b())
        await asyncio.wait_for(b_finished.wait(), timeout=5)
        release_a.set()
        return await asyncio.gather(task_a, task_b)

    result_a, result_b = asyncio.run(exercise())

    assert result_a == ("key-a", "model-a")
    assert result_b == ("key-b", "model-b")
    assert dict(os.environ) == before


def test_image_and_boolean_settings_are_typed_without_environment_mutation():
    before = dict(os.environ)
    settings = RuntimeSettings.from_sources(
        environ={},
        user_config={
            "DISABLE_IMAGE_GENERATION": False,
            "ENABLE_PARALLEL_IMAGE_GENERATION": "false",
            "IMAGE_PROVIDER": "openai_compatible",
        },
    )

    assert settings.get("DISABLE_IMAGE_GENERATION") is False
    assert settings.get("ENABLE_PARALLEL_IMAGE_GENERATION") is False
    assert settings.get("IMAGE_PROVIDER") == "openai_compatible"
    assert dict(os.environ) == before


def test_running_task_keeps_snapshot_after_process_registry_swap():
    settings_a = _openai_settings("key-a", "model-a")
    settings_b = _openai_settings("key-b", "model-b")
    previous = replace_process_runtime_settings(settings_a)

    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()

        async def worker():
            with runtime_settings_context() as captured:
                started.set()
                await release.wait()
                return captured, resolve_runtime_settings()

        task = asyncio.create_task(worker())
        await asyncio.wait_for(started.wait(), timeout=5)
        replace_process_runtime_settings(settings_b)
        release.set()
        old_pair = await task
        with runtime_settings_context() as new_snapshot:
            return old_pair, new_snapshot

    try:
        (captured, observed_after_swap), new_snapshot = asyncio.run(exercise())
    finally:
        replace_process_runtime_settings(previous)

    assert captured is settings_a
    assert observed_after_swap is settings_a
    assert new_snapshot is settings_b


def test_created_task_captures_current_request_snapshot_not_new_process_snapshot():
    settings_a = _openai_settings("key-a", "model-a")
    settings_b = _openai_settings("key-b", "model-b")
    previous = replace_process_runtime_settings(settings_a)

    async def exercise():
        with runtime_settings_context(settings_a):
            replace_process_runtime_settings(settings_b)

            async def worker():
                await asyncio.sleep(0)
                return resolve_runtime_settings()

            return await create_runtime_settings_task(worker())

    try:
        observed = asyncio.run(exercise())
    finally:
        replace_process_runtime_settings(previous)

    assert observed is settings_a
