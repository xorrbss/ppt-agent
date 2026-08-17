"""Regression tests for SessionAuthMiddleware path handling."""
import asyncio
import uuid
from types import SimpleNamespace

from starlette.requests import Request
from starlette.responses import Response

from api.middlewares import SessionAuthMiddleware
from utils.owner_context import (
    get_current_owner_id,
    get_current_owner_is_admin,
    reset_current_owner_id,
    set_current_owner_id,
)


def _mw() -> SessionAuthMiddleware:
    return SessionAuthMiddleware(app=None)


def _requires_auth(raw_path: str) -> bool:
    mw = _mw()
    return mw._requires_auth(mw._normalize(raw_path))


def _is_exempt(raw_path: str) -> bool:
    mw = _mw()
    return mw._is_exempt(mw._normalize(raw_path))


def test_traversal_out_of_images_requires_auth():
    # The core bypass: escape the image exemption up to the auth secret / keys.
    assert _requires_auth("/app_data/images/../userConfig.json") is True
    assert _requires_auth("/app_data/images/../../app_data/fastapi.db") is True
    assert _requires_auth("/app_data/images/authored/../../userConfig.json") is True


def test_all_direct_image_paths_require_auth():
    assert _requires_auth("/app_data/images/0e9c8226.png") is True
    assert _requires_auth("/app_data/images/photo.JPEG") is True
    assert _requires_auth("/app_data/images/authored/deck-id/slide_0.png") is True
    assert _requires_auth("/app_data/images/users/owner-id/slide.png") is True


def test_non_image_under_images_requires_auth():
    assert _requires_auth("/app_data/images/userConfig.json") is True
    assert _requires_auth("/app_data/images/secret.db") is True
    assert _requires_auth("/app_data/images/nodotfile") is True


def test_direct_secrets_and_api_require_auth():
    assert _requires_auth("/app_data/userConfig.json") is True
    assert _requires_auth("/app_data/fastapi.db") is True
    assert _requires_auth("/api/v1/ppt/presentation/create") is True


def test_normalize_keeps_leading_slash():
    mw = _mw()
    assert mw._normalize("/app_data/images/x.png") == "/app_data/images/x.png"
    assert (
        mw._normalize("/app_data/images/../userConfig.json")
        == "/app_data/userConfig.json"
    )


def test_public_share_link_is_exempt():
    # The read-only public share view is reachable without the admin session.
    assert _is_exempt("/api/v1/ppt/presentation/public/abcDEF123456xyz") is True


def test_public_share_traversal_cannot_ride_exemption():
    # A traversal from the public prefix must not ride the exemption to a
    # protected path: it normalizes off the prefix and still requires auth.
    raw = "/api/v1/ppt/presentation/public/../../../app_data/userConfig.json"
    assert _is_exempt(raw) is False
    assert _requires_auth(raw) is True


def test_admin_share_management_requires_auth():
    # Only GET-by-token is public; enabling/rotating/disabling a link is admin-only.
    assert _is_exempt("/api/v1/ppt/presentation/1234/share") is False
    assert _requires_auth("/api/v1/ppt/presentation/1234/share") is True


def test_disable_auth_uses_bootstrap_admin_request_owner(monkeypatch):
    owner_id = uuid.uuid4()
    admin = SimpleNamespace(
        id=owner_id,
        username="desktop-admin",
        is_superuser=True,
        is_active=True,
    )

    class AdminSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def scalar(self, _statement):
            return admin

    monkeypatch.setattr("api.middlewares.is_disable_auth_enabled", lambda: True)
    monkeypatch.setattr("api.middlewares.async_session_maker", AdminSession)
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/v1/ppt/test", "headers": []}
    )

    async def call_next(current_request):
        assert get_current_owner_id() == owner_id
        assert get_current_owner_is_admin() is True
        assert current_request.state.current_user is admin
        assert current_request.state.auth_principal.user_id == owner_id
        return Response(status_code=204)

    response = asyncio.run(_mw().dispatch(request, call_next))

    assert response.status_code == 204
    assert get_current_owner_id() is None
    assert get_current_owner_is_admin() is False


def test_disable_auth_fails_closed_without_bootstrap_admin(monkeypatch):
    class EmptySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def scalar(self, _statement):
            return None

    monkeypatch.setattr("api.middlewares.is_disable_auth_enabled", lambda: True)
    monkeypatch.setattr("api.middlewares.async_session_maker", EmptySession)
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/v1/ppt/test", "headers": []}
    )
    called = False

    async def call_next(_request):
        nonlocal called
        called = True
        return Response(status_code=204)

    response = asyncio.run(_mw().dispatch(request, call_next))

    assert response.status_code == 503
    assert called is False


def test_disable_auth_preserves_explicit_outer_owner_scope(monkeypatch):
    owner_id = uuid.uuid4()
    monkeypatch.setattr("api.middlewares.is_disable_auth_enabled", lambda: True)
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/v1/ppt/test", "headers": []}
    )

    async def call_next(_request):
        assert get_current_owner_id() == owner_id
        return Response(status_code=204)

    token = set_current_owner_id(owner_id)
    try:
        response = asyncio.run(_mw().dispatch(request, call_next))
    finally:
        reset_current_owner_id(token)

    assert response.status_code == 204
