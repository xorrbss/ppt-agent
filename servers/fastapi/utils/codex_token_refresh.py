"""Automatic refresh for the stored Codex OAuth access token.

`utils.llm_config._get_codex_access_token` calls
`ensure_fresh_codex_access_token` before returning a token to the caller. When
the stored token is within its configured leeway of expiry (or already past
it) and a refresh token is on hand, this module performs the same refresh the
`POST /api/v1/ppt/codex/auth/refresh` endpoint performs and persists the
result through the same shared function
(`persist_codex_token_result`, also used by
`api/v1/ppt/endpoints/codex_auth.py`), so both paths write identical fields
and publish the same runtime-settings refresh notification.

`refresh_access_token` (`utils/oauth/openai_codex.py`) is a synchronous
``httpx`` call, so this module does all of its work on the calling thread —
no event loop is created or bridged here. That matters because
`_get_codex_access_token` can be invoked from worker threads inside a
``ThreadPoolExecutor`` (see `services/generation_core/authoring/author_slide.py`), which is also
why the single-flight guard below is a `threading.Lock` rather than an
asyncio primitive.
"""
from __future__ import annotations

import logging
import threading
import time

from utils.get_env import (
    get_codex_access_token_env,
    get_codex_refresh_token_env,
    get_codex_token_refresh_leeway_seconds,
    get_codex_token_expires_env,
    is_codex_token_auto_refresh_enabled,
)
from utils.oauth.openai_codex import (
    CodexAccountProfile,
    TokenSuccess,
    get_account_profile,
    refresh_access_token,
)
from utils.runtime_settings import (
    RuntimeSettings,
    get_process_runtime_settings_envelope,
    resolve_runtime_settings,
)
from utils.user_config import save_codex_tokens_to_user_config

logger = logging.getLogger(__name__)

# Single-flight across threads within this process: only the caller that
# acquires the lock performs the network refresh. Every other concurrent
# caller blocks here and, once released, re-reads the (by then current)
# process settings instead of issuing a second refresh call.
_refresh_lock = threading.Lock()
_unauthorized_failure: tuple[int, str | None, str | None] | None = None
_unauthorized_unchanged: tuple[int, str | None, str | None] | None = None


def _published_revision() -> int:
    envelope = get_process_runtime_settings_envelope()
    return envelope.revision if envelope is not None else -1


def persist_codex_token_result(
    result: TokenSuccess, *, expected_revision: int | None = None,
) -> CodexAccountProfile:
    """Write a successful OAuth token result into the managed settings store.

    Shared by the ``/codex/auth/*`` endpoints
    (`api/v1/ppt/endpoints/codex_auth.py`) and the automatic-refresh path
    below, so both write the exact same fields through the exact same call —
    which also publishes the refreshed runtime-settings snapshot
    (`save_codex_tokens_to_user_config` ->
    `sync_legacy_file_to_provider_settings` ->
    `refresh_process_runtime_settings`).
    """
    profile = get_account_profile(result.access, result.id_token)
    save_codex_tokens_to_user_config(
        {
            "CODEX_ACCESS_TOKEN": result.access,
            "CODEX_REFRESH_TOKEN": result.refresh,
            "CODEX_TOKEN_EXPIRES": str(result.expires),
            "CODEX_ACCOUNT_ID": profile.account_id,
            "CODEX_USERNAME": profile.username,
            "CODEX_EMAIL": profile.email,
            "CODEX_IS_PRO": profile.is_pro,
        },
        **({"expected_revision": expected_revision} if expected_revision is not None else {}),
    )
    return profile


def _is_expiring(settings: RuntimeSettings, leeway_seconds: int) -> bool:
    expires_raw = get_codex_token_expires_env(settings)
    if not expires_raw:
        return False
    try:
        expires_ms = int(expires_raw)
    except (TypeError, ValueError):
        return False
    now_ms = int(time.time() * 1000)
    return now_ms >= expires_ms - leeway_seconds * 1000


def ensure_fresh_codex_access_token(settings: RuntimeSettings) -> str | None:
    """Return a Codex access token that is not within its refresh leeway.

    If auto-refresh is disabled, no refresh token is stored, or the stored
    token is not close to expiry, this returns the token already present on
    ``settings`` unchanged — identical to the behaviour before automatic
    refresh existed. Otherwise it refreshes the token, persists the result
    through `persist_codex_token_result`, and returns the new access token.

    Never raises. On any refresh failure this logs a warning and returns
    whatever token was on hand (which may still be expired), leaving the
    caller's existing "no token" / expiry handling to apply exactly as it did
    before this module existed.
    """
    if not is_codex_token_auto_refresh_enabled(settings):
        return get_codex_access_token_env(settings)

    stored_token = get_codex_access_token_env(settings)
    refresh_token = get_codex_refresh_token_env(settings)
    if not refresh_token:
        return stored_token

    leeway_seconds = get_codex_token_refresh_leeway_seconds(settings)
    if not _is_expiring(settings, leeway_seconds):
        return stored_token

    with _refresh_lock:
        # Re-read after acquiring the lock: another thread may have already
        # refreshed the token while this one was waiting.
        current = resolve_runtime_settings(None)
        if not _is_expiring(current, leeway_seconds):
            return get_codex_access_token_env(current)

        current_refresh_token = get_codex_refresh_token_env(current) or refresh_token
        result = refresh_access_token(current_refresh_token)
        if not isinstance(result, TokenSuccess):
            logger.warning(
                "Codex OAuth automatic refresh failed outcome=failed revision=%d",
                _published_revision(),
            )
            return get_codex_access_token_env(current)

        persist_codex_token_result(result)
        return result.access


def refresh_codex_access_token_after_unauthorized(
    failed_access_token: str,
) -> str | None:
    """Refresh once after a Codex 401 and return the process-current token.

    Read the process envelope, not a job's pinned ContextVar snapshot. Cache a
    failed attempt for this credential revision so waiters cannot retry it.
    The conditional managed-settings write rejects a refresh that loses a race
    with another process or a manual login, before touching the legacy mirror.
    """
    global _unauthorized_failure, _unauthorized_unchanged
    with _refresh_lock:
        envelope = get_process_runtime_settings_envelope()
        if envelope is None:
            return None
        current = envelope.settings
        current_access_token = get_codex_access_token_env(current)
        if current_access_token and current_access_token != failed_access_token:
            logger.info(
                "Codex OAuth 401 refresh outcome=already_refreshed revision=%d",
                _published_revision(),
            )
            return current_access_token

        refresh_token = get_codex_refresh_token_env(current)
        attempt = (envelope.revision, current_access_token, refresh_token)
        if attempt == _unauthorized_unchanged:
            return current_access_token
        if attempt == _unauthorized_failure:
            return None
        if not current_access_token or not refresh_token:
            _unauthorized_failure = attempt
            return None

        try:
            result = refresh_access_token(refresh_token)
            if not isinstance(result, TokenSuccess):
                raise RuntimeError("codex_auth_reauth_required")
            persist_codex_token_result(result, expected_revision=envelope.revision)
        except Exception:
            _unauthorized_failure = attempt
            logger.warning(
                "Codex OAuth 401 refresh outcome=failed revision=%d",
                _published_revision(),
            )
            return None

        _unauthorized_failure = None
        logger.info(
            "Codex OAuth 401 refresh outcome=refreshed revision=%d",
            _published_revision(),
        )
        published = get_process_runtime_settings_envelope()
        access = get_codex_access_token_env(published.settings) if published is not None else None
        # OAuth need not issue a different access-token string. Waiters still
        # share that completed refresh instead of each rotating the credential.
        _unauthorized_unchanged = (
            (published.revision, access, get_codex_refresh_token_env(published.settings))
            if published is not None and access == failed_access_token else None
        )
        return access
