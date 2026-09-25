from __future__ import annotations

from collections.abc import Mapping

import pytest
from fastapi import HTTPException

from constants.provider_registry import ProviderResolutionError
from models.generate_presentation_request import GeneratePresentationRequest
from services.generation_provider_admission import (
    generation_provider_capabilities,
    server_generation_profile_identity,
)
from services.presentation_profiles import resolve_presentation_profile
from utils import llm_provider
from utils.generation_model import generation_model_settings
from utils.llm_provider import (
    generation_provider_admission,
    generation_provider_policy,
    resolve_generation_provider,
)
from utils.runtime_settings import RuntimeSettings


def _settings(provider: str) -> RuntimeSettings:
    return RuntimeSettings.from_sources(environ={}, user_config={"LLM": provider})


@pytest.mark.parametrize(
    ("preset", "model", "effort"),
    [
        ("luna-medium", "gpt-5.6-luna", "medium"),
        ("luna-high", "gpt-5.6-luna", "high"),
        ("sol-low", "gpt-5.6-sol", "low"),
        ("sol-medium", "gpt-5.6-sol", "medium"),
        ("sol-high", "gpt-5.6-sol", "high"),
        ("sol6-low", "gpt-6-sol", "low"),
        ("sol6-medium", "gpt-6-sol", "medium"),
        ("sol6-high", "gpt-6-sol", "high"),
        ("astra-low", "gpt-6-astra", "low"),
        ("astra-medium", "gpt-6-astra", "medium"),
    ],
)
def test_upload_generation_preset_pins_codex_model_and_effort(
    preset: str,
    model: str,
    effort: str,
) -> None:
    request = GeneratePresentationRequest.model_validate(
        {"content": "preset", "codex_generation_preset": preset}
    )
    restored = GeneratePresentationRequest.model_validate(
        request.model_dump(mode="json")
    )

    settings = generation_model_settings(
        restored.codex_generation_preset,
        _settings("codex"),
    )

    assert settings.get_env("CODEX_MODEL") == model
    assert settings.get_env("CODEX_REASONING_EFFORT") == effort


@pytest.mark.parametrize(
    ("strategy", "template", "driver_id", "required"),
    [
        ("authored", "general", "authored", ("structuredOutput", "thinking")),
    ],
)
def test_generation_profiles_admit_supported_provider(
    strategy: str,
    template: str,
    driver_id: str,
    required: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.generation_provider_admission.disable_thinking",
        lambda: False,
    )
    request = GeneratePresentationRequest(
        content="provider admission",
        strategy=strategy,
        template=template,
    )
    identity = server_generation_profile_identity(request, None)
    profile = resolve_presentation_profile(identity)

    assert profile.driver_id == driver_id
    assert generation_provider_capabilities(request, profile.driver_id) == required
    admission = resolve_generation_provider(
        identity,
        required_capabilities=required,
        settings=_settings("openai"),
    )
    assert admission.presentation_driver == driver_id
    assert admission.provider_id == "openai"


def test_client_cannot_select_a_presentation_driver() -> None:
    request = GeneratePresentationRequest.model_validate(
        {
            "content": "server owned profile",
            "strategy": "authored",
            "profile_driver_id": "authored",
        }
    )

    identity = server_generation_profile_identity(request, None)

    assert isinstance(identity, Mapping)
    assert resolve_presentation_profile(identity).driver_id == "authored"


@pytest.mark.parametrize("driver_id", ["template-v2", "authored"])
def test_generation_driver_mismatch_fails_before_invocation(driver_id: str) -> None:
    identity_by_driver = {
        "template-v2": {
            "generation_mode": "standard",
            "mode": "template",
            "version": "v2-standard",
            "theme": {"mode": "template"},
        },
        "authored": {
            "generation_mode": "standard",
            "mode": "authored",
            "version": "v1-standard",
            "theme": {"mode": "authored"},
        },
    }
    invoked = False

    with pytest.raises(ProviderResolutionError) as error:
        with generation_provider_admission(
            identity_by_driver[driver_id],
            required_capabilities=("structuredOutput",),
            settings=_settings("cerebras"),
        ):
            invoked = True

    assert error.value.code == "provider_driver_mismatch"
    assert invoked is False


def test_authored_capability_mismatch_fails_before_invocation() -> None:
    invoked = False
    with pytest.raises(ProviderResolutionError) as error:
        with generation_provider_admission(
            {
                "generation_mode": "standard",
                "mode": "authored",
                "version": "v1-standard",
                "theme": {},
            },
            required_capabilities=("structuredOutput",),
            settings=_settings("cerebras"),
        ):
            invoked = True

    assert error.value.code == "provider_capability_mismatch"
    assert invoked is False


def test_client_factory_is_not_constructed_before_runtime_policy_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = RuntimeSettings.from_sources(
        environ={},
        user_config={
            "LLM": "cerebras",
            "OPENAI_API_KEY": "secret-must-not-reach-client",
        },
    )
    client_factory_calls = 0

    def forbidden_client_factory(**_kwargs: object) -> object:
        nonlocal client_factory_calls
        client_factory_calls += 1
        raise AssertionError("client factory must not run")

    monkeypatch.setattr(llm_provider, "OpenAI", forbidden_client_factory)
    with generation_provider_policy(
        {
            "generation_mode": "standard",
            "mode": "authored",
            "version": "v1-standard",
            "theme": {},
        },
        required_capabilities=("structuredOutput",),
    ):
        with pytest.raises(HTTPException) as error:
            llm_provider.get_llm_client(settings)

    assert error.value.detail == "provider_capability_mismatch"
    assert client_factory_calls == 0


def test_disabled_provider_is_rejected_at_the_client_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings("openai")
    client_factory_calls = 0

    def disabled_provider(*_args: object, **_kwargs: object) -> object:
        raise ProviderResolutionError("provider_disabled", "openai")

    def forbidden_client_factory(**_kwargs: object) -> object:
        nonlocal client_factory_calls
        client_factory_calls += 1
        return object()

    monkeypatch.setattr(llm_provider, "resolve_provider", disabled_provider)
    monkeypatch.setattr(llm_provider, "OpenAI", forbidden_client_factory)
    with generation_provider_policy(
        {
            "generation_mode": "standard",
            "mode": "authored",
            "version": "v1-standard",
            "theme": {},
        },
        required_capabilities=("structuredOutput",),
    ):
        with pytest.raises(HTTPException) as error:
            llm_provider.get_llm_client(settings)

    assert error.value.detail == "provider_disabled"
    assert client_factory_calls == 0


def test_unknown_runtime_provider_fails_closed_without_exposing_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "runtime-secret-must-not-leak"
    settings = RuntimeSettings.from_sources(
        environ={},
        user_config={"LLM": "openai", "OPENAI_API_KEY": secret},
    )
    monkeypatch.setattr(llm_provider, "get_llm_provider_env", lambda _settings: "future")

    with pytest.raises(ProviderResolutionError) as error:
        resolve_generation_provider(
            {
                "generation_mode": "standard",
                "mode": "authored",
                "version": "v1-standard",
                "theme": {},
            },
            required_capabilities=("structuredOutput",),
            settings=settings,
        )

    assert error.value.code == "provider_unknown"
    assert secret not in str(error.value)
    assert secret not in repr(error.value)


def test_web_search_and_vision_are_required_only_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.generation_provider_admission.disable_thinking",
        lambda: False,
    )
    request = GeneratePresentationRequest(
        content="capability derivation",
        strategy="authored",
        web_search=True,
        vision_qa=True,
    )

    capabilities = generation_provider_capabilities(request, "authored")

    assert capabilities == (
        "structuredOutput",
        "webSearch",
        "vision",
        "thinking",
    )
