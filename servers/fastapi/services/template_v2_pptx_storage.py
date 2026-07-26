from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import hmac
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Mapping
import unicodedata
from urllib.parse import unquote, urlsplit
import uuid

from fastapi import UploadFile

from templates.v2.pptx.package_reader import PptxPackageReader, UnsafePptxPackage
from templates.v2.pptx.source_inventory import SecretFreeSourceMetadata
from utils.get_env import get_app_data_directory_env

logger = logging.getLogger(__name__)


MAX_PPTX_UPLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_PRIVATE_SOURCE_RETENTION_DAYS = 7
MIN_PRIVATE_SOURCE_RETENTION_DAYS = 1
MAX_PRIVATE_SOURCE_RETENTION_DAYS = 90
PRIVATE_SOURCE_RETENTION_DAYS_ENV = "TEMPLATE_V2_PPTX_SOURCE_TTL_DAYS"
PPTX_MALWARE_SCAN_MODE_ENV = "TEMPLATE_V2_PPTX_MALWARE_SCAN_MODE"
PPTX_MALWARE_SCANNER_ENV = "TEMPLATE_V2_PPTX_MALWARE_SCANNER"
PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
_SAFE_DISPLAY_FILENAME = re.compile(r"[^a-zA-Z0-9._ ()\-\u0080-\uffff]+")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_SOURCE_FILENAME = "source.pptx"
_MAX_DISPLAY_FILENAME_CHARACTERS = 240
# The export runtime writes `pptx-to-json` media into `<output_dir>/images/`, which sits
# inside the served /app_data mount. Relocating it under the import's own private
# directory keeps one retention path for the source deck and its extracted media.
PRIVATE_ASSET_URL_PREFIX = "/api/v1/ppt/structured-templates/imports"
_PRIVATE_ASSET_DIRECTORY = "assets"
_RUNTIME_OUTPUT_DIRECTORY = "pptx-to-json"
_RUNTIME_ASSET_DIRECTORY = "images"
_SAFE_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ASSET_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".svg",
    ".emf",
    ".wmf",
)


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


@dataclass(frozen=True)
class PrivateStorageHealth:
    ready: bool
    code: str

    def as_dict(self) -> dict[str, object]:
        return {
            "private_storage_ready": self.ready,
            "private_storage_code": self.code,
        }


@dataclass(frozen=True)
class MalwareScanHealth:
    ready: bool
    code: str
    mode: str

    def as_dict(self) -> dict[str, object]:
        return {
            "malware_scan_ready": self.ready,
            "malware_scan_code": self.code,
            "malware_scan_mode": self.mode,
        }


def _malware_scan_executable(
    environ: Mapping[str, str] | None = None,
    *,
    which=shutil.which,
) -> str | None:
    values = os.environ if environ is None else environ
    configured = (values.get(PPTX_MALWARE_SCANNER_ENV) or "").strip()
    return which(configured or "clamscan")


def get_malware_scan_health(
    environ: Mapping[str, str] | None = None,
    *,
    which=shutil.which,
) -> MalwareScanHealth:
    """Report whether the opt-in upload scanner can enforce its policy."""

    values = os.environ if environ is None else environ
    mode = (values.get(PPTX_MALWARE_SCAN_MODE_ENV) or "disabled").strip().lower()
    if mode not in {"disabled", "required"}:
        return MalwareScanHealth(
            ready=False,
            code="template_v2_pptx_malware_scan_mode_invalid",
            mode="invalid",
        )
    if mode == "disabled":
        return MalwareScanHealth(
            ready=True,
            code="template_v2_pptx_malware_scan_disabled",
            mode=mode,
        )
    if _malware_scan_executable(values, which=which) is None:
        return MalwareScanHealth(
            ready=False,
            code="template_v2_pptx_malware_scanner_unavailable",
            mode=mode,
        )
    return MalwareScanHealth(
        ready=True,
        code="template_v2_pptx_malware_scan_required",
        mode=mode,
    )


def scan_private_pptx(
    source: Path,
    *,
    environ: Mapping[str, str] | None = None,
    runner=subprocess.run,
    which=shutil.which,
) -> str:
    """Scan a staged upload before it is promoted into retained private storage."""

    values = os.environ if environ is None else environ
    health = get_malware_scan_health(values, which=which)
    if health.mode == "disabled" and health.ready:
        return health.code
    if not health.ready:
        raise PptxUploadRejected(health.code)
    executable = _malware_scan_executable(values, which=which)
    if executable is None:  # Defensive: health and execution must agree.
        raise PptxUploadRejected("template_v2_pptx_malware_scanner_unavailable")
    try:
        result = runner(
            [executable, "--no-summary", "--infected", str(source)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PptxUploadRejected(
            "template_v2_pptx_malware_scan_failed"
        ) from error
    if result.returncode == 0:
        return "template_v2_pptx_malware_scan_clean"
    if result.returncode == 1:
        raise PptxUploadRejected("template_v2_pptx_malware_detected")
    raise PptxUploadRejected("template_v2_pptx_malware_scan_failed")


def get_private_storage_health() -> PrivateStorageHealth:
    """Inspect the private volume without reading or creating customer files."""

    try:
        root = private_import_root()
    except RuntimeError as error:
        return PrivateStorageHealth(ready=False, code=str(error))
    if not root.is_dir():
        return PrivateStorageHealth(
            ready=False,
            code="template_v2_private_storage_missing",
        )
    if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        return PrivateStorageHealth(
            ready=False,
            code="template_v2_private_storage_not_writable",
        )
    return PrivateStorageHealth(
        ready=True,
        code="template_v2_private_storage_ready",
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


def _asset_name(asset_name: str) -> str:
    if (
        not isinstance(asset_name, str)
        or _SAFE_ASSET_NAME.fullmatch(asset_name) is None
        or not asset_name.lower().endswith(_ASSET_SUFFIXES)
    ):
        raise PptxUploadRejected("unsafe_runtime_asset_name")
    return asset_name


def private_asset_reference(import_id: uuid.UUID, asset_name: str) -> str:
    """Build the owner-scoped reference an import-assets endpoint can serve."""

    if not isinstance(import_id, uuid.UUID):
        raise TypeError("import_id_must_be_uuid")
    return (
        f"{PRIVATE_ASSET_URL_PREFIX}/{import_id}"
        f"/{_PRIVATE_ASSET_DIRECTORY}/{_asset_name(asset_name)}"
    )


def _asset_reference_parts(reference: str) -> tuple[uuid.UUID, str]:
    prefix = f"{PRIVATE_ASSET_URL_PREFIX}/"
    if not isinstance(reference, str) or not reference.startswith(prefix):
        raise PptxUploadRejected("invalid_private_asset_reference")
    parts = reference[len(prefix):].split("/")
    if len(parts) != 3 or parts[1] != _PRIVATE_ASSET_DIRECTORY:
        raise PptxUploadRejected("invalid_private_asset_reference")
    try:
        owner = uuid.UUID(parts[0])
    except (AttributeError, ValueError) as error:
        raise PptxUploadRejected("invalid_private_asset_reference") from error
    if str(owner) != parts[0]:
        raise PptxUploadRejected("invalid_private_asset_reference")
    return owner, _asset_name(parts[2])


def _private_asset_directory(owner: uuid.UUID) -> Path:
    root = private_import_root().resolve()
    owner_directory = root / str(owner)
    lexical_candidate = owner_directory / _PRIVATE_ASSET_DIRECTORY
    if owner_directory.is_symlink() or lexical_candidate.is_symlink():
        raise PptxUploadRejected("private_storage_symlink_forbidden")
    candidate = lexical_candidate.resolve()
    if not candidate.is_relative_to(root):
        raise PptxUploadRejected("private_storage_path_escape")
    return candidate


def resolve_private_asset(
    reference: str,
    *,
    expected_import_id: uuid.UUID | None = None,
) -> Path:
    """Translate an owner-scoped asset reference back into its private path.

    `pptx-to-json` emits URLs but `json-to-image` and every server-side render need
    real filesystem paths, and an unreachable asset renders as a silently blank
    region -- so the translation lives here, beside the private-root layout.
    """

    owner, asset_name = _asset_reference_parts(reference)
    if expected_import_id is not None and owner != expected_import_id:
        raise PptxUploadRejected("private_storage_owner_mismatch")
    directory = _private_asset_directory(owner)
    lexical_candidate = directory / asset_name
    if lexical_candidate.is_symlink():
        raise PptxUploadRejected("private_storage_symlink_forbidden")
    candidate = lexical_candidate.resolve()
    if not candidate.is_relative_to(private_import_root().resolve()):
        raise PptxUploadRejected("private_storage_path_escape")
    return candidate


@dataclass(frozen=True)
class RelocatedRuntimeAssets:
    """Runtime-extracted media now owned by one import's private directory."""

    import_id: uuid.UUID
    asset_names: tuple[str, ...]

    def reference_for(self, runtime_url: str) -> str | None:
        """Map one emitted `pptx-to-json` asset URL onto its private reference."""

        if not isinstance(runtime_url, str):
            return None
        leaf = unquote(urlsplit(runtime_url).path).rsplit("/", 1)[-1]
        if leaf not in self.asset_names:
            return None
        return private_asset_reference(self.import_id, leaf)


def _runtime_output_root() -> Path:
    """Return the directory the export runtime allocates its `pptx-to-json` runs under."""

    raw_app_data = (get_app_data_directory_env() or "").strip()
    if not raw_app_data:
        raise RuntimeError("app_data_directory_required")
    return Path(raw_app_data).resolve() / _RUNTIME_OUTPUT_DIRECTORY


def _discard_runtime_output(run_directory: Path) -> None:
    """Drop the converter's run directory once its media has been taken over.

    It also holds `presentation.json` -- the full extracted text of the deck -- and
    the bundled runtime only removes it on failure, so leaving it would keep the
    deck's most content-rich derived artefact outside the tree retention manages,
    surviving the very cleanup that deletes the source.
    """

    try:
        if run_directory.is_symlink() or not run_directory.is_dir():
            return
        shutil.rmtree(run_directory)
    except OSError:
        # Best effort: a leftover run directory is untidy, not incorrect, and must
        # never fail an import that has already produced a valid template.
        logger.warning("Could not remove Template V2 converter run directory")


def relocate_runtime_assets(
    output_directory: str | Path,
    *,
    import_id: uuid.UUID,
) -> RelocatedRuntimeAssets:
    """Move runtime-extracted media off the served mount into the import's tree."""

    if not isinstance(import_id, uuid.UUID):
        raise TypeError("import_id_must_be_uuid")
    runtime_root = Path(output_directory)
    if not runtime_root.is_dir():
        raise PptxUploadRejected("runtime_output_directory_missing")
    # The `finally` below deletes this directory, so establish ownership before arming
    # it: `output_dir` defaults to "" in the converter's response model, and Path("")
    # is the process working directory, which is a directory and would be removed.
    if runtime_root.resolve().parent != _runtime_output_root():
        raise PptxUploadRejected("runtime_output_directory_untrusted")
    try:
        runtime_media = runtime_root / _RUNTIME_ASSET_DIRECTORY
        if runtime_media.is_symlink():
            raise PptxUploadRejected("runtime_asset_symlink_forbidden")
        if not runtime_media.is_dir():
            return RelocatedRuntimeAssets(import_id=import_id, asset_names=())
        asset_names: list[str] = []
        for entry in sorted(runtime_media.iterdir()):
            if entry.is_symlink():
                raise PptxUploadRejected("runtime_asset_symlink_forbidden")
            if not entry.is_file():
                raise PptxUploadRejected("unsupported_runtime_asset_entry")
            asset_names.append(_asset_name(entry.name))
        _private_asset_directory(import_id).mkdir(parents=True, exist_ok=True)
        for asset_name in asset_names:
            target = resolve_private_asset(
                private_asset_reference(import_id, asset_name),
                expected_import_id=import_id,
            )
            # Unique per call: a lapsed lease can leave two attempts for the same
            # import relocating concurrently into this directory, and a fixed name
            # let them interleave writes into one temp file and publish the mixed
            # result, or let one attempt's cleanup delete the other's file mid-flight.
            temporary = target.with_name(f"{asset_name}.{uuid.uuid4().hex}.relocating")
            try:
                shutil.copyfile(runtime_media / asset_name, temporary)
                temporary.replace(target)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            (runtime_media / asset_name).unlink()
        return RelocatedRuntimeAssets(
            import_id=import_id,
            asset_names=tuple(asset_names),
        )
    finally:
        # Once the directory exists this call owns it, so every exit discards it --
        # a rejected entry or a failed copy would otherwise strand the run's
        # `presentation.json` on the served mount for good, since the bundled
        # runtime only removes the directory when the converter itself fails.
        _discard_runtime_output(runtime_root)


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
        scan_private_pptx(temporary)
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


def _remove_private_assets(owner: uuid.UUID) -> None:
    directory = _private_asset_directory(owner)
    if not directory.is_dir():
        return
    for entry in directory.iterdir():
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
    try:
        directory.rmdir()
    except OSError:
        pass


def cleanup_private_import(storage_key: str) -> str:
    """Re-resolve and remove one retained import tree, returning an audit result.

    Relocated runtime assets live beside the source deck under the same per-import
    directory, so this single pass reclaims both -- no second asset location that a
    cleanup query could miss. The result reports the source, which is what the
    retention row tracks.
    """

    source = resolve_private_source(storage_key)
    existed = source.is_file()
    source.unlink(missing_ok=True)
    _remove_private_assets(_storage_key_owner(storage_key))
    try:
        source.parent.rmdir()
    except OSError:
        pass
    return "deleted" if existed else "already_missing"
