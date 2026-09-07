"""Accessors for the knobs of the 2026-09-05 authored quality/speed programme.

Design: ``docs/authored-quality-speed-programme-design-20260905.md`` section 12.
Every key here is registered in ``constants/settings_registry.json`` and read
through the same snapshot layer as ``utils.get_env`` (which is at the file-size
ratchet, hence the sibling module). Nothing here reads disk at request time.
"""

from __future__ import annotations

from utils.get_env import _int_setting, _is_truthy, get_setting_env
from utils.runtime_settings import RuntimeSettings

# Design section 8's rollout step: one fitted slide in twenty pays one extra
# render while the reconstruction claim is watched on real decks.
_VERIFY_SAMPLE_DEFAULT = 0.05


def _bool_setting(key: str, default: bool, settings: RuntimeSettings | None) -> bool:
    raw = (get_setting_env(key, settings) or "").strip()
    if not raw:
        return default
    return _is_truthy(raw)


def _clamped_int(key: str, default: int, low: int, high: int, settings) -> int:
    return max(low, min(high, _int_setting(key, default, settings)))


def get_image_generation_concurrency(settings: RuntimeSettings | None = None) -> int:
    """Concurrent image provider calls per process, clamped to 1..8."""
    return _clamped_int("IMAGE_GENERATION_CONCURRENCY", 3, 1, 8, settings)


def is_export_font_embedding_default_cjk_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Embed fonts by default in hybrid PPTX exports of CJK decks."""
    return _bool_setting("EXPORT_FONT_EMBEDDING_DEFAULT_CJK", True, settings)


def is_authored_style_anchor_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Author one anchor slide first and hand its stylesheet to the rest."""
    return _bool_setting("AUTHORED_STYLE_ANCHOR_ENABLED", True, settings)


def get_authored_illustration_share(settings: RuntimeSettings | None = None) -> float:
    """Share (0..1) of content slides that get one illustration slot; 0 disables."""
    raw = (get_setting_env("AUTHORED_ILLUSTRATION_SHARE", settings) or "").strip()
    if not raw:
        return 0.25
    try:
        value = float(raw)
    except ValueError:
        return 0.25
    return max(0.0, min(1.0, value))


def is_authored_exemplar_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Show the style's checked-in exemplar slide to the author."""
    return _bool_setting("AUTHORED_EXEMPLAR_ENABLED", True, settings)


def is_authored_vision_advisory_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """One advisory vision pass over the deck contact sheet; never a gate."""
    return _bool_setting("AUTHORED_VISION_ADVISORY_ENABLED", False, settings)


def get_outline_density_floor_chars(settings: RuntimeSettings | None = None) -> int:
    """Median usable chars per outline slide below which one expansion runs; 0 off."""
    return _clamped_int("OUTLINE_DENSITY_FLOOR_CHARS", 350, 0, 2000, settings)


def is_outline_density_ceiling_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Cap each outline slide's usable chars at a per-verbosity ceiling, condensing over it."""
    return _bool_setting("OUTLINE_DENSITY_CEILING_ENABLED", False, settings)


def is_authored_header_deterministic_repair_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Repair header geometry/part violations before spending a re-author call."""
    return _bool_setting("AUTHORED_HEADER_DETERMINISTIC_REPAIR", True, settings)


def is_authored_text_scale_rung_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Try the deterministic text-scale rung before the first remake."""
    return _bool_setting("AUTHORED_TEXT_SCALE_RUNG_ENABLED", True, settings)


def is_authored_fragmentation_gate_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Gate text fragmentation (owner count, type floor); repair by merging boxes."""
    return _bool_setting("AUTHORED_FRAGMENTATION_GATE_ENABLED", False, settings)


def get_authored_fragmentation_max_text_owners(
    settings: RuntimeSettings | None = None,
) -> int:
    """Most text-owning elements one slide may render, clamped to 10..200."""
    return _clamped_int("AUTHORED_FRAGMENTATION_MAX_TEXT_OWNERS", 50, 10, 200, settings)


def get_authored_type_floor_px(settings: RuntimeSettings | None = None) -> int:
    """Smallest rendered body type in px the gate accepts, clamped to 12..24."""
    return _clamped_int("AUTHORED_TYPE_FLOOR_PX", 15, 12, 24, settings)


def is_authored_in_render_fit_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Fit card copy inside its card during the first render (grow then shrink)."""
    return _bool_setting("AUTHORED_IN_RENDER_FIT_ENABLED", True, settings)


def get_authored_in_render_fit_verify_sample(
    settings: RuntimeSettings | None = None,
) -> float:
    """Fraction (0..1) of fitted slides re-rendered without the fit to verify.

    The default is the design's first-enablement sample, not zero: the fit is
    on by default now, and the layout-equivalence claim is watched on real
    decks until ``fit_reconstruction_mismatches`` has stayed at zero across
    200+ fitted slides (design section 8).
    """
    raw = (get_setting_env("AUTHORED_IN_RENDER_FIT_VERIFY_SAMPLE", settings) or "").strip()
    if not raw:
        return _VERIFY_SAMPLE_DEFAULT
    try:
        value = float(raw)
    except ValueError:
        return _VERIFY_SAMPLE_DEFAULT
    return max(0.0, min(1.0, value))


def is_authored_deck_instruments_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Record deck-level quality instruments in the receipt."""
    return _bool_setting("AUTHORED_DECK_INSTRUMENTS_ENABLED", True, settings)


def get_authored_metrics_cache_entries(settings: RuntimeSettings | None = None) -> int:
    """Browser-metrics LRU entries per process."""
    return _clamped_int("AUTHORED_METRICS_CACHE_ENTRIES", 64, 8, 1024, settings)


def get_authored_metrics_cache_mb(settings: RuntimeSettings | None = None) -> int:
    """Browser-metrics LRU byte budget in megabytes."""
    return _clamped_int("AUTHORED_METRICS_CACHE_MB", 96, 16, 2048, settings)


def is_authored_wave_barrier_disabled(settings: RuntimeSettings | None = None) -> bool:
    """Admit the next authoring call as soon as one finishes."""
    return _bool_setting("AUTHORED_WAVE_BARRIER_DISABLED", True, settings)


def get_openai_reasoning_effort_env(settings: RuntimeSettings | None = None) -> str | None:
    """Optional OpenAI reasoning effort for reasoning-capable chat-completions
    models (o1/o3/o4/gpt-5/gpt-6 families). Unset means no reasoning
    parameter is sent; other model families reject the parameter outright."""
    raw = (get_setting_env("OPENAI_REASONING_EFFORT", settings) or "").strip().lower()
    return raw or None


def get_llm_light_model_env(settings: RuntimeSettings | None = None) -> str | None:
    """Optional lighter model id for latency-sensitive structured tasks.

    Consumed by ``utils.llm_provider.get_model_for``; unset keeps every task
    on the main configured model.
    """
    raw = (get_setting_env("LLM_LIGHT_MODEL", settings) or "").strip()
    return raw or None


def is_authored_checkpoint_batching_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Share one fenced transaction across a slide stage's checkpoint writes."""
    return _bool_setting("AUTHORED_CHECKPOINT_BATCHING_ENABLED", True, settings)


def is_authored_checkpoint_html_dedupe_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Store NULL for ``illustrated_html`` when it repeats ``author_html``."""
    return _bool_setting("AUTHORED_CHECKPOINT_HTML_DEDUPE_ENABLED", False, settings)


def is_authored_local_stage_async_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Batch local stage-timing receipts through a bounded in-process queue."""
    return _bool_setting("AUTHORED_LOCAL_STAGE_ASYNC_ENABLED", True, settings)


def get_workspace_poll_idle_ceiling_seconds(
    settings: RuntimeSettings | None = None,
) -> int:
    """Ceiling for the worker-lane idle poll backoff, in seconds."""
    return _clamped_int("WORKSPACE_POLL_IDLE_CEILING_SECONDS", 5, 1, 60, settings)


def is_authored_renderer_lease_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Batch same-deck authored renders behind one per-deck renderer worker lease."""
    return _bool_setting("AUTHORED_RENDERER_LEASE_ENABLED", True, settings)


def get_authored_renderer_lease_idle_seconds(
    settings: RuntimeSettings | None = None,
) -> int:
    """Idle seconds before a per-deck renderer worker lease self-closes."""
    return _clamped_int("AUTHORED_RENDERER_LEASE_IDLE_SECONDS", 60, 1, 3600, settings)


def get_authored_renderer_lease_max_renders(
    settings: RuntimeSettings | None = None,
) -> int:
    """Renders a per-deck renderer worker lease serves before it force-closes."""
    return _clamped_int("AUTHORED_RENDERER_LEASE_MAX_RENDERS", 200, 1, 5000, settings)


def is_authored_renderer_warmup_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Render one throwaway slide at boot to pay the renderer's cold start early."""
    return _bool_setting("AUTHORED_RENDERER_WARMUP_ENABLED", True, settings)


def is_authored_renderer_framed_protocol_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Serve many renders per isolated worker process over the framed protocol."""
    return _bool_setting("AUTHORED_RENDERER_FRAMED_PROTOCOL_ENABLED", False, settings)


def get_authored_renderer_lease_pool_size(settings: RuntimeSettings | None = None) -> int:
    """Framed renderer worker processes a single deck lease may keep open."""
    return _clamped_int("AUTHORED_RENDERER_LEASE_POOL_SIZE", 2, 1, 4, settings)


def is_authored_tail_streaming_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Start a slide's illustrate/render/quality tail as its authoring lands."""
    return _bool_setting("AUTHORED_TAIL_STREAMING_ENABLED", True, settings)


def is_authored_author_streaming_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Read the authoring call as a stream and abort a runaway response early."""
    return _bool_setting("AUTHORED_AUTHOR_STREAMING_ENABLED", False, settings)


def is_authored_prompt_system_hoist_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Hoist the deck-invariant prompt block into the call's system message."""
    return _bool_setting("AUTHORED_PROMPT_SYSTEM_HOIST_ENABLED", True, settings)


def is_authored_prompt_cache_key_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Send a per-deck ``prompt_cache_key`` with every authoring call."""
    return _bool_setting("AUTHORED_PROMPT_CACHE_KEY_ENABLED", True, settings)


def is_authored_prompt_bundle_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Ship the 2026-09-05 composition-shaping prompt bundle.

    Off by default. The bundle's mechanical half (the semantic-role vocabulary,
    the G5 contradiction fixes, the compressed SVG contract, the shared
    universals and the deck-token block) ships either way; this knob only
    decides between the bundle's COMPOSITION INTENT block plus type ramp and
    the parent's pre-bundle type-scale wording with no intent block.
    """
    return _bool_setting("AUTHORED_PROMPT_BUNDLE_ENABLED", False, settings)


__all__ = [
    "get_authored_fragmentation_max_text_owners",
    "get_authored_illustration_share",
    "get_authored_in_render_fit_verify_sample",
    "get_authored_metrics_cache_entries",
    "get_authored_metrics_cache_mb",
    "get_authored_renderer_lease_idle_seconds",
    "get_authored_renderer_lease_max_renders",
    "get_authored_renderer_lease_pool_size",
    "get_authored_type_floor_px",
    "get_image_generation_concurrency",
    "get_llm_light_model_env",
    "get_openai_reasoning_effort_env",
    "get_outline_density_floor_chars",
    "get_workspace_poll_idle_ceiling_seconds",
    "is_authored_author_streaming_enabled",
    "is_authored_checkpoint_batching_enabled",
    "is_authored_checkpoint_html_dedupe_enabled",
    "is_authored_deck_instruments_enabled",
    "is_authored_local_stage_async_enabled",
    "is_authored_exemplar_enabled",
    "is_authored_fragmentation_gate_enabled",
    "is_authored_header_deterministic_repair_enabled",
    "is_authored_in_render_fit_enabled",
    "is_authored_renderer_framed_protocol_enabled",
    "is_authored_renderer_lease_enabled",
    "is_authored_renderer_warmup_enabled",
    "is_authored_prompt_bundle_enabled",
    "is_authored_prompt_cache_key_enabled",
    "is_authored_prompt_system_hoist_enabled",
    "is_authored_style_anchor_enabled",
    "is_authored_tail_streaming_enabled",
    "is_authored_text_scale_rung_enabled",
    "is_authored_vision_advisory_enabled",
    "is_authored_wave_barrier_disabled",
    "is_export_font_embedding_default_cjk_enabled",
    "is_outline_density_ceiling_enabled",
]
