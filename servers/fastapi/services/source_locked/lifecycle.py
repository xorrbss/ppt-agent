from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3

from services.source_locked.contracts import AnalysisStage
from services.source_locked.errors import SourceLockedError


_DEFAULT_RETENTION_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_MAX_ARTIFACT_BYTES = 250 * 1024 * 1024
_DEFAULT_MAX_OWNER_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_MAX_TOTAL_BYTES = 10 * 1024 * 1024 * 1024
_DEFAULT_ORPHAN_GRACE_SECONDS = 60 * 60


def _positive_environment_integer(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SourceLockedError(
            "SOURCE_LOCKED_LIFECYCLE_CONFIG_INVALID",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            f"{name} must be a positive integer",
        ) from exc
    if value <= 0:
        raise SourceLockedError(
            "SOURCE_LOCKED_LIFECYCLE_CONFIG_INVALID",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            f"{name} must be a positive integer",
        )
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ArtifactLifecyclePolicy:
    retention_seconds: int = _DEFAULT_RETENTION_SECONDS
    max_artifact_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES
    max_owner_bytes: int = _DEFAULT_MAX_OWNER_BYTES
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES
    orphan_grace_seconds: int = _DEFAULT_ORPHAN_GRACE_SECONDS

    def __post_init__(self) -> None:
        values = (
            self.retention_seconds,
            self.max_artifact_bytes,
            self.max_owner_bytes,
            self.max_total_bytes,
            self.orphan_grace_seconds,
        )
        if any(value <= 0 for value in values):
            raise ValueError("source-locked lifecycle limits must be positive")
        if self.max_artifact_bytes > self.max_owner_bytes:
            raise ValueError("max_artifact_bytes cannot exceed max_owner_bytes")
        if self.max_owner_bytes > self.max_total_bytes:
            raise ValueError("max_owner_bytes cannot exceed max_total_bytes")

    @classmethod
    def from_environment(cls) -> "ArtifactLifecyclePolicy":
        return cls(
            retention_seconds=_positive_environment_integer(
                "SOURCE_LOCKED_ARTIFACT_RETENTION_SECONDS", _DEFAULT_RETENTION_SECONDS
            ),
            max_artifact_bytes=_positive_environment_integer(
                "SOURCE_LOCKED_ARTIFACT_MAX_BYTES", _DEFAULT_MAX_ARTIFACT_BYTES
            ),
            max_owner_bytes=_positive_environment_integer(
                "SOURCE_LOCKED_ARTIFACT_MAX_OWNER_BYTES", _DEFAULT_MAX_OWNER_BYTES
            ),
            max_total_bytes=_positive_environment_integer(
                "SOURCE_LOCKED_ARTIFACT_MAX_TOTAL_BYTES", _DEFAULT_MAX_TOTAL_BYTES
            ),
            orphan_grace_seconds=_positive_environment_integer(
                "SOURCE_LOCKED_ARTIFACT_ORPHAN_GRACE_SECONDS",
                _DEFAULT_ORPHAN_GRACE_SECONDS,
            ),
        )

    def expires_at(self, created_at: datetime) -> datetime:
        return created_at + timedelta(seconds=self.retention_seconds)


@dataclass(frozen=True, slots=True)
class ArtifactLifecycleStats:
    expired_records_removed: int = 0
    expired_artifacts_removed: int = 0
    orphan_artifacts_removed: int = 0
    orphan_temp_files_removed: int = 0


class SourceLockedArtifactLifecycle:
    """Retention and capacity authority for the dedicated source-locked store."""

    def __init__(self, artifact_root: Path, policy: ArtifactLifecyclePolicy):
        self.artifact_root = artifact_root.resolve()
        self.policy = policy

    def cleanup(self, connection: sqlite3.Connection, *, now: datetime) -> ArtifactLifecycleStats:
        now_text = timestamp(now)
        expired = connection.execute(
            """
            SELECT artifact_relative_path
            FROM generation_records_v2
            WHERE expires_at <= ?
            """,
            (now_text,),
        ).fetchall()
        expired_records_removed = 0
        expired_artifacts_removed = 0
        for row in expired:
            path = self._validated_artifact_path(row["artifact_relative_path"])
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    # Keep the authority record when Windows or another reader
                    # still holds the artifact. A later maintenance pass retries.
                    continue
                expired_artifacts_removed += 1
            connection.execute(
                """
                DELETE FROM generation_records_v2
                WHERE artifact_relative_path = ? AND expires_at <= ?
                """,
                (row["artifact_relative_path"], now_text),
            )
            expired_records_removed += 1

        referenced = {
            self._validated_artifact_path(row["artifact_relative_path"])
            for row in connection.execute(
                "SELECT artifact_relative_path FROM generation_records_v2"
            ).fetchall()
        }
        legacy_table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'generation_records_v1'
            """
        ).fetchone()
        if legacy_table is not None:
            # Phase A records have no owner authority and remain unreadable by
            # the v2 API. Maintenance may only protect their recorded paths; it
            # must neither expose the records nor infer an owner for them.
            referenced.update(
                self._validated_artifact_path(row["artifact_relative_path"])
                for row in connection.execute(
                    "SELECT artifact_relative_path FROM generation_records_v1"
                ).fetchall()
            )
        cutoff = now.timestamp() - self.policy.orphan_grace_seconds
        orphan_artifacts_removed = 0
        orphan_temp_files_removed = 0
        for candidate in self.artifact_root.iterdir():
            if not candidate.is_file() or candidate.stat().st_mtime > cutoff:
                continue
            resolved = candidate.resolve()
            if candidate.suffix.lower() == ".pptx" and resolved not in referenced:
                try:
                    candidate.unlink()
                    orphan_artifacts_removed += 1
                except OSError:
                    pass
            elif candidate.suffix.lower() == ".tmp":
                try:
                    candidate.unlink()
                    orphan_temp_files_removed += 1
                except OSError:
                    pass

        return ArtifactLifecycleStats(
            expired_records_removed=expired_records_removed,
            expired_artifacts_removed=expired_artifacts_removed,
            orphan_artifacts_removed=orphan_artifacts_removed,
            orphan_temp_files_removed=orphan_temp_files_removed,
        )

    def enforce_capacity(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        incoming_bytes: int,
    ) -> None:
        if incoming_bytes > self.policy.max_artifact_bytes:
            raise SourceLockedError(
                "ARTIFACT_TOO_LARGE",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The source-locked artifact exceeds its configured size limit",
            )
        owner_bytes = connection.execute(
            """
            SELECT COALESCE(SUM(artifact_byte_length), 0)
            FROM generation_records_v2
            WHERE owner_id = ?
            """,
            (owner_id,),
        ).fetchone()[0]
        total_bytes = connection.execute(
            "SELECT COALESCE(SUM(artifact_byte_length), 0) FROM generation_records_v2"
        ).fetchone()[0]
        if owner_bytes + incoming_bytes > self.policy.max_owner_bytes:
            raise SourceLockedError(
                "OWNER_ARTIFACT_CAPACITY_EXCEEDED",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The source-locked artifact capacity for this owner is exhausted",
            )
        if total_bytes + incoming_bytes > self.policy.max_total_bytes:
            raise SourceLockedError(
                "ARTIFACT_CAPACITY_EXCEEDED",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The source-locked artifact store capacity is exhausted",
            )

    def _validated_artifact_path(self, relative_path: str) -> Path:
        candidate = (self.artifact_root.parent / relative_path).resolve()
        if not candidate.is_relative_to(self.artifact_root):
            raise SourceLockedError(
                "ARTIFACT_PATH_INVALID",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The stored source-locked artifact path is invalid",
            )
        return candidate
