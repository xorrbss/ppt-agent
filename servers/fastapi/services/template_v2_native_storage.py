"""Durable, integrity-checked storage for Template V2 native source decks."""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import uuid

from utils.get_env import get_app_data_directory_env


NATIVE_SOURCE_SCHEMA_VERSION = "presenton.template-v2-native-source/v1"
MAX_NATIVE_SOURCE_BYTES = 200 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TEMPLATE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class TemplateV2NativeStorageError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def native_source_storage_key(template_id: str, revision: int) -> str:
    _validate_scope(template_id, revision)
    return f"template-v2/native/{template_id}/revision-{revision}/source.pptx"


def native_source_root() -> Path:
    raw = (get_app_data_directory_env() or "").strip()
    if not raw:
        raise TemplateV2NativeStorageError("app_data_directory_required")
    return Path(raw).resolve() / "template-v2" / "native"


def promote_native_source(
    source: Path,
    *,
    template_id: str,
    revision: int,
    expected_sha256: str,
    expected_size_bytes: int,
) -> str:
    """Atomically retain a verified import source under its exact template scope."""

    storage_key = native_source_storage_key(template_id, revision)
    _validate_digest_and_size(expected_sha256, expected_size_bytes)
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise TemplateV2NativeStorageError("template_v2_native_source_missing")
    _verify_file(source, expected_sha256, expected_size_bytes)

    target = _resolve_scoped_key(
        storage_key,
        template_id=template_id,
        revision=revision,
        require_exists=False,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"source.pptx.{uuid.uuid4().hex}.promoting")
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        _verify_file(temporary, expected_sha256, expected_size_bytes)
        os.replace(temporary, target)
        _verify_file(target, expected_sha256, expected_size_bytes)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return storage_key


def resolve_native_source(
    storage_key: str,
    *,
    template_id: str,
    revision: int,
    expected_sha256: str,
    expected_size_bytes: int,
) -> Path:
    """Resolve only the canonical template/revision key and verify its contents."""

    _validate_digest_and_size(expected_sha256, expected_size_bytes)
    source = _resolve_scoped_key(
        storage_key,
        template_id=template_id,
        revision=revision,
        require_exists=True,
    )
    _verify_file(source, expected_sha256, expected_size_bytes)
    return source


def read_native_source(
    storage_key: str,
    *,
    template_id: str,
    revision: int,
    expected_sha256: str,
    expected_size_bytes: int,
) -> bytes:
    """Read and verify in one pass so compilation never consumes unchecked bytes."""

    source = _resolve_scoped_key(
        storage_key,
        template_id=template_id,
        revision=revision,
        require_exists=True,
    )
    _validate_digest_and_size(expected_sha256, expected_size_bytes)
    digest = hashlib.sha256()
    payload = bytearray()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            payload.extend(chunk)
            if len(payload) > MAX_NATIVE_SOURCE_BYTES:
                raise TemplateV2NativeStorageError("template_v2_native_source_size_invalid")
            digest.update(chunk)
    if len(payload) != expected_size_bytes:
        raise TemplateV2NativeStorageError("template_v2_native_source_size_mismatch")
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
        raise TemplateV2NativeStorageError("template_v2_native_source_integrity_mismatch")
    return bytes(payload)


def remove_native_source(
    storage_key: str,
    *,
    template_id: str,
    revision: int,
    storage_root: Path | None = None,
) -> None:
    """Remove only one canonical package after a failed owning transaction."""

    target = _resolve_scoped_key(
        storage_key,
        template_id=template_id,
        revision=revision,
        require_exists=False,
        storage_root=storage_root,
    )
    target.unlink(missing_ok=True)
    for directory in (target.parent, target.parent.parent):
        try:
            directory.rmdir()
        except OSError:
            break


def _validate_scope(template_id: str, revision: int) -> None:
    if not isinstance(template_id, str) or _SAFE_TEMPLATE_ID.fullmatch(template_id) is None:
        raise TemplateV2NativeStorageError("template_v2_native_template_scope_invalid")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
        raise TemplateV2NativeStorageError("template_v2_native_revision_scope_invalid")


def _validate_digest_and_size(expected_sha256: str, expected_size_bytes: int) -> None:
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise TemplateV2NativeStorageError("template_v2_native_source_sha256_invalid")
    if (
        isinstance(expected_size_bytes, bool)
        or not isinstance(expected_size_bytes, int)
        or expected_size_bytes <= 0
        or expected_size_bytes > MAX_NATIVE_SOURCE_BYTES
    ):
        raise TemplateV2NativeStorageError("template_v2_native_source_size_invalid")


def _resolve_scoped_key(
    storage_key: str,
    *,
    template_id: str,
    revision: int,
    require_exists: bool,
    storage_root: Path | None = None,
) -> Path:
    expected = native_source_storage_key(template_id, revision)
    if not isinstance(storage_key, str) or storage_key != expected:
        raise TemplateV2NativeStorageError("template_v2_native_storage_scope_mismatch")
    pure = PurePosixPath(storage_key)
    if pure.is_absolute() or ".." in pure.parts or "\\" in storage_key:
        raise TemplateV2NativeStorageError("template_v2_native_storage_key_invalid")

    root = (
        Path(storage_root).resolve(strict=False)
        if storage_root is not None
        else native_source_root().resolve()
    )
    if not root.is_absolute() or root == Path(root.anchor):
        raise TemplateV2NativeStorageError(
            "template_v2_native_storage_root_invalid"
        )
    relative = Path(*pure.parts[2:])
    lexical = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise TemplateV2NativeStorageError("template_v2_native_storage_symlink_forbidden")
    resolved = lexical.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise TemplateV2NativeStorageError("template_v2_native_storage_path_escape")
    if require_exists and not resolved.is_file():
        raise TemplateV2NativeStorageError("template_v2_native_source_missing")
    return resolved


def _verify_file(path: Path, expected_sha256: str, expected_size_bytes: int) -> None:
    if path.is_symlink() or not path.is_file():
        raise TemplateV2NativeStorageError("template_v2_native_source_missing")
    stat_size = path.stat().st_size
    if stat_size != expected_size_bytes:
        raise TemplateV2NativeStorageError("template_v2_native_source_size_mismatch")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > MAX_NATIVE_SOURCE_BYTES:
                raise TemplateV2NativeStorageError("template_v2_native_source_size_invalid")
            digest.update(chunk)
    if size != stat_size:
        raise TemplateV2NativeStorageError("template_v2_native_source_changed")
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
        raise TemplateV2NativeStorageError("template_v2_native_source_integrity_mismatch")


__all__ = [
    "MAX_NATIVE_SOURCE_BYTES",
    "NATIVE_SOURCE_SCHEMA_VERSION",
    "TemplateV2NativeStorageError",
    "native_source_root",
    "native_source_storage_key",
    "promote_native_source",
    "read_native_source",
    "remove_native_source",
    "resolve_native_source",
]
