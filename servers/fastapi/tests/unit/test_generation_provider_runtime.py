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
from utils.llm_provider import (
    generation_provider_admission,
    generation_provider_policy,
    resolve_generation_provider,
)
from utils.runtime_settings import RuntimeSettings


def _settings(provider: str) -> RuntimeSettings:
    return RuntimeSettings.from_sources(environ={}, user_config={"LLM": provider})


@pytest.mark.parametrize(
    ("strategy", "template", "driver_id", "required"),
    [
        ("legacy", "general", "standard", ("structuredOutput",)),
        ("adaptive", "adaptive", "smart", ("structuredOutput", "thinking")),
        ("template_v2", "adaptive", "template-v2", ("structuredOutput",)),
        ("authored", "adaptive", "authored", ("structuredOutput", "thinking")),
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
        template_v2_id="template" if strategy == "template_v2" else None,
        template_v2_revision=1 if strategy == "template_v2" else None,
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
            "strategy": "legacy",
            "template": "general",
            "profile_driver_id": "authored",
        }
    )

    identity = server_generation_profile_identity(request, None)

    assert isinstance(identity, Mapping)
    assert resolve_presentation_profile(identity).driver_id == "standard"


@pytest.mark.parametrize("driver_id", ["smart", "template-v2", "authored"])
def test_generation_driver_mismatch_fails_before_invocation(driver_id: str) -> None:
    identity_by_driver = {
        "smart": {
            "generation_mode": "smart",
            "mode": "adaptive",
            "version": "v1-standard",
            "theme": {"mode": "adaptive"},
        },
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


def test_standard_capability_mismatch_fails_before_invocation() -> None:
    invoked = False
    with pytest.raises(ProviderResolutionError) as error:
        with generation_provider_admission(
            {
                "generation_mode": "standard",
                "mode": None,
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
            "mode": None,
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
            "mode": None,
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
                "mode": None,
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
