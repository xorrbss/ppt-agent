from typing import Optional

from fastapi import HTTPException
from llmai import (
    BedrockClientConfig,
    FireworksClientConfig,
    LMStudioClientConfig,
    TogetherAIClientConfig,
)
from llmai.shared import (
    AnthropicClientConfig,
    AzureOpenAIClientConfig,
    CerebrasClientConfig,
    ChatGPTClientConfig,
    ClientConfig,
    GoogleClientConfig,
    LiteLLMClientConfig,
    OpenAIApiType,
    OpenAIClientConfig,
    OpenRouterClientConfig,
    VertexAIClientConfig,
)

from enums.llm_provider import LLMProvider
from utils.get_env import (
    get_azure_openai_api_key_env,
    get_azure_openai_api_version_env,
    get_azure_openai_base_url_env,
    get_azure_openai_deployment_env,
    get_azure_openai_endpoint_env,
    get_anthropic_api_key_env,
    get_bedrock_api_key_env,
    get_bedrock_aws_access_key_id_env,
    get_bedrock_aws_secret_access_key_env,
    get_bedrock_aws_session_token_env,
    get_bedrock_profile_name_env,
    get_bedrock_region_env,
    get_cerebras_api_key_env,
    get_cerebras_base_url_env,
    get_codex_access_token_env,
    get_codex_account_id_env,
    get_codex_reasoning_effort_env,
    get_custom_llm_api_key_env,
    get_custom_llm_url_env,
    get_disable_thinking_env,
    get_fireworks_api_key_env,
    get_fireworks_base_url_env,
    get_google_api_key_env,
    get_litellm_api_key_env,
    get_litellm_base_url_env,
    get_lmstudio_api_key_env,
    get_lmstudio_base_url_env,
    get_ollama_url_env,
    get_openai_api_key_env,
    get_openrouter_api_key_env,
    get_openrouter_base_url_env,
    get_together_api_key_env,
    get_together_base_url_env,
    get_vertex_api_key_env,
    get_vertex_base_url_env,
    get_vertex_location_env,
    get_vertex_project_env,
    get_web_grounding_env,
)
from utils.available_models import normalize_openai_compatible_base_url
from utils.llm_provider import get_llm_provider
from utils.parsers import parse_bool_or_none
from utils.runtime_settings import RuntimeSettings, resolve_runtime_settings
from services.provider_egress_policy import (
    EgressCategory,
    EgressPolicyError,
    validate_configured_provider_endpoint,
)
from services.provider_transport_security import require_pinned_transport


def enable_web_grounding(settings: RuntimeSettings | None = None) -> bool:
    return parse_bool_or_none(get_web_grounding_env(settings)) or False


def disable_thinking(settings: RuntimeSettings | None = None) -> bool:
    return parse_bool_or_none(get_disable_thinking_env(settings)) or False


def _provider_key(provider: LLMProvider, value: str | None) -> str | None:
    del provider
    # Secret getters resolve vault references at the common runtime-settings
    # boundary. Keeping this helper preserves provider config call sites without
    # accepting a second plaintext fallback here.
    return value


def enforce_llm_egress_policy(
    settings: RuntimeSettings | None = None,
) -> None:
    """Fail closed for operator-configured provider URLs immediately before I/O.

    Configuration parsing remains side-effect free. Call sites invoke this guard
    at the SDK boundary, after the client is built but before any request leaves
    the process.
    """

    snapshot = resolve_runtime_settings(settings)
    provider = get_llm_provider(snapshot)
    if provider is not LLMProvider.CUSTOM:
        return
    endpoint = get_custom_llm_url_env(snapshot)
    if not endpoint:
        raise HTTPException(status_code=400, detail="Custom LLM URL is not set")
    try:
        validated = validate_configured_provider_endpoint(
            endpoint,
            category=EgressCategory.PROVIDER,
            service_id="custom-llm",
            allowlist_env="PROVIDER_CUSTOM_EGRESS_ALLOWLIST",
        )
        # llmai constructs the OpenAI SDK internally and offers no hook for a
        # pinned resolver or post-connect peer-IP verification. DNS admission
        # alone is insufficient against rebinding in secure mode.
        require_pinned_transport(
            validated,
            transport_pins_connected_ip=False,
        )
    except EgressPolicyError as error:
        raise HTTPException(status_code=400, detail=error.code) from error


def _get_codex_access_token(settings: RuntimeSettings) -> str:
    access_token = get_codex_access_token_env(settings)
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail=(
                "Codex OAuth access token is not set. Please authenticate via "
                "/api/v1/ppt/codex/auth/initiate"
            ),
        )

    return access_token


def get_llm_config(settings: RuntimeSettings | None = None) -> ClientConfig:
    snapshot = resolve_runtime_settings(settings)
    llm_provider = get_llm_provider(snapshot)

    match llm_provider:
        case LLMProvider.OPENAI:
            api_key = _provider_key(
                LLMProvider.OPENAI, get_openai_api_key_env(snapshot)
            )
            if not api_key:
                raise HTTPException(status_code=400, detail="OpenAI API Key is not set")
            return OpenAIClientConfig(
                api_key=api_key,
                api_type=OpenAIApiType.COMPLETIONS,
            )
        case LLMProvider.GOOGLE:
            api_key = _provider_key(
                LLMProvider.GOOGLE, get_google_api_key_env(snapshot)
            )
            if not api_key:
                raise HTTPException(status_code=400, detail="Google API Key is not set")
            return GoogleClientConfig(api_key=api_key)
        case LLMProvider.VERTEX:
            api_key = _provider_key(
                LLMProvider.VERTEX, get_vertex_api_key_env(snapshot)
            )
            project = get_vertex_project_env(snapshot)
            location = get_vertex_location_env(snapshot)
            base_url = get_vertex_base_url_env(snapshot)

            if api_key and (project or location):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Vertex configuration is ambiguous. Configure either "
                        "VERTEX_API_KEY or VERTEX_PROJECT/VERTEX_LOCATION, not both."
                    ),
                )

            if api_key:
                return VertexAIClientConfig(
                    api_key=api_key,
                    base_url=base_url or None,
                )

            if not project:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Vertex configuration is incomplete. Set VERTEX_API_KEY "
                        "or VERTEX_PROJECT (optionally with VERTEX_LOCATION)."
                    ),
                )

            return VertexAIClientConfig(
                project=project,
                location=location or None,
                base_url=base_url or None,
            )
        case LLMProvider.AZURE:
            api_key = _provider_key(
                LLMProvider.AZURE, get_azure_openai_api_key_env(snapshot)
            )
            api_version = get_azure_openai_api_version_env(snapshot)
            endpoint = get_azure_openai_endpoint_env(snapshot)
            base_url = get_azure_openai_base_url_env(snapshot)
            deployment = get_azure_openai_deployment_env(snapshot)

            if not api_key:
                raise HTTPException(
                    status_code=400,
                    detail="Azure OpenAI API Key is not set",
                )
            if not api_version:
                raise HTTPException(
                    status_code=400,
                    detail="Azure OpenAI API Version is not set",
                )
            if not endpoint and not base_url:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Azure OpenAI endpoint is not set. "
                        "Configure AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_BASE_URL."
                    ),
                )

            return AzureOpenAIClientConfig(
                api_type=OpenAIApiType.RESPONSES,
                api_key=api_key,
                api_version=api_version,
                endpoint=endpoint or None,
                base_url=base_url or None,
                deployment=deployment or None,
            )
        case LLMProvider.BEDROCK:
            region = (get_bedrock_region_env(snapshot) or "us-east-1").strip()
            api_key = (
                _provider_key(LLMProvider.BEDROCK, get_bedrock_api_key_env(snapshot))
                or ""
            ).strip()
            aws_access_key_id = (get_bedrock_aws_access_key_id_env(snapshot) or "").strip()
            aws_secret_access_key = (
                get_bedrock_aws_secret_access_key_env(snapshot) or ""
            ).strip()
            aws_session_token = (
                get_bedrock_aws_session_token_env(snapshot) or ""
            ).strip()
            profile_name = (get_bedrock_profile_name_env(snapshot) or "").strip()

            kwargs = {
                "region": region,
                "api_key": api_key or None,
                "aws_access_key_id": aws_access_key_id or None,
                "aws_secret_access_key": aws_secret_access_key or None,
                "aws_session_token": aws_session_token or None,
                "profile_name": profile_name or None,
            }
            if not kwargs["api_key"] and not (
                kwargs["aws_access_key_id"] and kwargs["aws_secret_access_key"]
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Bedrock auth is incomplete. Set BEDROCK_API_KEY, or "
                        "set BEDROCK_AWS_ACCESS_KEY_ID and "
                        "BEDROCK_AWS_SECRET_ACCESS_KEY."
                    ),
                )
            return BedrockClientConfig(**kwargs)
        case LLMProvider.ANTHROPIC:
            api_key = _provider_key(
                LLMProvider.ANTHROPIC, get_anthropic_api_key_env(snapshot)
            )
            if not api_key:
                raise HTTPException(
                    status_code=400,
                    detail="Anthropic API Key is not set",
                )
            return AnthropicClientConfig(api_key=api_key)
        case LLMProvider.OPENROUTER:
            api_key = _provider_key(
                LLMProvider.OPENROUTER, get_openrouter_api_key_env(snapshot)
            )
            if not api_key:
                raise HTTPException(
                    status_code=400,
                    detail="OpenRouter API Key is not set",
                )
            base_url = get_openrouter_base_url_env(snapshot)
            return OpenRouterClientConfig(
                api_key=api_key,
                base_url=base_url or None,
            )
        case LLMProvider.FIREWORKS:
            api_key = (
                _provider_key(
                    LLMProvider.FIREWORKS, get_fireworks_api_key_env(snapshot)
                )
                or ""
            ).strip()
            if not api_key:
                raise HTTPException(
                    status_code=400,
                    detail="Fireworks API Key is not set",
                )
            base_url = (get_fireworks_base_url_env(snapshot) or "").strip()
            return FireworksClientConfig(
                api_key=api_key,
                base_url=base_url or None,
            )
        case LLMProvider.TOGETHER:
            api_key = (
                _provider_key(
                    LLMProvider.TOGETHER, get_together_api_key_env(snapshot)
                )
                or ""
            ).strip()
            if not api_key:
                raise HTTPException(
                    status_code=400,
                    detail="Together API Key is not set",
                )
            base_url = (get_together_base_url_env(snapshot) or "").strip()
            return TogetherAIClientConfig(
                api_key=api_key,
                base_url=base_url or None,
            )
        case LLMProvider.CEREBRAS:
            api_key = _provider_key(
                LLMProvider.CEREBRAS, get_cerebras_api_key_env(snapshot)
            )
            if not api_key:
                raise HTTPException(
                    status_code=400,
                    detail="Cerebras API Key is not set",
                )
            base_url = get_cerebras_base_url_env(snapshot)
            return CerebrasClientConfig(
                api_key=api_key,
                base_url=base_url or None,
            )
        case LLMProvider.LITELLM:
            base_url = normalize_openai_compatible_base_url(
                get_litellm_base_url_env(snapshot) or ""
            )
            if not base_url:
                raise HTTPException(
                    status_code=400,
                    detail="LiteLLM base URL is not set (LITELLM_BASE_URL).",
                )
            lk = (
                _provider_key(LLMProvider.LITELLM, get_litellm_api_key_env(snapshot))
                or ""
            ).strip()
            return LiteLLMClientConfig(
                base_url=base_url,
                api_key=lk if lk else None,
            )
        case LLMProvider.LMSTUDIO:
            base_url = (get_lmstudio_base_url_env(snapshot) or "").strip()
            lk = (
                _provider_key(
                    LLMProvider.LMSTUDIO, get_lmstudio_api_key_env(snapshot)
                )
                or ""
            ).strip()
            kwargs: dict = {"base_url": base_url or None}
            if lk:
                kwargs["api_key"] = lk
            return LMStudioClientConfig(**kwargs)
        case LLMProvider.OLLAMA:
            return OpenAIClientConfig(
                base_url=(get_ollama_url_env(snapshot) or "http://localhost:11434")
                + "/v1",
                api_key="ollama",
            )
        case LLMProvider.CUSTOM:
            base_url = get_custom_llm_url_env(snapshot)
            if not base_url:
                raise HTTPException(
                    status_code=400,
                    detail="Custom LLM URL is not set",
                )
            return OpenAIClientConfig(
                base_url=base_url,
                api_key=(
                    _provider_key(
                        LLMProvider.CUSTOM, get_custom_llm_api_key_env(snapshot)
                    )
                    or "null"
                ),
            )
        case LLMProvider.CODEX:
            return ChatGPTClientConfig(
                access_token=_get_codex_access_token(snapshot),
                account_id=get_codex_account_id_env(snapshot) or None,
            )
        case _:
            raise HTTPException(
                status_code=400,
                detail=(
                    "LLM Provider must be either openai, google, vertex, azure, "
                    "bedrock, openrouter, fireworks, together, cerebras, "
                    "anthropic, litellm, lmstudio, ollama, custom, or codex"
                ),
            )


def get_extra_body(settings: RuntimeSettings | None = None) -> Optional[dict]:
    snapshot = resolve_runtime_settings(settings)
    provider = get_llm_provider(snapshot)
    if provider == LLMProvider.CUSTOM and disable_thinking(snapshot):
        return {"enable_thinking": False}
    if provider == LLMProvider.CODEX:
        effort = (get_codex_reasoning_effort_env(snapshot) or "").strip().lower()
        # codex (gpt-5.x) reasoning-effort levels; other providers 400 on this param.
        if effort in ("minimal", "none", "low", "medium", "high", "xhigh"):
            return {"reasoning": {"effort": effort}}
    return None
