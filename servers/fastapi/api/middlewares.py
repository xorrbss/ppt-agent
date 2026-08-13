import ipaddress
import os
import posixpath
import secrets

from fastapi import Request
from sqlalchemy import func, select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from api.v1.auth.assets import is_app_data_path_authorized
from api.v1.auth.principal import resolve_request_principal
from utils.owner_context import (
    reset_current_owner_id,
    reset_current_owner_is_admin,
    set_current_owner_id,
    set_current_owner_is_admin,
)
from api.v1.auth.users import get_jwt_strategy
from models.sql.user import User
from services.database import async_session_maker
from services.provider_settings import import_legacy_file_to_provider_settings
from services.provider_settings import save_provider_settings_record
from utils.get_env import is_disable_auth_enabled
from utils.runtime_settings import (
    get_process_runtime_settings,
    reset_current_runtime_settings,
    set_current_runtime_settings,
)
from utils.user_config import refresh_process_runtime_settings


_RUNTIME_SETTINGS_REFRESH_PATH = "/_presenton/runtime-settings/refresh"
_RUNTIME_SETTINGS_REFRESH_TOKEN_ENV = (
    "PRESENTON_RUNTIME_SETTINGS_REFRESH_TOKEN"
)
_RUNTIME_SETTINGS_REFRESH_TOKEN_HEADER = (
    "x-presenton-runtime-settings-token"
)


def _is_loopback_request(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return False


async def _refresh_runtime_settings(request: Request) -> Response:
    expected_token = os.getenv(_RUNTIME_SETTINGS_REFRESH_TOKEN_ENV, "")
    provided_token = request.headers.get(_RUNTIME_SETTINGS_REFRESH_TOKEN_HEADER, "")
    authorized = (
        request.method == "POST"
        and _is_loopback_request(request)
        and bool(expected_token)
        and bool(provided_token)
        and secrets.compare_digest(
            provided_token.encode("utf-8"),
            expected_token.encode("utf-8"),
        )
    )
    if not authorized:
        # Keep this process-control surface undiscoverable unless the Electron
        # launcher enabled it and supplied its process-memory token.
        return Response(status_code=404)

    # The file is read only for this explicit post-save notification, never on
    # ordinary requests. Electron's local worker publishes immediately; the DB
    # revision lets any external workers converge without carrying settings in
    # this control request.
    try:
        async with async_session_maker() as session:
            body = await request.body()
            if len(body) > 1024 * 1024:
                return Response(status_code=413)
            if body:
                value = await request.json()
                if not isinstance(value, dict):
                    return Response(status_code=422)
                committed = await save_provider_settings_record(session, value)
            else:
                committed = await import_legacy_file_to_provider_settings(session)
        refresh_process_runtime_settings(
            persisted_config=committed.config,
            revision=committed.revision,
        )
    except Exception:
        # Do not reflect exception details, paths, config values, or secrets.
        return Response(status_code=500)
    return Response(status_code=204)


class RuntimeSettingsContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == _RUNTIME_SETTINGS_REFRESH_PATH:
            return await _refresh_runtime_settings(request)
        settings = get_process_runtime_settings()
        request.state.runtime_settings = settings
        token = set_current_runtime_settings(settings)
        try:
            return await call_next(request)
        finally:
            reset_current_runtime_settings(token)


class SessionAuthMiddleware(BaseHTTPMiddleware):
    # Public, unauthenticated endpoints. The share endpoint is read-only and
    # reachable ONLY by an unguessable token (never a presentation id); the check
    # runs on the NORMALIZED path so a traversal segment can't ride this prefix to
    # a protected route. Only GET-by-token lives under this prefix.
    _EXEMPT_PREFIXES = (
        "/api/v1/auth/",
        "/api/v1/ppt/presentation/public/",
    )
    _PROTECTED_NON_API_PATHS = {
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    @staticmethod
    def _normalize(path: str) -> str:
        # Collapse dot-segments before any prefix/exemption test so routing and
        # authorization cannot disagree about which protected path was requested.
        normalized = posixpath.normpath(path)
        return normalized if normalized.startswith("/") else "/" + normalized

    def _is_exempt(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self._EXEMPT_PREFIXES)

    def _requires_auth(self, path: str) -> bool:
        if path.startswith("/api/"):
            return True
        if path.startswith("/app_data/"):
            return True
        return path in self._PROTECTED_NON_API_PATHS

    async def dispatch(self, request: Request, call_next):
        if is_disable_auth_enabled():
            return await call_next(request)

        # Decide auth on the NORMALIZED path so dot-segment traversal can't slip a
        # protected path past the exemptions (routing still uses the real path).
        path = self._normalize(request.url.path)

        if (
            request.method == "OPTIONS"
            or not self._requires_auth(path)
            or self._is_exempt(path)
        ):
            return await call_next(request)

        async with async_session_maker() as session:
            configured = bool(
                await session.scalar(select(func.count()).select_from(User))
            )
            if not configured:
                return JSONResponse(
                    status_code=428,
                    content={
                        "detail": "Login setup is required",
                        "setup_required": True,
                    },
                )
            principal, user = await resolve_request_principal(request, session)
            if principal is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized"},
                )
            admin_only = (
                path.startswith("/api/v1/admin/")
                or path.startswith("/api/v1/auth/token/")
                or path.startswith("/api/v1/ppt/codex/auth/")
                or (
                    path.startswith("/api/v1/ppt/fonts/")
                    and request.method in {"POST", "DELETE"}
                )
                or (
                    path == "/api/v1/ppt/ollama/models/pull"
                    and request.method == "POST"
                )
            )
            if admin_only and (
                principal.method != "jwt" or not principal.is_admin
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Admin browser session required"},
                )
            if path.startswith("/app_data/") and not is_app_data_path_authorized(
                path,
                user_id=principal.user_id,
                is_admin=principal.is_admin,
            ):
                return JSONResponse(
                    status_code=404,
                    content={"detail": "Asset not found"},
                )
            request.state.auth_principal = principal
            request.state.current_user = user
            request.state.auth_username = principal.username
            if principal.method == "api_key" and user is not None:
                request.state.internal_session_token = (
                    await get_jwt_strategy().write_token(user)
                )
            context_token = set_current_owner_id(principal.user_id)
            admin_context_token = set_current_owner_is_admin(principal.is_admin)
            try:
                return await call_next(request)
            finally:
                reset_current_owner_is_admin(admin_context_token)
                reset_current_owner_id(context_token)
