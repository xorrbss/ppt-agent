from __future__ import annotations

import logging
from typing import Any, Mapping

from utils.get_env import get_user_config_path_env
from utils.user_config_store import update_user_config_file


logger = logging.getLogger(__name__)

LEGACY_PROVIDER_SETTINGS_REVISION_KEY = (
    "_PRESENTON_PROVIDER_SETTINGS_REVISION"
)
LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY = (
    "_PRESENTON_PROVIDER_SETTINGS_PENDING_REFRESH"
)
_LEGACY_BOOKKEEPING_KEYS = frozenset(
    {
        LEGACY_PROVIDER_SETTINGS_REVISION_KEY,
        LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY,
    }
)


def _has_provider_settings(config: Mapping[str, object]) -> bool:
    """Whether ``config`` carries any real provider setting, not bookkeeping."""
    return any(
        key not in _LEGACY_BOOKKEEPING_KEYS and not key.upper().startswith("AUTH_")
        for key in config
    )


def legacy_provider_settings_revision(
    config: Mapping[str, object],
) -> int | None:
    value = config.get(LEGACY_PROVIDER_SETTINGS_REVISION_KEY)
    if isinstance(value, bool):
        return None
    try:
        revision = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return revision if revision >= 0 else None


def legacy_pending_refresh_token(
    config: Mapping[str, object],
) -> str | None:
    value = config.get(LEGACY_PROVIDER_SETTINGS_PENDING_REFRESH_KEY)
    return value if isinstance(value, str) and bool(value) else None


def mirror_provider_settings_to_legacy_file(
    config: dict[str, Any],
    revision: int,
    *,
    force: bool = False,
    expected_pending_token: str | None = None,
    fence_pending_token: bool = False,
) -> None:
    """Revision-order the compatibility mirror under its atomic file lock."""
    path = get_user_config_path_env()
    if not path:
        return

    def replace_provider_config(existing: dict[str, Any]) -> dict[str, Any]:
        pending_token = legacy_pending_refresh_token(existing)
        if (
            fence_pending_token or expected_pending_token is not None
        ) and pending_token != expected_pending_token:
            return existing
        if not force and pending_token is not None:
            return existing
        existing_revision = legacy_provider_settings_revision(existing)
        if (
            not force
            and existing_revision is not None
            and existing_revision >= revision
        ):
            return existing

        if (
            force
            and not _has_provider_settings(config)
            and _has_provider_settings(existing)
        ):
            # ``force`` exists to bypass the revision/pending-token ordering
            # checks above for bootstrap and pending-write-recovery mirrors,
            # never to bypass correctness. Every ``force=True`` caller is one
            # of those two cases; a normal, user-driven save never sets it.
            # An empty incoming projection winning over a file that already
            # holds real provider settings is never that recovery's intent --
            # it is the read path that produced ``config`` having silently
            # failed (see ``read_user_config_file``). Leave the file alone and
            # log loudly instead of erasing configured providers.
            logger.error(
                "Refusing to mirror provider settings revision=%d: the "
                "incoming projection has no provider settings but the "
                "existing file does. Leaving the file unchanged instead of "
                "overwriting it with an empty/less-complete projection.",
                revision,
            )
            return existing

        mirrored = dict(config)
        mirrored[LEGACY_PROVIDER_SETTINGS_REVISION_KEY] = revision
        for key, value in existing.items():
            if key.upper().startswith("AUTH_"):
                mirrored[key] = value
        return mirrored

    update_user_config_file(path, replace_provider_config)
