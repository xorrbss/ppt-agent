"""Durable, fenced cleanup for storage objects whose database owner is deleted.

The source rows and their files cannot be removed atomically.  Callers first
snapshot every target into ``workspace_cleanup_journal`` in the same database
transaction as the destructive mutation.  This worker then performs the
idempotent filesystem side effect and records its outcome independently of the
presentation/user rows that may already have cascaded away.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import and_, event, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session as SyncSession

from constants.documents import UPLOAD_ACCEPTED_EXTENSIONS
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.template_v2_revision import TemplateV2Revision
from models.sql.workspace import (
    WorkspaceArtifact,
    WorkspaceAsset,
    WorkspaceCleanupJournal,
    WorkspaceConversation,
    WorkspaceOperation,
)
from services.authored_checkpoint_service import checkpoint_root
from services.database import async_session_maker
from services.template_v2_native_storage import native_source_root, remove_native_source
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactCleanupClaim,
    _app_data_root,
    _delete_claimed_artifact_files,
    _remove_owned_tree,
)
from services.workspace_metrics import WORKSPACE_METRICS
from utils.datetime_utils import get_current_utc_datetime
from utils.get_env import get_app_data_directory_env, get_temp_directory_env
from utils.path_helpers import get_writable_path

DEFAULT_CLEANUP_LEASE_SECONDS = 60
DEFAULT_CLEANUP_RETRY_SECONDS = 15
DEFAULT_CLEANUP_MAX_ATTEMPTS = 8
DEFAULT_CLEANUP_MAINTENANCE_INTERVAL_SECONDS = 60
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CLEANUP_METADATA_SCHEMA_VERSION = "workspace.cleanup-target/v2"
_FILESYSTEM_STORAGE_BACKEND = "filesystem"
_WORKSPACE_ASSET_ROOT_KIND = "workspace_assets"
_WORKSPACE_ARTIFACT_ROOT_KIND = "app_data"
_AUTHORED_CHECKPOINT_ROOT_KIND = "authored_checkpoints"
_TEMPLATE_V2_NATIVE_ROOT_KIND = "template_v2_native"
_AUTHORED_IMAGES_ROOT_KIND = "authored_images"
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

logger = logging.getLogger(__name__)
_cleanup_maintenance_task: asyncio.Task[None] | None = None
_cleanup_maintenance_stop: asyncio.Event | None = None
_CLEANUP_METRIC_QUEUE_KEY = "workspace_cleanup_metric_outcomes"


def _record_cleanup_metric(outcome: str, *, count: int = 1) -> None:
    """Keep post-commit telemetry failures outside cleanup correctness."""

    try:
        for _ in range(max(0, count)):
            WORKSPACE_METRICS.record_cleanup_outcome(outcome)
    except Exception:
        logger.warning(
            "Unable to record workspace cleanup outcome %s",
            outcome,
            exc_info=True,
        )


def _queue_cleanup_metric_after_commit(
    session: AsyncSession,
    outcome: str,
    *,
    count: int = 1,
) -> None:
    """Delay duplicate observations until the caller's transaction commits."""

    pending = session.sync_session.info.setdefault(_CLEANUP_METRIC_QUEUE_KEY, {})
    pending[outcome] = int(pending.get(outcome, 0)) + max(0, count)


@event.listens_for(SyncSession, "after_commit")
def _flush_cleanup_metrics_after_commit(session: SyncSession) -> None:
    # SQLAlchemy also emits after_commit for SAVEPOINT release.  The nested
    # transaction is still visible during that callback, so only the outer
    # commit proves the cleanup replay observation durable.
    if session.in_nested_transaction():
        return
    pending = session.info.pop(_CLEANUP_METRIC_QUEUE_KEY, {})
    for outcome, count in pending.items():
        _record_cleanup_metric(str(outcome), count=int(count))


@event.listens_for(SyncSession, "after_rollback")
def _discard_cleanup_metrics_after_rollback(session: SyncSession) -> None:
    if not session.in_nested_transaction():
        session.info.pop(_CLEANUP_METRIC_QUEUE_KEY, None)


@dataclass(frozen=True)
class WorkspaceCleanupTarget:
    """Immutable description of one owned filesystem resource."""

    identity: str
    owner_id: uuid.UUID | None
    presentation_id: uuid.UUID | None
    operation_id: uuid.UUID | None
    storage_namespace: str
    resource_kind: str
    storage_key: str
    sha256: str | None = None
    size_bytes: int | None = None
    source_revision: int | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class WorkspaceCleanupClaim:
    journal_id: uuid.UUID
    owner_id: uuid.UUID | None
    presentation_id: uuid.UUID | None
    operation_id: uuid.UUID | None
    storage_namespace: str
    resource_kind: str
    storage_key: str
    sha256: str | None
    size_bytes: int | None
    source_revision: int | None
    resource_metadata: dict[str, Any]
    token: str
    fencing_token: int
    attempts: int
    max_attempts: int


@dataclass(frozen=True)
class WorkspaceCleanupRedriveResult:
    journal_id: uuid.UUID
    outcome: str
    status: str | None
    max_attempts: int | None
    redrive_count: int | None


class WorkspaceCleanupDeleteError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def _canonical_storage_root(root: Path) -> Path:
    """Return a stable absolute filesystem identity suitable for journaling."""

    return Path(os.path.abspath(root)).resolve(strict=False)


def _storage_backend_generation(*, root_kind: str, root: Path) -> str:
    canonical = json.dumps(
        {
            "backend": _FILESYSTEM_STORAGE_BACKEND,
            "root_kind": root_kind,
            "root": os.path.normcase(str(_canonical_storage_root(root))),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _storage_root_metadata(*, root_kind: str, root: Path) -> dict[str, str]:
    canonical_root = _canonical_storage_root(root)
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


def _claim_storage_root(
    claim: WorkspaceCleanupClaim,
    *,
    expected_root_kind: str,
    invalid_code: str,
) -> Path:
    """Validate and recover the exact trusted root captured before mutation.

    Cleanup intentionally never consults the current environment.  A root
    rotation can therefore create the same relative key without an older
    journal entry deleting the new generation.
    """

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


def _authored_images_root(owner_id: uuid.UUID | None) -> Path:
    root = _app_data_root() / "images"
    if owner_id is not None:
        root = root / "users" / str(owner_id)
    return _canonical_storage_root(root)


def workspace_asset_cleanup_target(
    asset: WorkspaceAsset,
    *,
    presentation_id: uuid.UUID | None = None,
    storage_root: Path | None = None,
) -> WorkspaceCleanupTarget:
    owner_scope = str(asset.owner_id) if asset.owner_id is not None else "anonymous"
    root = _canonical_storage_root(
        storage_root or Path(get_writable_path("workspace_assets"))
    )
    return WorkspaceCleanupTarget(
        identity=f"workspace_asset:{asset.storage_key}",
        owner_id=asset.owner_id,
        presentation_id=presentation_id,
        operation_id=None,
        storage_namespace="workspace_asset",
        resource_kind="workspace.asset",
        storage_key=asset.storage_key,
        sha256=asset.sha256,
        size_bytes=asset.size_bytes,
        source_revision=asset.version,
        metadata={
            **_storage_root_metadata(
                root_kind=_WORKSPACE_ASSET_ROOT_KIND,
                root=root,
            ),
            "asset_id": str(asset.id),
            "owner_scope": owner_scope,
        },
    )


def workspace_artifact_cleanup_target(
    artifact: WorkspaceArtifact,
    *,
    storage_root: Path | None = None,
) -> WorkspaceCleanupTarget:
    root = _canonical_storage_root(storage_root or _app_data_root())
    chat_target = _workspace_chat_tree_cleanup_target(
        artifact,
        storage_root=root,
    )
    if chat_target is not None:
        return chat_target
    return WorkspaceCleanupTarget(
        identity=f"workspace_artifact:{artifact.storage_key}",
        owner_id=artifact.owner_id,
        presentation_id=artifact.presentation_id,
        operation_id=artifact.operation_id,
        storage_namespace="workspace_artifact",
        resource_kind="workspace.artifact",
        storage_key=artifact.storage_key,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        source_revision=artifact.source_revision,
        metadata={
            **_storage_root_metadata(
                root_kind=_WORKSPACE_ARTIFACT_ROOT_KIND,
                root=root,
            ),
            "artifact_id": str(artifact.id),
            "artifact_kind": artifact.kind,
        },
    )


def _workspace_chat_tree_cleanup_target(
    artifact: WorkspaceArtifact,
    *,
    storage_root: Path,
) -> WorkspaceCleanupTarget | None:
    """Collapse a journal and all of its image children to one owned tree."""

    if artifact.kind not in {"chat.image", "chat.image.journal"}:
        return None
    pure = PurePosixPath(artifact.storage_key)
    if pure.is_absolute() or "\\" in artifact.storage_key or ".." in pure.parts:
        return None
    if artifact.owner_id is None:
        prefix = (
            "images",
            "workspace-chat",
            "anonymous",
            str(artifact.operation_id),
        )
    else:
        prefix = (
            "images",
            "users",
            str(artifact.owner_id),
            "workspace-chat",
            str(artifact.operation_id),
        )
    parts = pure.parts
    tail = parts[len(prefix) :]
    if tuple(parts[: len(prefix)]) != prefix:
        return None
    if artifact.kind == "chat.image.journal":
        if len(tail) != 3 or tail[2] != ".journal":
            return None
    else:
        if (
            len(tail) != 4
            or tail[2] != "artifacts"
            or PurePosixPath(tail[3]).suffix.lower() not in _CHAT_IMAGE_SUFFIXES
        ):
            return None
    attempt_digest, journal_id_raw = tail[:2]
    if _SHA256_PATTERN.fullmatch(attempt_digest) is None:
        return None
    try:
        journal_id = uuid.UUID(journal_id_raw)
    except ValueError:
        return None
    journal_key = PurePosixPath(*prefix, attempt_digest, str(journal_id), ".journal")
    return WorkspaceCleanupTarget(
        identity=f"workspace_chat_tree:{journal_key.as_posix()}",
        owner_id=artifact.owner_id,
        presentation_id=artifact.presentation_id,
        operation_id=artifact.operation_id,
        storage_namespace="workspace_artifact",
        resource_kind="workspace.artifact.chat_tree",
        storage_key=journal_key.as_posix(),
        source_revision=artifact.source_revision,
        metadata={
            **_storage_root_metadata(
                root_kind=_WORKSPACE_ARTIFACT_ROOT_KIND,
                root=storage_root,
            ),
            "artifact_id": str(journal_id),
            "artifact_kind": "chat.image.journal",
        },
    )


def workspace_asset_file_cleanup_target(
    file_path: str,
    *,
    owner_id: uuid.UUID | None,
    presentation_id: uuid.UUID | None,
) -> WorkspaceCleanupTarget | None:
    """Snapshot an exact legacy presentation path in the asset namespace."""

    if (
        not isinstance(file_path, str)
        or not file_path
        or not Path(file_path).is_absolute()
    ):
        return None
    root = _canonical_storage_root(Path(get_writable_path("workspace_assets")))
    lexical = Path(os.path.abspath(file_path))
    try:
        relative = lexical.relative_to(root)
    except ValueError:
        return None
    parts = relative.parts
    expected_scope = str(owner_id) if owner_id is not None else "anonymous"
    if len(parts) != 3 or parts[0] != expected_scope:
        return None
    try:
        asset_id = uuid.UUID(parts[1])
    except ValueError:
        return None
    leaf = PurePosixPath(parts[2])
    digest = leaf.stem
    suffix = leaf.suffix.lower()
    if (
        _SHA256_PATTERN.fullmatch(digest) is None
        or suffix not in _WORKSPACE_ASSET_SUFFIXES
        or leaf.name != f"{digest}{suffix}"
    ):
        return None
    storage_key = PurePosixPath(*parts).as_posix()
    return WorkspaceCleanupTarget(
        identity=f"workspace_asset:{storage_key}",
        owner_id=owner_id,
        presentation_id=presentation_id,
        operation_id=None,
        storage_namespace="workspace_asset",
        resource_kind="workspace.asset.presentation_path",
        storage_key=storage_key,
        sha256=digest,
        size_bytes=(
            lexical.stat().st_size
            if lexical.exists() and lexical.is_file() and not lexical.is_symlink()
            else None
        ),
        metadata={
            **_storage_root_metadata(
                root_kind=_WORKSPACE_ASSET_ROOT_KIND,
                root=root,
            ),
            "asset_id": str(asset_id),
            "owner_scope": expected_scope,
            "source": "presentation.file_paths",
        },
    )


def authored_checkpoint_cleanup_target(
    job_id: uuid.UUID,
    *,
    owner_id: uuid.UUID | None,
    presentation_id: uuid.UUID | None,
    operation_id: uuid.UUID | None = None,
) -> WorkspaceCleanupTarget:
    root = _canonical_storage_root(checkpoint_root())
    return WorkspaceCleanupTarget(
        identity=f"authored_checkpoint:{job_id}",
        owner_id=owner_id,
        presentation_id=presentation_id,
        operation_id=operation_id,
        storage_namespace="authored_checkpoint",
        resource_kind="authored.checkpoint_directory",
        storage_key=str(job_id),
        metadata={
            **_storage_root_metadata(
                root_kind=_AUTHORED_CHECKPOINT_ROOT_KIND,
                root=root,
            ),
            "job_id": str(job_id),
        },
    )


def template_v2_native_cleanup_target(
    *,
    template_id: str,
    revision: int,
    storage_key: str,
    sha256_value: str | None,
    size_bytes: int | None,
    owner_id: uuid.UUID | None,
    presentation_id: uuid.UUID | None,
) -> WorkspaceCleanupTarget:
    root = _canonical_storage_root(native_source_root())
    return WorkspaceCleanupTarget(
        identity=f"template_v2_native:{template_id}:{revision}",
        owner_id=owner_id,
        presentation_id=presentation_id,
        operation_id=None,
        storage_namespace="template_v2_native",
        resource_kind="template_v2.native_source",
        storage_key=storage_key,
        sha256=sha256_value,
        size_bytes=size_bytes,
        source_revision=revision,
        metadata={
            **_storage_root_metadata(
                root_kind=_TEMPLATE_V2_NATIVE_ROOT_KIND,
                root=root,
            ),
            "template_id": template_id,
            "revision": revision,
        },
    )


def authored_image_tree_cleanup_target(
    *,
    owner_id: uuid.UUID | None,
    presentation_id: uuid.UUID,
    storage_root: Path | None = None,
) -> WorkspaceCleanupTarget:
    """Snapshot the immutable authored slide-image tree for one presentation."""

    root = _canonical_storage_root(
        storage_root or _authored_images_root(owner_id)
    )
    storage_key = PurePosixPath("authored", str(presentation_id)).as_posix()
    owner_scope = str(owner_id) if owner_id is not None else "anonymous"
    return WorkspaceCleanupTarget(
        identity=f"authored_image_tree:{owner_scope}:{presentation_id}",
        owner_id=owner_id,
        presentation_id=presentation_id,
        operation_id=None,
        storage_namespace="authored_image_tree",
        resource_kind="authored.slide_image_tree",
        storage_key=storage_key,
        metadata={
            **_storage_root_metadata(
                root_kind=_AUTHORED_IMAGES_ROOT_KIND,
                root=root,
            ),
            "owner_scope": owner_scope,
            "presentation_id": str(presentation_id),
        },
    )


def _account_owned_tree_roots() -> dict[str, Path]:
    """Resolve the configured roots that contain one UUID directory per user."""

    roots: dict[str, Path] = {}
    app_data = get_app_data_directory_env()
    if isinstance(app_data, str) and app_data.strip():
        for name in _ACCOUNT_APP_DATA_TREE_NAMES:
            roots[f"app_data.{name}"] = Path(
                os.path.abspath(os.path.join(app_data, name, "users"))
            ).resolve(strict=False)
    temp_directory = get_temp_directory_env() or "/tmp/presenton"
    roots[_ACCOUNT_TEMP_TREE_KIND] = Path(
        os.path.abspath(temp_directory)
    ).resolve(strict=False)
    return roots


def account_owned_tree_cleanup_target(
    *,
    owner_id: uuid.UUID,
    root_kind: str,
    root: Path,
) -> WorkspaceCleanupTarget:
    """Snapshot one exact ``<configured-root>/<owner UUID>`` directory."""

    resolved_root = _canonical_storage_root(root)
    owned_directory = resolved_root / str(owner_id)
    return WorkspaceCleanupTarget(
        identity=f"account_owned_tree:{root_kind}:{owner_id}",
        owner_id=owner_id,
        presentation_id=None,
        operation_id=None,
        storage_namespace="account_owned_tree",
        resource_kind="account.owned_tree",
        storage_key=str(owned_directory),
        metadata={
            **_storage_root_metadata(root_kind=root_kind, root=resolved_root),
            "owner_id": str(owner_id),
        },
    )


def _idempotency_hash(
    target: WorkspaceCleanupTarget,
) -> str:
    # Physical immutable key identity makes duplicate commands, a chat journal
    # plus its child image rows, and legacy file_paths converge on one intent.
    canonical = json.dumps(
        {
            "namespace": target.storage_namespace,
            "storage_key": target.storage_key,
            "storage_backend_generation": (
                dict(target.metadata or {}).get("storage_backend_generation")
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _json_snapshot(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    # Also proves that journal metadata is JSON-safe before the source rows are
    # mutated, and prevents later in-memory changes from altering the snapshot.
    return json.loads(json.dumps(dict(value), sort_keys=True))


async def enqueue_workspace_cleanup_targets(
    session: AsyncSession,
    *,
    targets: list[WorkspaceCleanupTarget],
) -> int:
    """Persist cleanup intents inside the caller's destructive transaction."""

    inserted = 0
    for target in targets:
        digest = _idempotency_hash(target)
        existing = await session.scalar(
            select(WorkspaceCleanupJournal.id).where(
                WorkspaceCleanupJournal.idempotency_key_hash == digest
            )
        )
        if existing is not None:
            _queue_cleanup_metric_after_commit(session, "duplicate")
            continue
        try:
            async with session.begin_nested():
                session.add(
                    WorkspaceCleanupJournal(
                        owner_id_snapshot=target.owner_id,
                        presentation_id_snapshot=target.presentation_id,
                        operation_id_snapshot=target.operation_id,
                        storage_namespace=target.storage_namespace,
                        resource_kind=target.resource_kind,
                        storage_key=target.storage_key,
                        sha256=target.sha256,
                        size_bytes=target.size_bytes,
                        source_revision=target.source_revision,
                        resource_metadata=_json_snapshot(target.metadata),
                        idempotency_key_hash=digest,
                        max_attempts=DEFAULT_CLEANUP_MAX_ATTEMPTS,
                    )
                )
                await session.flush()
        except IntegrityError:
            # A concurrent retry already recorded the same immutable resource.
            _queue_cleanup_metric_after_commit(session, "duplicate")
            continue
        inserted += 1
    return inserted


async def account_cleanup_tombstone_exists(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
) -> bool:
    """Return whether account deletion durably reached its snapshot phase.

    Account snapshots always include at least the configured temporary owner
    tree.  Those owner-independent journal rows survive the user FK cascade,
    so they distinguish a completed DELETE retry from an arbitrary unknown
    UUID without adding a second deletion-receipt table.
    """

    return (
        await session.scalar(
            select(WorkspaceCleanupJournal.id)
            .where(
                WorkspaceCleanupJournal.owner_id_snapshot == owner_id,
                WorkspaceCleanupJournal.storage_namespace == "account_owned_tree",
                WorkspaceCleanupJournal.resource_kind == "account.owned_tree",
            )
            .limit(1)
            .execution_options(skip_owner_scope=True)
        )
        is not None
    )


async def snapshot_account_workspace_cleanup_targets(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
) -> int:
    """Snapshot every private storage target before the user's FK cascade."""

    presentations = list(
        (
            await session.scalars(
                select(PresentationModel)
                .where(PresentationModel.owner_id == owner_id)
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    presentation_ids = {row.id for row in presentations}
    conversation_predicates = [WorkspaceConversation.owner_id == owner_id]
    if presentation_ids:
        conversation_predicates.append(
            WorkspaceConversation.presentation_id.in_(presentation_ids)
        )
    conversation_ids = set(
        (
            await session.scalars(
                select(WorkspaceConversation.id)
                .where(or_(*conversation_predicates))
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    operation_predicates = [WorkspaceOperation.owner_id == owner_id]
    if presentation_ids:
        operation_predicates.append(
            WorkspaceOperation.presentation_id.in_(presentation_ids)
        )
    if conversation_ids:
        operation_predicates.append(
            WorkspaceOperation.conversation_id.in_(conversation_ids)
        )
    operation_ids = set(
        (
            await session.scalars(
                select(WorkspaceOperation.id)
                .where(or_(*operation_predicates))
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    asset_predicates = [WorkspaceAsset.owner_id == owner_id]
    if conversation_ids:
        asset_predicates.append(WorkspaceAsset.conversation_id.in_(conversation_ids))
    asset_rows = list(
        (
            await session.execute(
                select(WorkspaceAsset, WorkspaceConversation.presentation_id)
                .outerjoin(
                    WorkspaceConversation,
                    WorkspaceConversation.id == WorkspaceAsset.conversation_id,
                )
                .where(or_(*asset_predicates))
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    artifact_predicates = [WorkspaceArtifact.owner_id == owner_id]
    if presentation_ids:
        artifact_predicates.append(
            WorkspaceArtifact.presentation_id.in_(presentation_ids)
        )
    if operation_ids:
        artifact_predicates.append(WorkspaceArtifact.operation_id.in_(operation_ids))
    artifacts = list(
        (
            await session.scalars(
                select(WorkspaceArtifact)
                .where(or_(*artifact_predicates))
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )

    surviving_file_paths = {
        normalized
        for paths in (
            await session.scalars(
                select(PresentationModel.file_paths)
                .where(
                    or_(
                        PresentationModel.owner_id.is_(None),
                        PresentationModel.owner_id != owner_id,
                    )
                )
                .execution_options(skip_owner_scope=True)
            )
        ).all()
        if isinstance(paths, list)
        for value in paths
        if (normalized := _normalized_absolute_path(value)) is not None
    }

    targets: list[WorkspaceCleanupTarget] = [
        account_owned_tree_cleanup_target(
            owner_id=owner_id,
            root_kind=root_kind,
            root=root,
        )
        for root_kind, root in _account_owned_tree_roots().items()
    ]
    asset_root = _canonical_storage_root(Path(get_writable_path("workspace_assets")))
    artifact_root = _canonical_storage_root(_app_data_root())
    for asset, presentation_id in asset_rows:
        physical_path = _normalized_absolute_path(
            str(asset_root.joinpath(*PurePosixPath(asset.storage_key).parts))
        )
        if physical_path not in surviving_file_paths:
            targets.append(
                workspace_asset_cleanup_target(
                    asset,
                    presentation_id=presentation_id,
                    storage_root=asset_root,
                )
            )
    targets.extend(
        workspace_artifact_cleanup_target(row, storage_root=artifact_root)
        for row in artifacts
    )

    owned_task_ids = select(AsyncPresentationGenerationTaskModel.id).where(
        AsyncPresentationGenerationTaskModel.owner_id == owner_id
    )
    job_predicates = [PresentationGenerationJob.task_id.in_(owned_task_ids)]
    if presentation_ids:
        job_predicates.append(
            PresentationGenerationJob.presentation_id.in_(presentation_ids)
        )
    jobs = list(
        (
            await session.scalars(
                select(PresentationGenerationJob)
                .where(or_(*job_predicates))
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    targets.extend(
        authored_checkpoint_cleanup_target(
            job.id,
            owner_id=owner_id,
            presentation_id=job.presentation_id,
            operation_id=job.workspace_operation_id,
        )
        for job in jobs
    )

    sidecar_template_ids: set[str] = set()
    if presentation_ids:
        sidecar_template_ids = set(
            (
                await session.scalars(
                    select(TemplateV2LocalState.template_id)
                    .where(TemplateV2LocalState.presentation_id.in_(presentation_ids))
                    .execution_options(skip_owner_scope=True)
                )
            ).all()
        )
    template_predicates = [TemplateV2.owner_id == owner_id]
    if presentation_ids:
        template_predicates.append(TemplateV2.presentation_id.in_(presentation_ids))
    if sidecar_template_ids:
        template_predicates.append(TemplateV2.id.in_(sidecar_template_ids))
    templates = list(
        (
            await session.scalars(
                select(TemplateV2)
                .where(or_(*template_predicates))
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    template_ids = {row.id for row in templates}
    revisions = (
        list(
            (
                await session.scalars(
                    select(TemplateV2Revision)
                    .where(TemplateV2Revision.template_id.in_(template_ids))
                    .execution_options(skip_owner_scope=True)
                )
            ).all()
        )
        if template_ids
        else []
    )
    presentation_by_template = {
        row.id: row.presentation_id for row in templates
    }
    targets.extend(
        target
        for template in templates
        if (
            target := _template_native_target_from_assets(
                template_id=template.id,
                revision=template.revision,
                assets=template.assets,
                owner_id=owner_id,
                presentation_id=template.presentation_id,
            )
        )
        is not None
    )
    targets.extend(
        target
        for revision in revisions
        if (
            target := _template_native_target_from_assets(
                template_id=revision.template_id,
                revision=revision.revision,
                assets=revision.assets,
                owner_id=owner_id,
                presentation_id=presentation_by_template.get(revision.template_id),
            )
        )
        is not None
    )

    for presentation in presentations:
        if not isinstance(presentation.file_paths, list):
            continue
        for file_path in presentation.file_paths:
            normalized = _normalized_absolute_path(file_path)
            if normalized is None or normalized in surviving_file_paths:
                continue
            target = workspace_asset_file_cleanup_target(
                file_path,
                owner_id=owner_id,
                presentation_id=presentation.id,
            )
            if target is not None:
                targets.append(target)
    return await enqueue_workspace_cleanup_targets(session, targets=targets)


async def snapshot_presentation_workspace_cleanup_targets(
    session: AsyncSession,
    *,
    presentation: PresentationModel,
) -> int:
    """Snapshot every physical target before a presentation is tombstoned.

    The current delete operation and its conversation remain durable long
    enough for the runner to append the terminal event, so artifact ownership
    is resolved both through ``presentation_id`` and through its operation.
    """

    presentation_id = presentation.id
    owner_id = presentation.owner_id
    conversation_ids = set(
        (
            await session.scalars(
                select(WorkspaceConversation.id)
                .where(WorkspaceConversation.presentation_id == presentation_id)
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    assets = (
        list(
            (
                await session.scalars(
                    select(WorkspaceAsset)
                    .where(WorkspaceAsset.conversation_id.in_(conversation_ids))
                    .execution_options(skip_owner_scope=True)
                )
            ).all()
        )
        if conversation_ids
        else []
    )
    operation_ids = set(
        (
            await session.scalars(
                select(WorkspaceOperation.id)
                .where(WorkspaceOperation.presentation_id == presentation_id)
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    artifact_predicates = [WorkspaceArtifact.presentation_id == presentation_id]
    if operation_ids:
        artifact_predicates.append(WorkspaceArtifact.operation_id.in_(operation_ids))
    artifacts = list(
        (
            await session.scalars(
                select(WorkspaceArtifact)
                .where(or_(*artifact_predicates))
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )

    surviving_file_paths = {
        normalized
        for paths in (
            await session.scalars(
                select(PresentationModel.file_paths)
                .where(PresentationModel.id != presentation_id)
                .execution_options(skip_owner_scope=True)
            )
        ).all()
        if isinstance(paths, list)
        for value in paths
        if (normalized := _normalized_absolute_path(value)) is not None
    }
    asset_root = _canonical_storage_root(Path(get_writable_path("workspace_assets")))
    artifact_root = _canonical_storage_root(_app_data_root())
    targets: list[WorkspaceCleanupTarget] = []
    if presentation.mode == "authored":
        targets.append(
            authored_image_tree_cleanup_target(
                owner_id=owner_id,
                presentation_id=presentation_id,
            )
        )
    for asset in assets:
        physical_path = _normalized_absolute_path(
            str(asset_root.joinpath(*PurePosixPath(asset.storage_key).parts))
        )
        if physical_path not in surviving_file_paths:
            targets.append(
                workspace_asset_cleanup_target(
                    asset,
                    presentation_id=presentation_id,
                    storage_root=asset_root,
                )
            )
    targets.extend(
        workspace_artifact_cleanup_target(row, storage_root=artifact_root)
        for row in artifacts
    )

    jobs = list(
        (
            await session.scalars(
                select(PresentationGenerationJob)
                .where(PresentationGenerationJob.presentation_id == presentation_id)
                .with_for_update()
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    targets.extend(
        authored_checkpoint_cleanup_target(
            job.id,
            owner_id=owner_id,
            presentation_id=presentation_id,
            operation_id=job.workspace_operation_id,
        )
        for job in jobs
    )

    canonical_templates = list(
        (
            await session.scalars(
                select(TemplateV2)
                .where(TemplateV2.presentation_id == presentation_id)
                .with_for_update()
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    template_ids = {template.id for template in canonical_templates}
    revisions = (
        list(
            (
                await session.scalars(
                    select(TemplateV2Revision)
                    .where(TemplateV2Revision.template_id.in_(template_ids))
                    .execution_options(skip_owner_scope=True)
                )
            ).all()
        )
        if template_ids
        else []
    )
    targets.extend(
        target
        for template in canonical_templates
        if (
            target := _template_native_target_from_assets(
                template_id=template.id,
                revision=template.revision,
                assets=template.assets,
                owner_id=template.owner_id,
                presentation_id=presentation_id,
            )
        )
        is not None
    )
    template_by_id = {template.id: template for template in canonical_templates}
    targets.extend(
        target
        for revision in revisions
        if (
            template := template_by_id.get(revision.template_id)
        )
        is not None
        if (
            target := _template_native_target_from_assets(
                template_id=revision.template_id,
                revision=revision.revision,
                assets=revision.assets,
                owner_id=template.owner_id,
                presentation_id=presentation_id,
            )
        )
        is not None
    )

    if isinstance(presentation.file_paths, list):
        for file_path in presentation.file_paths:
            normalized = _normalized_absolute_path(file_path)
            if normalized is None or normalized in surviving_file_paths:
                continue
            target = workspace_asset_file_cleanup_target(
                file_path,
                owner_id=owner_id,
                presentation_id=presentation_id,
            )
            if target is not None:
                targets.append(target)
    return await enqueue_workspace_cleanup_targets(session, targets=targets)


def _normalized_absolute_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return None
    return os.path.normcase(os.path.normpath(os.path.abspath(value)))


def _template_native_target_from_assets(
    *,
    template_id: str,
    revision: int,
    assets: Any,
    owner_id: uuid.UUID | None,
    presentation_id: uuid.UUID | None,
) -> WorkspaceCleanupTarget | None:
    if not isinstance(assets, dict):
        return None
    native = assets.get("native_pptx")
    if not isinstance(native, dict):
        return None
    storage_key = native.get("storage_key")
    if not isinstance(storage_key, str) or not storage_key:
        return None
    digest = native.get("source_sha256")
    size_bytes = native.get("source_size_bytes")
    return template_v2_native_cleanup_target(
        template_id=template_id,
        revision=revision,
        storage_key=storage_key,
        sha256_value=(
            digest
            if isinstance(digest, str) and _SHA256_PATTERN.fullmatch(digest)
            else None
        ),
        size_bytes=(
            size_bytes
            if isinstance(size_bytes, int)
            and not isinstance(size_bytes, bool)
            and size_bytes >= 0
            else None
        ),
        owner_id=owner_id,
        presentation_id=presentation_id,
    )


def _eligible_cleanup(now: datetime):
    return or_(
        WorkspaceCleanupJournal.status == "pending",
        and_(
            WorkspaceCleanupJournal.status == "retry_wait",
            or_(
                WorkspaceCleanupJournal.next_attempt_at.is_(None),
                WorkspaceCleanupJournal.next_attempt_at <= now,
            ),
        ),
        and_(
            WorkspaceCleanupJournal.status == "claimed",
            WorkspaceCleanupJournal.cleanup_lease_expires_at.is_not(None),
            WorkspaceCleanupJournal.cleanup_lease_expires_at <= now,
        ),
    )


def _claim_from_row(row: Any, *, token: str) -> WorkspaceCleanupClaim:
    return WorkspaceCleanupClaim(
        journal_id=row.id,
        owner_id=row.owner_id_snapshot,
        presentation_id=row.presentation_id_snapshot,
        operation_id=row.operation_id_snapshot,
        storage_namespace=row.storage_namespace,
        resource_kind=row.resource_kind,
        storage_key=row.storage_key,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        source_revision=row.source_revision,
        resource_metadata=dict(row.resource_metadata or {}),
        token=token,
        fencing_token=int(row.fencing_token),
        attempts=int(row.cleanup_attempts),
        max_attempts=int(row.max_attempts),
    )


async def claim_workspace_cleanup_target(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_CLEANUP_LEASE_SECONDS,
) -> WorkspaceCleanupClaim | None:
    """Atomically lease one due row; expired workers are fenced on finalize."""

    claimed_at = now or get_current_utc_datetime()
    lease_until = claimed_at + timedelta(seconds=max(1, lease_seconds))
    token = secrets.token_urlsafe(32)
    async with session_factory() as session:
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            candidate_id = (
                select(WorkspaceCleanupJournal.id)
                .where(_eligible_cleanup(claimed_at))
                .order_by(
                    WorkspaceCleanupJournal.next_attempt_at,
                    WorkspaceCleanupJournal.created_at,
                    WorkspaceCleanupJournal.id,
                )
                .limit(1)
                .scalar_subquery()
            )
            result = await session.execute(
                update(WorkspaceCleanupJournal)
                .where(
                    WorkspaceCleanupJournal.id == candidate_id,
                    _eligible_cleanup(claimed_at),
                )
                .values(
                    status="claimed",
                    cleanup_token=token,
                    cleanup_lease_expires_at=lease_until,
                    next_attempt_at=None,
                    cleanup_attempts=WorkspaceCleanupJournal.cleanup_attempts + 1,
                    fencing_token=WorkspaceCleanupJournal.fencing_token + 1,
                    updated_at=claimed_at,
                )
                .returning(WorkspaceCleanupJournal.id)
            )
            claimed_id = result.scalar_one_or_none()
            row = (
                await session.get(WorkspaceCleanupJournal, claimed_id)
                if claimed_id is not None
                else None
            )
        else:
            row = (
                await session.scalars(
                    select(WorkspaceCleanupJournal)
                    .where(_eligible_cleanup(claimed_at))
                    .order_by(
                        WorkspaceCleanupJournal.next_attempt_at,
                        WorkspaceCleanupJournal.created_at,
                        WorkspaceCleanupJournal.id,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).first()
            if row is not None:
                row.status = "claimed"
                row.cleanup_token = token
                row.cleanup_lease_expires_at = lease_until
                row.next_attempt_at = None
                row.cleanup_attempts += 1
                row.fencing_token += 1
                row.updated_at = claimed_at
                await session.flush()
        if row is None:
            await session.rollback()
            return None
        claim = _claim_from_row(row, token=token)
        await session.commit()
        _record_cleanup_metric("claimed")
        return claim


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
        # is_symlink also catches a dangling link and the loop includes the leaf.
        if current.is_symlink():
            raise WorkspaceCleanupDeleteError(invalid_code, retryable=False)
    resolved = current.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise WorkspaceCleanupDeleteError(invalid_code, retryable=False) from error
    return relative, resolved


def _remove_workspace_asset(claim: WorkspaceCleanupClaim) -> None:
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
    metadata = claim.resource_metadata
    asset_id = _as_uuid(
        metadata.get("asset_id"),
        code="workspace_cleanup_asset_identity_invalid",
    )
    owner_scope = metadata.get("owner_scope")
    expected_owner_scope = (
        str(claim.owner_id) if claim.owner_id is not None else "anonymous"
    )
    parts = relative.parts
    leaf = PurePosixPath(parts[-1]) if parts else PurePosixPath("")
    suffix = leaf.suffix.lower()
    if (
        len(parts) != 3
        or owner_scope != expected_owner_scope
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


def _remove_workspace_artifact(claim: WorkspaceCleanupClaim) -> None:
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


def _remove_authored_checkpoint(claim: WorkspaceCleanupClaim) -> None:
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


def _remove_template_v2_native(claim: WorkspaceCleanupClaim) -> None:
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


def _remove_authored_image_tree(claim: WorkspaceCleanupClaim) -> None:
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


def _remove_account_owned_tree(claim: WorkspaceCleanupClaim) -> None:
    """Remove only an exact UUID child of the snapshotted owner root."""

    owner_id = claim.owner_id
    metadata = claim.resource_metadata
    if owner_id is None or metadata.get("owner_id") != str(owner_id):
        raise WorkspaceCleanupDeleteError(
            "workspace_cleanup_account_tree_identity_invalid", retryable=False
        )
    root_kind = metadata.get("storage_root_kind")
    if (
        not isinstance(root_kind, str)
        or (
            root_kind not in _ACCOUNT_APP_DATA_ROOT_KINDS
            and root_kind != _ACCOUNT_TEMP_TREE_KIND
        )
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
        expected_suffix = (
            _ACCOUNT_APP_DATA_ROOT_KINDS[root_kind],
            "users",
        )
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


def _delete_claimed_storage(claim: WorkspaceCleanupClaim) -> None:
    try:
        if claim.storage_namespace == "workspace_asset":
            _remove_workspace_asset(claim)
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


async def finalize_workspace_cleanup_target(
    claim: WorkspaceCleanupClaim,
    *,
    succeeded: bool,
    error: WorkspaceCleanupDeleteError | None = None,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    now: datetime | None = None,
    retry_seconds: int = DEFAULT_CLEANUP_RETRY_SECONDS,
) -> str:
    completed_at = now or get_current_utc_datetime()
    values: dict[str, Any] = {
        "cleanup_token": None,
        "cleanup_lease_expires_at": None,
        "updated_at": completed_at,
    }
    if succeeded:
        outcome = "succeeded"
        values.update(
            status="succeeded",
            next_attempt_at=None,
            last_error=None,
            deleted_at=completed_at,
        )
    else:
        retryable = error is None or error.retryable
        exhausted = claim.attempts >= claim.max_attempts
        if retryable and not exhausted:
            outcome = "retry_wait"
            delay = min(3600, max(1, retry_seconds) * (2 ** (claim.attempts - 1)))
            values.update(
                status="retry_wait",
                next_attempt_at=completed_at + timedelta(seconds=delay),
                last_error={
                    "code": error.code if error else "cleanup_failed",
                    "retryable": retryable,
                },
            )
        else:
            outcome = "dead_letter"
            values.update(
                status="dead_letter",
                next_attempt_at=None,
                last_error={
                    "code": error.code if error else "cleanup_failed",
                    "retryable": retryable,
                },
            )

    async with session_factory() as session:
        result = await session.execute(
            update(WorkspaceCleanupJournal)
            .where(
                WorkspaceCleanupJournal.id == claim.journal_id,
                WorkspaceCleanupJournal.status == "claimed",
                WorkspaceCleanupJournal.cleanup_token == claim.token,
                WorkspaceCleanupJournal.fencing_token == claim.fencing_token,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            await session.rollback()
            _record_cleanup_metric("stale")
            return "stale"
        await session.commit()
    _record_cleanup_metric(outcome)
    return outcome


async def redrive_workspace_cleanup_dead_letter(
    session: AsyncSession,
    *,
    journal_id: uuid.UUID,
    requested_by: uuid.UUID,
    now: datetime | None = None,
) -> WorkspaceCleanupRedriveResult:
    """CAS-redrive one safely retryable cleanup dead letter.

    The update is intentionally a single statement.  Competing administrators
    or workers cannot both extend the retry budget, and the fencing-token bump
    prevents any stale claimant from finalizing after the redrive.  Older rows
    that predate the persisted ``retryable`` classification fail closed.
    """

    redriven_at = now or get_current_utc_datetime()
    audit_error = {
        "code": "workspace_cleanup_redrive_requested",
        "retryable": True,
        "requested_by": str(requested_by),
        "requested_at": redriven_at.isoformat(),
    }
    redriven = (
        await session.execute(
            update(WorkspaceCleanupJournal)
            .where(
                WorkspaceCleanupJournal.id == journal_id,
                WorkspaceCleanupJournal.status == "dead_letter",
                WorkspaceCleanupJournal.cleanup_token.is_(None),
                WorkspaceCleanupJournal.cleanup_lease_expires_at.is_(None),
                WorkspaceCleanupJournal.last_error["retryable"]
                .as_boolean()
                .is_(True),
            )
            .values(
                status="retry_wait",
                next_attempt_at=redriven_at,
                max_attempts=(
                    WorkspaceCleanupJournal.max_attempts
                    + DEFAULT_CLEANUP_MAX_ATTEMPTS
                ),
                fencing_token=WorkspaceCleanupJournal.fencing_token + 1,
                redrive_count=WorkspaceCleanupJournal.redrive_count + 1,
                last_redriven_at=redriven_at,
                last_redriven_by_snapshot=requested_by,
                last_error=audit_error,
                updated_at=redriven_at,
            )
            .returning(
                WorkspaceCleanupJournal.id,
                WorkspaceCleanupJournal.status,
                WorkspaceCleanupJournal.max_attempts,
                WorkspaceCleanupJournal.redrive_count,
            )
            .execution_options(synchronize_session=False)
        )
    ).one_or_none()
    if redriven is not None:
        return WorkspaceCleanupRedriveResult(
            journal_id=redriven.id,
            outcome="redriven",
            status=redriven.status,
            max_attempts=redriven.max_attempts,
            redrive_count=redriven.redrive_count,
        )

    current = (
        await session.execute(
            select(
                WorkspaceCleanupJournal.status,
                WorkspaceCleanupJournal.max_attempts,
                WorkspaceCleanupJournal.redrive_count,
                WorkspaceCleanupJournal.last_error,
            )
            .where(WorkspaceCleanupJournal.id == journal_id)
            .execution_options(skip_owner_scope=True)
        )
    ).one_or_none()
    if current is None:
        return WorkspaceCleanupRedriveResult(
            journal_id=journal_id,
            outcome="not_found",
            status=None,
            max_attempts=None,
            redrive_count=None,
        )
    if current.status != "dead_letter":
        return WorkspaceCleanupRedriveResult(
            journal_id=journal_id,
            outcome="already_active",
            status=current.status,
            max_attempts=current.max_attempts,
            redrive_count=current.redrive_count,
        )
    retryable = (
        isinstance(current.last_error, dict)
        and current.last_error.get("retryable") is True
    )
    return WorkspaceCleanupRedriveResult(
        journal_id=journal_id,
        outcome="conflict" if retryable else "non_retryable",
        status=current.status,
        max_attempts=current.max_attempts,
        redrive_count=current.redrive_count,
    )


async def sweep_workspace_cleanup_journal(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, int]:
    """Run one bounded pass over durable cleanup intents."""

    result = {
        "claimed": 0,
        "deleted": 0,
        "retry_pending": 0,
        "dead_lettered": 0,
        "stale": 0,
    }
    for _ in range(max(1, min(limit, 1000))):
        claim = await claim_workspace_cleanup_target(
            session_factory=session_factory,
            now=now,
        )
        if claim is None:
            break
        result["claimed"] += 1
        failure: WorkspaceCleanupDeleteError | None = None
        try:
            await asyncio.to_thread(_delete_claimed_storage, claim)
        except WorkspaceCleanupDeleteError as error:
            failure = error
        outcome = await finalize_workspace_cleanup_target(
            claim,
            succeeded=failure is None,
            error=failure,
            session_factory=session_factory,
            now=now,
        )
        if outcome == "succeeded":
            result["deleted"] += 1
        elif outcome == "retry_wait":
            result["retry_pending"] += 1
        elif outcome == "dead_letter":
            result["dead_lettered"] += 1
        else:
            result["stale"] += 1
    return result


def _cleanup_maintenance_interval_seconds() -> int:
    raw = (os.getenv("WORKSPACE_CLEANUP_MAINTENANCE_INTERVAL_SECONDS") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CLEANUP_MAINTENANCE_INTERVAL_SECONDS
    return max(1, value) if value > 0 else DEFAULT_CLEANUP_MAINTENANCE_INTERVAL_SECONDS


async def _workspace_cleanup_maintenance_loop() -> None:
    assert _cleanup_maintenance_stop is not None
    while not _cleanup_maintenance_stop.is_set():
        try:
            result = await sweep_workspace_cleanup_journal()
            if any(result.values()):
                logger.info(
                    "Workspace cleanup maintenance completed: "
                    "claimed=%d deleted=%d retry_pending=%d "
                    "dead_lettered=%d stale=%d",
                    result["claimed"],
                    result["deleted"],
                    result["retry_pending"],
                    result["dead_lettered"],
                    result["stale"],
                )
        except Exception:
            # Cleanup rows are durable. A transient database or storage fault
            # must not prevent the API from starting or discard retry state.
            logger.exception("Workspace cleanup maintenance iteration failed")
        try:
            await asyncio.wait_for(
                _cleanup_maintenance_stop.wait(),
                timeout=_cleanup_maintenance_interval_seconds(),
            )
        except TimeoutError:
            pass


async def start_workspace_cleanup_maintenance() -> None:
    """Sweep durable deletion intents independently of chat-first rollout.

    Account deletion remains available while the workbench is rolled back, so
    this lifecycle cannot be coupled to the chat-first feature flag.
    """

    global _cleanup_maintenance_stop, _cleanup_maintenance_task
    if (
        _cleanup_maintenance_task is not None
        and not _cleanup_maintenance_task.done()
    ):
        return
    _cleanup_maintenance_stop = asyncio.Event()
    try:
        await sweep_workspace_cleanup_journal()
    except Exception:
        logger.exception("Initial workspace cleanup maintenance failed")
    _cleanup_maintenance_task = asyncio.create_task(
        _workspace_cleanup_maintenance_loop()
    )


async def stop_workspace_cleanup_maintenance() -> None:
    global _cleanup_maintenance_stop, _cleanup_maintenance_task
    if _cleanup_maintenance_stop is not None:
        _cleanup_maintenance_stop.set()
    if _cleanup_maintenance_task is not None:
        await _cleanup_maintenance_task
    _cleanup_maintenance_task = None
    _cleanup_maintenance_stop = None


__all__ = [
    "WorkspaceCleanupClaim",
    "WorkspaceCleanupDeleteError",
    "WorkspaceCleanupRedriveResult",
    "WorkspaceCleanupTarget",
    "account_cleanup_tombstone_exists",
    "account_owned_tree_cleanup_target",
    "authored_checkpoint_cleanup_target",
    "authored_image_tree_cleanup_target",
    "claim_workspace_cleanup_target",
    "enqueue_workspace_cleanup_targets",
    "finalize_workspace_cleanup_target",
    "redrive_workspace_cleanup_dead_letter",
    "snapshot_account_workspace_cleanup_targets",
    "snapshot_presentation_workspace_cleanup_targets",
    "start_workspace_cleanup_maintenance",
    "stop_workspace_cleanup_maintenance",
    "sweep_workspace_cleanup_journal",
    "template_v2_native_cleanup_target",
    "workspace_artifact_cleanup_target",
    "workspace_asset_cleanup_target",
    "workspace_asset_file_cleanup_target",
]
