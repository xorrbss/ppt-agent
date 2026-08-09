from __future__ import annotations

import os
from collections.abc import Callable

from constants.settings_registry import get_settings_by_key
from utils.runtime_settings import RuntimeSettings, resolve_runtime_settings


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_setting_env(
    key: str,
    settings: RuntimeSettings | None = None,
) -> str | None:
    """Resolve a registry setting from an immutable snapshot.

    Non-registry operational settings remain process-start environment values.
    Provider, credential, and image settings always resolve from the supplied or
    active request/task snapshot.
    """
    entry = get_settings_by_key().get(key)
    if entry is not None and "fastapi.runtime" in entry["targets"]:
        return resolve_runtime_settings(settings).get_env(key)
    return os.getenv(key)


def _make_getter(key: str) -> Callable[[RuntimeSettings | None], str | None]:
    def getter(settings: RuntimeSettings | None = None) -> str | None:
        return get_setting_env(key, settings)

    getter.__name__ = f"get_{key.lower()}_env"
    return getter


_GETTER_KEYS = {
    "get_can_change_keys_env": "CAN_CHANGE_KEYS",
    "get_database_url_env": "DATABASE_URL",
    "get_app_data_directory_env": "APP_DATA_DIRECTORY",
    "get_temp_directory_env": "TEMP_DIRECTORY",
    "get_user_config_path_env": "USER_CONFIG_PATH",
    "get_disable_auth_env": "DISABLE_AUTH",
    "get_llm_provider_env": "LLM",
    "get_anthropic_api_key_env": "ANTHROPIC_API_KEY",
    "get_anthropic_model_env": "ANTHROPIC_MODEL",
    "get_ollama_url_env": "OLLAMA_URL",
    "get_custom_llm_url_env": "CUSTOM_LLM_URL",
    "get_openai_api_key_env": "OPENAI_API_KEY",
    "get_openai_model_env": "OPENAI_MODEL",
    "get_google_api_key_env": "GOOGLE_API_KEY",
    "get_google_model_env": "GOOGLE_MODEL",
    "get_vertex_api_key_env": "VERTEX_API_KEY",
    "get_vertex_model_env": "VERTEX_MODEL",
    "get_vertex_project_env": "VERTEX_PROJECT",
    "get_vertex_location_env": "VERTEX_LOCATION",
    "get_vertex_base_url_env": "VERTEX_BASE_URL",
    "get_azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
    "get_azure_openai_model_env": "AZURE_OPENAI_MODEL",
    "get_azure_openai_endpoint_env": "AZURE_OPENAI_ENDPOINT",
    "get_azure_openai_base_url_env": "AZURE_OPENAI_BASE_URL",
    "get_azure_openai_api_version_env": "AZURE_OPENAI_API_VERSION",
    "get_azure_openai_deployment_env": "AZURE_OPENAI_DEPLOYMENT",
    "get_bedrock_region_env": "BEDROCK_REGION",
    "get_bedrock_api_key_env": "BEDROCK_API_KEY",
    "get_bedrock_aws_access_key_id_env": "BEDROCK_AWS_ACCESS_KEY_ID",
    "get_bedrock_aws_secret_access_key_env": "BEDROCK_AWS_SECRET_ACCESS_KEY",
    "get_bedrock_aws_session_token_env": "BEDROCK_AWS_SESSION_TOKEN",
    "get_bedrock_profile_name_env": "BEDROCK_PROFILE_NAME",
    "get_bedrock_model_env": "BEDROCK_MODEL",
    "get_openrouter_api_key_env": "OPENROUTER_API_KEY",
    "get_openrouter_model_env": "OPENROUTER_MODEL",
    "get_openrouter_base_url_env": "OPENROUTER_BASE_URL",
    "get_fireworks_api_key_env": "FIREWORKS_API_KEY",
    "get_fireworks_model_env": "FIREWORKS_MODEL",
    "get_fireworks_base_url_env": "FIREWORKS_BASE_URL",
    "get_together_api_key_env": "TOGETHER_API_KEY",
    "get_together_model_env": "TOGETHER_MODEL",
    "get_together_base_url_env": "TOGETHER_BASE_URL",
    "get_cerebras_api_key_env": "CEREBRAS_API_KEY",
    "get_cerebras_model_env": "CEREBRAS_MODEL",
    "get_cerebras_base_url_env": "CEREBRAS_BASE_URL",
    "get_litellm_base_url_env": "LITELLM_BASE_URL",
    "get_litellm_api_key_env": "LITELLM_API_KEY",
    "get_litellm_model_env": "LITELLM_MODEL",
    "get_lmstudio_base_url_env": "LMSTUDIO_BASE_URL",
    "get_lmstudio_api_key_env": "LMSTUDIO_API_KEY",
    "get_lmstudio_model_env": "LMSTUDIO_MODEL",
    "get_custom_llm_api_key_env": "CUSTOM_LLM_API_KEY",
    "get_ollama_model_env": "OLLAMA_MODEL",
    "get_custom_model_env": "CUSTOM_MODEL",
    "get_pexels_api_key_env": "PEXELS_API_KEY",
    "get_disable_image_generation_env": "DISABLE_IMAGE_GENERATION",
    "get_enable_mock_endpoints_env": "ENABLE_MOCK_ENDPOINTS",
    "get_authored_resume_max_attempts_env": "AUTHORED_RESUME_MAX_ATTEMPTS",
    "get_image_provider_env": "IMAGE_PROVIDER",
    "get_pixabay_api_key_env": "PIXABAY_API_KEY",
    "get_disable_thinking_env": "DISABLE_THINKING",
    "get_web_grounding_env": "WEB_GROUNDING",
    "get_comfyui_url_env": "COMFYUI_URL",
    "get_comfyui_workflow_env": "COMFYUI_WORKFLOW",
    "get_dall_e_3_quality_env": "DALL_E_3_QUALITY",
    "get_gpt_image_1_5_quality_env": "GPT_IMAGE_1_5_QUALITY",
    "get_gpt_image_2_quality_env": "GPT_IMAGE_2_QUALITY",
    "get_codex_access_token_env": "CODEX_ACCESS_TOKEN",
    "get_codex_refresh_token_env": "CODEX_REFRESH_TOKEN",
    "get_codex_token_expires_env": "CODEX_TOKEN_EXPIRES",
    "get_codex_account_id_env": "CODEX_ACCOUNT_ID",
    "get_codex_username_env": "CODEX_USERNAME",
    "get_codex_email_env": "CODEX_EMAIL",
    "get_codex_is_pro_env": "CODEX_IS_PRO",
    "get_codex_model_env": "CODEX_MODEL",
    "get_codex_reasoning_effort_env": "CODEX_REASONING_EFFORT",
    "get_migrate_database_on_startup_env": "MIGRATE_DATABASE_ON_STARTUP",
    "get_sentry_dsn_env": "SENTRY_DSN",
    "get_sentry_traces_sample_rate_env": "SENTRY_TRACES_SAMPLE_RATE",
    "get_sentry_send_default_pii_env": "SENTRY_SEND_DEFAULT_PII",
    "get_open_webui_image_url_env": "OPEN_WEBUI_IMAGE_URL",
    "get_open_webui_image_api_key_env": "OPEN_WEBUI_IMAGE_API_KEY",
    "get_openai_compat_image_base_url_env": "OPENAI_COMPAT_IMAGE_BASE_URL",
    "get_openai_compat_image_api_key_env": "OPENAI_COMPAT_IMAGE_API_KEY",
    "get_openai_compat_image_model_env": "OPENAI_COMPAT_IMAGE_MODEL",
}

globals().update({name: _make_getter(key) for name, key in _GETTER_KEYS.items()})


def get_fastapi_public_base_url(
    settings: RuntimeSettings | None = None,
) -> str | None:
    del settings
    value = (os.getenv("NEXT_PUBLIC_FAST_API") or "").strip().rstrip("/")
    return value or None


def get_next_internal_base_url(settings: RuntimeSettings | None = None) -> str:
    del settings
    value = (os.getenv("NEXT_INTERNAL_URL") or "").strip().rstrip("/")
    return value or "http://localhost"


def is_disable_auth_enabled(settings: RuntimeSettings | None = None) -> bool:
    return _is_truthy(get_setting_env("DISABLE_AUTH", settings))


def is_parallel_image_generation_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    return _is_truthy(get_setting_env("ENABLE_PARALLEL_IMAGE_GENERATION", settings))
