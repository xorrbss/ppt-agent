from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import HTTPException

from templates import fonts_and_slides_preview
from templates.legacy_pptx_preflight import stage_legacy_preview_pptx


class ChunkedUpload:
    def __init__(self, payload: bytes) -> None:
        self.filename = "presentation.pptx"
        self._payload = BytesIO(payload)
        self.read_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self._payload.read(size)


def _pptx_bytes(*, include_required_parts: bool = True) -> bytes:
    payload = BytesIO()
    with ZipFile(payload, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        if include_required_parts:
            archive.writestr("ppt/presentation.xml", b"<p:presentation/>")
            archive.writestr(
                "ppt/_rels/presentation.xml.rels",
                b"<Relationships/>",
            )
    return payload.getvalue()


@pytest.mark.anyio
async def test_stages_valid_pptx_with_bounded_reads(tmp_path: Path) -> None:
    payload = _pptx_bytes()
    upload = ChunkedUpload(payload)
    target = tmp_path / "presentation.pptx"

    await stage_legacy_preview_pptx(upload, target, max_bytes=len(payload))

    assert target.read_bytes() == payload
    assert upload.read_sizes
    assert max(upload.read_sizes) <= len(payload) + 1


@pytest.mark.anyio
async def test_rejects_oversize_upload_and_removes_partial_file(
    tmp_path: Path,
) -> None:
    upload = ChunkedUpload(b"PK\x03\x04" + (b"x" * 125))
    target = tmp_path / "presentation.pptx"

    with pytest.raises(HTTPException) as raised:
        await stage_legacy_preview_pptx(upload, target, max_bytes=128)

    assert raised.value.status_code == 413
    assert not target.exists()
    assert max(upload.read_sizes) <= 129


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        b"not-a-zip",
        pytest.param(
            _pptx_bytes(include_required_parts=False),
            id="missing-required-ooxml-parts",
        ),
    ],
)
async def test_rejects_invalid_package_without_retaining_content(
    tmp_path: Path,
    payload: bytes,
) -> None:
    target = tmp_path / "presentation.pptx"

    with pytest.raises(HTTPException) as raised:
        await stage_legacy_preview_pptx(
            ChunkedUpload(payload),
            target,
            max_bytes=len(payload) + 1,
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "Invalid or unsafe PPTX package"
    assert not target.exists()


@pytest.mark.anyio
async def test_font_check_handler_preflights_before_parsing() -> None:
    with pytest.raises(HTTPException) as raised:
        await fonts_and_slides_preview.check_fonts_in_pptx_handler(
            ChunkedUpload(b"not-a-zip")
        )

    assert raised.value.status_code == 400


@pytest.mark.anyio
async def test_slide_preview_handler_preflights_before_conversion() -> None:
    with pytest.raises(HTTPException) as raised:
        await fonts_and_slides_preview.upload_fonts_and_preview_handler(
            ChunkedUpload(b"not-a-zip"),
            upload_fonts=False,
            get_slide_images=False,
            upload_presentation=False,
        )

    assert raised.value.status_code == 400
