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
