from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.ppt.endpoints.images import IMAGES_ROUTER, _image_asset_api_dict
from models.sql.image_asset import ImageAsset
from services.assets import AssetGatewayError
from services.database import get_async_session


def _build_client(fake_async_session):
    app = FastAPI()
    app.include_router(IMAGES_ROUTER)
    app.dependency_overrides[get_async_session] = lambda: fake_async_session
    return TestClient(app)


def test_search_images_with_provider_alias_returns_list(fake_async_session):
    client = _build_client(fake_async_session)

    with patch(
        "api.v1.ppt.endpoints.images.get_images_directory", return_value="/tmp"
    ), patch("api.v1.ppt.endpoints.images.ImageGenerationService") as mock_service_cls:
        service = Mock()
        service.get_image_from_pexels = AsyncMock(
            return_value=["https://img.example.com/a.jpg", "https://img.example.com/b.jpg"]
        )
        mock_service_cls.return_value = service

        response = client.get("/images/search?query=ai&provider=pexel&limit=2")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert all(item.startswith("https://") for item in body)


def test_search_images_strict_mode_requires_api_key(fake_async_session):
    client = _build_client(fake_async_session)

    with patch("api.v1.ppt.endpoints.images.get_images_directory", return_value="/tmp"):
        response = client.get(
            "/images/search?query=ai&provider=pexels&strict_api_key=true"
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Pexels API key is required"


def test_search_images_uses_configured_key_for_redacted_runtime_secret(
    fake_async_session,
):
    client = _build_client(fake_async_session)

    with patch(
        "api.v1.ppt.endpoints.images.get_images_directory", return_value="/tmp"
    ), patch(
        "api.v1.ppt.endpoints.images.get_pexels_api_key_env",
        return_value="server-pexels-key",
    ), patch(
        "api.v1.ppt.endpoints.images.ImageGenerationService"
    ) as mock_service_cls:
        service = Mock()
        service.get_image_from_pexels = AsyncMock(
            side_effect=[
                "https://img.example.com/validation.jpg",
                ["https://img.example.com/a.jpg"],
            ]
        )
        mock_service_cls.return_value = service

        response = client.get(
            "/images/search?query=business&provider=pexels&strict_api_key=true",
            headers={"X-Provider-Api-Key": "__configured__"},
        )

    assert response.status_code == 200
    assert response.json() == ["https://img.example.com/a.jpg"]
    assert service.get_image_from_pexels.await_count == 2
    assert all(
        call.kwargs["api_key"] == "server-pexels-key"
        for call in service.get_image_from_pexels.await_args_list
    )


def test_generate_image_returns_image_path_and_persists_image_asset(fake_async_session):
    client = _build_client(fake_async_session)
    generated_asset = ImageAsset(path="/tmp/generated/a.png", is_uploaded=False)

    with patch(
        "api.v1.ppt.endpoints.images.get_images_directory", return_value="/tmp"
    ), patch(
        "api.v1.ppt.endpoints.images.canonicalize_legacy_image_asset",
        new_callable=AsyncMock,
    ) as canonicalize, patch(
        "api.v1.ppt.endpoints.images.ImageGenerationService"
    ) as mock_service_cls:
        service = Mock()
        service.generate_image = AsyncMock(return_value=generated_asset)
        mock_service_cls.return_value = service

        response = client.get("/images/generate?prompt=business-dashboard")

    assert response.status_code == 200
    assert response.json() == "/tmp/generated/a.png"
    assert fake_async_session.added[-1] == generated_asset
    assert fake_async_session.commit_count == 1
    canonicalize.assert_awaited_once()


def test_generate_image_returns_placeholder_without_db_write(fake_async_session):
    client = _build_client(fake_async_session)

    with patch(
        "api.v1.ppt.endpoints.images.get_images_directory", return_value="/tmp"
    ), patch("api.v1.ppt.endpoints.images.ImageGenerationService") as mock_service_cls:
        service = Mock()
        service.generate_image = AsyncMock(return_value="/static/images/placeholder.jpg")
        mock_service_cls.return_value = service

        response = client.get("/images/generate?prompt=business-dashboard")

    assert response.status_code == 200
    assert response.json() == "/static/images/placeholder.jpg"
    assert fake_async_session.added == []


def test_upload_removes_raw_file_when_canonical_admission_fails(
    fake_async_session,
    tmp_path,
):
    client = _build_client(fake_async_session)
    fake_async_session.flush = AsyncMock()

    with patch(
        "api.v1.ppt.endpoints.images.get_images_directory",
        return_value=str(tmp_path),
    ), patch(
        "api.v1.ppt.endpoints.images.canonicalize_legacy_image_asset",
        new_callable=AsyncMock,
        side_effect=AssetGatewayError("asset_scan_failed", status_code=422),
    ):
        response = client.post(
            "/images/upload",
            files={"file": ("sample.png", b"\x89PNG\r\n\x1a\nrest", "image/png")},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "asset_scan_failed"
    assert list(tmp_path.iterdir()) == []


def test_image_endpoint_generic_failure_uses_stable_code_without_path(
    fake_async_session,
):
    secret_path = r"C:\private\owner\gallery.png"
    fake_async_session.scalars = AsyncMock(
        side_effect=RuntimeError(f"database failed for {secret_path}")
    )
    client = _build_client(fake_async_session)

    response = client.get("/images/generated")

    assert response.status_code == 500
    assert response.json()["detail"] == "image_gallery_read_failed"
    assert secret_path not in response.text


def test_image_gallery_file_url_allows_only_public_or_confined_urls():
    asset = ImageAsset(path="ignored", is_uploaded=True)
    allowed = [
        "https://images.example.test/a.png",
        "http://images.example.test/a.png",
        "data:image/png;base64,AAAA",
        "blob:https://app.example.test/asset-id",
        "/app_data/images/users/owner/a.png",
    ]
    rejected = [
        "relative/image.png",
        "/private/gallery.png",
        "C:\\private\\gallery.png",
        "/app_data/images/../secrets/a.png",
        "/app_data/images/%2e%2e/secrets/a.png",
        "/app_data/images/%252e%252e/secrets/a.png",
        "javascript:alert(1)",
        "//evil.example.test/a.png",
        "http://[invalid-ipv6/a.png",
    ]
    for value in allowed:
        with patch(
            "api.v1.ppt.endpoints.images.filesystem_image_path_to_app_data_url",
            return_value=value,
        ):
            assert _image_asset_api_dict(asset)["file_url"] == value
    for value in rejected:
        with patch(
            "api.v1.ppt.endpoints.images.filesystem_image_path_to_app_data_url",
            return_value=value,
        ):
            assert _image_asset_api_dict(asset)["file_url"] is None
