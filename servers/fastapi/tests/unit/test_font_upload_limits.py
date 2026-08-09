from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from api.v1.ppt.endpoints import fonts


def _font_upload(payload: bytes) -> UploadFile:
    return UploadFile(
        filename="BrandFont.ttf",
        file=BytesIO(payload),
        headers={"content-type": "font/ttf"},
    )


def test_upload_font_streams_normal_file_within_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(fonts, "get_fonts_directory", lambda: str(tmp_path))
    monkeypatch.setattr(fonts, "get_single_upload_limit_bytes", lambda: 4)
    monkeypatch.setattr(fonts.uuid, "uuid4", lambda: "12345678-0000-0000-0000-000000000000")

    response = asyncio.run(fonts.upload_font(_font_upload(b"font")))

    assert response.success is True
    assert response.font_name == "BrandFont"
    assert Path(response.font_path).read_bytes() == b"font"


def test_upload_font_rejects_stream_over_limit_and_removes_partial_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(fonts, "get_fonts_directory", lambda: str(tmp_path))
    monkeypatch.setattr(fonts, "get_single_upload_limit_bytes", lambda: 4)
    monkeypatch.setattr(fonts.uuid, "uuid4", lambda: "12345678-0000-0000-0000-000000000000")
    monkeypatch.setattr("utils.upload_limits.UPLOAD_CHUNK_BYTES", 2)

    with pytest.raises(HTTPException) as error:
        asyncio.run(fonts.upload_font(_font_upload(b"abcde")))

    assert error.value.status_code == 413
    assert "4 bytes" in str(error.value.detail)
    assert list(tmp_path.iterdir()) == []
