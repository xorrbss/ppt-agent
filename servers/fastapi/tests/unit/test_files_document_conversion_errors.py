import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from api.v1.ppt.endpoints import files as files_endpoint
from services.document_conversion_service import (
    DocumentConversionError,
    DocumentConversionService,
)


def test_image_conversion_without_imagemagick_has_actionable_error_metadata(
    tmp_path, monkeypatch
):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"jpeg")
    service = DocumentConversionService()
    monkeypatch.setattr(service, "imagemagick_binary", None)

    with pytest.raises(DocumentConversionError) as exc:
        service.convert_image_to_png(str(source), str(tmp_path))

    detail = exc.value.to_detail()
    assert detail["code"] == "IMAGE_CONVERTER_UNAVAILABLE"
    assert detail["filename"] == "photo.jpg"
    assert detail["retryable"] is False
    assert "ImageMagick" in detail["message"]


def test_decompose_exposes_image_converter_failure_as_structured_422(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"jpeg")
    monkeypatch.setattr(files_endpoint.TEMP_FILE_SERVICE, "base_dir", str(tmp_path))

    class FailingLoader:
        def __init__(self, *args, **kwargs):
            pass

        async def load_documents(self, *args, **kwargs):
            raise DocumentConversionError(
                "ImageMagick failed stderr=C:\\secret\\input.jpg token=internal",
                code="IMAGE_CONVERTER_UNAVAILABLE",
                filename="source.jpg",
                retryable=False,
                public_message="Image conversion is unavailable. Try again later.",
            )

    monkeypatch.setattr(files_endpoint, "DocumentsLoader", FailingLoader)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            files_endpoint.decompose_files(file_paths=[str(source)], language="ko")
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == {
        "code": "IMAGE_CONVERTER_UNAVAILABLE",
        "message": "Image conversion is unavailable. Try again later.",
        "filename": "source.jpg",
        "retryable": False,
    }
    assert "secret" not in str(exc.value.detail)
    assert "token" not in str(exc.value.detail)


def test_conversion_error_uses_safe_default_public_message():
    error = DocumentConversionError(
        "stderr=/private/path stdout=credential",
        filename="source.docx",
    )

    detail = error.to_detail()
    assert detail["message"] == "Document conversion failed. Check the file and try again."
    assert "/private/path" not in str(detail)
    assert "credential" not in str(detail)


def test_upload_rejects_fasoo_drm_with_original_filename_and_actionable_message(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(files_endpoint.TEMP_FILE_SERVICE, "base_dir", str(tmp_path))
    filename = "운영자매뉴얼.pptx"
    protected_payload = (
        b"\xff DRMONE  This Document is encrypted and protected by Fasoo DRM "
        b"\x00" * 32
    )
    upload = UploadFile(file=BytesIO(protected_payload), filename=filename)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(files_endpoint.upload_files([upload]))

    assert exc.value.status_code == 422
    assert exc.value.detail == {
        "code": "DOCUMENT_DRM_PROTECTED",
        "message": (
            f"'{filename}' 파일은 Fasoo DRM으로 보호되어 내용을 읽을 수 없습니다. "
            "DRM을 해제하거나 PowerPoint에서 DRM 없는 .pptx 파일로 다시 저장한 뒤 "
            "업로드해 주세요."
        ),
        "filename": filename,
        "retryable": False,
    }
    assert list(tmp_path.rglob("*.pptx")) == []
