"""Durable, owner-scoped workspace asset lifecycle and cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import mimetypes
import os
import shutil
import subprocess
import unicodedata
import uuid
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path, PurePosixPath

from fastapi import HTTPException, UploadFile
from sqlalchemy import and_, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from constants.documents import (
    IMAGE_EXTENSIONS,
    OFFICE_EXTENSIONS,
    PDF_EXTENSIONS,
    TEXT_EXTENSIONS,
    UPLOAD_ACCEPTED_EXTENSIONS,
    UPLOAD_ACCEPTED_MIME_TYPES,
)
from models.sql.workspace import (
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceOperation,
    get_workspace_owner_scope,
)
from models.workspace import WorkspaceAssetResponse, WorkspaceAssetTransitionRequest
from services.account_deletion_service import (
    AccountDeletionFenceConflict,
    require_active_account_generation,
)
from services.workspace_service import (
    WorkspaceServiceError,
    _asset_block_error,
    _asset_operation_state,
    _enqueue_event_notification,
    _operation_asset_ids,
    _owner_id_predicate,
    append_workspace_operation_event,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.document_path_security import validated_upload_extension
from utils.path_helpers import get_writable_path
from utils.upload_limits import get_single_upload_limit_bytes, stream_upload_to_file
from utils.owner_context import get_current_owner_id

_ASSET_TRANSITIONS: dict[str, frozenset[str]] = {
    "uploaded": frozenset(
        {"scanning", "processing", "failed", "quarantined", "expired", "deleted"}
    ),
    "scanning": frozenset(
        {"processing", "ready", "failed", "quarantined", "expired", "deleted"}
    ),
    "processing": frozenset({"ready", "failed", "quarantined", "expired", "deleted"}),
    "ready": frozenset({"quarantined", "expired", "deleted"}),
    "failed": frozenset({"uploaded", "expired", "deleted"}),
    "quarantined": frozenset({"expired", "deleted"}),
    "expired": frozenset({"deleted"}),
    "deleted": frozenset(),
}
_OPERATION_ASSET_STATES = {
    "queued",
    "waiting_for_assets",
    "blocked",
    "awaiting_approval",
}
_GENERIC_UPLOAD_MIME_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
_MALWARE_SCAN_MODE_ENV = "WORKSPACE_ASSET_MALWARE_SCAN_MODE"
_MALWARE_SCANNER_ENV = "WORKSPACE_ASSET_MALWARE_SCANNER"
_DEPLOYMENT_TIER_ENV = "TEMPLATE_V2_DEPLOYMENT_TIER"
_LOCAL_DEPLOYMENT_TIERS = frozenset({"local", "development", "test"})
_MANAGED_DEPLOYMENT_TIERS = frozenset({"staging", "production"})
_KNOWN_DEPLOYMENT_TIERS = _LOCAL_DEPLOYMENT_TIERS | _MANAGED_DEPLOYMENT_TIERS
_MIME_BY_EXTENSION = {
    ".pdf": {"application/pdf"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".markdown": {"text/markdown", "text/plain"},
    ".csv": {"text/csv", "application/csv", "text/plain"},
    ".tsv": {"text/tab-separated-values", "text/tsv", "text/plain"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
    ".gif": {"image/gif"},
    ".bmp": {"image/bmp"},
    ".tif": {"image/tiff"},
    ".tiff": {"image/tiff"},
    ".webp": {"image/webp"},
    ".svg": {"image/svg+xml", "text/xml", "application/xml"},
    ".doc": {"application/msword"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    },
    ".docm": {"application/vnd.ms-word.document.macroenabled.12"},
    ".odt": {"application/vnd.oasis.opendocument.text"},
    ".rtf": {"application/rtf", "text/rtf"},
    ".ppt": {"application/vnd.ms-powerpoint"},
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    },
    ".pptm": {"application/vnd.ms-powerpoint.presentation.macroenabled.12"},
    ".odp": {"application/vnd.oasis.opendocument.presentation"},
    ".xls": {"application/vnd.ms-excel"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".xlsm": {"application/vnd.ms-excel.sheet.macroenabled.12"},
    ".ods": {"application/vnd.oasis.opendocument.spreadsheet"},
}


async def _require_active_asset_owner(session: AsyncSession) -> int | None:
    """Fence upload/worker publication against durable account deletion."""

    owner_id = get_current_owner_id()
    if owner_id is None:
        return None
    try:
        return await require_active_account_generation(
            session,
            user_id=owner_id,
        )
    except AccountDeletionFenceConflict as error:
        raise WorkspaceServiceError(
            "account_deletion_in_progress",
            status_code=409,
            retryable=False,
        ) from error


def normalize_workspace_asset_name(value: str | None) -> str:
    """Return a display-only basename with controls and path components removed."""

    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = PurePosixPath(normalized.replace("\\", "/")).name
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character)[0] != "C"
    ).strip(" .")
    if not normalized:
        normalized = "attachment"
    stem, extension = os.path.splitext(normalized)
    if len(normalized) > 512:
        normalized = f"{stem[: max(1, 512 - len(extension))]}{extension}"
    if normalized.upper().split(".", 1)[0] in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }:
        normalized = f"_{normalized}"
    return normalized


def _asset_expiry(now: datetime) -> datetime:
    try:
        seconds = int(os.getenv("WORKSPACE_ASSET_ORPHAN_TTL_SECONDS", "86400"))
    except ValueError:
        seconds = 86400
    return now + timedelta(seconds=max(300, min(seconds, 30 * 86400)))


def _validate_declared_mime(extension: str, content_type: str | None) -> str | None:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type in _GENERIC_UPLOAD_MIME_TYPES:
        guessed = mimetypes.guess_type(f"upload{extension}")[0]
        return guessed or None
    if media_type not in set(UPLOAD_ACCEPTED_MIME_TYPES):
        raise WorkspaceServiceError(
            "workspace_asset_media_type_invalid", status_code=400
        )
    allowed = _MIME_BY_EXTENSION.get(extension)
    if allowed is not None and media_type not in allowed:
        raise WorkspaceServiceError(
            "workspace_asset_media_type_mismatch", status_code=400
        )
    return media_type


def _content_sha256_and_signature(path: Path, extension: str) -> str:
    digest = sha256()
    first = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if not first:
                first = chunk[:4096]
            digest.update(chunk)
    if not first:
        raise WorkspaceServiceError("workspace_asset_empty", status_code=400)
    lower = first.lstrip().lower()
    ole = first.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    zipped = first.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
    valid = True
    if extension in PDF_EXTENSIONS:
        valid = first.startswith(b"%PDF-")
    elif extension in {
        ".docx",
        ".docm",
        ".pptx",
        ".pptm",
        ".xlsx",
        ".xlsm",
        ".odt",
        ".odp",
        ".ods",
    }:
        valid = zipped
    elif extension in {".doc", ".ppt", ".xls"}:
        valid = ole
    elif extension == ".rtf":
        valid = lower.startswith(b"{\\rtf")
    elif extension in {".jpg", ".jpeg"}:
        valid = first.startswith(b"\xff\xd8\xff")
    elif extension == ".png":
        valid = first.startswith(b"\x89PNG\r\n\x1a\n")
    elif extension == ".gif":
        valid = first.startswith((b"GIF87a", b"GIF89a"))
    elif extension == ".bmp":
        valid = first.startswith(b"BM")
    elif extension in {".tif", ".tiff"}:
        valid = first.startswith((b"II*\x00", b"MM\x00*"))
    elif extension == ".webp":
        valid = first.startswith(b"RIFF") and first[8:12] == b"WEBP"
    elif extension == ".svg":
        valid = b"<svg" in lower and b"\x00" not in first
    elif extension in TEXT_EXTENSIONS or extension in {".csv", ".tsv"}:
        valid = b"\x00" not in first
    elif extension in OFFICE_EXTENSIONS:
        valid = zipped or ole
    elif extension in IMAGE_EXTENSIONS:
        valid = False
    if not valid:
        raise WorkspaceServiceError(
            "workspace_asset_signature_mismatch", status_code=400
        )
    return digest.hexdigest()


def _scan_workspace_asset(path: Path) -> None:
    """Run an optional fail-closed malware scan before immutable promotion."""

    mode = os.getenv(_MALWARE_SCAN_MODE_ENV, "disabled").strip().lower()
    if mode == "disabled":
        return
    if mode != "required":
        raise WorkspaceServiceError(
            "workspace_asset_malware_scan_mode_invalid",
            status_code=503,
            retryable=True,
        )
    configured = os.getenv(_MALWARE_SCANNER_ENV, "").strip()
    executable = shutil.which(configured or "clamscan")
    if executable is None:
        raise WorkspaceServiceError(
            "workspace_asset_malware_scanner_unavailable",
            status_code=503,
            retryable=True,
        )
    try:
        result = subprocess.run(
            [executable, "--no-summary", "--infected", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceServiceError(
            "workspace_asset_malware_scan_failed",
            status_code=503,
            retryable=True,
        ) from error
    if result.returncode == 0:
        return
    if result.returncode == 1:
        raise WorkspaceServiceError(
            "workspace_asset_malware_detected",
            status_code=400,
        )
    raise WorkspaceServiceError(
        "workspace_asset_malware_scan_failed",
        status_code=503,
        retryable=True,
    )


def require_workspace_asset_malware_scan_safety(
    *,
    feature_enabled: bool,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> None:
    """Fail startup when an enabled chat-first deployment cannot scan assets.

    ``TEMPLATE_V2_DEPLOYMENT_TIER`` is the repository-wide deployment profile
    already used by managed release gates.  Local, development, and test
    profiles retain the opt-out needed for desktop and developer workflows;
    staging and production require an explicitly configured, executable
    scanner.  A disabled feature bypasses this validation so rollback does not
    depend on scanner availability.
    """

    if not feature_enabled:
        return

    values = os.environ if environ is None else environ
    tier = (values.get(_DEPLOYMENT_TIER_ENV) or "local").strip().lower()
    if tier not in _KNOWN_DEPLOYMENT_TIERS:
        raise RuntimeError("chat_first_deployment_tier_invalid")

    configured_mode = values.get(_MALWARE_SCAN_MODE_ENV)
    mode = (
        "disabled"
        if configured_mode is None
        else configured_mode.strip().lower()
    )
    if mode not in {"disabled", "required"}:
        raise RuntimeError("workspace_asset_malware_scan_mode_invalid")
    if tier in _MANAGED_DEPLOYMENT_TIERS and mode != "required":
        raise RuntimeError("chat_first_asset_malware_scan_required")
    if mode == "disabled":
        return

    configured_scanner = (values.get(_MALWARE_SCANNER_ENV) or "").strip()
    resolve_executable = shutil.which if which is None else which
    if resolve_executable(configured_scanner or "clamscan") is None:
        raise RuntimeError("workspace_asset_malware_scanner_unavailable")


async def inspect_workspace_document_asset(
    *, source_path: str, original_name: str
) -> tuple[str, str | None, int, str, str]:
    """Return normalized, content-derived metadata for a validated document."""

    source = Path(source_path).resolve(strict=True)
    display_name = normalize_workspace_asset_name(original_name)
    try:
        extension = validated_upload_extension(
            display_name,
            UPLOAD_ACCEPTED_EXTENSIONS,
        )
    except HTTPException as error:
        raise WorkspaceServiceError(
            "workspace_asset_file_type_invalid", status_code=400
        ) from error
    media_type = _validate_declared_mime(extension, None)
    size_bytes = source.stat().st_size
    digest = await asyncio.to_thread(_content_sha256_and_signature, source, extension)
    return display_name, media_type, size_bytes, digest, extension


async def import_workspace_document_asset(
    session: AsyncSession,
    *,
    source_path: str,
    original_name: str,
    storage_root: Path | None = None,
) -> tuple[WorkspaceAsset, Path]:
    """Copy a validated legacy document into immutable workspace storage.

    The caller owns the surrounding transaction and must remove the returned
    path if that transaction is rolled back after this function returns.
    """

    source = Path(source_path).resolve(strict=True)
    display_name, media_type, size_bytes, digest, extension = (
        await inspect_workspace_document_asset(
            source_path=source_path,
            original_name=original_name,
        )
    )
    root = (storage_root or Path(get_writable_path("workspace_assets"))).resolve()
    asset_id = uuid.uuid4()
    asset_dir = (root / get_workspace_owner_scope() / str(asset_id)).resolve()
    if root not in asset_dir.parents:
        raise WorkspaceServiceError("workspace_asset_storage_invalid", status_code=500)
    asset_dir.mkdir(parents=True, exist_ok=False)
    staging_path = asset_dir / "import.part"
    final_path: Path | None = None
    try:
        await asyncio.to_thread(shutil.copyfile, source, staging_path)
        copied_digest = await asyncio.to_thread(
            _content_sha256_and_signature, staging_path, extension
        )
        if copied_digest != digest:
            raise WorkspaceServiceError(
                "workspace_asset_source_changed", status_code=409, retryable=True
            )
        await asyncio.to_thread(_scan_workspace_asset, staging_path)
        final_path = asset_dir / f"{digest}{extension}"
        await asyncio.to_thread(os.replace, staging_path, final_path)
        now = get_current_utc_datetime()
        asset = WorkspaceAsset(
            id=asset_id,
            state="ready",
            original_name=display_name,
            media_type=media_type,
            size_bytes=size_bytes,
            storage_key=final_path.relative_to(root).as_posix(),
            sha256=digest,
            expires_at=_asset_expiry(now),
            created_at=now,
            updated_at=now,
        )
        session.add(asset)
        await session.flush()
        return asset, final_path
    except Exception:
        for path in (staging_path, final_path):
            if path is not None:
                path.unlink(missing_ok=True)
        try:
            asset_dir.rmdir()
        except OSError:
            pass
        raise
def workspace_asset_response(asset: WorkspaceAsset) -> WorkspaceAssetResponse:
    return WorkspaceAssetResponse(
        asset_id=asset.id,
        file_name=asset.original_name,
        media_type=asset.media_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        status=asset.state,
        version=asset.version,
        failure_code=asset.failure_code,
        expires_at=asset.expires_at,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


async def resolve_workspace_asset_download(
    session: AsyncSession,
    *,
    asset_id: uuid.UUID,
    storage_root: Path | None = None,
) -> tuple[WorkspaceAsset, Path]:
    """Authorize and resolve one immutable attachment without exposing its key.

    Owner filtering happens before lifecycle or filesystem inspection, so an
    asset belonging to another account is indistinguishable from a missing id.
    Only live ``ready`` assets may be downloaded; preview is intentionally not
    offered because active-content formats require a separate sanitization
    pipeline.
    """

    asset = await get_workspace_asset(session, asset_id=asset_id)
    now = get_current_utc_datetime()
    state_error = {
        "failed": ("workspace_asset_failed", True),
        "quarantined": ("workspace_asset_quarantined", False),
        "expired": ("workspace_asset_expired", False),
        "deleted": ("workspace_asset_deleted", False),
    }.get(asset.state)
    if state_error is not None:
        code, retryable = state_error
        raise WorkspaceServiceError(
            code,
            status_code=409,
            retryable=retryable,
        )
    expires_at = asset.expires_at
    comparison_now = (
        now.replace(tzinfo=None)
        if expires_at is not None and expires_at.tzinfo is None
        else now
    )
    if expires_at is not None and expires_at <= comparison_now:
        raise WorkspaceServiceError("workspace_asset_expired", status_code=409)
    if asset.state != "ready":
        raise WorkspaceServiceError(
            "workspace_asset_not_ready",
            status_code=409,
            retryable=True,
        )

    root = (storage_root or Path(get_writable_path("workspace_assets"))).resolve()
    storage_key = asset.storage_key or ""
    key = PurePosixPath(storage_key)
    if (
        not storage_key
        or "\\" in storage_key
        or key.is_absolute()
        or any(part in {"", ".", ".."} for part in key.parts)
    ):
        raise WorkspaceServiceError("workspace_asset_not_found", status_code=404)
    try:
        path = (root / Path(*key.parts)).resolve(strict=True)
    except (OSError, RuntimeError):
        raise WorkspaceServiceError(
            "workspace_asset_not_found",
            status_code=404,
        ) from None
    if root not in path.parents or not path.is_file():
        raise WorkspaceServiceError("workspace_asset_not_found", status_code=404)
    return asset, path


async def get_workspace_asset(
    session: AsyncSession,
    *,
    asset_id: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
) -> WorkspaceAsset:
    predicates = [
        WorkspaceAsset.id == asset_id,
        _owner_id_predicate(WorkspaceAsset.owner_id),
    ]
    if conversation_id is not None:
        predicates.append(WorkspaceAsset.conversation_id == conversation_id)
    asset = (
        await session.execute(select(WorkspaceAsset).where(*predicates))
    ).scalar_one_or_none()
    if asset is None:
        raise WorkspaceServiceError("workspace_asset_not_found", status_code=404)
    return asset


async def create_workspace_asset(
    session: AsyncSession,
    *,
    upload: UploadFile,
    conversation_id: uuid.UUID | None = None,
    storage_root: Path | None = None,
    process_immediately: bool = False,
) -> WorkspaceAsset:
    """Store an immutable upload and optionally complete its legacy pipeline.

    Public workspace upload admission deliberately leaves the row in
    ``uploaded`` after the immutable source has committed.  The maintenance
    runner resumes that durable state after request completion.  The optional
    synchronous path remains for trusted compatibility callers only.
    """

    display_name = normalize_workspace_asset_name(upload.filename)
    try:
        extension = validated_upload_extension(display_name, UPLOAD_ACCEPTED_EXTENSIONS)
    except HTTPException as error:
        raise WorkspaceServiceError(
            "workspace_asset_file_type_invalid", status_code=400
        ) from error
    media_type = _validate_declared_mime(extension, upload.content_type)
    if conversation_id is not None:
        conversation = (
            await session.execute(
                select(WorkspaceConversation).where(
                    WorkspaceConversation.id == conversation_id,
                    WorkspaceConversation.owner_scope == get_workspace_owner_scope(),
                )
            )
        ).scalar_one_or_none()
        if conversation is None:
            raise WorkspaceServiceError(
                "workspace_conversation_not_found", status_code=404
            )

    # Keep the account writer fence through both the immutable file move and
    # the asset-row commit.  Account deletion therefore either wins first and
    # rejects the upload, or waits and snapshots the newly committed target.
    await _require_active_asset_owner(session)

    root = (storage_root or Path(get_writable_path("workspace_assets"))).resolve()
    asset_id = uuid.uuid4()
    asset_dir = (root / get_workspace_owner_scope() / str(asset_id)).resolve()
    if root not in asset_dir.parents:
        raise WorkspaceServiceError("workspace_asset_storage_invalid", status_code=500)
    asset_dir.mkdir(parents=True, exist_ok=False)
    staging_path = asset_dir / "upload.part"
    final_path: Path | None = None
    persisted = False
    try:
        try:
            size_bytes = await stream_upload_to_file(
                upload,
                staging_path,
                limit_bytes=get_single_upload_limit_bytes(),
                label="Workspace asset",
            )
        except HTTPException as error:
            code = (
                "workspace_asset_too_large"
                if error.status_code == 413
                else "workspace_asset_upload_invalid"
            )
            raise WorkspaceServiceError(code, status_code=error.status_code) from error
        digest = await asyncio.to_thread(
            _content_sha256_and_signature, staging_path, extension
        )
        final_path = asset_dir / f"{digest}{extension}"
        await asyncio.to_thread(os.replace, staging_path, final_path)
        now = get_current_utc_datetime()
        storage_key = final_path.relative_to(root).as_posix()
        asset = WorkspaceAsset(
            id=asset_id,
            conversation_id=conversation_id,
            state="uploaded",
            original_name=display_name,
            media_type=media_type,
            size_bytes=size_bytes,
            storage_key=storage_key,
            sha256=digest,
            expires_at=None if conversation_id is not None else _asset_expiry(now),
            created_at=now,
            updated_at=now,
        )
        session.add(asset)
        await session.commit()
        persisted = True
        if not process_immediately:
            return asset

        asset = await transition_workspace_asset(
            session,
            asset_id=asset.id,
            request=WorkspaceAssetTransitionRequest(
                state="scanning",
                expected_version=asset.version,
            ),
        )
        try:
            await asyncio.to_thread(_scan_workspace_asset, final_path)
        except WorkspaceServiceError as error:
            terminal_state = (
                "quarantined"
                if error.code == "workspace_asset_malware_detected"
                else "failed"
            )
            await transition_workspace_asset(
                session,
                asset_id=asset.id,
                request=WorkspaceAssetTransitionRequest(
                    state=terminal_state,
                    expected_version=asset.version,
                    failure_code=error.code,
                ),
            )
            # Only the stable error code crosses the API boundary. Scanner
            # stdout/stderr and executable details are intentionally discarded.
            raise
        asset = await transition_workspace_asset(
            session,
            asset_id=asset.id,
            request=WorkspaceAssetTransitionRequest(
                state="processing",
                expected_version=asset.version,
            ),
        )
        asset = await transition_workspace_asset(
            session,
            asset_id=asset.id,
            request=WorkspaceAssetTransitionRequest(
                state="ready",
                expected_version=asset.version,
            ),
        )
        return asset
    except Exception:
        await session.rollback()
        if not persisted:
            for path in (staging_path, final_path):
                if path is not None:
                    path.unlink(missing_ok=True)
            try:
                asset_dir.rmdir()
            except OSError:
                pass
        raise


async def process_workspace_asset(
    session: AsyncSession,
    *,
    asset_id: uuid.UUID,
    expected_version: int | None = None,
    storage_root: Path | None = None,
) -> WorkspaceAsset:
    """Resume one durable asset lifecycle from its current fenced state.

    Each boundary commits through :func:`transition_workspace_asset`, so a
    worker crash can only leave ``scanning`` or ``processing`` behind.  A later
    maintenance pass safely resumes either state; the version CAS makes a
    duplicate discovery harmless.  This function intentionally contains no
    request-owned background work.
    """

    # Existing ``scanning`` work also fences before touching external state;
    # every committed transition repeats the fence below after a prior commit.
    await _require_active_asset_owner(session)
    asset = await get_workspace_asset(session, asset_id=asset_id)
    if expected_version is not None and asset.version != expected_version:
        raise WorkspaceServiceError("workspace_asset_version_conflict", status_code=409)
    if asset.state not in {"uploaded", "scanning", "processing"}:
        return asset

    root = (storage_root or Path(get_writable_path("workspace_assets"))).resolve()
    source_path = _storage_path(root, asset.storage_key)
    if source_path is None or not source_path.is_file():
        return await transition_workspace_asset(
            session,
            asset_id=asset.id,
            request=WorkspaceAssetTransitionRequest(
                state="failed",
                expected_version=asset.version,
                failure_code="workspace_asset_storage_missing",
            ),
        )

    if asset.state == "uploaded":
        asset = await transition_workspace_asset(
            session,
            asset_id=asset.id,
            request=WorkspaceAssetTransitionRequest(
                state="scanning",
                expected_version=asset.version,
            ),
        )

    if asset.state == "scanning":
        try:
            await asyncio.to_thread(_scan_workspace_asset, source_path)
        except WorkspaceServiceError as error:
            # The stable scanner outcome is durable and safe to project to an
            # attachment/operation card; scanner output never leaves the worker.
            return await transition_workspace_asset(
                session,
                asset_id=asset.id,
                request=WorkspaceAssetTransitionRequest(
                    state=(
                        "quarantined"
                        if error.code == "workspace_asset_malware_detected"
                        else "failed"
                    ),
                    expected_version=asset.version,
                    failure_code=error.code,
                ),
            )
        asset = await transition_workspace_asset(
            session,
            asset_id=asset.id,
            request=WorkspaceAssetTransitionRequest(
                state="processing",
                expected_version=asset.version,
            ),
        )

    # The v1 processing stage has no derived material to persist yet.  Keeping
    # it as a committed state is still essential: future extraction can replace
    # this final transition without changing upload admission or recovery.
    if asset.state == "processing":
        asset = await transition_workspace_asset(
            session,
            asset_id=asset.id,
            request=WorkspaceAssetTransitionRequest(
                state="ready",
                expected_version=asset.version,
            ),
        )
    return asset


def _desired_operation_state(operation: WorkspaceOperation, states: list[str]) -> str:
    desired = _asset_operation_state(states)
    if (
        desired == "queued"
        and operation.request_payload.get("initial_operation_state")
        == "awaiting_approval"
    ):
        return "awaiting_approval"
    return desired


async def _project_asset_transition_to_operations(
    session: AsyncSession,
    *,
    asset: WorkspaceAsset,
) -> None:
    if asset.conversation_id is None:
        return
    operations = list(
        (
            await session.scalars(
                select(WorkspaceOperation)
                .where(
                    WorkspaceOperation.conversation_id == asset.conversation_id,
                    WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
                    WorkspaceOperation.state.in_(_OPERATION_ASSET_STATES),
                )
                .with_for_update()
            )
        ).all()
    )
    for operation in operations:
        asset_ids = _operation_asset_ids(operation)
        if asset.id not in asset_ids:
            continue
        referenced_assets = list(
            (
                await session.scalars(
                    select(WorkspaceAsset).where(
                        WorkspaceAsset.id.in_(asset_ids),
                        _owner_id_predicate(WorkspaceAsset.owner_id),
                        WorkspaceAsset.conversation_id == operation.conversation_id,
                    )
                )
            ).all()
        )
        states = [item.state for item in referenced_assets]
        if len(referenced_assets) != len(set(asset_ids)):
            states.append("deleted")
        desired = _desired_operation_state(operation, states)
        previous_state = operation.state
        error_code = _asset_block_error(states) if desired == "blocked" else None
        if (
            desired == "blocked"
            and asset.state == "failed"
            and asset.failure_code == "asset_processing_timeout"
        ):
            error_code = "asset_processing_timeout"
        error_retryable = error_code in {
            "workspace_asset_failed",
            "asset_processing_timeout",
        }
        if desired != previous_state or operation.error_code != error_code:
            values = {
                "state": desired,
                "error_code": error_code,
                "error_detail": (
                    {"retryable": error_retryable}
                    if error_code
                    else None
                ),
                "next_attempt_at": (
                    get_current_utc_datetime() if desired == "queued" else None
                ),
                "version": WorkspaceOperation.version + 1,
                "updated_at": get_current_utc_datetime(),
            }
            changed = (
                await session.execute(
                    update(WorkspaceOperation)
                    .where(
                        WorkspaceOperation.id == operation.id,
                        WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
                        WorkspaceOperation.state == previous_state,
                        WorkspaceOperation.version == operation.version,
                    )
                    .values(**values)
                    .returning(WorkspaceOperation.id)
                )
            ).scalar_one_or_none()
            if changed is None:
                continue
            operation = (
                await session.execute(
                    select(WorkspaceOperation)
                    .where(
                        WorkspaceOperation.id == operation.id,
                        WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
                    )
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
        event = await append_workspace_operation_event(
            session,
            operation=operation,
            event_type="asset_state_changed",
            payload={
                "status": operation.state,
                "message": (
                    "Attachments are ready."
                    if operation.state == "queued"
                    else "Waiting for attachments."
                    if operation.state == "waiting_for_assets"
                    else "An attachment cannot be used."
                    if operation.state == "blocked"
                    else "Awaiting approval."
                ),
                "asset_id": str(asset.id),
                "asset_status": asset.state,
                "error": (
                    {
                        "code": error_code,
                        "retryable": error_retryable,
                    }
                    if error_code
                    else None
                ),
            },
        )
        await _enqueue_event_notification(
            session,
            operation=operation,
            event=event,
            topic=(
                "workspace.operation.ready"
                if previous_state != "queued" and operation.state == "queued"
                else "workspace.operation.event"
            ),
        )


async def transition_workspace_asset(
    session: AsyncSession,
    *,
    asset_id: uuid.UUID,
    request: WorkspaceAssetTransitionRequest,
    commit: bool = True,
) -> WorkspaceAsset:
    await _require_active_asset_owner(session)
    asset = await get_workspace_asset(session, asset_id=asset_id)
    if asset.version != request.expected_version:
        raise WorkspaceServiceError("workspace_asset_version_conflict", status_code=409)
    if request.state not in _ASSET_TRANSITIONS[asset.state]:
        raise WorkspaceServiceError(
            "workspace_asset_transition_invalid", status_code=409
        )
    now = get_current_utc_datetime()
    failure_code = request.failure_code
    if request.state == "failed":
        failure_code = failure_code or "asset_processing_failed"
    elif request.state == "quarantined":
        failure_code = failure_code or "asset_security_quarantine"
    elif request.state not in {"expired", "deleted"}:
        failure_code = None
    values = {
        "state": request.state,
        "failure_code": failure_code,
        "failure_detail": (
            {"retryable": request.state == "failed"}
            if failure_code is not None
            else None
        ),
        "expires_at": now if request.state == "expired" else asset.expires_at,
        "deleted_at": now if request.state == "deleted" else asset.deleted_at,
        "version": WorkspaceAsset.version + 1,
        "updated_at": now,
    }
    if request.state == "uploaded":
        values["expires_at"] = (
            _asset_expiry(now) if asset.conversation_id is None else None
        )
        values["deleted_at"] = None
    changed = (
        await session.execute(
            update(WorkspaceAsset)
            .where(
                WorkspaceAsset.id == asset.id,
                _owner_id_predicate(WorkspaceAsset.owner_id),
                WorkspaceAsset.state == asset.state,
                WorkspaceAsset.version == request.expected_version,
            )
            .values(**values)
            .returning(WorkspaceAsset.id)
        )
    ).scalar_one_or_none()
    if changed is None:
        raise WorkspaceServiceError("workspace_asset_version_conflict", status_code=409)
    asset = (
        await session.execute(
            select(WorkspaceAsset)
            .where(
                WorkspaceAsset.id == asset.id,
                _owner_id_predicate(WorkspaceAsset.owner_id),
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    # Message attachments are immutable snapshots of the asset at send time.
    # Later scan/processing/deletion transitions affect only the live asset row.
    await _project_asset_transition_to_operations(session, asset=asset)
    await session.flush()
    if commit:
        await session.commit()
    return asset


def _storage_path(root: Path, storage_key: str) -> Path | None:
    if not storage_key or PurePosixPath(storage_key).is_absolute():
        return None
    candidate = (root / Path(*PurePosixPath(storage_key).parts)).resolve()
    return candidate if root == candidate or root in candidate.parents else None


async def expire_orphan_workspace_assets(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    older_than: datetime | None = None,
    limit: int = 100,
    commit: bool = True,
) -> int:
    now = now or get_current_utc_datetime()
    older_than = older_than or now
    eligible_expiry = or_(
        WorkspaceAsset.expires_at <= now,
        and_(
            WorkspaceAsset.expires_at.is_(None),
            WorkspaceAsset.created_at <= older_than,
        ),
    )
    assets = list(
        (
            await session.scalars(
                select(WorkspaceAsset)
                .where(
                    WorkspaceAsset.conversation_id.is_(None),
                    WorkspaceAsset.state.notin_({"expired", "deleted"}),
                    eligible_expiry,
                )
                .order_by(WorkspaceAsset.created_at, WorkspaceAsset.id)
                .limit(max(1, min(limit, 1000)))
            )
        ).all()
    )
    expired = 0
    for asset in assets:
        changed = (
            await session.execute(
                update(WorkspaceAsset)
                .where(
                    WorkspaceAsset.id == asset.id,
                    WorkspaceAsset.conversation_id.is_(None),
                    WorkspaceAsset.state.notin_({"expired", "deleted"}),
                    WorkspaceAsset.version == asset.version,
                    eligible_expiry,
                )
                .values(
                    state="expired",
                    failure_code="asset_orphan_expired",
                    failure_detail={"retryable": False},
                    expires_at=now,
                    version=WorkspaceAsset.version + 1,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
                .returning(WorkspaceAsset.id)
            )
        ).scalar_one_or_none()
        if changed is not None:
            expired += 1
    await session.flush()
    if commit:
        await session.commit()
    return expired


async def cleanup_expired_workspace_assets(
    session: AsyncSession,
    *,
    storage_root: Path | None = None,
    limit: int = 100,
    commit: bool = True,
) -> int:
    root = (storage_root or Path(get_writable_path("workspace_assets"))).resolve()
    assets = list(
        (
            await session.scalars(
                select(WorkspaceAsset)
                .where(WorkspaceAsset.state == "expired")
                .order_by(WorkspaceAsset.updated_at, WorkspaceAsset.id)
                .limit(max(1, min(limit, 1000)))
            )
        ).all()
    )
    deleted = 0
    now = get_current_utc_datetime()
    for asset in assets:
        path = _storage_path(root, asset.storage_key)
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
        asset.state = "deleted"
        asset.failure_code = "asset_expired_deleted"
        asset.failure_detail = {"retryable": False}
        asset.deleted_at = now
        asset.version += 1
        asset.updated_at = now
        deleted += 1
    await session.flush()
    if commit:
        await session.commit()
    return deleted
