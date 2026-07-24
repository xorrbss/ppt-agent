from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import uuid

from fastapi import UploadFile

from utils.get_env import get_app_data_directory_env


MAX_PPTX_UPLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_PRIVATE_SOURCE_RETENTION_DAYS = 7
MIN_PRIVATE_SOURCE_RETENTION_DAYS = 1
MAX_PRIVATE_SOURCE_RETENTION_DAYS = 90
PRIVATE_SOURCE_RETENTION_DAYS_ENV = "TEMPLATE_V2_PPTX_SOURCE_TTL_DAYS"
PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
_SAFE_DISPLAY_FILENAME = re.compile(r"[^a-zA-Z0-9._ ()\-\u0080-\uffff]+")


class PptxUploadRejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StoredPptx:
    display_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    storage_key: str


def get_private_source_retention_ttl() -> timedelta:
    raw = os.getenv(PRIVATE_SOURCE_RETENTION_DAYS_ENV)
    if raw is None or not raw.strip():
        days = DEFAULT_PRIVATE_SOURCE_RETENTION_DAYS
    else:
        value = raw.strip()
        if not value.isascii() or not value.isdecimal():
            raise RuntimeError("invalid_template_v2_pptx_source_ttl_days")
        days = int(value)
    if not MIN_PRIVATE_SOURCE_RETENTION_DAYS <= days <= MAX_PRIVATE_SOURCE_RETENTION_DAYS:
        raise RuntimeError("template_v2_pptx_source_ttl_days_out_of_range")
    return timedelta(days=days)


def private_import_root() -> Path:
    """Return a writable root outside FastAPI's /app_data static mount."""

    raw_app_data = (get_app_data_directory_env() or "").strip()
    if not raw_app_data:
        raise RuntimeError("app_data_directory_required")
    app_data = Path(raw_app_data).resolve()
    if app_data == app_data.parent:
        raise RuntimeError("unsafe_app_data_directory")
    return app_data.parent / f"{app_data.name}-private" / "template-v2-imports"


def resolve_private_source(storage_key: str) -> Path:
    key = PurePosixPath(storage_key)
    if (
        key.is_absolute()
        or not key.parts
        or any(part in {"", ".", ".."} for part in key.parts)
        or "\\" in storage_key
        or ":" in storage_key
    ):
        raise PptxUploadRejected("invalid_private_storage_key")
    root = private_import_root().resolve()
    candidate = root.joinpath(*key.parts).resolve()
    if not candidate.is_relative_to(root):
        raise PptxUploadRejected("private_storage_path_escape")
    return candidate


def _display_filename(filename: str | None) -> str:
    leaf = Path((filename or "source.pptx").replace("\\", "/")).name
    cleaned = _SAFE_DISPLAY_FILENAME.sub("_", leaf).strip(" .")
    return (cleaned or "source.pptx")[:240]


async def store_private_pptx(
    upload: UploadFile,
    *,
    import_id: uuid.UUID,
    max_bytes: int = MAX_PPTX_UPLOAD_BYTES,
) -> StoredPptx:
    filename = _display_filename(upload.filename)
    if Path(filename).suffix.lower() != ".pptx":
        raise PptxUploadRejected("pptx_extension_required")
    media_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if media_type != PPTX_MEDIA_TYPE:
        raise PptxUploadRejected("pptx_media_type_required")
    storage_key = f"{import_id}/source.pptx"
    target = resolve_private_source(storage_key)
    target.parent.mkdir(parents=True, exist_ok=False)
    temporary = target.with_suffix(".uploading")
    digest = hashlib.sha256()
    size = 0
    prefix = bytearray()
    try:
        with temporary.open("xb") as stream:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise PptxUploadRejected("pptx_upload_size_limit_exceeded")
                if len(prefix) < 4:
                    prefix.extend(chunk[: 4 - len(prefix)])
                digest.update(chunk)
                stream.write(chunk)
        if size == 0:
            raise PptxUploadRejected("empty_pptx_upload")
        if bytes(prefix) != b"PK\x03\x04":
            raise PptxUploadRejected("invalid_pptx_zip_signature")
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        try:
            target.parent.rmdir()
        except OSError:
            pass
        raise
    finally:
        await upload.close()
    return StoredPptx(
        display_filename=filename,
        media_type=media_type,
        size_bytes=size,
        sha256=digest.hexdigest(),
        storage_key=storage_key,
    )


def verify_private_source(storage_key: str, expected_sha256: str) -> Path:
    source = resolve_private_source(storage_key)
    if not source.is_file():
        raise PptxUploadRejected("private_source_missing")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise PptxUploadRejected("private_source_integrity_mismatch")
    return source


def remove_private_source(storage_key: str) -> None:
    """Remove an unreferenced source after a failed database transaction."""

    source = resolve_private_source(storage_key)
    source.unlink(missing_ok=True)
    try:
        source.parent.rmdir()
    except OSError:
        pass


def cleanup_private_source(storage_key: str) -> str:
    """Re-resolve and remove one retained source, returning an audit result."""

    source = resolve_private_source(storage_key)
    existed = source.is_file()
    source.unlink(missing_ok=True)
    try:
        source.parent.rmdir()
    except OSError:
        pass
    return "deleted" if existed else "already_missing"
