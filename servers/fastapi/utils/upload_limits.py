from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException, UploadFile


MIB = 1024 * 1024
SINGLE_UPLOAD_LIMIT_ENV = "PRESENTON_MAX_UPLOAD_MB"
TOTAL_UPLOAD_LIMIT_ENV = "PRESENTON_MAX_UPLOAD_TOTAL_MB"
IMAGE_UPLOAD_LIMIT_ENV = "PRESENTON_MAX_IMAGE_UPLOAD_MB"
DEFAULT_SINGLE_UPLOAD_MIB = 100
DEFAULT_TOTAL_UPLOAD_MIB = 512
DEFAULT_IMAGE_UPLOAD_MIB = 20
HARD_SINGLE_UPLOAD_MIB = 512
HARD_TOTAL_UPLOAD_MIB = 512
HARD_IMAGE_UPLOAD_MIB = 64
UPLOAD_CHUNK_BYTES = MIB


def _configured_mib(
    env_name: str,
    *,
    default_mib: int,
    hard_max_mib: int,
) -> int:
    raw_value = os.getenv(env_name, "").strip()
    if not raw_value:
        return default_mib
    try:
        requested_mib = int(raw_value)
    except ValueError:
        return default_mib
    if requested_mib <= 0:
        return default_mib
    return min(requested_mib, hard_max_mib)


def get_single_upload_limit_bytes() -> int:
    return (
        _configured_mib(
            SINGLE_UPLOAD_LIMIT_ENV,
            default_mib=DEFAULT_SINGLE_UPLOAD_MIB,
            hard_max_mib=HARD_SINGLE_UPLOAD_MIB,
        )
        * MIB
    )


def get_total_upload_limit_bytes() -> int:
    configured = (
        _configured_mib(
            TOTAL_UPLOAD_LIMIT_ENV,
            default_mib=DEFAULT_TOTAL_UPLOAD_MIB,
            hard_max_mib=HARD_TOTAL_UPLOAD_MIB,
        )
        * MIB
    )
    return max(configured, get_single_upload_limit_bytes())


def get_image_upload_limit_bytes() -> int:
    return (
        _configured_mib(
            IMAGE_UPLOAD_LIMIT_ENV,
            default_mib=DEFAULT_IMAGE_UPLOAD_MIB,
            hard_max_mib=HARD_IMAGE_UPLOAD_MIB,
        )
        * MIB
    )


def format_limit(limit_bytes: int) -> str:
    if limit_bytes < MIB:
        return f"{limit_bytes} bytes"
    return f"{limit_bytes // MIB} MB"


def upload_limits_payload() -> dict[str, int | str]:
    single_bytes = get_single_upload_limit_bytes()
    total_bytes = get_total_upload_limit_bytes()
    image_bytes = get_image_upload_limit_bytes()
    return {
        "single_file_bytes": single_bytes,
        "single_file_mb": single_bytes // MIB,
        "request_total_bytes": total_bytes,
        "request_total_mb": total_bytes // MIB,
        "image_bytes": image_bytes,
        "image_mb": image_bytes // MIB,
        "hard_single_file_mb": HARD_SINGLE_UPLOAD_MIB,
        "hard_request_total_mb": HARD_TOTAL_UPLOAD_MIB,
        "hard_image_mb": HARD_IMAGE_UPLOAD_MIB,
        "reason": (
            "Limits bound request memory, temporary disk use, conversion time, "
            "and denial-of-service exposure."
        ),
    }


def reject_if_declared_too_large(
    upload: UploadFile,
    *,
    limit_bytes: int,
    label: str = "File",
) -> None:
    declared_size = upload.size
    if declared_size is not None and declared_size > limit_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{label} '{upload.filename or 'upload'}' exceeds the "
                f"{format_limit(limit_bytes)} upload limit."
            ),
        )


async def stream_upload_to_file(
    upload: UploadFile,
    destination: str | Path | BinaryIO,
    *,
    limit_bytes: int,
    label: str = "File",
) -> int:
    """Stream an UploadFile to disk and enforce the limit even without Content-Length."""

    reject_if_declared_too_large(upload, limit_bytes=limit_bytes, label=label)
    size = 0
    owns_stream = not hasattr(destination, "write")
    stream = (
        Path(destination).open("wb")
        if owns_stream
        else destination
    )
    try:
        while True:
            chunk = await upload.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > limit_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"{label} '{upload.filename or 'upload'}' exceeds the "
                        f"{format_limit(limit_bytes)} upload limit."
                    ),
                )
            stream.write(chunk)
    except Exception:
        if owns_stream:
            stream.close()
            Path(destination).unlink(missing_ok=True)
        raise
    finally:
        if owns_stream and not stream.closed:
            stream.close()
    return size
