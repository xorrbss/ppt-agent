import posixpath

from fastapi import Request
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from utils.get_env import get_can_change_keys_env, is_disable_auth_enabled
from utils.simple_auth import (
    get_auth_status,
    get_basic_auth_credentials_from_request,
    get_session_token_from_request,
    verify_credentials,
)
from utils.user_config import update_env_with_user_config


class UserConfigEnvUpdateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if get_can_change_keys_env() != "false":
            update_env_with_user_config()
        return await call_next(request)


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
    # Public, unauthenticated assets are image files under /app_data/images/
    # (incl. authored subfolders like images/authored/<id>/slide_N.png). The
    # exemption is checked on a NORMALIZED path AND requires an image extension,
    # so a traversal segment can't ride it up to secrets (userConfig.json / the
    # DB) elsewhere under app_data.
    _PUBLIC_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")

    @staticmethod
    def _normalize(path: str) -> str:
        # Collapse dot-segments BEFORE any prefix/exemption test. Without this,
        # /app_data/images/%2e%2e/userConfig.json (uvicorn-decoded to
        # /app_data/images/../userConfig.json) matches the image exemption here
        # while StaticFiles normalizes it to /app_data/userConfig.json and serves
        # the auth secret + provider keys unauthenticated.
        normalized = posixpath.normpath(path)
        return normalized if normalized.startswith("/") else "/" + normalized

    def _is_exempt(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self._EXEMPT_PREFIXES)

    def _requires_auth(self, path: str) -> bool:
        if path.startswith("/api/"):
            return True
        # PPTX export may re-fetch slide images without session/basic headers.
        if path.startswith("/app_data/images/") and path.lower().endswith(
            self._PUBLIC_IMAGE_EXTS
        ):
            return False
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

        auth_status = get_auth_status(get_session_token_from_request(request))
        if not auth_status["configured"]:
            return JSONResponse(
                status_code=428,
                content={
                    "detail": "Login setup is required",
                    "setup_required": True,
                },
            )

        if not auth_status["authenticated"]:
            basic_credentials = get_basic_auth_credentials_from_request(request)
            if basic_credentials and verify_credentials(
                basic_credentials[0], basic_credentials[1]
            ):
                request.state.auth_username = basic_credentials[0].strip()
                return await call_next(request)

            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
            )

        request.state.auth_username = auth_status.get("username")
        return await call_next(request)
