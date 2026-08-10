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
    monkeypatch.setattr(
        fonts,
        "validate_and_extract_font_name",
        lambda _path: "Brand Font",
    )

    response = asyncio.run(fonts.upload_font(_font_upload(b"font")))

    assert response.success is True
    assert response.font_name == "Brand Font"
    assert "font_path" not in response.model_dump()
    assert (tmp_path / "BrandFont_12345678.ttf").read_bytes() == b"font"


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


def test_upload_font_rejects_traversal_filename(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    upload = _font_upload(b"font")
    upload.filename = "../BrandFont.ttf"
    monkeypatch.setattr(fonts, "get_fonts_directory", lambda: str(tmp_path))

    with pytest.raises(HTTPException) as error:
        asyncio.run(fonts.upload_font(upload))

    assert error.value.status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_upload_font_removes_bytes_when_parser_rejects_them(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(fonts, "get_fonts_directory", lambda: str(tmp_path))
    monkeypatch.setattr(fonts, "get_single_upload_limit_bytes", lambda: 8)
    monkeypatch.setattr(fonts.uuid, "uuid4", lambda: "12345678-0000-0000-0000-000000000000")

    def reject(_path: str) -> str:
        raise HTTPException(status_code=422, detail="Invalid font file")

    monkeypatch.setattr(fonts, "validate_and_extract_font_name", reject)

    with pytest.raises(HTTPException) as error:
        asyncio.run(fonts.upload_font(_font_upload(b"badfont")))

    assert error.value.status_code == 422
    assert list(tmp_path.iterdir()) == []


def test_delete_font_rejects_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    outside = tmp_path / "outside.ttf"
    outside.write_bytes(b"keep")
    monkeypatch.setattr(fonts, "get_fonts_directory", lambda: str(fonts_dir))

    with pytest.raises(HTTPException) as error:
        asyncio.run(fonts.delete_font("../outside.ttf"))

    assert error.value.status_code == 400
    assert outside.read_bytes() == b"keep"
