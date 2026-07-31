from __future__ import annotations

import asyncio
import io
from pathlib import Path
import zipfile

from fastapi import HTTPException, UploadFile
import pytest

from templates import fonts_and_slides_preview as preview


def _pptx_bytes(*, extra: list[tuple[str, bytes]] | None = None) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/presentation.xml", b"<p:presentation/>")
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            b"<Relationships/>",
        )
        for name, payload in extra or []:
            archive.writestr(name, payload)
    return stream.getvalue()


def _upload(payload: bytes, *, filename: str = "preview.pptx") -> UploadFile:
    return UploadFile(file=io.BytesIO(payload), filename=filename)


def test_legacy_preview_preflight_stores_only_safe_pptx(tmp_path: Path) -> None:
    target = tmp_path / "preview.pptx"

    asyncio.run(
        preview._store_and_preflight_preview_pptx(_upload(_pptx_bytes()), str(target))
    )

    assert target.exists()
    assert target.read_bytes().startswith(b"PK\x03\x04")


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (b"not a zip", "invalid_pptx_zip_signature"),
        (_pptx_bytes(extra=[("../escape.xml", b"escape")]), "unsafe_zip_member_path"),
    ],
)
def test_legacy_preview_preflight_rejects_unsafe_packages(
    tmp_path: Path,
    payload: bytes,
    expected_code: str,
) -> None:
    target = tmp_path / "preview.pptx"

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            preview._store_and_preflight_preview_pptx(_upload(payload), str(target))
        )

    assert caught.value.status_code == 400
    assert caught.value.detail.endswith(expected_code)
    assert not target.exists()


def test_legacy_preview_preflight_enforces_shared_upload_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "preview.pptx"
    monkeypatch.setattr(preview, "get_single_upload_limit_bytes", lambda: 8)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            preview._store_and_preflight_preview_pptx(
                _upload(b"PK\x03\x04" + b"x" * 8), str(target)
            )
        )

    assert caught.value.status_code == 413
    assert not target.exists()

# Handler-level guarantees carried over: the preview endpoints must preflight
# before any font parsing or LibreOffice conversion touches the upload.

class ChunkedUpload:
    def __init__(self, payload: bytes) -> None:
        self.filename = "presentation.pptx"
        self._payload = io.BytesIO(payload)
        self.read_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self._payload.read(size)


@pytest.mark.anyio
async def test_font_check_handler_preflights_before_parsing() -> None:
    with pytest.raises(HTTPException) as raised:
        await preview.check_fonts_in_pptx_handler(
            ChunkedUpload(b"not-a-zip")
        )

    assert raised.value.status_code == 400


@pytest.mark.anyio
async def test_slide_preview_handler_preflights_before_conversion() -> None:
    with pytest.raises(HTTPException) as raised:
        await preview.upload_fonts_and_preview_handler(
            ChunkedUpload(b"not-a-zip"),
            upload_fonts=False,
            get_slide_images=False,
            upload_presentation=False,
        )

    assert raised.value.status_code == 400
