from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import HTTPException
from google import genai
from openai import OpenAI

from constants.llm import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_AZURE_MODEL,
    DEFAULT_BEDROCK_MODEL,
    DEFAULT_CEREBRAS_MODEL,
    DEFAULT_CODEX_MODEL,
    DEFAULT_FIREWORKS_MODEL,
    DEFAULT_LITELLM_MODEL,
    DEFAULT_GOOGLE_MODEL,
    DEFAULT_LMSTUDIO_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_TOGETHER_MODEL,
    DEFAULT_VERTEX_MODEL,
)
from enums.llm_provider import LLMProvider
from utils.get_env import (
    get_azure_openai_deployment_env,
    get_azure_openai_model_env,
    get_anthropic_model_env,
    get_bedrock_model_env,
    get_codex_model_env,
    get_custom_model_env,
    get_fireworks_model_env,
    get_google_api_key_env,
    get_google_model_env,
    get_litellm_model_env,
    get_lmstudio_model_env,
    get_llm_provider_env,
    get_ollama_model_env,
    get_openai_api_key_env,
    get_openai_model_env,
    get_cerebras_model_env,
    get_openrouter_model_env,
    get_together_model_env,
    get_vertex_model_env,
)
from constants.provider_registry import ProviderResolutionError, resolve_provider
from services.presentation_profiles import (
    PersistedIdentity,
    PresentationDriverId,
    PresentationProfile,
    resolve_presentation_driver,
)
from utils.runtime_settings import RuntimeSettings, resolve_runtime_settings


@dataclass(frozen=True, slots=True)
class GenerationProviderAdmission:
    """Server-derived provider powers for one generation execution."""

    profile: PresentationProfile
    presentation_driver: PresentationDriverId
    required_capabilities: tuple[str, ...]
    provider_id: str | None


_GENERATION_PROVIDER_ADMISSION: ContextVar[GenerationProviderAdmission | None] = (
    ContextVar("generation_provider_admission", default=None)
)


def _resolve_configured_llm_provider(
    settings: RuntimeSettings,
    *,
    presentation_driver: str | None = None,
    required_capabilities: Iterable[str] = (),
) -> LLMProvider:
    provider_id = get_llm_provider_env(settings) or ""
    try:
        provider = LLMProvider(provider_id)
    except ValueError as error:
        raise ProviderResolutionError("provider_unknown", provider_id) from error
    resolved = resolve_provider(
        provider.value,
        presentation_driver=presentation_driver,
        required_capabilities=required_capabilities,
    )
    if resolved.kind != "llm":
        raise ProviderResolutionError("provider_kind_mismatch", provider.value)
    return provider


def resolve_generation_provider(
    identity: PersistedIdentity,
    *,
    required_capabilities: Iterable[str],
    settings: RuntimeSettings | None = None,
) -> GenerationProviderAdmission:
    """Admit the configured LLM against a server-derived presentation profile."""

    profile, _capabilities, _driver = resolve_presentation_driver(identity)
    required = tuple(dict.fromkeys(required_capabilities))
    snapshot = resolve_runtime_settings(settings)
    provider = _resolve_configured_llm_provider(
        snapshot,
        presentation_driver=profile.driver_id,
        required_capabilities=required,
    )
    return GenerationProviderAdmission(
        profile=profile,
        presentation_driver=profile.driver_id,
        required_capabilities=required,
        provider_id=provider.value,
    )


@contextmanager
def generation_provider_policy(
    identity: PersistedIdentity,
    *,
    required_capabilities: Iterable[str],
) -> Iterator[GenerationProviderAdmission]:
    """Install policy that resolves the provider at the client-call boundary."""

    profile, _capabilities, _driver = resolve_presentation_driver(identity)
    policy = GenerationProviderAdmission(
        profile=profile,
        presentation_driver=profile.driver_id,
        required_capabilities=tuple(dict.fromkeys(required_capabilities)),
        provider_id=None,
    )
    token = _GENERATION_PROVIDER_ADMISSION.set(policy)
    try:
        yield policy
    finally:
        _GENERATION_PROVIDER_ADMISSION.reset(token)


@contextmanager
def generation_provider_admission(
    identity: PersistedIdentity,
    *,
    required_capabilities: Iterable[str],
    settings: RuntimeSettings | None = None,
) -> Iterator[GenerationProviderAdmission]:
    """Pin fail-closed provider policy to one async generation task context."""

    admission = resolve_generation_provider(
        identity,
        required_capabilities=required_capabilities,
        settings=settings,
    )
    token = _GENERATION_PROVIDER_ADMISSION.set(admission)
    try:
        yield admission
    finally:
        _GENERATION_PROVIDER_ADMISSION.reset(token)


def get_llm_provider(settings: RuntimeSettings | None = None):
    snapshot = resolve_runtime_settings(settings)
    try:
        admission = _GENERATION_PROVIDER_ADMISSION.get()
        return _resolve_configured_llm_provider(
            snapshot,
            presentation_driver=(
                admission.presentation_driver if admission is not None else None
            ),
            required_capabilities=(
                admission.required_capabilities if admission is not None else ()
            ),
        )
    except ProviderResolutionError as error:
        raise HTTPException(status_code=422, detail=error.code) from error


def is_openai_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.OPENAI


def is_google_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.GOOGLE


def is_anthropic_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.ANTHROPIC


def is_vertex_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.VERTEX


def is_azure_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.AZURE


def is_bedrock_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.BEDROCK


def is_ollama_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.OLLAMA


def is_custom_llm_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.CUSTOM


def is_cerebras_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.CEREBRAS


def is_openrouter_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.OPENROUTER


def is_fireworks_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.FIREWORKS


def is_together_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.TOGETHER


def is_codex_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.CODEX


def is_litellm_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.LITELLM


def is_lmstudio_selected(settings: RuntimeSettings | None = None):
    return get_llm_provider(settings) == LLMProvider.LMSTUDIO


def get_model(settings: RuntimeSettings | None = None):
    snapshot = resolve_runtime_settings(settings)
    selected_llm = get_llm_provider(snapshot)
    if selected_llm == LLMProvider.OPENAI:
        return get_openai_model_env(snapshot) or DEFAULT_OPENAI_MODEL
    elif selected_llm == LLMProvider.GOOGLE:
        return get_google_model_env(snapshot) or DEFAULT_GOOGLE_MODEL
    elif selected_llm == LLMProvider.VERTEX:
        return get_vertex_model_env(snapshot) or DEFAULT_VERTEX_MODEL
    elif selected_llm == LLMProvider.AZURE:
        return (
            get_azure_openai_model_env(snapshot)
            or get_azure_openai_deployment_env(snapshot)
            or DEFAULT_AZURE_MODEL
        )
    elif selected_llm == LLMProvider.BEDROCK:
        return get_bedrock_model_env(snapshot) or DEFAULT_BEDROCK_MODEL
    elif selected_llm == LLMProvider.OPENROUTER:
        return get_openrouter_model_env(snapshot) or DEFAULT_OPENROUTER_MODEL
    elif selected_llm == LLMProvider.FIREWORKS:
        return get_fireworks_model_env(snapshot) or DEFAULT_FIREWORKS_MODEL
    elif selected_llm == LLMProvider.TOGETHER:
        return get_together_model_env(snapshot) or DEFAULT_TOGETHER_MODEL
    elif selected_llm == LLMProvider.CEREBRAS:
        return get_cerebras_model_env(snapshot) or DEFAULT_CEREBRAS_MODEL
    elif selected_llm == LLMProvider.ANTHROPIC:
        return get_anthropic_model_env(snapshot) or DEFAULT_ANTHROPIC_MODEL
    elif selected_llm == LLMProvider.OLLAMA:
        return get_ollama_model_env(snapshot)
    elif selected_llm == LLMProvider.CUSTOM:
        return get_custom_model_env(snapshot)
    elif selected_llm == LLMProvider.LITELLM:
        return get_litellm_model_env(snapshot) or DEFAULT_LITELLM_MODEL
    elif selected_llm == LLMProvider.LMSTUDIO:
        return get_lmstudio_model_env(snapshot) or DEFAULT_LMSTUDIO_MODEL
    elif selected_llm == LLMProvider.CODEX:
        return get_codex_model_env(snapshot) or DEFAULT_CODEX_MODEL
    else:
        raise HTTPException(
            status_code=500,
            detail=(
                "Invalid LLM provider. Please select one of: "
                "openai, google, vertex, azure, bedrock, openrouter, "
                "fireworks, together, cerebras, anthropic, litellm, "
                "lmstudio, ollama, custom, codex"
            ),
        )


def get_google_llm_client(settings: RuntimeSettings | None = None) -> genai.Client:
    snapshot = resolve_runtime_settings(settings)
    get_llm_provider(snapshot)
    api_key = get_google_api_key_env(snapshot)
    if not api_key:
        raise HTTPException(status_code=400, detail="Google API Key is not set")
    return genai.Client(api_key=api_key)


def get_llm_client(settings: RuntimeSettings | None = None) -> OpenAI:
    snapshot = resolve_runtime_settings(settings)
    get_llm_provider(snapshot)
    api_key = get_openai_api_key_env(snapshot)
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API Key is not set")
    return OpenAI(api_key=api_key)


def get_large_model(settings: RuntimeSettings | None = None) -> str:
    """Resolved model name for the configured LLM provider (same as runtime `get_model`)."""
    return get_model(settings)
