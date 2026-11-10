import asyncio

import pytest

from services import image_generation_service as image_service_module
from services.image_generation_service import ImageGenerationService
from services.mem0_oss_memory import _oss_config_from_env
from utils.runtime_settings import (
    RuntimeSettings,
)


def _service_without_credentials() -> ImageGenerationService:
    service = object.__new__(ImageGenerationService)
    service.settings = RuntimeSettings.from_sources(environ={}, user_config={})
    return service


def test_openai_image_generation_fails_before_network_without_api_key(
    monkeypatch, tmp_path
):
    service = _service_without_credentials()
    client_created = False

    def unexpected_client(**_kwargs):
        nonlocal client_created
        client_created = True
        raise AssertionError("network client must not be created without a key")

    monkeypatch.setattr(image_service_module, "AsyncOpenAI", unexpected_client)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        asyncio.run(
            service.generate_image_openai("prompt", str(tmp_path), "gpt-image-2", "medium")
        )

    assert client_created is False


def test_google_image_generation_fails_before_network_without_api_key(
    monkeypatch, tmp_path
):
    service = _service_without_credentials()
    client_created = False

    def unexpected_client(**_kwargs):
        nonlocal client_created
        client_created = True
        raise AssertionError("network client must not be created without a key")

    monkeypatch.setattr(image_service_module.genai, "Client", unexpected_client)

    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        asyncio.run(
            service._generate_image_google("prompt", str(tmp_path), "image-model")
        )

    assert client_created is False


def test_mem0_ollama_fallback_uses_snapshot_without_reusing_openai_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-reused")
    monkeypatch.delenv("MEM0_LLM_API_KEY", raising=False)
    settings = RuntimeSettings.from_sources(
        environ={},
        user_config={
            "OLLAMA_MODEL": "snapshot-model",
            "OLLAMA_URL": "http://snapshot-ollama:11434",
        },
    )

    *_paths, config = _oss_config_from_env(settings)
    llm_config = config["llm"]["config"]

    assert llm_config["model"] == "snapshot-model"
    assert llm_config["openai_base_url"] == "http://snapshot-ollama:11434/v1"
    assert llm_config["api_key"] == "ollama"
