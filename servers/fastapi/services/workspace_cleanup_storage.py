"""Fail-closed filesystem boundary for durable workspace cleanup claims.

The journal service owns claim leasing, fencing, retry, and transaction state.
This module owns the narrower storage contract: a claim may delete only the
exact root generation and immutable resource identity captured before the
database mutation committed.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from constants.documents import UPLOAD_ACCEPTED_EXTENSIONS
from services.template_v2_native_storage import remove_native_source
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactCleanupClaim,
    _delete_claimed_artifact_files,
    _remove_owned_tree,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CLEANUP_METADATA_SCHEMA_VERSION = "workspace.cleanup-target/v2"
_FILESYSTEM_STORAGE_BACKEND = "filesystem"
_WORKSPACE_ASSET_ROOT_KIND = "workspace_assets"
_WORKSPACE_ARTIFACT_ROOT_KIND = "app_data"
_AUTHORED_CHECKPOINT_ROOT_KIND = "authored_checkpoints"
_TEMPLATE_V2_NATIVE_ROOT_KIND = "template_v2_native"
_AUTHORED_IMAGES_ROOT_KIND = "authored_images"
_LEGACY_GALLERY_IMAGE_ROOT_KIND = "legacy_gallery_images"
_CHAT_IMAGE_SUFFIXES = frozenset(
    {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
)
_WORKSPACE_ASSET_SUFFIXES = frozenset(UPLOAD_ACCEPTED_EXTENSIONS)
_ACCOUNT_APP_DATA_TREE_NAMES = (
    "images",
    "exports",
    "uploads",
    "pptx-to-html",
    "pptx-to-json",
)
_ACCOUNT_APP_DATA_ROOT_KINDS = {
    f"app_data.{name}": name for name in _ACCOUNT_APP_DATA_TREE_NAMES
}
_ACCOUNT_TEMP_TREE_KIND = "temp"


class CleanupClaim(Protocol):
    owner_id: uuid.UUID | None
    presentation_id: uuid.UUID | None
    operation_id: uuid.UUID | None
    storage_namespace: str
    resource_kind: str
    storage_key: str
    sha256: str | None
    size_bytes: int | None
    resource_metadata: dict[str, Any]
    token: str
    attempts: int


class WorkspaceCleanupDeleteError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def canonical_storage_root(root: Path) -> Path:
    """Return a stable absolute filesystem identity suitable for journaling."""

    return Path(os.path.abspath(root)).resolve(strict=False)


def _storage_backend_generation(*, root_kind: str, root: Path) -> str:
    canonical = json.dumps(
        {
            "backend": _FILESYSTEM_STORAGE_BACKEND,
            "root_kind": root_kind,
            "root": os.path.normcase(str(canonical_storage_root(root))),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def storage_root_metadata(*, root_kind: str, root: Path) -> dict[str, str]:
    canonical_root = canonical_storage_root(root)
    return {
        "schema_version": _CLEANUP_METADATA_SCHEMA_VERSION,
        "storage_backend": _FILESYSTEM_STORAGE_BACKEND,
        "storage_root_kind": root_kind,
        "storage_root": str(canonical_root),
        "storage_backend_generation": _storage_backend_generation(
            root_kind=root_kind,
            root=canonical_root,
        ),
    }


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _claim_storage_root(
    claim: CleanupClaim,
    *,
    expected_root_kind: str,
    invalid_code: str,
) -> Path:
    """Recover only the trusted root generation captured before mutation."""

    metadata = claim.resource_metadata
    root_value = metadata.get("storage_root")
    generation = metadata.get("storage_backend_generation")
    if (
        metadata.get("schema_version") != _CLEANUP_METADATA_SCHEMA_VERSION
        or metadata.get("storage_backend") != _FILESYSTEM_STORAGE_BACKEND
        or metadata.get("storage_root_kind") != expected_root_kind
        or not isinstance(root_value, str)
        or not root_value
        or os.path.normpath(root_value) != root_value
        or not isinstance(generation, str)
        or _SHA256_PATTERN.fullmatch(generation) is None
    ):
        raise WorkspaceCleanupDeleteError(invalid_code, retryable=False)
    root = Path(root_value)
    anchor = Path(root.anchor)
    if (
        not root.is_absolute()
        or root == anchor
        or root.is_symlink()
        or root.resolve(strict=False) != root
        or generation
        != _storage_backend_generation(root_kind=expected_root_kind, root=root)
    ):
        raise WorkspaceCleanupDeleteError(invalid_code, retryable=False)
    return root


def _as_uuid(value: Any, *, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as error:
        raise WorkspaceCleanupDeleteError(code, retryable=False) from error


def _strict_lexical_storage_path(
    root: Path,
    storage_key: str,
    *,
    invalid_code: str,
) -> tuple[PurePosixPath, Path]:
    if not isinstance(storage_key, str) or not storage_key or "\\" in storage_key:
        raise WorkspaceCleanupDeleteError(invalid_code, retryable=False)
    relative = PurePosixPath(storage_key)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise WorkspaceCleanupDeleteError(invalid_code, retryable=False)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise WorkspaceCleanupDeleteError(invalid_code, retryable=False)
    resolved = current.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise WorkspaceCleanupDeleteError(invalid_code, retryable=False) from error
    return relative, resolved


def _remove_workspace_asset(claim: CleanupClaim) -> None:
    root = _claim_storage_root(
        claim,
        expected_root_kind=_WORKSPACE_ASSET_ROOT_KIND,
        invalid_code="workspace_cleanup_asset_root_invalid",
    )
    relative, target = _strict_lexical_storage_path(
        root,
        claim.storage_key,
        invalid_code="workspace_cleanup_asset_path_invalid",
    )
    asset_id = _as_uuid(
        claim.resource_metadata.get("asset_id"),
        code="workspace_cleanup_asset_identity_invalid",
    )
    expected_owner_scope = (
        str(claim.owner_id) if claim.owner_id is not None else "anonymous"
    )
    parts = relative.parts
    leaf = PurePosixPath(parts[-1]) if parts else PurePosixPath("")
    suffix = leaf.suffix.lower()
    if (
        len(parts) != 3
        or claim.resource_metadata.get("owner_scope") != expected_owner_scope
        or parts[0] != expected_owner_scope
        or parts[1] != str(asset_id)
        or not isinstance(claim.sha256, str)
        or _SHA256_PATTERN.fullmatch(claim.sha256) is None
        or suffix not in _WORKSPACE_ASSET_SUFFIXES
        or leaf.name != f"{claim.sha256}{suffix}"
        or target == root
    ):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_asset_path_invalid", retryable=False
        )
    if target.exists() and not target.is_file():
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_asset_path_invalid", retryable=False
        )
    target.unlink(missing_ok=True)
    parent = target.parent
    while parent != root and root in parent.parents:
        if parent.is_symlink():
            raise WorkspaceCleanupDeleteError(
                "workspace_cleanup_asset_path_invalid", retryable=False
            )
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def _remove_legacy_gallery_image(claim: CleanupClaim) -> None:
    root = _claim_storage_root(
        claim,
        expected_root_kind=_LEGACY_GALLERY_IMAGE_ROOT_KIND,
        invalid_code="workspace_cleanup_legacy_gallery_root_invalid",
    )
    relative, target = _strict_lexical_storage_path(
        root,
        claim.storage_key,
        invalid_code="workspace_cleanup_legacy_gallery_path_invalid",
    )
    _as_uuid(
        claim.resource_metadata.get("image_id"),
        code="workspace_cleanup_legacy_gallery_identity_invalid",
    )
    expected_owner_scope = (
        str(claim.owner_id) if claim.owner_id is not None else "anonymous"
    )
    if (
        claim.resource_kind != "legacy.gallery_image"
        or claim.resource_metadata.get("owner_scope") != expected_owner_scope
        or len(relative.parts) != 1
        or target == root
        or target.suffix.lower() not in _CHAT_IMAGE_SUFFIXES
        or not isinstance(claim.sha256, str)
        or _SHA256_PATTERN.fullmatch(claim.sha256) is None
        or (target.exists() and not target.is_file())
    ):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_legacy_gallery_path_invalid", retryable=False
        )
    if target.is_file():
        digest = sha256_file(target)
        if target.stat().st_size != claim.size_bytes or digest != claim.sha256:
            raise WorkspaceCleanupDeleteError(
                "workspace_cleanup_legacy_gallery_content_mismatch",
                retryable=False,
            )
    target.unlink(missing_ok=True)


def _remove_workspace_artifact(claim: CleanupClaim) -> None:
    artifact_id = _as_uuid(
        claim.resource_metadata.get("artifact_id"),
        code="workspace_cleanup_artifact_identity_invalid",
    )
    operation_id = claim.operation_id
    artifact_kind = claim.resource_metadata.get("artifact_kind")
    if operation_id is None or not isinstance(artifact_kind, str):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_artifact_identity_invalid", retryable=False
        )
    root = _claim_storage_root(
        claim,
        expected_root_kind=_WORKSPACE_ARTIFACT_ROOT_KIND,
        invalid_code="workspace_cleanup_artifact_root_invalid",
    )
    _strict_lexical_storage_path(
        root,
        claim.storage_key,
        invalid_code="workspace_cleanup_artifact_symlink_invalid",
    )
    _delete_claimed_artifact_files(
        WorkspaceArtifactCleanupClaim(
            artifact_id=artifact_id,
            owner_id=claim.owner_id,
            operation_id=operation_id,
            kind=artifact_kind,
            storage_key=claim.storage_key,
            token=claim.token,
            attempts=claim.attempts,
        ),
        storage_root=root,
    )


def _remove_authored_checkpoint(claim: CleanupClaim) -> None:
    job_id = _as_uuid(
        claim.resource_metadata.get("job_id", claim.storage_key),
        code="workspace_cleanup_checkpoint_identity_invalid",
    )
    if claim.storage_key != str(job_id):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_checkpoint_identity_invalid", retryable=False
        )
    root = _claim_storage_root(
        claim,
        expected_root_kind=_AUTHORED_CHECKPOINT_ROOT_KIND,
        invalid_code="workspace_cleanup_checkpoint_root_invalid",
    )
    _relative, directory = _strict_lexical_storage_path(
        root,
        claim.storage_key,
        invalid_code="workspace_cleanup_checkpoint_path_invalid",
    )
    if directory.parent != root or directory.name != str(job_id):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_checkpoint_path_invalid", retryable=False
        )
    if directory.exists() and not directory.is_dir():
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_checkpoint_path_invalid", retryable=False
        )
    _remove_owned_tree(directory)


def _remove_template_v2_native(claim: CleanupClaim) -> None:
    template_id = claim.resource_metadata.get("template_id")
    revision = claim.resource_metadata.get("revision")
    if not isinstance(template_id, str) or isinstance(revision, bool):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_template_identity_invalid", retryable=False
        )
    try:
        revision_value = int(revision)
    except (TypeError, ValueError) as error:
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_template_identity_invalid", retryable=False
        ) from error
    root = _claim_storage_root(
        claim,
        expected_root_kind=_TEMPLATE_V2_NATIVE_ROOT_KIND,
        invalid_code="workspace_cleanup_template_root_invalid",
    )
    remove_native_source(
        claim.storage_key,
        template_id=template_id,
        revision=revision_value,
        storage_root=root,
    )


def _remove_authored_image_tree(claim: CleanupClaim) -> None:
    presentation_id = claim.presentation_id
    metadata = claim.resource_metadata
    if (
        presentation_id is None
        or metadata.get("presentation_id") != str(presentation_id)
        or metadata.get("owner_scope")
        != (str(claim.owner_id) if claim.owner_id is not None else "anonymous")
        or claim.resource_kind != "authored.slide_image_tree"
    ):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_authored_image_identity_invalid", retryable=False
        )
    root = _claim_storage_root(
        claim,
        expected_root_kind=_AUTHORED_IMAGES_ROOT_KIND,
        invalid_code="workspace_cleanup_authored_image_root_invalid",
    )
    relative, directory = _strict_lexical_storage_path(
        root,
        claim.storage_key,
        invalid_code="workspace_cleanup_authored_image_path_invalid",
    )
    if (
        relative.parts != ("authored", str(presentation_id))
        or directory.parent != root / "authored"
        or directory.name != str(presentation_id)
        or directory == root
    ):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_authored_image_path_invalid", retryable=False
        )
    if directory.exists() and not directory.is_dir():
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_authored_image_path_invalid", retryable=False
        )
    _remove_owned_tree(directory)


def _remove_account_owned_tree(claim: CleanupClaim) -> None:
    """Remove only an exact UUID child of the snapshotted owner root."""

    owner_id = claim.owner_id
    metadata = claim.resource_metadata
    if owner_id is None or metadata.get("owner_id") != str(owner_id):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_account_tree_identity_invalid", retryable=False
        )
    root_kind = metadata.get("storage_root_kind")
    if not isinstance(root_kind, str) or (
        root_kind not in _ACCOUNT_APP_DATA_ROOT_KINDS
        and root_kind != _ACCOUNT_TEMP_TREE_KIND
    ):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_account_tree_root_invalid", retryable=False
        )
    snapshotted_root = _claim_storage_root(
        claim,
        expected_root_kind=root_kind,
        invalid_code="workspace_cleanup_account_tree_root_invalid",
    )
    if root_kind in _ACCOUNT_APP_DATA_ROOT_KINDS:
        expected_suffix = (_ACCOUNT_APP_DATA_ROOT_KINDS[root_kind], "users")
        if tuple(snapshotted_root.parts[-2:]) != expected_suffix:
            raise WorkspaceCleanupDeleteError(
                "workspace_cleanup_account_tree_root_invalid", retryable=False
            )
    expected = snapshotted_root / str(owner_id)
    supplied = Path(claim.storage_key)
    if (
        not supplied.is_absolute()
        or supplied != expected
        or os.path.normpath(claim.storage_key) != claim.storage_key
        or supplied.parent != snapshotted_root
        or supplied.name != str(owner_id)
        or claim.resource_kind != "account.owned_tree"
        or expected == snapshotted_root
        or expected.is_symlink()
    ):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_account_tree_path_invalid", retryable=False
        )
    if expected.exists() and not expected.is_dir():
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_account_tree_path_invalid", retryable=False
        )
    if expected.is_dir():
        _remove_owned_tree(expected)


def delete_claimed_storage(claim: CleanupClaim) -> None:
    """Apply one claim's idempotent filesystem side effect fail-closed."""

    try:
        if claim.storage_namespace == "workspace_asset":
            _remove_workspace_asset(claim)
        elif claim.storage_namespace == "legacy_gallery_image":
            _remove_legacy_gallery_image(claim)
        elif claim.storage_namespace == "workspace_artifact":
            _remove_workspace_artifact(claim)
        elif claim.storage_namespace == "authored_checkpoint":
            _remove_authored_checkpoint(claim)
        elif claim.storage_namespace == "template_v2_native":
            _remove_template_v2_native(claim)
        elif claim.storage_namespace == "authored_image_tree":
            _remove_authored_image_tree(claim)
        elif claim.storage_namespace == "account_owned_tree":
            _remove_account_owned_tree(claim)
        else:
            raise WorkspaceCleanupDeleteError(
                "workspace_cleanup_namespace_invalid", retryable=False
            )
    except WorkspaceCleanupDeleteError:
        raise
    except Exception as error:
        code = getattr(error, "code", "workspace_cleanup_delete_failed")
        normalized = str(code)
        nonretryable = any(
            marker in normalized
            for marker in ("invalid", "escape", "mismatch", "forbidden", "unsafe")
        )
        raise WorkspaceCleanupDeleteError(
            normalized,
            retryable=not nonretryable,
        ) from error


__all__ = [
    "WorkspaceCleanupDeleteError",
    "canonical_storage_root",
    "delete_claimed_storage",
    "sha256_file",
    "storage_root_metadata",
]
