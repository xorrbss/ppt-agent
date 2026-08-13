"""Process-boundary policy for the isolated authored renderer worker."""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Mapping, Sequence


_TRUE = {"1", "true", "yes", "on"}
_PROVIDER_ENV_MARKERS = (
    "API_KEY",
    "APIKEY",
    "ACCESS_TOKEN",
    "AUTH_TOKEN",
    "BEARER_TOKEN",
    "CLIENT_SECRET",
    "PRIVATE_KEY",
    "PASSWORD",
    "PROVIDER",
)
_WORKER_ENV_ALLOWLIST = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "CHROME_PATH",
    "PUPPETEER_EXECUTABLE_PATH",
    "CHROMIUM_PATH",
}


class RendererPolicyError(ValueError):
    """Authored input or renderer configuration violates the isolation contract."""


class RendererDisabledError(RuntimeError):
    """The authored render kill switch is active."""


def env_enabled(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in _TRUE


def renderer_mode() -> str:
    mode = os.getenv("AUTHORED_RENDERER_MODE", "isolated").strip().lower()
    if mode not in {"isolated", "legacy"}:
        raise RendererPolicyError("AUTHORED_RENDERER_MODE must be isolated or legacy")
    if mode == "legacy" and not env_enabled("AUTHORED_RENDERER_LEGACY_ENABLED"):
        raise RendererPolicyError(
            "legacy authored rendering requires AUTHORED_RENDERER_LEGACY_ENABLED=1"
        )
    return mode


def ensure_renderer_enabled() -> None:
    if env_enabled("AUTHORED_RENDERER_KILL_SWITCH"):
        raise RendererDisabledError("authored rendering is disabled by kill switch")


def renderer_requires_external_worker() -> bool:
    deployment_tier = os.getenv("TEMPLATE_V2_DEPLOYMENT_TIER", "").strip().lower()
    return (
        deployment_tier == "production"
        or env_enabled("AUTHORED_RENDERER_PRODUCTION")
        or env_enabled("AUTHORED_RENDERER_PRODUCTION_STRICT")
        or env_enabled("AUTHORED_RENDERER_REQUIRE_EXTERNAL")
    )


def worker_command() -> list[str]:
    configured = os.getenv("AUTHORED_RENDERER_WORKER_COMMAND_JSON", "").strip()
    strict = renderer_requires_external_worker()
    if strict and os.getenv("AUTHORED_RENDERER_OS_ISOLATION", "").strip() != (
        "nonroot-readonly-tmpfs-network-none"
    ):
        raise RendererPolicyError(
            "production renderer requires the non-root read-only tmpfs network-none attestation"
        )
    if not configured:
        if strict:
            raise RendererPolicyError(
                "production-strict isolation requires AUTHORED_RENDERER_WORKER_COMMAND_JSON"
            )
        return [sys.executable, "-m", "utils.authored_renderer_worker"]
    try:
        value = json.loads(configured)
    except json.JSONDecodeError as exc:
        raise RendererPolicyError(
            "AUTHORED_RENDERER_WORKER_COMMAND_JSON must be a JSON argv array"
        ) from exc
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item or "\x00" in item for item in value)
    ):
        raise RendererPolicyError(
            "AUTHORED_RENDERER_WORKER_COMMAND_JSON must be a non-empty JSON string array"
        )
    if strict and not os.path.isabs(value[0]):
        raise RendererPolicyError(
            "production renderer worker executable must be an absolute path"
        )
    if strict:
        if len(value) != 2 or not os.path.isabs(value[1]):
            raise RendererPolicyError(
                "production renderer command must be absolute Python plus the reviewed invoke.py launcher"
            )
        executable_name = pathlib.Path(value[0]).name.casefold()
        launcher = pathlib.Path(value[1]).resolve(strict=False)
        launcher_tail = tuple(part.casefold() for part in launcher.parts[-3:])
        if not executable_name.startswith("python") or launcher_tail != (
            "contracts",
            "authored-renderer",
            "invoke.py",
        ):
            raise RendererPolicyError(
                "production renderer command must use contracts/authored-renderer/invoke.py"
            )
        if not launcher.is_file():
            raise RendererPolicyError(
                "production renderer invoke.py launcher does not exist"
            )
    return value


def build_worker_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    incoming = os.environ if source is None else source
    result = {
        key: value
        for key, value in incoming.items()
        if key.upper() in _WORKER_ENV_ALLOWLIST
        and not any(marker in key.upper() for marker in _PROVIDER_ENV_MARKERS)
    }
    result["AUTHORED_RENDERER_WORKER"] = "1"
    result["AUTHORED_RENDERER_MODE"] = "isolated"
    return result


def assert_no_provider_secrets(environment: Mapping[str, str]) -> None:
    leaked = [
        name
        for name in environment
        if any(marker in name.upper() for marker in _PROVIDER_ENV_MARKERS)
    ]
    if leaked:
        raise RendererPolicyError("renderer worker environment contains provider secrets")


def validate_command(command: Sequence[str]) -> list[str]:
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise RendererPolicyError("renderer worker command is invalid")
    return list(command)
