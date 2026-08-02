"""Immutable artifact promotion and fenced chat-first publication.

The presentation generator writes an ordinary export first.  Chat-first
publication copies that file into an operation-scoped, immutable location,
hashes it, and commits the artifact pointer with the presentation revision and
operation result.  A stale worker can therefore leave no published DB pointer.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import (
    WorkspaceArtifact,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
    get_workspace_owner_scope,
)
from utils.get_env import get_app_data_directory_env
from utils.owner_context import get_current_owner_id

LOGGER = logging.getLogger(__name__)

DEFAULT_ARTIFACT_STAGING_TIMEOUT_SECONDS = 60 * 60
DEFAULT_ARTIFACT_CLEANUP_LEASE_SECONDS = 60
DEFAULT_ARTIFACT_CLEANUP_RETRY_SECONDS = 30
DEFAULT_ARTIFACT_CLEANUP_MAX_ATTEMPTS = 8
DEFAULT_EXPORT_ARTIFACT_TTL_SECONDS = 24 * 60 * 60
_STAGING_SHA256 = "0" * 64
_CHAT_IMAGE_SUFFIXES = frozenset({".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class WorkspaceArtifactPublicationError(RuntimeError):
    """Safe publication failure with a stable client/runner error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PreparedWorkspaceArtifact:
    artifact_id: uuid.UUID
    owner_id: uuid.UUID | None
    operation_id: uuid.UUID
    presentation_id: uuid.UUID
    kind: str
    absolute_path: Path
    storage_key: str
    public_path: str
    sha256: str
    size_bytes: int
    source_revision: int

    @property
    def temporary_path(self) -> Path:
        """Deterministic copy path recoverable from the durable intent."""

        return self.absolute_path.parent / f".{self.artifact_id}.tmp"

    def result_payload(self) -> dict[str, object]:
        return {
            "artifact_id": str(self.artifact_id),
            "artifact_kind": self.kind,
            "artifact_sha256": self.sha256,
            "artifact_size_bytes": self.size_bytes,
            "path": self.public_path,
        }


@dataclass(frozen=True)
class PreparedWorkspaceChatArtifactStaging:
    """Durable owner/fence-scoped journal for provider image side effects."""

    artifact_id: uuid.UUID
    owner_id: uuid.UUID | None
    operation_id: uuid.UUID
    presentation_id: uuid.UUID
    attempt_digest: str
    source_revision: int
    storage_key: str
    staging_directory: Path
    artifact_directory: Path


@dataclass(frozen=True)
class PromotedWorkspaceChatArtifacts:
    artifacts: tuple[PreparedWorkspaceArtifact, ...]
    replacements: dict[str, str]


@dataclass(frozen=True)
class WorkspaceArtifactCleanupClaim:
    artifact_id: uuid.UUID
    owner_id: uuid.UUID | None
    operation_id: uuid.UUID
    kind: str
    storage_key: str
    token: str
    attempts: int


@dataclass(frozen=True)
class ResolvedWorkspaceExportArtifact:
    artifact_id: uuid.UUID
    operation_id: uuid.UUID
    presentation_id: uuid.UUID
    kind: str
    source_revision: int
    sha256: str
    size_bytes: int
    expires_at: datetime
    absolute_path: Path


def _configured_positive_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value) if value > 0 else default


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


def _remove_prepared_file(artifact: PreparedWorkspaceArtifact) -> bool:
    removed = True
    for path in (artifact.temporary_path, artifact.absolute_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            removed = False
            LOGGER.warning(
                "Unable to remove unpublished workspace artifact path %s",
                path,
                exc_info=True,
            )
    return removed


def _copy_and_hash(source: Path, output: BinaryIO) -> tuple[str, int]:
    """Copy one source while computing the immutable artifact identity."""

    digest = hashlib.sha256()
    size_bytes = 0
    with source.open("rb") as input_file:
        while chunk := input_file.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def _fsync_output(output: BinaryIO) -> None:
    output.flush()
    os.fsync(output.fileno())


def _atomic_promote(temporary: Path, destination: Path) -> None:
    # A hard-link is an atomic no-overwrite promotion on the same volume.
    os.link(temporary, destination)


def _fsync_directory(directory: Path) -> None:
    """Persist the directory entry where the platform supports directory fsync."""

    if os.name == "nt":
        # Windows does not allow opening directories with the flags accepted by
        # os.fsync.  The no-overwrite hard-link remains atomic there.
        return
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _remove_failed_promotion(temporary: Path, destination: Path) -> bool:
    """Remove only a destination that was promoted from this exact temp file.

    A destination that is not the same inode is a no-overwrite collision.  It
    belongs to neither this intent nor its sweeper, so leaving it untouched is
    a successful compensation of *our* external state.
    """

    try:
        if not destination.exists():
            return True
        if not temporary.exists():
            # Ownership cannot be proven.  Leave a cleanup record rather than
            # risking deletion of an unrelated path.
            return False
        if not os.path.samefile(temporary, destination):
            # Never delete a pre-existing collision belonging to another writer.
            return True
        destination.unlink()
        return True
    except OSError:
        LOGGER.warning(
            "Unable to remove failed workspace artifact promotion %s",
            destination,
            exc_info=True,
        )
        return False


async def _staging_intent_was_committed(
    session: AsyncSession,
    artifact: PreparedWorkspaceArtifact,
) -> bool:
    row = (
        await session.execute(
            select(WorkspaceArtifact).where(
                WorkspaceArtifact.id == artifact.artifact_id,
                WorkspaceArtifact.operation_id == artifact.operation_id,
                WorkspaceArtifact.presentation_id == artifact.presentation_id,
                WorkspaceArtifact.status == "staging",
                WorkspaceArtifact.storage_key == artifact.storage_key,
                WorkspaceArtifact.source_revision == artifact.source_revision,
            ).execution_options(skip_owner_scope=True)
        )
    ).scalar_one_or_none()
    return row is not None


def _new_independent_session(session: AsyncSession) -> AsyncSession:
    """Return a session whose transaction cannot commit caller-owned work.

    Artifact intent durability intentionally crosses the filesystem boundary,
    while the caller can already be assembling the presentation publication
    unit of work.  Reusing the caller session here would commit those pending
    presentation/slide mutations before the immutable file exists.  Bind a new
    session to the same engine instead, with autoflush disabled so even intent
    validation cannot flush caller state.
    """

    bind = session.bind
    if bind is None:
        raise WorkspaceArtifactPublicationError("database_bind_required")
    return AsyncSession(
        bind=bind,
        expire_on_commit=False,
        autoflush=False,
    )


async def _mark_artifact_deleted(
    session: AsyncSession,
    artifact: PreparedWorkspaceArtifact,
) -> bool:
    """Close a durable intent after compensation using an independent session."""

    bind = session.bind
    if bind is None:
        LOGGER.error(
            "Unable to close artifact intent without a database bind for %s",
            artifact.artifact_id,
        )
        return False
    cleanup_session = AsyncSession(bind=bind, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    try:
        result = await cleanup_session.execute(
            update(WorkspaceArtifact)
            .where(
                WorkspaceArtifact.id == artifact.artifact_id,
                or_(
                    WorkspaceArtifact.status == "staging",
                    and_(
                        WorkspaceArtifact.status == "cleanup_pending",
                        WorkspaceArtifact.cleanup_token.is_(None),
                    ),
                ),
            )
            .values(
                status="deleted",
                expires_at=None,
                cleanup_token=None,
                cleanup_lease_expires_at=None,
                cleanup_error=None,
                deleted_at=now,
                updated_at=now,
            )
        )
        await cleanup_session.commit()
        if result.rowcount == 1:
            return True
        existing = (
            await cleanup_session.execute(
                select(WorkspaceArtifact.status)
                .where(WorkspaceArtifact.id == artifact.artifact_id)
                .execution_options(skip_owner_scope=True)
            )
        ).scalar_one_or_none()
        return existing == "deleted"
    except Exception:
        await cleanup_session.rollback()
        LOGGER.error(
            "Unable to close compensated workspace artifact intent %s",
            artifact.artifact_id,
            exc_info=True,
        )
        return False
    finally:
        await cleanup_session.close()


async def _mark_chat_artifact_tree_deleted(
    session: AsyncSession,
    staging: PreparedWorkspaceChatArtifactStaging,
) -> bool:
    """Close a journal and any defensively persisted provisional children."""

    bind = session.bind
    if bind is None:
        return False
    cleanup_session = AsyncSession(bind=bind, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    child_prefix = (
        str(PurePosixPath(staging.storage_key).parent / "artifacts") + "/"
    )
    try:
        await cleanup_session.execute(
            update(WorkspaceArtifact)
            .where(
                WorkspaceArtifact.operation_id == staging.operation_id,
                WorkspaceArtifact.kind == "chat.image",
                WorkspaceArtifact.status == "staging",
                WorkspaceArtifact.storage_key.startswith(child_prefix),
            )
            .values(
                status="deleted",
                expires_at=None,
                cleanup_token=None,
                cleanup_lease_expires_at=None,
                cleanup_error=None,
                deleted_at=now,
                updated_at=now,
            )
        )
        journal_result = await cleanup_session.execute(
            update(WorkspaceArtifact)
            .where(
                WorkspaceArtifact.id == staging.artifact_id,
                WorkspaceArtifact.status.in_(("staging", "cleanup_pending")),
            )
            .values(
                status="deleted",
                expires_at=None,
                cleanup_token=None,
                cleanup_lease_expires_at=None,
                cleanup_error=None,
                deleted_at=now,
                updated_at=now,
            )
        )
        await cleanup_session.commit()
        return journal_result.rowcount == 1
    except Exception:
        await cleanup_session.rollback()
        LOGGER.error(
            "Unable to close chat artifact tree %s",
            staging.artifact_id,
            exc_info=True,
        )
        return False
    finally:
        await cleanup_session.close()


async def prepare_workspace_generation_artifact(
    session: AsyncSession,
    *,
    source_path: str,
    operation_id: uuid.UUID,
    presentation_id: uuid.UUID,
    source_revision: int,
    kind: str = "presentation.export",
) -> PreparedWorkspaceArtifact:
    """Reserve a durable intent, then promote one immutable external file.

    The intent commit deliberately precedes the filesystem copy.  A process
    death at any later instruction therefore leaves a staging row from which
    maintenance can derive both the final and deterministic temporary paths.
    """

    root = _app_data_root()
    source = _safe_source(source_path, root)
    owner_scope = get_workspace_owner_scope()
    artifact_id = uuid.uuid4()
    suffix = (
        source.suffix.lower()
        if source.suffix.lower() in {".pptx", ".pdf"}
        else ".bin"
    )
    now = datetime.now(timezone.utc)
    staging_timeout = _configured_positive_int(
        "WORKSPACE_ARTIFACT_STAGING_TIMEOUT_SECONDS",
        DEFAULT_ARTIFACT_STAGING_TIMEOUT_SECONDS,
        minimum=60,
    )
    intent_session = _new_independent_session(session)
    try:
        operation_identity = (
            await intent_session.execute(
                select(
                    WorkspaceOperation.owner_id,
                    WorkspaceOperation.owner_scope,
                ).where(
                    WorkspaceOperation.id == operation_id,
                    WorkspaceOperation.presentation_id == presentation_id,
                    WorkspaceOperation.owner_scope == owner_scope,
                )
            )
        ).one_or_none()
        if operation_identity is None:
            raise WorkspaceArtifactPublicationError(
                "workspace_artifact_operation_not_found"
            )
        operation_owner_id = operation_identity.owner_id
        expected_owner_scope = (
            str(operation_owner_id)
            if operation_owner_id is not None
            else "anonymous"
        )
        if operation_identity.owner_scope != expected_owner_scope:
            raise WorkspaceArtifactPublicationError(
                "workspace_artifact_owner_scope_invalid"
            )
        relative = _artifact_relative_path(
            owner_id=operation_owner_id,
            operation_id=operation_id,
            artifact_id=artifact_id,
            suffix=suffix,
        )
        destination = root.joinpath(*relative.parts)
        storage_key = relative.as_posix()
        artifact_intent = PreparedWorkspaceArtifact(
            artifact_id=artifact_id,
            owner_id=operation_owner_id,
            operation_id=operation_id,
            presentation_id=presentation_id,
            kind=kind,
            absolute_path=destination,
            storage_key=storage_key,
            public_path=f"/app_data/{storage_key}",
            sha256=_STAGING_SHA256,
            size_bytes=0,
            source_revision=source_revision,
        )
        intent_session.add(
            WorkspaceArtifact(
                id=artifact_id,
                owner_id=operation_owner_id,
                operation_id=operation_id,
                presentation_id=presentation_id,
                kind=kind,
                status="staging",
                storage_key=storage_key,
                sha256=_STAGING_SHA256,
                size_bytes=0,
                source_revision=source_revision,
                expires_at=now + timedelta(seconds=staging_timeout),
            )
        )
        try:
            await intent_session.commit()
        except BaseException as intent_commit_error:
            await intent_session.rollback()
            # Commit acknowledgements are allowed to be ambiguous. Continue
            # only when the exact intent is independently visible after
            # rollback. The caller transaction is never touched.
            if not await _staging_intent_was_committed(
                intent_session,
                artifact_intent,
            ):
                await intent_session.rollback()
                raise
            await intent_session.rollback()
            if isinstance(intent_commit_error, asyncio.CancelledError):
                # Cancellation is control flow, not an ambiguous ACK to ignore.
                # The intent is durable but no external copy has started, so
                # close it and propagate cancellation before publication.
                await _mark_artifact_deleted(session, artifact_intent)
                raise
    finally:
        await intent_session.close()

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = artifact_intent.temporary_path
    sha256 = _STAGING_SHA256
    size_bytes = 0
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            # Keep the actual copy in a small helper so write failures can be
            # injected without replacing Path.open process-wide.
            sha256, size_bytes = _copy_and_hash(source, output)
            _fsync_output(output)
        # A hard-link gives the operation-owned path atomic no-overwrite
        # semantics.  The deterministic temp name remains derivable on crash.
        _atomic_promote(temporary, destination)
        _fsync_directory(destination.parent)
        temporary.unlink(missing_ok=True)
    except BaseException as preparation_error:
        failed_artifact = PreparedWorkspaceArtifact(
            artifact_id=artifact_id,
            owner_id=operation_owner_id,
            operation_id=operation_id,
            presentation_id=presentation_id,
            kind=kind,
            absolute_path=destination,
            storage_key=storage_key,
            public_path=f"/app_data/{storage_key}",
            sha256=sha256,
            size_bytes=size_bytes,
            source_revision=source_revision,
        )
        destination_removed = _remove_failed_promotion(temporary, destination)
        try:
            temporary.unlink(missing_ok=True)
            temporary_removed = True
        except OSError:
            temporary_removed = False
            LOGGER.warning(
                "Unable to remove failed workspace artifact temp %s",
                temporary,
                exc_info=True,
            )
        if destination_removed and temporary_removed:
            await _mark_artifact_deleted(session, failed_artifact)
            raise
        cleanup_recorded = await _record_cleanup_pending(
            session,
            failed_artifact,
            error_code="artifact_preparation_cleanup_failed",
        )
        code = (
            "artifact_cleanup_pending"
            if cleanup_recorded
            else "artifact_cleanup_record_failed"
        )
        raise WorkspaceArtifactPublicationError(code) from preparation_error

    return PreparedWorkspaceArtifact(
        artifact_id=artifact_id,
        owner_id=operation_owner_id,
        operation_id=operation_id,
        presentation_id=presentation_id,
        kind=kind,
        absolute_path=destination,
        storage_key=storage_key,
        public_path=f"/app_data/{storage_key}",
        sha256=sha256,
        size_bytes=size_bytes,
        source_revision=source_revision,
    )


async def prepare_workspace_chat_artifact_staging(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    presentation_id: uuid.UUID,
    source_revision: int,
    attempt_token: str,
    fencing_token: int,
) -> PreparedWorkspaceChatArtifactStaging:
    """Commit a cleanup journal before an image provider may write a byte."""

    if not attempt_token:
        raise WorkspaceArtifactPublicationError("operation_lease_lost")
    root = _app_data_root()
    owner_scope = get_workspace_owner_scope()
    journal_id = uuid.uuid4()
    attempt_digest = hashlib.sha256(attempt_token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    staging_timeout = _configured_positive_int(
        "WORKSPACE_ARTIFACT_STAGING_TIMEOUT_SECONDS",
        DEFAULT_ARTIFACT_STAGING_TIMEOUT_SECONDS,
        minimum=60,
    )
    intent_session = _new_independent_session(session)
    journal: PreparedWorkspaceChatArtifactStaging | None = None
    try:
        operation_identity = (
            await intent_session.execute(
                select(
                    WorkspaceOperation.owner_id,
                    WorkspaceOperation.owner_scope,
                ).where(
                    WorkspaceOperation.id == operation_id,
                    WorkspaceOperation.presentation_id == presentation_id,
                    WorkspaceOperation.owner_scope == owner_scope,
                    WorkspaceOperation.state == "running",
                    WorkspaceOperation.attempt_token == attempt_token,
                    WorkspaceOperation.fencing_token == fencing_token,
                )
            )
        ).one_or_none()
        if operation_identity is None:
            raise WorkspaceArtifactPublicationError("operation_lease_lost")
        operation_owner_id = operation_identity.owner_id
        expected_owner_scope = (
            str(operation_owner_id)
            if operation_owner_id is not None
            else "anonymous"
        )
        if operation_identity.owner_scope != expected_owner_scope:
            raise WorkspaceArtifactPublicationError(
                "workspace_artifact_owner_scope_invalid"
            )
        relative_directory = _chat_image_relative_directory(
            owner_id=operation_owner_id,
            operation_id=operation_id,
            attempt_digest=attempt_digest,
            journal_id=journal_id,
        )
        owned_directory = root.joinpath(*relative_directory.parts)
        journal_relative = relative_directory / ".journal"
        journal = PreparedWorkspaceChatArtifactStaging(
            artifact_id=journal_id,
            owner_id=operation_owner_id,
            operation_id=operation_id,
            presentation_id=presentation_id,
            attempt_digest=attempt_digest,
            source_revision=source_revision,
            storage_key=journal_relative.as_posix(),
            staging_directory=owned_directory / "staging",
            artifact_directory=owned_directory / "artifacts",
        )
        intent_session.add(
            WorkspaceArtifact(
                id=journal_id,
                owner_id=operation_owner_id,
                operation_id=operation_id,
                presentation_id=presentation_id,
                kind="chat.image.journal",
                status="staging",
                storage_key=journal.storage_key,
                sha256=_STAGING_SHA256,
                size_bytes=0,
                source_revision=source_revision,
                expires_at=now + timedelta(seconds=staging_timeout),
            )
        )
        try:
            await intent_session.commit()
        except BaseException as intent_commit_error:
            await intent_session.rollback()
            committed = (
                await intent_session.execute(
                    select(WorkspaceArtifact.id).where(
                        WorkspaceArtifact.id == journal_id,
                        WorkspaceArtifact.operation_id == operation_id,
                        WorkspaceArtifact.status == "staging",
                        WorkspaceArtifact.storage_key == journal.storage_key,
                    ).execution_options(skip_owner_scope=True)
                )
            ).scalar_one_or_none()
            await intent_session.rollback()
            if committed is None:
                raise
            if isinstance(intent_commit_error, asyncio.CancelledError):
                await compensate_workspace_chat_artifact_staging(session, journal)
                raise
    finally:
        await intent_session.close()

    assert journal is not None
    try:
        _assert_path_parents_are_not_symlinks(root, journal.staging_directory.parent)
        journal.staging_directory.mkdir(parents=True, exist_ok=False)
    except BaseException:
        await compensate_workspace_chat_artifact_staging(session, journal)
        raise
    return journal


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


async def promote_workspace_chat_image_artifacts(
    session: AsyncSession,
    staging: PreparedWorkspaceChatArtifactStaging,
) -> PromotedWorkspaceChatArtifacts:
    """Promote provider outputs to immutable content-hash image paths.

    Child artifact rows remain provisional in the caller transaction.  The
    durable journal owns the complete attempt directory until the final chat
    revision transaction changes the children to ``ready`` and closes it.
    """

    root = _app_data_root()
    _assert_path_parents_are_not_symlinks(root, staging.staging_directory)
    if staging.staging_directory.is_symlink() or not staging.staging_directory.is_dir():
        raise WorkspaceArtifactPublicationError(
            "workspace_chat_artifact_directory_invalid"
        )
    staging.artifact_directory.mkdir(parents=False, exist_ok=True)
    promoted_by_path: dict[Path, PreparedWorkspaceArtifact] = {}
    replacements: dict[str, str] = {}
    images_root = root / "images"

    for source in sorted(staging.staging_directory.iterdir(), key=lambda item: item.name):
        if source.is_symlink() or not source.is_file():
            raise WorkspaceArtifactPublicationError(
                "workspace_chat_artifact_source_invalid"
            )
        suffix = source.suffix.lower()
        if suffix not in _CHAT_IMAGE_SUFFIXES:
            raise WorkspaceArtifactPublicationError(
                "workspace_chat_artifact_suffix_invalid"
            )
        sha256, size_bytes = _hash_file(source)
        destination = staging.artifact_directory / f"{sha256}{suffix}"
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise WorkspaceArtifactPublicationError(
                    "workspace_chat_artifact_collision"
                )
            existing_sha256, existing_size = _hash_file(destination)
            if existing_sha256 != sha256 or existing_size != size_bytes:
                raise WorkspaceArtifactPublicationError(
                    "workspace_chat_artifact_collision"
                )
        else:
            os.link(source, destination)
            _fsync_directory(destination.parent)

        artifact = promoted_by_path.get(destination)
        if artifact is None:
            artifact_id = uuid.uuid5(
                staging.artifact_id,
                f"{sha256}:{suffix}",
            )
            relative = destination.relative_to(root)
            artifact = PreparedWorkspaceArtifact(
                artifact_id=artifact_id,
                owner_id=staging.owner_id,
                operation_id=staging.operation_id,
                presentation_id=staging.presentation_id,
                kind="chat.image",
                absolute_path=destination,
                storage_key=PurePosixPath(*relative.parts).as_posix(),
                public_path=f"/app_data/{PurePosixPath(*relative.parts).as_posix()}",
                sha256=sha256,
                size_bytes=size_bytes,
                source_revision=staging.source_revision,
            )
            session.add(
                WorkspaceArtifact(
                    id=artifact.artifact_id,
                    owner_id=artifact.owner_id,
                    operation_id=artifact.operation_id,
                    presentation_id=artifact.presentation_id,
                    kind=artifact.kind,
                    status="staging",
                    storage_key=artifact.storage_key,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    source_revision=artifact.source_revision,
                )
            )
            promoted_by_path[destination] = artifact

        old_relative = source.relative_to(images_root).as_posix()
        new_relative = destination.relative_to(images_root).as_posix()
        replacements[str(source)] = str(destination)
        replacements[source.as_posix()] = destination.as_posix()
        replacements[f"/app_data/images/{old_relative}"] = (
            f"/app_data/images/{new_relative}"
        )
        replacements[old_relative] = new_relative
        source.unlink()

    staging.staging_directory.rmdir()

    return PromotedWorkspaceChatArtifacts(
        artifacts=tuple(promoted_by_path.values()),
        replacements=replacements,
    )


async def stage_workspace_chat_artifacts_for_publication(
    session: AsyncSession,
    *,
    staging: PreparedWorkspaceChatArtifactStaging,
    artifacts: tuple[PreparedWorkspaceArtifact, ...],
) -> None:
    """Bind image pointers and journal closure to the caller's final CAS UoW."""

    await session.flush()
    now = datetime.now(timezone.utc)
    for artifact in artifacts:
        if (
            artifact.operation_id != staging.operation_id
            or artifact.presentation_id != staging.presentation_id
            or artifact.source_revision != staging.source_revision
            or artifact.owner_id != staging.owner_id
            or not artifact.absolute_path.is_file()
            or artifact.absolute_path.is_symlink()
            or _hash_file(artifact.absolute_path)
            != (artifact.sha256, artifact.size_bytes)
        ):
            raise WorkspaceArtifactPublicationError(
                "workspace_chat_artifact_publication_invalid"
            )
        result = await session.execute(
            update(WorkspaceArtifact)
            .where(
                WorkspaceArtifact.id == artifact.artifact_id,
                WorkspaceArtifact.operation_id == staging.operation_id,
                WorkspaceArtifact.status == "staging",
                WorkspaceArtifact.storage_key == artifact.storage_key,
                WorkspaceArtifact.sha256 == artifact.sha256,
                WorkspaceArtifact.size_bytes == artifact.size_bytes,
            )
            .values(status="ready", expires_at=None, updated_at=now)
        )
        if result.rowcount != 1:
            raise WorkspaceArtifactPublicationError(
                "workspace_chat_artifact_intent_conflict"
            )
    journal_result = await session.execute(
        update(WorkspaceArtifact)
        .where(
            WorkspaceArtifact.id == staging.artifact_id,
            WorkspaceArtifact.operation_id == staging.operation_id,
            WorkspaceArtifact.status == "staging",
            WorkspaceArtifact.storage_key == staging.storage_key,
        )
        .values(
            status="deleted",
            expires_at=None,
            deleted_at=now,
            updated_at=now,
        )
    )
    if journal_result.rowcount != 1:
        raise WorkspaceArtifactPublicationError(
            "workspace_chat_artifact_journal_conflict"
        )


async def workspace_chat_artifacts_were_published(
    session: AsyncSession,
    *,
    staging: PreparedWorkspaceChatArtifactStaging,
    artifacts: tuple[PreparedWorkspaceArtifact, ...],
) -> bool:
    """Reconcile an ambiguous final commit before deleting external files."""

    reconcile_session = _new_independent_session(session)
    try:
        journal_status = await reconcile_session.scalar(
            select(WorkspaceArtifact.status)
            .where(
                WorkspaceArtifact.id == staging.artifact_id,
                WorkspaceArtifact.storage_key == staging.storage_key,
            )
            .execution_options(skip_owner_scope=True)
        )
        operation = (
            await reconcile_session.execute(
                select(
                    WorkspaceOperation.state,
                    WorkspaceOperation.result_revision,
                ).where(
                    WorkspaceOperation.id == staging.operation_id,
                    WorkspaceOperation.presentation_id == staging.presentation_id,
                )
            )
        ).one_or_none()
        if (
            journal_status != "deleted"
            or operation is None
            or operation.state != "succeeded"
            or operation.result_revision != staging.source_revision
        ):
            return False
        for artifact in artifacts:
            row = await reconcile_session.get(WorkspaceArtifact, artifact.artifact_id)
            if (
                row is None
                or row.status != "ready"
                or row.storage_key != artifact.storage_key
                or row.sha256 != artifact.sha256
                or row.size_bytes != artifact.size_bytes
                or not artifact.absolute_path.is_file()
            ):
                return False
        return True
    finally:
        await reconcile_session.close()


async def compensate_workspace_chat_artifact_staging(
    session: AsyncSession,
    staging: PreparedWorkspaceChatArtifactStaging,
) -> None:
    """Delete exactly one unpublished chat-attempt tree and close its journal."""

    lookup_session = _new_independent_session(session)
    try:
        status = await lookup_session.scalar(
            select(WorkspaceArtifact.status)
            .where(
                WorkspaceArtifact.id == staging.artifact_id,
                WorkspaceArtifact.storage_key == staging.storage_key,
            )
            .execution_options(skip_owner_scope=True)
        )
    finally:
        await lookup_session.close()
    if status in {None, "deleted"}:
        return

    root = _app_data_root()
    owned_directory = staging.staging_directory.parent
    expected = _chat_image_relative_directory(
        owner_id=staging.owner_id,
        operation_id=staging.operation_id,
        attempt_digest=staging.attempt_digest,
        journal_id=staging.artifact_id,
    )
    if owned_directory != root.joinpath(*expected.parts):
        raise WorkspaceArtifactPublicationError(
            "workspace_chat_artifact_cleanup_path_invalid"
        )
    try:
        _assert_path_parents_are_not_symlinks(root, owned_directory.parent)
        _remove_owned_tree(owned_directory)
    except BaseException as cleanup_error:
        artifact = PreparedWorkspaceArtifact(
            artifact_id=staging.artifact_id,
            owner_id=staging.owner_id,
            operation_id=staging.operation_id,
            presentation_id=staging.presentation_id,
            kind="chat.image.journal",
            absolute_path=owned_directory / ".journal",
            storage_key=staging.storage_key,
            public_path="",
            sha256=_STAGING_SHA256,
            size_bytes=0,
            source_revision=staging.source_revision,
        )
        recorded = await _record_cleanup_pending(
            session,
            artifact,
            error_code="workspace_chat_artifact_cleanup_failed",
        )
        if recorded:
            raise WorkspaceArtifactPublicationError(
                "artifact_cleanup_pending"
            ) from cleanup_error
        raise WorkspaceArtifactPublicationError(
            "artifact_cleanup_record_failed"
        ) from cleanup_error

    await _mark_chat_artifact_tree_deleted(session, staging)


async def _record_cleanup_pending(
    session: AsyncSession,
    artifact: PreparedWorkspaceArtifact,
    *,
    error_code: str = "artifact_compensation_delete_failed",
) -> bool:
    """Durably record an orphan using a session independent of the failed one."""

    bind = session.bind
    if bind is None:
        LOGGER.error(
            "Unable to record cleanup_pending without a database bind for %s",
            artifact.artifact_id,
        )
        return False
    cleanup_session = AsyncSession(bind=bind, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    try:
        result = await cleanup_session.execute(
            update(WorkspaceArtifact)
            .where(
                WorkspaceArtifact.id == artifact.artifact_id,
                or_(
                    WorkspaceArtifact.status == "staging",
                    and_(
                        WorkspaceArtifact.status == "cleanup_pending",
                        WorkspaceArtifact.cleanup_token.is_(None),
                    ),
                ),
            )
            .values(
                status="cleanup_pending",
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                expires_at=now,
                cleanup_token=None,
                cleanup_lease_expires_at=None,
                cleanup_attempts=WorkspaceArtifact.cleanup_attempts + 1,
                cleanup_error={"code": error_code},
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            existing = (
                await cleanup_session.execute(
                    select(WorkspaceArtifact.status)
                    .where(WorkspaceArtifact.id == artifact.artifact_id)
                    .execution_options(skip_owner_scope=True)
                )
            ).scalar_one_or_none()
            if existing is not None:
                await cleanup_session.rollback()
                return existing == "cleanup_pending"
            cleanup_session.add(
                WorkspaceArtifact(
                    id=artifact.artifact_id,
                    owner_id=artifact.owner_id,
                    operation_id=artifact.operation_id,
                    presentation_id=artifact.presentation_id,
                    kind=artifact.kind,
                    status="cleanup_pending",
                    storage_key=artifact.storage_key,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    source_revision=artifact.source_revision,
                    expires_at=now,
                    cleanup_attempts=1,
                    cleanup_error={"code": error_code},
                )
            )
        await cleanup_session.commit()
        return True
    except IntegrityError:
        await cleanup_session.rollback()
        existing = (
            await cleanup_session.execute(
                select(WorkspaceArtifact.status)
                .where(WorkspaceArtifact.id == artifact.artifact_id)
                .execution_options(skip_owner_scope=True)
            )
        ).scalar_one_or_none()
        return existing == "cleanup_pending"
    except Exception:
        await cleanup_session.rollback()
        LOGGER.error(
            "Unable to record cleanup_pending for workspace artifact %s",
            artifact.artifact_id,
            exc_info=True,
        )
        return False
    finally:
        await cleanup_session.close()


def _cleanup_candidate_predicate(now: datetime):
    retry_ready = or_(
        WorkspaceArtifact.cleanup_lease_expires_at.is_(None),
        WorkspaceArtifact.cleanup_lease_expires_at <= now,
    )
    return or_(
        and_(
            WorkspaceArtifact.status == "ready",
            WorkspaceArtifact.expires_at.is_not(None),
            WorkspaceArtifact.expires_at <= now,
        ),
        and_(
            WorkspaceArtifact.status == "staging",
            WorkspaceArtifact.expires_at.is_not(None),
            WorkspaceArtifact.expires_at <= now,
        ),
        and_(
            WorkspaceArtifact.status == "cleanup_pending",
            retry_ready,
        ),
    )


async def claim_workspace_artifact_cleanup(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> WorkspaceArtifactCleanupClaim | None:
    """Claim one expired intent/orphan with a cross-runtime lease fence."""

    claimed_at = now or datetime.now(timezone.utc)
    lease_seconds = _configured_positive_int(
        "WORKSPACE_ARTIFACT_CLEANUP_LEASE_SECONDS",
        DEFAULT_ARTIFACT_CLEANUP_LEASE_SECONDS,
        minimum=5,
    )
    bind = session.bind
    dialect_name = bind.dialect.name if bind is not None else ""
    if dialect_name == "sqlite":
        # SQLite has no SKIP LOCKED.  Serialize the tiny candidate+CAS section
        # with the same local-runtime primitive used by durable generation jobs.
        await session.execute(text("BEGIN IMMEDIATE"))

    candidate_statement = (
        select(
            WorkspaceArtifact.id,
            WorkspaceArtifact.owner_id,
            WorkspaceArtifact.operation_id,
            WorkspaceArtifact.kind,
            WorkspaceArtifact.storage_key,
            WorkspaceArtifact.cleanup_attempts,
        )
        .where(_cleanup_candidate_predicate(claimed_at))
        .order_by(
            WorkspaceArtifact.cleanup_lease_expires_at,
            WorkspaceArtifact.expires_at,
            WorkspaceArtifact.created_at,
            WorkspaceArtifact.id,
        )
        .limit(1)
        .execution_options(skip_owner_scope=True)
    )
    if dialect_name == "postgresql":
        candidate_statement = candidate_statement.with_for_update(skip_locked=True)
    candidate = (await session.execute(candidate_statement)).one_or_none()
    if candidate is None:
        await session.rollback()
        return None

    token = uuid.uuid4().hex
    attempts = int(candidate.cleanup_attempts) + 1
    result = await session.execute(
        update(WorkspaceArtifact)
        .where(
            WorkspaceArtifact.id == candidate.id,
            _cleanup_candidate_predicate(claimed_at),
        )
        .values(
            status="cleanup_pending",
            cleanup_token=token,
            cleanup_lease_expires_at=claimed_at + timedelta(seconds=lease_seconds),
            cleanup_attempts=attempts,
            updated_at=claimed_at,
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        return None
    await session.commit()
    return WorkspaceArtifactCleanupClaim(
        artifact_id=candidate.id,
        owner_id=candidate.owner_id,
        operation_id=candidate.operation_id,
        kind=candidate.kind,
        storage_key=candidate.storage_key,
        token=token,
        attempts=attempts,
    )


def _cleanup_paths_for_claim(
    claim: WorkspaceArtifactCleanupClaim,
) -> tuple[Path, Path]:
    root = _app_data_root()
    relative = PurePosixPath(claim.storage_key)
    suffix = relative.suffix.lower()
    expected = _artifact_relative_path(
        owner_id=claim.owner_id,
        operation_id=claim.operation_id,
        artifact_id=claim.artifact_id,
        suffix=suffix,
    )
    if (
        suffix not in {".pptx", ".pdf", ".bin"}
        or claim.storage_key != expected.as_posix()
    ):
        raise WorkspaceArtifactPublicationError(
            "workspace_artifact_cleanup_path_invalid"
        )
    destination = root.joinpath(*expected.parts).resolve()
    try:
        destination.relative_to(root)
    except ValueError as error:
        raise WorkspaceArtifactPublicationError(
            "workspace_artifact_cleanup_path_invalid"
        ) from error
    temporary = destination.parent / f".{claim.artifact_id}.tmp"
    return temporary, destination


def _chat_cleanup_directory_for_claim(
    claim: WorkspaceArtifactCleanupClaim,
) -> Path:
    root = _app_data_root()
    relative = PurePosixPath(claim.storage_key)
    parts = relative.parts
    if claim.owner_id is None:
        prefix = (
            "images",
            "workspace-chat",
            "anonymous",
            str(claim.operation_id),
        )
    else:
        prefix = (
            "images",
            "users",
            str(claim.owner_id),
            "workspace-chat",
            str(claim.operation_id),
        )
    if (
        len(parts) != len(prefix) + 3
        or tuple(parts[: len(prefix)]) != prefix
        or not _SHA256_PATTERN.fullmatch(parts[len(prefix)])
        or parts[len(prefix) + 1] != str(claim.artifact_id)
        or parts[len(prefix) + 2] != ".journal"
    ):
        raise WorkspaceArtifactPublicationError(
            "workspace_chat_artifact_cleanup_path_invalid"
        )
    directory = root.joinpath(*parts[:-1])
    _assert_path_parents_are_not_symlinks(root, directory.parent)
    return directory


def _delete_claimed_artifact_files(
    claim: WorkspaceArtifactCleanupClaim,
) -> None:
    """Idempotently remove both process-boundary filesystem possibilities."""

    if claim.kind == "chat.image.journal":
        _remove_owned_tree(_chat_cleanup_directory_for_claim(claim))
        return

    temporary, destination = _cleanup_paths_for_claim(claim)
    failures: list[tuple[Path, OSError]] = []
    for path in (temporary, destination):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            failures.append((path, error))
    if failures:
        failed_paths = ", ".join(str(path) for path, _error in failures)
        raise OSError(f"workspace artifact cleanup failed: {failed_paths}") from failures[0][1]


async def _finish_workspace_artifact_cleanup(
    session_factory: async_sessionmaker[AsyncSession],
    claim: WorkspaceArtifactCleanupClaim,
    *,
    now: datetime,
) -> bool:
    async with session_factory() as session:
        if claim.kind == "chat.image.journal":
            child_prefix = str(PurePosixPath(claim.storage_key).parent / "artifacts") + "/"
            await session.execute(
                update(WorkspaceArtifact)
                .where(
                    WorkspaceArtifact.operation_id == claim.operation_id,
                    WorkspaceArtifact.kind == "chat.image",
                    WorkspaceArtifact.status == "staging",
                    WorkspaceArtifact.storage_key.startswith(child_prefix),
                )
                .values(
                    status="deleted",
                    expires_at=None,
                    cleanup_token=None,
                    cleanup_lease_expires_at=None,
                    cleanup_error=None,
                    deleted_at=now,
                    updated_at=now,
                )
            )
        result = await session.execute(
            update(WorkspaceArtifact)
            .where(
                WorkspaceArtifact.id == claim.artifact_id,
                WorkspaceArtifact.status == "cleanup_pending",
                WorkspaceArtifact.cleanup_token == claim.token,
            )
            .values(
                status="deleted",
                expires_at=None,
                cleanup_token=None,
                cleanup_lease_expires_at=None,
                cleanup_error=None,
                deleted_at=now,
                updated_at=now,
            )
        )
        await session.commit()
        return result.rowcount == 1


async def _fail_workspace_artifact_cleanup(
    session_factory: async_sessionmaker[AsyncSession],
    claim: WorkspaceArtifactCleanupClaim,
    error: Exception,
    *,
    now: datetime,
) -> str:
    max_attempts = _configured_positive_int(
        "WORKSPACE_ARTIFACT_CLEANUP_MAX_ATTEMPTS",
        DEFAULT_ARTIFACT_CLEANUP_MAX_ATTEMPTS,
    )
    base_retry_seconds = _configured_positive_int(
        "WORKSPACE_ARTIFACT_CLEANUP_RETRY_SECONDS",
        DEFAULT_ARTIFACT_CLEANUP_RETRY_SECONDS,
    )
    dead_lettered = claim.attempts >= max_attempts
    retry_seconds = min(
        base_retry_seconds * (2 ** max(0, claim.attempts - 1)),
        60 * 60,
    )
    error_code = (
        error.code
        if isinstance(error, WorkspaceArtifactPublicationError)
        else "artifact_cleanup_delete_failed"
    )
    async with session_factory() as session:
        result = await session.execute(
            update(WorkspaceArtifact)
            .where(
                WorkspaceArtifact.id == claim.artifact_id,
                WorkspaceArtifact.status == "cleanup_pending",
                WorkspaceArtifact.cleanup_token == claim.token,
            )
            .values(
                status="failed" if dead_lettered else "cleanup_pending",
                cleanup_token=None,
                cleanup_lease_expires_at=(
                    None
                    if dead_lettered
                    else now + timedelta(seconds=retry_seconds)
                ),
                cleanup_error={
                    "code": error_code,
                    "message": str(error)[:512],
                },
                updated_at=now,
            )
        )
        await session.commit()
        if result.rowcount != 1:
            return "stale"
    return "dead_lettered" if dead_lettered else "retry_pending"


async def sweep_workspace_artifact_cleanup(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, int]:
    """Run a bounded leased cleanup pass in the existing maintenance loop."""

    current = now or datetime.now(timezone.utc)
    bounded_limit = max(1, min(limit, 1000))
    result = {
        "claimed": 0,
        "deleted": 0,
        "retry_pending": 0,
        "dead_lettered": 0,
        "stale": 0,
    }
    for _index in range(bounded_limit):
        async with session_factory() as session:
            claim = await claim_workspace_artifact_cleanup(session, now=current)
        if claim is None:
            break
        result["claimed"] += 1
        try:
            _delete_claimed_artifact_files(claim)
        except Exception as error:
            outcome = await _fail_workspace_artifact_cleanup(
                session_factory,
                claim,
                error,
                now=current,
            )
            result[outcome] += 1
        else:
            finalized = await _finish_workspace_artifact_cleanup(
                session_factory,
                claim,
                now=current,
            )
            result["deleted" if finalized else "stale"] += 1
    return result


async def _publication_was_committed(
    session: AsyncSession,
    *,
    artifact: PreparedWorkspaceArtifact,
) -> bool:
    """Resolve an ambiguous commit acknowledgement without deleting live data."""

    artifact_row = await session.get(WorkspaceArtifact, artifact.artifact_id)
    if artifact_row is None:
        return False
    operation_row = (
        await session.execute(
            select(
                WorkspaceOperation.state,
                WorkspaceOperation.result_revision,
                WorkspaceOperation.result,
            ).where(
                WorkspaceOperation.id == artifact.operation_id,
                WorkspaceOperation.presentation_id == artifact.presentation_id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
            )
        )
    ).one_or_none()
    presentation_row = (
        await session.execute(
            select(
                PresentationModel.lifecycle_status,
                PresentationModel.revision,
            ).where(PresentationModel.id == artifact.presentation_id)
        )
    ).one_or_none()
    linked_job_row = (
        await session.execute(
            select(
                PresentationGenerationJob.state,
                PresentationGenerationJob.attempt_token,
            ).where(
                PresentationGenerationJob.workspace_operation_id
                == artifact.operation_id,
                PresentationGenerationJob.presentation_id
                == artifact.presentation_id,
            )
        )
    ).one_or_none()
    terminal_event_cursor = (
        await session.execute(
            select(WorkspaceOperationEvent.cursor)
            .where(
                WorkspaceOperationEvent.operation_id == artifact.operation_id,
                WorkspaceOperationEvent.event_type == "operation.result",
            )
            .order_by(WorkspaceOperationEvent.sequence.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        operation_row is None
        or presentation_row is None
        or linked_job_row is None
        or terminal_event_cursor is None
    ):
        return False
    outbox_exists = (
        await session.execute(
            select(WorkspaceOutbox.id)
            .where(
                WorkspaceOutbox.operation_id == artifact.operation_id,
                WorkspaceOutbox.event_cursor == terminal_event_cursor,
                WorkspaceOutbox.topic == "workspace.operation.event",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return bool(
        artifact.absolute_path.is_file()
        and artifact_row.status == "ready"
        and artifact_row.storage_key == artifact.storage_key
        and artifact_row.sha256 == artifact.sha256
        and artifact_row.size_bytes == artifact.size_bytes
        and artifact_row.source_revision == artifact.source_revision
        and operation_row.state == "succeeded"
        and operation_row.result_revision == artifact.source_revision
        and operation_row.result == artifact.result_payload()
        and presentation_row.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
        and presentation_row.revision == artifact.source_revision
        and linked_job_row.state == "published"
        and linked_job_row.attempt_token is None
        and outbox_exists is not None
    )


async def _compensate_unpublished_artifact(
    session: AsyncSession,
    artifact: PreparedWorkspaceArtifact,
    *,
    error_code: str = "artifact_compensation_delete_failed",
) -> str | None:
    """Remove external state and durably project every unlink failure."""

    if _remove_prepared_file(artifact):
        # A failed status update is safe: the still-staging intent remains
        # discoverable and the sweeper's idempotent unlink will close it later.
        await _mark_artifact_deleted(session, artifact)
        return None
    cleanup_recorded = await _record_cleanup_pending(
        session,
        artifact,
        error_code=error_code,
    )
    return (
        "artifact_cleanup_pending"
        if cleanup_recorded
        else "artifact_cleanup_record_failed"
    )


async def compensate_workspace_artifact(
    session: AsyncSession,
    artifact: PreparedWorkspaceArtifact,
) -> None:
    """Public cancellation hook for a prepared but unpublished artifact."""

    error_code = await _compensate_unpublished_artifact(session, artifact)
    if error_code is not None:
        raise WorkspaceArtifactPublicationError(error_code)


async def publish_workspace_generation_artifact(
    session: AsyncSession,
    *,
    artifact: PreparedWorkspaceArtifact,
    expected_revision: int,
    attempt_token: str,
) -> int:
    """Fence and commit artifact, presentation revision, and operation result."""

    if artifact.source_revision != expected_revision + 1:
        await session.rollback()
        cleanup_error = await _compensate_unpublished_artifact(
            session,
            artifact,
        )
        if cleanup_error is not None:
            raise WorkspaceArtifactPublicationError(cleanup_error)
        raise WorkspaceArtifactPublicationError("workspace_artifact_revision_invalid")
    if not attempt_token:
        await session.rollback()
        cleanup_error = await _compensate_unpublished_artifact(
            session,
            artifact,
        )
        if cleanup_error is not None:
            raise WorkspaceArtifactPublicationError(cleanup_error)
        raise WorkspaceArtifactPublicationError("operation_lease_lost")

    now = datetime.now(timezone.utc)
    result_payload = artifact.result_payload()
    try:
        linked_job_result = await session.execute(
            update(PresentationGenerationJob)
            .where(
                PresentationGenerationJob.workspace_operation_id
                == artifact.operation_id,
                PresentationGenerationJob.presentation_id
                == artifact.presentation_id,
                PresentationGenerationJob.attempt_token == attempt_token,
                PresentationGenerationJob.state.in_(("running", "staging")),
            )
            .values(
                state="published",
                attempt_token=None,
                lease_expires_at=None,
                heartbeat_at=now,
                current_stage="completed",
                last_error=None,
                resume_allowed=False,
                published_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if linked_job_result.rowcount != 1:
            # Authored generation already fences and stages its job through
            # complete_publish in this same transaction. Accept only that exact
            # terminal projection; a missing or differently-owned job is an
            # invariant violation, never a reason to publish the operation alone.
            linked_job = (
                await session.execute(
                    select(PresentationGenerationJob).where(
                        PresentationGenerationJob.workspace_operation_id
                        == artifact.operation_id,
                        PresentationGenerationJob.presentation_id
                        == artifact.presentation_id,
                    )
                )
            ).scalar_one_or_none()
            if linked_job is None:
                raise WorkspaceArtifactPublicationError(
                    "presentation_generation_job_not_linked"
                )
            if not (
                linked_job.state == "published"
                and linked_job.attempt_token is None
            ):
                raise WorkspaceArtifactPublicationError("operation_lease_lost")

        operation_result = await session.execute(
            update(WorkspaceOperation)
            .where(
                WorkspaceOperation.id == artifact.operation_id,
                WorkspaceOperation.presentation_id == artifact.presentation_id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
                WorkspaceOperation.state == "running",
                WorkspaceOperation.attempt_token == attempt_token,
                WorkspaceOperation.expected_revision == expected_revision,
            )
            .values(
                state="succeeded",
                result_revision=artifact.source_revision,
                result=result_payload,
                completed_at=now,
                lease_expires_at=None,
                heartbeat_at=now,
                attempt_token=None,
                version=WorkspaceOperation.version + 1,
                updated_at=now,
            )
        )
        if operation_result.rowcount != 1:
            raise WorkspaceArtifactPublicationError("operation_lease_lost")

        presentation_result = await session.execute(
            update(PresentationModel)
            .where(
                PresentationModel.id == artifact.presentation_id,
                PresentationModel.lifecycle_status
                == PRESENTATION_LIFECYCLE_STAGING,
                PresentationModel.revision == expected_revision,
            )
            .values(
                lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
                revision=artifact.source_revision,
                updated_at=now,
            )
        )
        if presentation_result.rowcount != 1:
            raise WorkspaceArtifactPublicationError("revision_conflict")

        artifact_result = await session.execute(
            update(WorkspaceArtifact)
            .where(
                WorkspaceArtifact.id == artifact.artifact_id,
                WorkspaceArtifact.operation_id == artifact.operation_id,
                WorkspaceArtifact.presentation_id == artifact.presentation_id,
                WorkspaceArtifact.status == "staging",
                WorkspaceArtifact.storage_key == artifact.storage_key,
                WorkspaceArtifact.source_revision == artifact.source_revision,
                WorkspaceArtifact.cleanup_token.is_(None),
            )
            .values(
                status="ready",
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                expires_at=None,
                cleanup_token=None,
                cleanup_lease_expires_at=None,
                cleanup_error=None,
                updated_at=now,
            )
        )
        if artifact_result.rowcount != 1:
            raise WorkspaceArtifactPublicationError(
                "workspace_artifact_intent_conflict"
            )

        # Publication consumes the operation lease. Its terminal event and
        # notification must therefore be in this transaction too; otherwise a
        # crash before the runner finalizer leaves a succeeded operation with no
        # timeline result and no outbox work for reconnecting clients.
        from services.workspace_operation_runner import append_operation_event

        terminal_event = await append_operation_event(
            session,
            operation_id=artifact.operation_id,
            event_type="operation.result",
            payload={"state": "succeeded", **result_payload},
            allowed_states=("succeeded",),
            additional_conditions=(
                WorkspaceOperation.result_revision == artifact.source_revision,
            ),
        )
        if terminal_event is None:
            raise WorkspaceArtifactPublicationError(
                "workspace_publication_event_conflict"
            )
        await session.commit()
        return artifact.source_revision
    except BaseException as publication_error:
        try:
            await session.rollback()
            # A driver may report commit failure after the database committed.
            # Reconcile the complete tuple before compensating the external file.
            if await _publication_was_committed(session, artifact=artifact):
                return artifact.source_revision
            await session.rollback()
        except Exception:
            await session.rollback()
            LOGGER.warning(
                "Unable to reconcile workspace artifact publication %s",
                artifact.artifact_id,
                exc_info=True,
            )

        cleanup_error = await _compensate_unpublished_artifact(
            session,
            artifact,
        )
        if cleanup_error is None:
            raise
        raise WorkspaceArtifactPublicationError(cleanup_error) from publication_error


async def _export_publication_was_committed(
    session: AsyncSession,
    *,
    artifact: PreparedWorkspaceArtifact,
    result_payload: dict[str, object],
) -> bool:
    """Reconcile an ambiguous export commit acknowledgement as one tuple."""

    artifact_row = await session.get(WorkspaceArtifact, artifact.artifact_id)
    operation_row = (
        await session.execute(
            select(
                WorkspaceOperation.state,
                WorkspaceOperation.result_revision,
                WorkspaceOperation.result,
            ).where(
                WorkspaceOperation.id == artifact.operation_id,
                WorkspaceOperation.presentation_id == artifact.presentation_id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
            )
        )
    ).one_or_none()
    presentation_revision = await session.scalar(
        select(PresentationModel.revision).where(
            PresentationModel.id == artifact.presentation_id
        )
    )
    terminal_cursor = await session.scalar(
        select(WorkspaceOperationEvent.cursor)
        .where(
            WorkspaceOperationEvent.operation_id == artifact.operation_id,
            WorkspaceOperationEvent.event_type == "operation.result",
        )
        .order_by(WorkspaceOperationEvent.sequence.desc())
        .limit(1)
    )
    if artifact_row is None or operation_row is None or terminal_cursor is None:
        return False
    outbox_id = await session.scalar(
        select(WorkspaceOutbox.id)
        .where(
            WorkspaceOutbox.operation_id == artifact.operation_id,
            WorkspaceOutbox.event_cursor == terminal_cursor,
            WorkspaceOutbox.topic == "workspace.operation.event",
        )
        .limit(1)
    )
    return bool(
        artifact.absolute_path.is_file()
        and artifact_row.status == "ready"
        and artifact_row.storage_key == artifact.storage_key
        and artifact_row.sha256 == artifact.sha256
        and artifact_row.size_bytes == artifact.size_bytes
        and artifact_row.source_revision == artifact.source_revision
        and operation_row.state == "succeeded"
        and operation_row.result_revision == artifact.source_revision
        and operation_row.result == result_payload
        and presentation_revision == artifact.source_revision
        and outbox_id is not None
    )


async def publish_workspace_export_artifact(
    session: AsyncSession,
    *,
    artifact: PreparedWorkspaceArtifact,
    attempt_token: str,
    fencing_token: int,
    export_format: str,
) -> int:
    """Publish a read-only export without advancing the deck revision.

    The presentation no-op CAS, artifact pointer, terminal operation projection,
    event and outbox notification commit together. The external immutable file
    is compensated whenever that tuple was not durably committed.
    """

    if export_format not in {"pptx", "pdf"}:
        raise WorkspaceArtifactPublicationError("workspace_export_format_invalid")
    if not attempt_token:
        await session.rollback()
        await _compensate_unpublished_artifact(session, artifact)
        raise WorkspaceArtifactPublicationError("operation_lease_lost")

    now = datetime.now(timezone.utc)
    export_expires_at = now + timedelta(
        seconds=_configured_positive_int(
            "WORKSPACE_EXPORT_ARTIFACT_TTL_SECONDS",
            DEFAULT_EXPORT_ARTIFACT_TTL_SECONDS,
            minimum=60,
        )
    )
    # Storage paths and request-scoped access URLs are deliberately absent
    # from the durable operation/event projection. Clients resolve an
    # artifact id through the owner-authenticated endpoint at click time.
    result_payload: dict[str, object] = {
        "artifact_id": str(artifact.artifact_id),
        "artifact_kind": artifact.kind,
        "artifact_sha256": artifact.sha256,
        "artifact_size_bytes": artifact.size_bytes,
        "format": export_format,
        "source_revision": artifact.source_revision,
        "expires_at": export_expires_at.isoformat(),
    }
    try:
        presentation_cas = await session.execute(
            update(PresentationModel)
            .where(
                PresentationModel.id == artifact.presentation_id,
                PresentationModel.revision == artifact.source_revision,
            )
            .values(revision=PresentationModel.revision)
        )
        if presentation_cas.rowcount != 1:
            raise WorkspaceArtifactPublicationError("stale_presentation_revision")

        operation_cas = await session.execute(
            update(WorkspaceOperation)
            .where(
                WorkspaceOperation.id == artifact.operation_id,
                WorkspaceOperation.presentation_id == artifact.presentation_id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
                WorkspaceOperation.state == "running",
                WorkspaceOperation.attempt_token == attempt_token,
                WorkspaceOperation.fencing_token == fencing_token,
                WorkspaceOperation.expected_revision == artifact.source_revision,
            )
            .values(
                state="succeeded",
                result_revision=artifact.source_revision,
                result=result_payload,
                completed_at=now,
                lease_expires_at=None,
                heartbeat_at=now,
                attempt_token=None,
                version=WorkspaceOperation.version + 1,
                updated_at=now,
            )
        )
        if operation_cas.rowcount != 1:
            raise WorkspaceArtifactPublicationError("operation_lease_lost")

        artifact_cas = await session.execute(
            update(WorkspaceArtifact)
            .where(
                WorkspaceArtifact.id == artifact.artifact_id,
                WorkspaceArtifact.operation_id == artifact.operation_id,
                WorkspaceArtifact.presentation_id == artifact.presentation_id,
                WorkspaceArtifact.status == "staging",
                WorkspaceArtifact.storage_key == artifact.storage_key,
                WorkspaceArtifact.source_revision == artifact.source_revision,
                WorkspaceArtifact.cleanup_token.is_(None),
            )
            .values(
                status="ready",
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                expires_at=export_expires_at,
                cleanup_token=None,
                cleanup_lease_expires_at=None,
                cleanup_error=None,
                updated_at=now,
            )
        )
        if artifact_cas.rowcount != 1:
            raise WorkspaceArtifactPublicationError(
                "workspace_artifact_intent_conflict"
            )

        from services.workspace_operation_runner import append_operation_event

        terminal_event = await append_operation_event(
            session,
            operation_id=artifact.operation_id,
            event_type="operation.result",
            payload={"state": "succeeded", **result_payload},
            allowed_states=("succeeded",),
            additional_conditions=(
                WorkspaceOperation.result_revision == artifact.source_revision,
            ),
        )
        if terminal_event is None:
            raise WorkspaceArtifactPublicationError(
                "workspace_publication_event_conflict"
            )
        await session.commit()
        return artifact.source_revision
    except BaseException as publication_error:
        try:
            await session.rollback()
            if await _export_publication_was_committed(
                session,
                artifact=artifact,
                result_payload=result_payload,
            ):
                return artifact.source_revision
            await session.rollback()
        except Exception:
            await session.rollback()
            LOGGER.warning(
                "Unable to reconcile workspace export publication %s",
                artifact.artifact_id,
                exc_info=True,
            )
        cleanup_error = await _compensate_unpublished_artifact(session, artifact)
        if cleanup_error is None:
            raise
        raise WorkspaceArtifactPublicationError(cleanup_error) from publication_error


async def resolve_workspace_export_artifact(
    session: AsyncSession,
    *,
    artifact_id: uuid.UUID,
    now: datetime | None = None,
) -> ResolvedWorkspaceExportArtifact:
    """Resolve one live export through its owner-scoped durable pointer.

    This method creates no durable access credential. Every call rechecks the
    artifact state, expiry and operation ownership before returning a private
    server-side path for the API's direct FileResponse.
    """

    current = now or datetime.now(timezone.utc)
    row = (
        await session.execute(
            select(WorkspaceArtifact, WorkspaceOperation.owner_scope)
            .join(
                WorkspaceOperation,
                WorkspaceOperation.id == WorkspaceArtifact.operation_id,
            )
            .where(
                WorkspaceArtifact.id == artifact_id,
                WorkspaceArtifact.owner_id == get_current_owner_id(),
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
            )
        )
    ).one_or_none()
    if row is None:
        raise WorkspaceArtifactPublicationError("workspace_export_not_found")
    artifact, _owner_scope = row
    if artifact.kind not in {"export.pptx", "export.pdf"}:
        raise WorkspaceArtifactPublicationError("workspace_export_not_found")
    expires_at = artifact.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is None or expires_at <= current:
        raise WorkspaceArtifactPublicationError("workspace_export_expired")
    if artifact.status != "ready":
        raise WorkspaceArtifactPublicationError("workspace_export_not_ready")
    if artifact.presentation_id is None or artifact.source_revision is None:
        raise WorkspaceArtifactPublicationError("workspace_export_not_ready")

    absolute_path = (_app_data_root() / artifact.storage_key).resolve()
    try:
        absolute_path.relative_to(_app_data_root())
    except ValueError as error:
        raise WorkspaceArtifactPublicationError(
            "workspace_artifact_path_invalid"
        ) from error
    if not absolute_path.is_file():
        raise WorkspaceArtifactPublicationError("workspace_export_missing")

    return ResolvedWorkspaceExportArtifact(
        artifact_id=artifact.id,
        operation_id=artifact.operation_id,
        presentation_id=artifact.presentation_id,
        kind=artifact.kind,
        source_revision=artifact.source_revision,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        expires_at=expires_at,
        absolute_path=absolute_path,
    )
