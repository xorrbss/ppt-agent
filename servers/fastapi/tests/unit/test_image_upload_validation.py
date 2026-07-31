from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1.ppt.endpoints.images import _validate_uploaded_image


def _upload(filename: str, content_type: str):
    return SimpleNamespace(filename=filename, content_type=content_type)


def test_validate_uploaded_image_accepts_matching_png(tmp_path):
    image_path = tmp_path / "valid.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    _validate_uploaded_image(_upload("valid.png", "image/png"), image_path)


@pytest.mark.parametrize(
    ("filename", "content_type", "payload"),
    [
        ("spoofed.png", "image/png", b"%PDF-1.7"),
        ("vector.svg", "image/svg+xml", b"<svg></svg>"),
    ],
)
def test_validate_uploaded_image_rejects_invalid_or_unsupported_images(
    tmp_path, filename, content_type, payload
):
    image_path = tmp_path / filename
    image_path.write_bytes(payload)

    with pytest.raises(HTTPException) as error:
        _validate_uploaded_image(_upload(filename, content_type), image_path)

    assert error.value.status_code == 415
