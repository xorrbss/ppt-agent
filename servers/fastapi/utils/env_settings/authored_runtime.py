"""Typed accessors for the authored-runtime, SlideSpec and raster env knobs.

These are the unregistered operational knobs the authored generation lane,
the isolated renderer boundary, the SlideSpec rollout policy, the raster
redesign pipeline and a few upload/compatibility surfaces read directly from
the process environment before 2026-09-29 (``docs/refactoring-audit-20260929.md``
section 5, phase 6).  Every read goes through ``utils.get_env`` so a name that
is also a registered runtime setting (``TEMPLATE_V2_DEPLOYMENT_TIER``,
``CODEX_REASONING_EFFORT``, ``RASTER_ANALYSIS_PROCESS_WORKERS``) honours the
admin-managed snapshot.

Nothing here reads the environment at import time.  An accessor is inert
until called.

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
    env_int,
    get_codex_reasoning_effort_env,
    get_setting_env,
    get_template_v2_deployment_tier_env,
)
from utils.runtime_settings import RuntimeSettings

ENV_NAMES: tuple[str, ...] = (
    # Test hooks that run in production code (see get_authored_test_failure_hooks).
    # Authored storage and evidence retention.
    "AUTHORED_CHECKPOINT_DIRECTORY",
    "AUTHORED_QUALITY_RECEIPT_DIRECTORY",
    "AUTHORED_DOM_METRICS_CACHE_BYTE_BUDGET",
    # Authored pipeline behaviour.
    "AUTHORED_QUALITY_RECEIPT_V3_PROFILE",
    "AUTHORED_POWERPOINT_QA_INTERACTIVE_OBSERVATION",
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
