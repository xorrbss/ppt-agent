"""Path namespace and owned-tree helpers for workspace artifact publication.

Durable artifacts live in exactly one storage namespace per operation, and the
only trees compensation may delete are the ones it can prove that namespace
owns.  Those rules are resolved here, below the publication transaction, so the
service module can stay about promotion and fencing.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path, PurePosixPath

from utils.get_env import get_app_data_directory_env


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class WorkspaceArtifactPublicationError(RuntimeError):
    """Safe publication failure with a stable client/runner error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _app_data_root() -> Path:
    raw = (get_app_data_directory_env() or "").strip()
    if not raw:
        raise WorkspaceArtifactPublicationError("app_data_directory_required")
    root = Path(raw).resolve()
    if root == root.parent:
        raise WorkspaceArtifactPublicationError("unsafe_app_data_directory")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_source(source_path: str, app_data_root: Path) -> Path:
    source = Path(source_path).resolve()
    try:
        source.relative_to(app_data_root)
    except ValueError as error:
        raise WorkspaceArtifactPublicationError(
            "workspace_artifact_source_outside_app_data"
        ) from error
    if not source.is_file():
        raise WorkspaceArtifactPublicationError("workspace_artifact_source_missing")
    return source


def _artifact_relative_path(
    *,
    owner_id: uuid.UUID | None,
    operation_id: uuid.UUID,
    artifact_id: uuid.UUID,
    suffix: str,
) -> PurePosixPath:
    """Build the only storage namespace accepted for a durable artifact."""

    filename = f"{artifact_id}{suffix}"
    if owner_id is None:
        return PurePosixPath(
            "workspace-artifacts",
            "anonymous",
            str(operation_id),
            filename,
        )
    return PurePosixPath(
        "workspace-artifacts",
        "users",
        str(owner_id),
        str(operation_id),
        filename,
    )


def _chat_image_relative_directory(
    *,
    owner_id: uuid.UUID | None,
    operation_id: uuid.UUID,
    attempt_digest: str,
    journal_id: uuid.UUID,
) -> PurePosixPath:
    """Return the exact image namespace owned by one durable chat journal."""

    if not _SHA256_PATTERN.fullmatch(attempt_digest):
        raise WorkspaceArtifactPublicationError(
            "workspace_chat_artifact_attempt_invalid"
        )
    if owner_id is None:
        return PurePosixPath(
            "images",
            "workspace-chat",
            "anonymous",
            str(operation_id),
            attempt_digest,
            str(journal_id),
        )
    return PurePosixPath(
        "images",
        "users",
        str(owner_id),
        "workspace-chat",
        str(operation_id),
        attempt_digest,
        str(journal_id),
    )


def _assert_path_parents_are_not_symlinks(root: Path, path: Path) -> None:
    """Reject a path whose existing parents could escape through a symlink."""

    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise WorkspaceArtifactPublicationError(
            "workspace_artifact_path_invalid"
        ) from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise WorkspaceArtifactPublicationError(
                "workspace_artifact_symlink_invalid"
            )


def _remove_owned_tree(path: Path) -> None:
    """Delete one validated journal-owned tree without following symlinks."""

    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if not path.exists():
        return
    if not path.is_dir():
        raise WorkspaceArtifactPublicationError(
            "workspace_chat_artifact_directory_invalid"
        )
    with os.scandir(path) as entries:
        for entry in entries:
            child = Path(entry.path)
            if entry.is_symlink():
                child.unlink(missing_ok=True)
            elif entry.is_dir(follow_symlinks=False):
                _remove_owned_tree(child)
            else:
                child.unlink(missing_ok=True)
    path.rmdir()


__all__ = [
    "WorkspaceArtifactPublicationError",
    "_SHA256_PATTERN",
    "_app_data_root",
    "_artifact_relative_path",
    "_assert_path_parents_are_not_symlinks",
    "_chat_image_relative_directory",
    "_remove_owned_tree",
    "_safe_source",
]
