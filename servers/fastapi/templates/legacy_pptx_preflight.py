"""Bounded staging and ZIP preflight for legacy template-preview uploads."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, UploadFile

from templates.v2.pptx.package_reader import PptxPackageReader, UnsafePptxPackage


MAX_LEGACY_PREVIEW_PPTX_BYTES = 100 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def stage_legacy_preview_pptx(
    upload: UploadFile,
    target: str | Path,
    *,
    max_bytes: int = MAX_LEGACY_PREVIEW_PPTX_BYTES,
) -> None:
    """Stage one upload without unbounded reads, then reject unsafe OOXML ZIPs."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("invalid_legacy_preview_pptx_size_limit")

    target = Path(target)
    size = 0
    prefix = bytearray()
    try:
        with target.open("wb") as stream:
            while True:
                remaining_with_sentinel = max_bytes - size + 1
                chunk = await upload.read(
                    min(_UPLOAD_CHUNK_BYTES, remaining_with_sentinel)
                )
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="PPTX file exceeds the legacy preview size limit",
                    )
                if len(prefix) < 4:
                    prefix.extend(chunk[: 4 - len(prefix)])
                stream.write(chunk)

        if size == 0 or bytes(prefix) != b"PK\x03\x04":
            raise HTTPException(
                status_code=400,
                detail="Invalid or unsafe PPTX package",
            )
        try:
            PptxPackageReader(target).preflight()
        except UnsafePptxPackage as error:
            raise HTTPException(
                status_code=400,
                detail="Invalid or unsafe PPTX package",
            ) from error
    except Exception:
        target.unlink(missing_ok=True)
        raise
