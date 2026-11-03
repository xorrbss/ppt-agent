"""Typed accessors for the authored-runtime, SlideSpec and raster env knobs.

These are the unregistered operational knobs the authored generation lane,
the isolated renderer boundary, the SlideSpec rollout policy, the raster
redesign pipeline and a few upload/compatibility surfaces read directly from
the process environment before 2026-09-29 (``docs/refactoring-audit-20260929.md``
section 5, phase 6).  Every read goes through ``utils.get_env`` so a name that
is also a registered runtime setting (``TEMPLATE_V2_DEPLOYMENT_TIER``,
``CODEX_REASONING_EFFORT``, ``RASTER_ANALYSIS_PROCESS_WORKERS``) honours the
admin-managed snapshot.

Nothing here reads the environment at import time.  Call sites that resolve a
module constant at import (``AUTHORED_SLIDE_PIPELINE_CONCURRENCY``) call the
accessor at import; the accessor itself is inert until called.

Bool contracts, stated once: an *opt-in* flag enables only on ``1/true/yes/on``
and is off for anything else (``is_truthy``); a *default-on* flag is disabled
only by ``0/false/no/off`` and keeps its default for unrecognised text
(``env_bool``).  A *set-or-default* flag (renderer / rollout flags that predate
this module) keeps its default when unset and is otherwise opt-in on the text
as set, including blank text, which is off.

This module must not import ``services`` or ``api``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from utils.env_parse import FALSY_VALUES, TRUTHY_VALUES, is_truthy
from utils.get_env import (
    env_bool,
    env_float,
    env_int,
    get_codex_reasoning_effort_env,
    get_setting_env,
    get_template_v2_deployment_tier_env,
)
from utils.runtime_settings import RuntimeSettings

ENV_NAMES: tuple[str, ...] = (
    # Test hooks that run in production code (see get_authored_test_failure_hooks).
    "AUTHORED_TEST_FAILURES_ENABLED",
    "AUTHORED_TEST_FAILURE_SLIDE",
    "AUTHORED_TEST_FAILURE_STAGE",
    # Authored storage and evidence retention.
    "AUTHORED_CHECKPOINT_DIRECTORY",
    "AUTHORED_QUALITY_RECEIPT_DIRECTORY",
    "AUTHORED_EVIDENCE_RETENTION_TTL_SECONDS",
    "AUTHORED_EVIDENCE_RETENTION_BYTE_BUDGET",
    "AUTHORED_DOM_METRICS_CACHE_BYTE_BUDGET",
    # Authored pipeline behaviour.
    "AUTHORED_SLIDE_PIPELINE_CONCURRENCY",
    "AUTHORED_QUALITY_RECEIPT_V3_PROFILE",
    "AUTHORED_POWERPOINT_QA_INTERACTIVE_OBSERVATION",
    "AUTHORED_SEMANTIC_DUAL_OUTPUT_MODE",
    "AUTHORED_REASONING_EFFORT",
    "CODEX_REASONING_EFFORT",
    "AUTHORED_EXACT_RECONSTRUCTION_ENABLED",
    "AUTHORED_EXACT_STYLE_IDS",
    # Isolated renderer boundary.
    "AUTHORED_RENDERER_MODE",
    "AUTHORED_RENDERER_WORKER",
    "AUTHORED_RENDERER_WORKER_COMMAND_JSON",
    "AUTHORED_RENDERER_OS_ISOLATION",
    "AUTHORED_RENDERER_LEGACY_ENABLED",
    "AUTHORED_RENDERER_KILL_SWITCH",
    "AUTHORED_RENDERER_PRODUCTION",
    "AUTHORED_RENDERER_PRODUCTION_STRICT",
    "AUTHORED_RENDERER_REQUIRE_EXTERNAL",
    "TEMPLATE_V2_DEPLOYMENT_TIER",
    # SlideSpec executor and rollout policy.
    "SLIDESPEC_REPAIR_PROPOSAL_LLM_ENABLED",
    "SLIDESPEC_SLIDE_CONCURRENCY",
    "SLIDESPEC_REPAIR_SHADOW",
    "SLIDESPEC_REPAIR_CANARY",
    "SLIDESPEC_REPAIR_APPLY",
    "SLIDESPEC_REPAIR_ALL_SLIDES",
    "SLIDESPEC_KILL_SWITCH",
    "SLIDESPEC_REPAIR_ROLLOUT_SALT",
    # Raster redesign pipeline.
    "RASTER_STRICT_PAGE_ERRORS",
    "RASTER_ANALYSIS_PROCESS_WORKERS",
    # Icon search and compatibility retirement gates.
    "PRESENTON_FASTEMBED_ICON_CACHE_DIR",
    "LEGACY_IMAGE_PATH_CALLERS_DISABLED",
    "LEGACY_GENERATION_MODE_READERS_DISABLED",
    "LEGACY_RAW_PATH_CALLERS_DISABLED",
    "COMPATIBILITY_ARCHIVE_SIGNING_KEY",
    "COMPATIBILITY_ARCHIVE_SIGNING_KEY_ID",
)

RENDERER_FLAG_NAMES: frozenset[str] = frozenset(
    {
        "AUTHORED_RENDERER_LEGACY_ENABLED",
        "AUTHORED_RENDERER_KILL_SWITCH",
        "AUTHORED_RENDERER_PRODUCTION",
        "AUTHORED_RENDERER_PRODUCTION_STRICT",
        "AUTHORED_RENDERER_REQUIRE_EXTERNAL",
    }
)

DEFAULT_EVIDENCE_RETENTION_TTL_SECONDS = 86400.0
DEFAULT_EVIDENCE_RETENTION_BYTE_BUDGET = 512 * 1024 * 1024
DEFAULT_SLIDE_PIPELINE_CONCURRENCY = 8
DEFAULT_SLIDESPEC_SLIDE_CONCURRENCY = 3
MAX_SLIDESPEC_SLIDE_CONCURRENCY = 8
DEFAULT_SLIDESPEC_ROLLOUT_SALT = "v1"
DEFAULT_ARCHIVE_SIGNING_KEY_ID = "compatibility-v1"

QualityReceiptV3Profile = Literal["public", "local-development", "unit-test"]


def _text(name: str, settings: RuntimeSettings | None) -> str | None:
    """Stripped text of ``name``; ``None`` when unset or blank."""
    value = (get_setting_env(name, settings) or "").strip()
    return value or None


def _set_or_default_flag(
    name: str, default: bool, settings: RuntimeSettings | None
) -> bool:
    """Unset -> ``default``; set -> opt-in on the text (blank counts as off)."""
    raw = get_setting_env(name, settings)
    return default if raw is None else is_truthy(raw)


# --------------------------------------------------------------------------
# Test hooks
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthoredTestFailureHooks:
    """Failure-injection hooks for the authored checkpoint pipeline.

    ``slide`` is the 1-based slide to fail (``0`` when unset, so nothing
    matches) and ``None`` when the configured text is not an integer, which
    the caller treats as "inject nothing".
    """

    enabled: bool
    slide: int | None
    stage: str


def get_authored_test_failure_hooks(
    settings: RuntimeSettings | None = None,
) -> AuthoredTestFailureHooks:
    """Read the authored failure-injection test hooks.

    These are TEST HOOKS THAT RUN IN PRODUCTION CODE.  ``should_inject_failure``
    in ``services/authored_checkpoint_state.py`` consults them on every stage
    of every slide, and only the caller's tier gate (``ENVIRONMENT`` in
    ``development``/``test``, or an active ``PYTEST_CURRENT_TEST``) stops them
    from firing on a production deployment.  They exist so the resumable
    checkpoint machinery can be exercised end-to-end; never set them outside a
    non-production tier.  Enablement is strict opt-in (``1/true/yes/on``).
    """
    raw_slide = get_setting_env("AUTHORED_TEST_FAILURE_SLIDE", settings)
    try:
        slide: int | None = int((raw_slide or "0").strip() or "0")
    except ValueError:
        slide = None
    return AuthoredTestFailureHooks(
        enabled=is_truthy(get_setting_env("AUTHORED_TEST_FAILURES_ENABLED", settings)),
        slide=slide,
        stage=(get_setting_env("AUTHORED_TEST_FAILURE_STAGE", settings) or "")
        .strip()
        .lower(),
    )


# --------------------------------------------------------------------------
# Authored storage, retention and pipeline knobs
# --------------------------------------------------------------------------


def get_authored_checkpoint_directory(
    settings: RuntimeSettings | None = None,
) -> str | None:
    """Explicit root for resumable authored checkpoints; ``None`` -> app data."""
    return _text("AUTHORED_CHECKPOINT_DIRECTORY", settings)


def get_authored_quality_receipt_directory(
    settings: RuntimeSettings | None = None,
) -> str | None:
    """Explicit root for QualityReceipt v2 idempotency records; ``None`` -> app data."""
    return _text("AUTHORED_QUALITY_RECEIPT_DIRECTORY", settings)


@dataclass(frozen=True, slots=True)
class AuthoredEvidenceRetentionSettings:
    ttl_seconds: float
    byte_budget: int


def get_authored_evidence_retention_settings(
    settings: RuntimeSettings | None = None,
) -> AuthoredEvidenceRetentionSettings:
    """Evidence-ledger retention: negatives clamp to zero, malformed text defaults."""
    return AuthoredEvidenceRetentionSettings(
        ttl_seconds=env_float(
            "AUTHORED_EVIDENCE_RETENTION_TTL_SECONDS",
            DEFAULT_EVIDENCE_RETENTION_TTL_SECONDS,
            minimum=0.0,
            on_invalid="clamp",
            settings=settings,
        ),
        byte_budget=env_int(
            "AUTHORED_EVIDENCE_RETENTION_BYTE_BUDGET",
            DEFAULT_EVIDENCE_RETENTION_BYTE_BUDGET,
            minimum=0,
            on_invalid="clamp",
            settings=settings,
        ),
    )


def get_authored_dom_metrics_cache_byte_budget(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Legacy explicit byte budget for the browser-metrics LRU.

    ``None`` means "not configured, use the registered MB knob".  Malformed or
    negative text raises ``EnvValueError`` so the caller can keep its own
    import-time default, exactly as the private parser did.
    """
    return env_int(
        "AUTHORED_DOM_METRICS_CACHE_BYTE_BUDGET",
        None,
        minimum=0,
        on_invalid="raise",
        settings=settings,
    )


def get_authored_slide_pipeline_concurrency(
    settings: RuntimeSettings | None = None,
) -> int:
    """Per-deck slide worker count; ``max(1, n)`` semantics, malformed -> 8."""
    return env_int(
        "AUTHORED_SLIDE_PIPELINE_CONCURRENCY",
        DEFAULT_SLIDE_PIPELINE_CONCURRENCY,
        minimum=1,
        on_invalid="clamp",
        settings=settings,
    )


def get_authored_quality_receipt_v3_profile(
    settings: RuntimeSettings | None = None,
) -> QualityReceiptV3Profile:
    """Durability profile of the V3 receipt chain; anything unknown is ``public``."""
    value = (
        get_setting_env("AUTHORED_QUALITY_RECEIPT_V3_PROFILE", settings) or ""
    ).strip().lower()
    if value == "local-development":
        return "local-development"
    if value == "unit-test":
        return "unit-test"
    return "public"


def is_authored_powerpoint_qa_interactive_observation_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Opt-in: let PowerPoint COM QA observe interactive dialogs."""
    return is_truthy(
        get_setting_env("AUTHORED_POWERPOINT_QA_INTERACTIVE_OBSERVATION", settings)
    )


def get_authored_semantic_dual_output_mode(
    settings: RuntimeSettings | None = None,
) -> str:
    """Raw (stripped, lower-cased) rollout mode; the caller validates the set."""
    value = get_setting_env("AUTHORED_SEMANTIC_DUAL_OUTPUT_MODE", settings)
    return (value if value is not None else "shadow").strip().lower()


def get_authored_reasoning_effort(
    settings: RuntimeSettings | None = None,
) -> str:
    """Raw authored reasoning-effort text (``""`` when unset); caller normalises."""
    return get_setting_env("AUTHORED_REASONING_EFFORT", settings) or ""


def get_codex_reasoning_effort(
    settings: RuntimeSettings | None = None,
) -> str | None:
    """Registered ``CODEX_REASONING_EFFORT``; ``None`` when unset or blank."""
    return get_codex_reasoning_effort_env(settings) or None


@dataclass(frozen=True, slots=True)
class AuthoredExactReconstructionSettings:
    enabled: bool
    style_ids: tuple[str, ...]


def get_authored_exact_reconstruction_settings(
    settings: RuntimeSettings | None = None,
) -> AuthoredExactReconstructionSettings:
    """Private exact-preset rollout: opt-in flag plus a comma-separated id list.

    ``style_ids`` are stripped, non-empty items in configured order; the
    caller applies its identifier pattern.
    """
    raw_ids = get_setting_env("AUTHORED_EXACT_STYLE_IDS", settings) or ""
    return AuthoredExactReconstructionSettings(
        enabled=is_truthy(
            get_setting_env("AUTHORED_EXACT_RECONSTRUCTION_ENABLED", settings)
        ),
        style_ids=tuple(
            item for item in (part.strip() for part in raw_ids.split(",")) if item
        ),
    )


# --------------------------------------------------------------------------
# Isolated renderer boundary
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthoredRendererSettings:
    mode: str
    deployment_tier: str
    worker_command_json: str
    os_isolation: str


def get_authored_renderer_settings(
    settings: RuntimeSettings | None = None,
) -> AuthoredRendererSettings:
    """Renderer process-boundary configuration (raw text; policy lives in caller)."""
    return AuthoredRendererSettings(
        mode=(_text("AUTHORED_RENDERER_MODE", settings) or "isolated").lower(),
        deployment_tier=(get_template_v2_deployment_tier_env(settings) or "")
        .strip()
        .lower(),
        worker_command_json=(
            _text("AUTHORED_RENDERER_WORKER_COMMAND_JSON", settings) or ""
        ),
        os_isolation=_text("AUTHORED_RENDERER_OS_ISOLATION", settings) or "",
    )


def renderer_flag(
    name: str,
    *,
    default: bool = False,
    settings: RuntimeSettings | None = None,
) -> bool:
    """One of ``RENDERER_FLAG_NAMES`` under the set-or-default contract."""
    if name not in RENDERER_FLAG_NAMES:
        raise ValueError(f"{name} is not a renderer flag")
    return _set_or_default_flag(name, default, settings)


def is_authored_renderer_worker_process(
    settings: RuntimeSettings | None = None,
) -> bool:
    """True inside the spawned renderer worker (launcher sets the marker to ``1``)."""
    return is_truthy(get_setting_env("AUTHORED_RENDERER_WORKER", settings))


# --------------------------------------------------------------------------
# SlideSpec executor and rollout policy
# --------------------------------------------------------------------------


def is_slidespec_repair_proposal_llm_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Default-on; only ``0/false/no/off`` stops LLM repair-plan proposals."""
    return env_bool("SLIDESPEC_REPAIR_PROPOSAL_LLM_ENABLED", True, settings=settings)


def get_slidespec_slide_concurrency(
    settings: RuntimeSettings | None = None,
) -> int:
    """Bounded 1..8 slide scheduler width; malformed text -> 3."""
    return env_int(
        "SLIDESPEC_SLIDE_CONCURRENCY",
        DEFAULT_SLIDESPEC_SLIDE_CONCURRENCY,
        minimum=1,
        maximum=MAX_SLIDESPEC_SLIDE_CONCURRENCY,
        on_invalid="clamp",
        settings=settings,
    )


@dataclass(frozen=True, slots=True)
class SlidespecRolloutSettings:
    repair_shadow: bool
    repair_apply: bool
    repair_all_slides: bool
    kill_switch: bool
    canary_basis_points: int
    rollout_salt: str


def _slidespec_canary_basis_points(settings: RuntimeSettings | None) -> int:
    """Explicit percent (0..100, fractional allowed); a truthy word is 100%.

    Malformed text is a closed canary (``0``) rather than a default, exactly
    as the rollout policy has always parsed it.
    """
    raw_value = get_setting_env("SLIDESPEC_REPAIR_CANARY", settings)
    raw = (raw_value if raw_value is not None else "100").strip().lower()
    if raw in TRUTHY_VALUES:
        return 10_000
    try:
        return max(0, min(10_000, round(float(raw) * 100)))
    except (TypeError, ValueError):
        return 0


def get_slidespec_rollout_settings(
    settings: RuntimeSettings | None = None,
) -> SlidespecRolloutSettings:
    """The documented ``SLIDESPEC_*`` review rollout knobs, semantics unchanged."""
    salt = get_setting_env("SLIDESPEC_REPAIR_ROLLOUT_SALT", settings)
    return SlidespecRolloutSettings(
        repair_shadow=_set_or_default_flag("SLIDESPEC_REPAIR_SHADOW", False, settings),
        repair_apply=_set_or_default_flag("SLIDESPEC_REPAIR_APPLY", True, settings),
        repair_all_slides=_set_or_default_flag(
            "SLIDESPEC_REPAIR_ALL_SLIDES", True, settings
        ),
        kill_switch=_set_or_default_flag("SLIDESPEC_KILL_SWITCH", False, settings),
        canary_basis_points=_slidespec_canary_basis_points(settings),
        rollout_salt=DEFAULT_SLIDESPEC_ROLLOUT_SALT if salt is None else salt,
    )


# --------------------------------------------------------------------------
# Raster redesign pipeline
# --------------------------------------------------------------------------


def get_raster_strict_page_errors(
    settings: RuntimeSettings | None = None,
) -> bool | None:
    """Tri-state: ``True`` re-raise, ``False`` force fallback, ``None`` undeclared."""
    text = (get_setting_env("RASTER_STRICT_PAGE_ERRORS", settings) or "").strip().lower()
    if text in TRUTHY_VALUES:
        return True
    if text in FALSY_VALUES:
        return False
    return None


def get_raster_analysis_process_workers(
    *,
    default: int,
    settings: RuntimeSettings | None = None,
) -> int:
    """Configured page-worker count; malformed text raises ``EnvValueError``.

    The registry default is passed in by the caller (this module does not
    import ``constants``).  Sign validation stays with the caller so its
    error code is unchanged.
    """
    return env_int(
        "RASTER_ANALYSIS_PROCESS_WORKERS",
        default,
        on_invalid="raise",
        settings=settings,
    )


# --------------------------------------------------------------------------
# Icon search and compatibility surfaces
# --------------------------------------------------------------------------


def get_fastembed_icon_cache_dir(
    settings: RuntimeSettings | None = None,
) -> str | None:
    """Override directory for the icon-search ONNX weights; ``None`` -> writable path."""
    return _text("PRESENTON_FASTEMBED_ICON_CACHE_DIR", settings)


@dataclass(frozen=True, slots=True)
class CompatibilityRetirementFlags:
    legacy_image_path_callers_disabled: bool
    legacy_generation_mode_readers_disabled: bool
    legacy_raw_path_callers_disabled: bool


def get_compatibility_retirement_flags(
    settings: RuntimeSettings | None = None,
) -> CompatibilityRetirementFlags:
    """Opt-in gates that declare a legacy persistence facade retired."""
    return CompatibilityRetirementFlags(
        legacy_image_path_callers_disabled=is_truthy(
            get_setting_env("LEGACY_IMAGE_PATH_CALLERS_DISABLED", settings)
        ),
        legacy_generation_mode_readers_disabled=is_truthy(
            get_setting_env("LEGACY_GENERATION_MODE_READERS_DISABLED", settings)
        ),
        legacy_raw_path_callers_disabled=is_truthy(
            get_setting_env("LEGACY_RAW_PATH_CALLERS_DISABLED", settings)
        ),
    )


@dataclass(frozen=True, slots=True)
class CompatibilityArchiveSigning:
    key: bytes
    key_id: str


def get_compatibility_archive_signing(
    settings: RuntimeSettings | None = None,
) -> CompatibilityArchiveSigning:
    """HMAC key (UTF-8 bytes, empty when unset) and key id for compatibility archives."""
    return CompatibilityArchiveSigning(
        key=(get_setting_env("COMPATIBILITY_ARCHIVE_SIGNING_KEY", settings) or "").encode(
            "utf-8"
        ),
        key_id=_text("COMPATIBILITY_ARCHIVE_SIGNING_KEY_ID", settings)
        or DEFAULT_ARCHIVE_SIGNING_KEY_ID,
    )
