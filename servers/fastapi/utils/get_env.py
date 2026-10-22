from __future__ import annotations

import os
from collections.abc import Callable
from typing import overload

from constants.settings_registry import get_settings_by_key
from services.provider_settings_values import SECRET_SETTING_KEYS
from utils.env_parse import (
    InvalidPolicy,
    is_truthy,
    parse_bool,
    parse_float,
    parse_int,
)
from utils.parsers import parse_bool_or_none
from utils.runtime_settings import RuntimeSettings, resolve_runtime_settings
from utils.get_env_canvas import get_canvas_stages_path_env  # noqa: F401
from utils.get_env_authored_publish import (
    is_authored_publish_flagged_slides_enabled,
    is_authored_publish_flagged_slides_source_reflow_enabled,
)
from utils.get_env_concurrency import (
    get_authored_cluster_job_concurrency_env,
    get_authored_job_concurrency_env,
    get_public_export_scheduler_global_cap_env,
    get_svg_author_concurrency_env,
    get_svg_author_concurrency_source_reflow_env,
    get_svg_illustration_budget_env,
    get_svg_illustration_timeout_seconds_env,
    get_svg_render_concurrency_env,
    get_svg_render_concurrency_source_reflow_env,
    get_svg_source_output_view_env,
    get_workspace_operation_max_concurrency_env,
)


_is_truthy = is_truthy


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
        value = resolve_runtime_settings(settings).get_env(key)
    else:
        value = os.getenv(key)
    if key in SECRET_SETTING_KEYS:
        # Lazy import avoids the database URL helper's dependency on this module.
        from services.provider_secure_runtime import (
            resolve_provider_secret,
            setting_credential_id,
        )

        return resolve_provider_secret(setting_credential_id(key), value)
    return value


@overload
def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = ...,
    maximum: int | None = ...,
    on_invalid: InvalidPolicy = ...,
    settings: RuntimeSettings | None = ...,
) -> int: ...


@overload
def env_int(
    name: str,
    default: None,
    *,
    minimum: int | None = ...,
    maximum: int | None = ...,
    on_invalid: InvalidPolicy = ...,
    settings: RuntimeSettings | None = ...,
) -> int | None: ...


def env_int(
    name: str,
    default: int | None,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    on_invalid: InvalidPolicy = "default",
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Integer knob read through ``get_setting_env`` (admin values honoured).

    The malformed/out-of-range contract is chosen per call site by
    ``on_invalid``; see ``utils/env_parse.py`` for the four policies.
    ``default=None`` expresses an optional override ("explicit value or
    nothing").
    """
    return parse_int(
        get_setting_env(name, settings),
        name=name,
        default=default,
        minimum=minimum,
        maximum=maximum,
        on_invalid=on_invalid,
    )


def env_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    on_invalid: InvalidPolicy = "default",
    settings: RuntimeSettings | None = None,
) -> float:
    """Finite float knob read through ``get_setting_env``; see ``env_int``."""
    return parse_float(
        get_setting_env(name, settings),
        name=name,
        default=default,
        minimum=minimum,
        maximum=maximum,
        on_invalid=on_invalid,
    )


def env_bool(
    name: str,
    default: bool,
    *,
    settings: RuntimeSettings | None = None,
) -> bool:
    """Boolean knob: ``1/true/yes/on`` or ``0/false/no/off``, else ``default``."""
    return parse_bool(get_setting_env(name, settings), default=default)


def get_vnext_tenant_id(settings: RuntimeSettings | None = None) -> str:
    """Tenant scope for authored-vNext repositories (``AUTHORED_VNEXT_TENANT_ID``).

    Single-tenant deployments leave it unset and get ``"default"``; an empty
    value is treated the same way rather than producing an empty scope.
    """
    return (get_setting_env("AUTHORED_VNEXT_TENANT_ID", settings) or "").strip() or "default"


def get_environment_name(
    default: str = "",
    *,
    settings: RuntimeSettings | None = None,
) -> str:
    """Normalised deployment tier from ``ENVIRONMENT`` (stripped, lower-cased).

    Call sites disagree on what an unset tier means (``development`` at boot,
    ``production`` for public-service decisions, ``""`` for "not declared"),
    so the caller passes its own ``default`` and only the normalisation is
    shared.
    """
    value = (get_setting_env("ENVIRONMENT", settings) or "").strip().lower()
    return value or default


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
    "get_gpt_image_2_5_quality_env": "GPT_IMAGE_2_5_QUALITY",
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
    "get_provider_durable_runtime_required_env": "PROVIDER_DURABLE_RUNTIME_REQUIRED",
    "get_template_v2_deployment_tier_env": "TEMPLATE_V2_DEPLOYMENT_TIER",
    "get_webhook_private_target_allowlist_env": "WEBHOOK_PRIVATE_TARGET_ALLOWLIST",
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


def get_image_generation_timeout_seconds(
    settings: RuntimeSettings | None = None,
) -> float:
    """Per-attempt deadline for one image provider call.

    The OpenAI SDK's own default is ten minutes, and image calls are
    serialised behind a process-wide lock, so one hung picture can hold a
    whole deck past its generation deadline. A picture is decoration: it
    degrades to the HTML-only figure long before that.
    """
    return float(
        max(5, min(600, _int_setting("IMAGE_GENERATION_TIMEOUT_SECONDS", 180, settings)))
    )


def is_authored_source_images_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Whether ordinary authored generation may place the user's own pictures.

    Off by default. With the flag unset a deck is authored, prompted, and
    illustrated exactly as it was before source-image reuse existed.
    """
    return _is_truthy(get_setting_env("AUTHORED_SOURCE_IMAGES_ENABLED", settings))


def _int_setting(key: str, default: int, settings: RuntimeSettings | None) -> int:
    return env_int(key, default, settings=settings)


def is_authored_html_normalize_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Whether harmless authored-HTML admission findings are normalised first.

    On by default: the whitelist in ``utils/authored_html_normalize.py`` is
    strictly a subset of what admission already treats as harmless, so the
    flag exists to allow disabling the rewrite for isolation, not because the
    default carries a real risk.
    """
    parsed = parse_bool_or_none(get_setting_env("AUTHORED_HTML_NORMALIZE_ENABLED", settings))
    return True if parsed is None else parsed


def is_authored_quality_loop_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Whether a figure slide is rendered as several CSS-only candidates.

    Off by default. With the flag unset every slide is rendered exactly once,
    from the HTML its author wrote, as it was before best-of-N existed.
    """
    return _is_truthy(get_setting_env("AUTHORED_QUALITY_LOOP_ENABLED", settings))


def get_authored_quality_loop_candidates(
    settings: RuntimeSettings | None = None,
) -> int:
    """Candidates rendered per figure slide, baseline included, clamped to 1-4."""
    return max(1, min(4, _int_setting("AUTHORED_QUALITY_LOOP_CANDIDATES", 3, settings)))


def is_authored_design_critique_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Whether the closed-operation design critic runs. Off by default."""
    return _is_truthy(get_setting_env("AUTHORED_DESIGN_CRITIQUE_ENABLED", settings))


def is_authored_retain_checkpoint_html_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Whether published decks keep their pre-repair slide HTML in checkpoints.

    Off by default: publication nulls ``author_html``/``illustrated_html`` to
    save storage. Bench runs turn it on so the offline fit replay
    (``docs/authored-fit-magnitude-measurement-design-20260904.md`` section 6)
    can measure how far slides missed before repair.
    """
    parsed = parse_bool_or_none(
        get_setting_env("AUTHORED_RETAIN_CHECKPOINT_HTML", settings)
    )
    return False if parsed is None else parsed


def is_authored_infographic_plan_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Whether the deck-wide infographic plan and archetype component library run.

    Off by default (2026-09-05): an ordinary deck is authored by free
    composition exactly as before commit 7c95334e1 — no plan call, no figure
    brief, no library CSS. On restores the plan/library path.
    """
    return _is_truthy(get_setting_env("AUTHORED_INFOGRAPHIC_PLAN_ENABLED", settings))


def is_authored_composition_envelope_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Whether the server-frozen composition envelope binds ordinary decks.

    Off by default (2026-09-05): no envelope or text-plan brief in the prompt,
    no qid / visible-text / font-floor capacity violations at the gate, and
    the 12px authored font floor instead of 15px. Source-verbatim and
    source-outline profiles keep their envelope regardless. On restores the
    460-char / 40-qid / 15px envelope.
    """
    return _is_truthy(
        get_setting_env("AUTHORED_COMPOSITION_ENVELOPE_ENABLED", settings)
    )


def is_authored_visual_story_plan_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Whether the deck-wide visual story plan is frozen and briefed to the author.

    The plan (story kind + silhouette per slide, 2026-08-22) is off by default
    (2026-09-05): its silhouettes (stepped path, diagonal balance) and
    metric-dominance rule produced stair-stepped roadmaps, cascading cards and
    pseudo-chart pill bars the user rejected; off, each slide composes as
    before 2026-08-22.
    """
    return _is_truthy(
        get_setting_env("AUTHORED_VISUAL_STORY_PLAN_ENABLED", settings)
    )


def get_authored_export_fallback_limit(
    settings: RuntimeSettings | None = None,
) -> int:
    """Raster fallbacks a hybrid-exported slide may keep before it is flagged."""
    return max(0, _int_setting("AUTHORED_EXPORT_FALLBACK_LIMIT", 2, settings))


def is_codex_token_auto_refresh_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Whether a near-expiry Codex OAuth access token is refreshed automatically.

    On by default. With the flag unset explicitly to false, an expiring token
    is returned as-is, exactly as before automatic refresh existed.
    """
    parsed = parse_bool_or_none(get_setting_env("CODEX_TOKEN_AUTO_REFRESH", settings))
    return True if parsed is None else parsed


def get_codex_token_refresh_leeway_seconds(
    settings: RuntimeSettings | None = None,
) -> int:
    """Seconds of lead time before expiry at which an automatic refresh fires."""
    return max(0, _int_setting("CODEX_TOKEN_REFRESH_LEEWAY_SECONDS", 600, settings))


def _optional_positive_int_setting(
    key: str, settings: RuntimeSettings | None
) -> int | None:
    """An explicit positive integer knob, or ``None`` when unset or invalid."""
    return parse_int(get_setting_env(key, settings), name=key, default=None, minimum=1)


def get_authored_provider_rate_profile_env(
    settings: RuntimeSettings | None = None,
) -> str:
    """Requested authored rate profile: ``auto`` (default), ``token`` or ``api``."""
    value = (get_setting_env("AUTHORED_PROVIDER_RATE_PROFILE", settings) or "").strip()
    return value.lower() or "auto"


def get_authored_api_provider_concurrency_max(
    settings: RuntimeSettings | None = None,
) -> int:
    """Author concurrency ceiling under the api rate profile (default 16)."""
    return _optional_positive_int_setting(
        "AUTHORED_API_PROVIDER_CONCURRENCY_MAX", settings
    ) or 16


def get_authored_author_concurrency_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit author concurrency, or ``None`` to take the profile default."""
    return _optional_positive_int_setting("AUTHORED_AUTHOR_CONCURRENCY", settings)


def get_authored_batch_slides_per_call_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit slides per authoring call, or ``None`` for the profile default."""
    return _optional_positive_int_setting("AUTHORED_BATCH_SLIDES_PER_CALL", settings)


def get_authored_batch_wave_concurrency_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit steady calls per wave, or ``None`` for the profile default."""
    return _optional_positive_int_setting("AUTHORED_BATCH_WAVE_CONCURRENCY", settings)


def get_authored_batch_recovery_successes_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit successes per recovery step, or ``None`` for the profile default."""
    return _optional_positive_int_setting(
        "AUTHORED_BATCH_RECOVERY_SUCCESSES", settings
    )


def get_public_generation_deadline_per_slide_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit per-slide-in-call deadline allowance, or ``None`` for the default."""
    return _optional_positive_int_setting(
        "PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS", settings
    )


def get_public_generation_deadline_call_overhead_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit fixed per-call deadline allowance, or ``None`` for the default."""
    return _optional_positive_int_setting(
        "PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS", settings
    )


def is_mem0_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Whether Mem0 presentation memory is enabled. On by default."""
    parsed = parse_bool_or_none(get_setting_env("MEM0_ENABLED", settings))
    return True if parsed is None else parsed


def get_mem0_timeout_seconds(settings: RuntimeSettings | None = None) -> float:
    """Per-call deadline in seconds for one Mem0 add/search call (default 15).

    A call exceeding this is treated as a failed, non-fatal call by the
    caller, the same as any other Mem0 runtime error.
    """
    return float(max(1, _int_setting("MEM0_TIMEOUT_SECONDS", 15, settings)))


def _optional_nonnegative_float_setting(
    key: str, settings: RuntimeSettings | None
) -> float | None:
    """An explicit non-negative float knob, or ``None`` when unset or invalid."""
    return parse_float(get_setting_env(key, settings), name=key, default=None, minimum=0)


# Content brief stage deadlines (services/content_brief_policy.py).
# Each returns the explicit override or ``None`` for the policy's code default:
# base deadline of one attempt (600), seconds per 1,000 input characters (1),
# cap on one attempt (900), ceiling on both attempts together (1500) and the
# least deadline a retry needs to be attempted at all (0.8 x base).
def get_content_brief_timeout_seconds_env(settings: RuntimeSettings | None = None) -> float | None:
    return _optional_nonnegative_float_setting("CONTENT_BRIEF_TIMEOUT_SECONDS", settings)


def get_content_brief_timeout_per_kchar_seconds_env(settings: RuntimeSettings | None = None) -> float | None:
    return _optional_nonnegative_float_setting("CONTENT_BRIEF_TIMEOUT_PER_KCHAR_SECONDS", settings)


def get_content_brief_timeout_max_seconds_env(settings: RuntimeSettings | None = None) -> float | None:
    return _optional_nonnegative_float_setting("CONTENT_BRIEF_TIMEOUT_MAX_SECONDS", settings)


def get_content_brief_total_budget_seconds_env(settings: RuntimeSettings | None = None) -> float | None:
    return _optional_nonnegative_float_setting("CONTENT_BRIEF_TOTAL_BUDGET_SECONDS", settings)


def get_content_brief_retry_floor_seconds_env(settings: RuntimeSettings | None = None) -> float | None:
    return _optional_nonnegative_float_setting("CONTENT_BRIEF_RETRY_FLOOR_SECONDS", settings)
