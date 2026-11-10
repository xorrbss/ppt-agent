"""Typed accessors for the workspace maintenance / metrics / retention knobs.

Replaces the private ``os.getenv`` parsers that lived in
``workspace_asset_service``, ``workspace_cleanup_journal_service``,
``workspace_asset_maintenance``, ``workspace_artifact_runtime_config``,
``workspace_service``, ``workspace_metrics``, ``workspace_metrics_exporter``,
``account_deletion_controller`` and ``export_retention_service``.

Malformed-value policy per knob (chosen from what the old copy did):

* ``WORKSPACE_ASSET_ORPHAN_TTL_SECONDS``: ``clamp`` to 300..30 days; malformed
  text yields 86400. (The old ``int(...)`` crashed on garbage; no test pinned
  that, so the safe default now applies.)
* ``WORKSPACE_DESTRUCTIVE_APPROVAL_TTL_SECONDS``: ``clamp`` to 60..7 days.
* ``positive_int_knob`` (interval / timeout / TTL names read by wrappers):
  malformed or non-positive yields the default silently; a positive value below
  ``minimum`` is raised to ``minimum``.
* ``ACCOUNT_DELETION_CONTROLLER_INTERVAL_SECONDS``: ``warn`` on malformed, then
  floored at 1.
* ``CHAT_FIRST_WORKSPACE_SLO_*``: ``default`` (silent) on malformed/non-finite.
* ``WORKSPACE_OPERATION_METRICS_PORT``: ``raise`` (``EnvValueError``) so a bad
  scrape endpoint fails startup; unset/blank disables the listener.
* Malware scan mode/scanner and metrics host are strings; an explicitly set
  empty value is preserved (an empty scan mode is still "invalid", as before).

No environment is read at import time, and this module never imports
``services`` or ``api``.
"""

from __future__ import annotations

from dataclasses import dataclass

from utils.get_env import env_bool, env_float, env_int, get_setting_env
from utils.runtime_settings import RuntimeSettings

ENV_NAMES: tuple[str, ...] = (
    # workspace_asset_service
    "WORKSPACE_ASSET_ORPHAN_TTL_SECONDS",
    "WORKSPACE_ASSET_MALWARE_SCAN_MODE",
    "WORKSPACE_ASSET_MALWARE_SCANNER",
    "TEMPLATE_V2_DEPLOYMENT_TIER",
    # workspace_cleanup_journal_service
    "WORKSPACE_CLEANUP_MAINTENANCE_INTERVAL_SECONDS",
    # workspace_asset_maintenance
    "WORKSPACE_ASSET_PROCESSING_TIMEOUT_SECONDS",
    "WORKSPACE_ASSET_MAINTENANCE_INTERVAL_SECONDS",
    # workspace_service
    "WORKSPACE_DESTRUCTIVE_APPROVAL_TTL_SECONDS",
    # workspace_metrics
    "CHAT_FIRST_WORKSPACE_SLO_SHADOW_PARITY",
    "CHAT_FIRST_WORKSPACE_SLO_ERROR_RATIO",
    "CHAT_FIRST_WORKSPACE_SLO_DEAD_LETTER_RATIO",
    # workspace_metrics_exporter
    "WORKSPACE_OPERATION_METRICS_HOST",
    "WORKSPACE_OPERATION_METRICS_PORT",
    # account_deletion_controller
    "ACCOUNT_DELETION_CONTROLLER_INTERVAL_SECONDS",
    "AUTHORED_VNEXT_MASTER_ENABLED",
    # export_retention_service
    "EXPORT_RETENTION_DAYS",
    "EXPORT_SWEEP_INTERVAL_SECONDS",
    # workspace_artifact_runtime_config callers
    "WORKSPACE_ARTIFACT_STAGING_TIMEOUT_SECONDS",
    "WORKSPACE_ARTIFACT_CLEANUP_LEASE_SECONDS",
    "WORKSPACE_ARTIFACT_CLEANUP_MAX_ATTEMPTS",
    "WORKSPACE_ARTIFACT_CLEANUP_RETRY_SECONDS",
    "WORKSPACE_EXPORT_ARTIFACT_TTL_SECONDS",
)

DEFAULT_ASSET_ORPHAN_TTL_SECONDS = 86_400
DEFAULT_METRICS_HOST = "127.0.0.1"
_DAY = 24 * 60 * 60


def positive_int_knob(
    name: str,
    default: int,
    *,
    minimum: int = 1,
    settings: RuntimeSettings | None = None,
) -> int:
    """Positive integer knob: malformed / non-positive -> ``default``;
    a positive value below ``minimum`` is raised to ``minimum``."""
    value = env_int(name, None, minimum=1, on_invalid="default", settings=settings)
    if value is None:
        return default
    return max(minimum, value)


@dataclass(frozen=True)
class WorkspaceAssetSettings:
    orphan_ttl_seconds: int
    malware_scan_mode: str
    malware_scanner: str


def get_workspace_asset_settings(
    settings: RuntimeSettings | None = None,
) -> WorkspaceAssetSettings:
    mode = get_setting_env("WORKSPACE_ASSET_MALWARE_SCAN_MODE", settings)
    scanner = get_setting_env("WORKSPACE_ASSET_MALWARE_SCANNER", settings)
    return WorkspaceAssetSettings(
        orphan_ttl_seconds=env_int(
            "WORKSPACE_ASSET_ORPHAN_TTL_SECONDS",
            DEFAULT_ASSET_ORPHAN_TTL_SECONDS,
            minimum=300,
            maximum=30 * _DAY,
            on_invalid="clamp",
            settings=settings,
        ),
        malware_scan_mode="disabled" if mode is None else mode.strip().lower(),
        malware_scanner=(scanner or "").strip(),
    )


def get_destructive_approval_ttl_seconds(
    default: int, settings: RuntimeSettings | None = None
) -> int:
    return env_int(
        "WORKSPACE_DESTRUCTIVE_APPROVAL_TTL_SECONDS",
        default,
        minimum=60,
        maximum=7 * _DAY,
        on_invalid="clamp",
        settings=settings,
    )


def get_slo_threshold(
    name: str, default: float, settings: RuntimeSettings | None = None
) -> float:
    return env_float(name, default, on_invalid="default", settings=settings)


@dataclass(frozen=True)
class WorkspaceMetricsExporterSettings:
    port: int | None  # None: listener disabled
    host: str


def get_metrics_exporter_settings(
    settings: RuntimeSettings | None = None,
) -> WorkspaceMetricsExporterSettings:
    """Raises ``EnvValueError`` when the port is set but not 1..65535."""
    host = get_setting_env("WORKSPACE_OPERATION_METRICS_HOST", settings)
    return WorkspaceMetricsExporterSettings(
        port=env_int(
            "WORKSPACE_OPERATION_METRICS_PORT",
            None,
            minimum=1,
            maximum=65_535,
            on_invalid="raise",
            settings=settings,
        ),
        host=DEFAULT_METRICS_HOST if host is None else host.strip(),
    )


def get_account_deletion_interval_seconds(
    default: int, settings: RuntimeSettings | None = None
) -> int:
    return max(
        1,
        env_int(
            "ACCOUNT_DELETION_CONTROLLER_INTERVAL_SECONDS",
            default,
            on_invalid="warn",
            settings=settings,
        ),
    )


def is_authored_vnext_master_storage_required(
    settings: RuntimeSettings | None = None,
) -> bool:
    """True when the authored-vNext master flag asks for master asset storage."""
    return env_bool("AUTHORED_VNEXT_MASTER_ENABLED", False, settings=settings)
