from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import hmac
import os
from pathlib import Path
import re
import unicodedata
import uuid

from fastapi import UploadFile

from templates.v2.pptx.package_reader import PptxPackageReader, UnsafePptxPackage
from templates.v2.pptx.source_inventory import SecretFreeSourceMetadata
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
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_SOURCE_FILENAME = "source.pptx"
_MAX_DISPLAY_FILENAME_CHARACTERS = 240


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

    def secret_free_metadata(self) -> SecretFreeSourceMetadata:
        return SecretFreeSourceMetadata(
            display_filename=self.display_filename,
            media_type=self.media_type,
            size_bytes=self.size_bytes,
            sha256=self.sha256,
        )


def get_private_source_retention_ttl() -> timedelta:
    raw = os.getenv(PRIVATE_SOURCE_RETENTION_DAYS_ENV)
    if raw is None or not raw.strip():
        days = DEFAULT_PRIVATE_SOURCE_RETENTION_DAYS
    else:
        value = raw.strip()
        if not value.isascii() or not value.isdecimal():
            raise RuntimeError("invalid_template_v2_pptx_source_ttl_days")
        days = int(value)
    if not (
        MIN_PRIVATE_SOURCE_RETENTION_DAYS
        <= days
        <= MAX_PRIVATE_SOURCE_RETENTION_DAYS
    ):
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
    root = app_data.parent / f"{app_data.name}-private" / "template-v2-imports"
    if root.is_symlink():
        raise RuntimeError("unsafe_private_import_root")
    return root


def private_source_storage_key(import_id: uuid.UUID) -> str:
    if not isinstance(import_id, uuid.UUID):
        raise TypeError("import_id_must_be_uuid")
    return f"{import_id}/{_PRIVATE_SOURCE_FILENAME}"


def _storage_key_owner(storage_key: str) -> uuid.UUID:
    if not isinstance(storage_key, str):
        raise PptxUploadRejected("invalid_private_storage_key")
    parts = storage_key.split("/")
    if len(parts) != 2 or parts[1] != _PRIVATE_SOURCE_FILENAME:
        raise PptxUploadRejected("invalid_private_storage_key")
    try:
        owner = uuid.UUID(parts[0])
    except (AttributeError, ValueError) as error:
        raise PptxUploadRejected("invalid_private_storage_key") from error
    if str(owner) != parts[0] or storage_key != private_source_storage_key(owner):
        raise PptxUploadRejected("invalid_private_storage_key")
    return owner


def resolve_private_source(
    storage_key: str,
    *,
    expected_import_id: uuid.UUID | None = None,
) -> Path:
    owner = _storage_key_owner(storage_key)
    if expected_import_id is not None and owner != expected_import_id:
        raise PptxUploadRejected("private_storage_owner_mismatch")
    root = private_import_root().resolve()
    owner_directory = root / str(owner)
    lexical_candidate = owner_directory / _PRIVATE_SOURCE_FILENAME
    if owner_directory.is_symlink() or lexical_candidate.is_symlink():
        raise PptxUploadRejected("private_storage_symlink_forbidden")
    candidate = lexical_candidate.resolve()
    if not candidate.is_relative_to(root):
        raise PptxUploadRejected("private_storage_path_escape")
    return candidate


def _display_filename(filename: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", filename or _PRIVATE_SOURCE_FILENAME)
    normalized = "".join(
        "_"
        if unicodedata.category(character).startswith("C")
        else character
        for character in normalized
    )
    leaf = normalized.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _SAFE_DISPLAY_FILENAME.sub("_", leaf).strip(" .")
    cleaned = cleaned or _PRIVATE_SOURCE_FILENAME
    if len(cleaned) > _MAX_DISPLAY_FILENAME_CHARACTERS:
        suffix = Path(cleaned).suffix
        cleaned = (
            cleaned[: _MAX_DISPLAY_FILENAME_CHARACTERS - len(suffix)] + suffix
        )
    return cleaned


def _effective_upload_limit(max_bytes: int) -> int:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("invalid_pptx_upload_size_limit")
    return min(max_bytes, MAX_PPTX_UPLOAD_BYTES)


async def store_private_pptx(
    upload: UploadFile,
    *,
    import_id: uuid.UUID,
    max_bytes: int = MAX_PPTX_UPLOAD_BYTES,
) -> StoredPptx:
    upload_limit = _effective_upload_limit(max_bytes)
    filename = _display_filename(upload.filename)
    if Path(filename).suffix.lower() != ".pptx":
        raise PptxUploadRejected("pptx_extension_required")
    media_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if media_type != PPTX_MEDIA_TYPE:
        raise PptxUploadRejected("pptx_media_type_required")
    storage_key = private_source_storage_key(import_id)
    target = resolve_private_source(storage_key, expected_import_id=import_id)
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
                if size > upload_limit:
                    raise PptxUploadRejected("pptx_upload_size_limit_exceeded")
                if len(prefix) < 4:
                    prefix.extend(chunk[: 4 - len(prefix)])
                digest.update(chunk)
                stream.write(chunk)
        if size == 0:
            raise PptxUploadRejected("empty_pptx_upload")
        if bytes(prefix) != b"PK\x03\x04":
            raise PptxUploadRejected("invalid_pptx_zip_signature")
        try:
            PptxPackageReader(temporary).preflight()
        except UnsafePptxPackage as error:
            raise PptxUploadRejected(error.code) from error
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


def verify_private_source(
    storage_key: str,
    expected_sha256: str,
    *,
    expected_import_id: uuid.UUID | None = None,
    expected_size_bytes: int | None = None,
    max_bytes: int = MAX_PPTX_UPLOAD_BYTES,
) -> Path:
    if (
        not isinstance(expected_sha256, str)
        or _SHA256.fullmatch(expected_sha256) is None
    ):
        raise PptxUploadRejected("invalid_source_sha256")
    if expected_size_bytes is not None and (
        isinstance(expected_size_bytes, bool)
        or not isinstance(expected_size_bytes, int)
        or expected_size_bytes <= 0
    ):
        raise PptxUploadRejected("invalid_source_size_bytes")
    source_limit = _effective_upload_limit(max_bytes)
    source = resolve_private_source(
        storage_key,
        expected_import_id=expected_import_id,
    )
    if not source.is_file():
        raise PptxUploadRejected("private_source_missing")
    source_size = source.stat().st_size
    if source_size > source_limit:
        raise PptxUploadRejected("private_source_size_limit_exceeded")
    if expected_size_bytes is not None and source_size != expected_size_bytes:
        raise PptxUploadRejected("private_source_size_mismatch")
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > source_limit:
                raise PptxUploadRejected("private_source_size_limit_exceeded")
            digest.update(chunk)
    if size != source_size:
        raise PptxUploadRejected("private_source_changed_during_verification")
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
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
