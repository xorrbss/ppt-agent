"""Regression tests for SessionAuthMiddleware path handling.

Guards the /app_data/images/ auth exemption against dot-segment traversal: a
request like /app_data/images/../userConfig.json is decoded by uvicorn to a path
that would ride the image exemption while StaticFiles normalizes it back to the
secret file. The middleware must decide auth on the NORMALIZED path.
"""
from api.middlewares import SessionAuthMiddleware


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


def test_legit_images_stay_public():
    assert _requires_auth("/app_data/images/0e9c8226.png") is False
    assert _requires_auth("/app_data/images/photo.JPEG") is False
    # authored slide images live in subfolders and must stay publicly fetchable
    assert _requires_auth("/app_data/images/authored/deck-id/slide_0.png") is False


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
    assert mw._normalize("/app_data/images/../userConfig.json") == "/app_data/userConfig.json"


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
