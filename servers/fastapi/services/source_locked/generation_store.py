from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from services.source_locked.contracts import AnalysisStage, SourceLockedGenerationResponseV1
from services.source_locked.digests import canonical_json_bytes, sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.lifecycle import (
    ArtifactLifecyclePolicy,
    ArtifactLifecycleStats,
    SourceLockedArtifactLifecycle,
    timestamp,
    utc_now,
)
from utils.get_env import get_app_data_directory_env


_ARTIFACT_ID = re.compile(r"^sla_[0-9a-f]{64}$")
_GENERATION_RECORD_ID = re.compile(r"^slgen_[0-9a-f]{64}$")


class DurableSourceLockedGenerationStore:
    """Owner-scoped durable store independent from the central model registry."""

    def __init__(
        self,
        root: Path,
        *,
        lifecycle_policy: ArtifactLifecyclePolicy | None = None,
    ):
        self.root = root.resolve()
        self.artifact_root = (self.root / "artifacts").resolve()
        self.database_path = self.root / "source_locked.sqlite3"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.lifecycle = SourceLockedArtifactLifecycle(
            self.artifact_root,
            lifecycle_policy or ArtifactLifecyclePolicy.from_environment(),
        )
        self._initialize()

    @classmethod
    def from_app_data(cls) -> "DurableSourceLockedGenerationStore":
        app_data = (get_app_data_directory_env() or "").strip()
        if not app_data:
            raise SourceLockedError(
                "SOURCE_LOCKED_STORAGE_UNAVAILABLE",
                AnalysisStage.SOURCE_ANALYSIS,
                "APP_DATA_DIRECTORY is required for source-locked generation",
            )
        return cls(Path(app_data) / "source_locked" / "v1")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            # Phase A records had no owner authority. They remain untouched and are
            # intentionally not readable through the owner-scoped v2 API.
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_records_v2 (
                    generation_record_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    idempotency_payload_digest TEXT NOT NULL,
                    artifact_id TEXT NOT NULL UNIQUE,
                    artifact_relative_path TEXT NOT NULL UNIQUE,
                    artifact_sha256 TEXT NOT NULL,
                    artifact_byte_length INTEGER NOT NULL CHECK (artifact_byte_length > 0),
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(owner_id, idempotency_key)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_source_locked_v2_owner_created
                ON generation_records_v2(owner_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_source_locked_v2_expiry
                ON generation_records_v2(expires_at)
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_source_locked_generation_immutable
                BEFORE UPDATE ON generation_records_v2
                BEGIN
                    SELECT RAISE(ABORT, 'source_locked_generation_record_immutable');
                END
                """
            )

    @staticmethod
    def _owner(owner_id: str) -> str:
        normalized = str(owner_id).strip()
        if not normalized or len(normalized) > 200:
            raise SourceLockedError(
                "SOURCE_LOCKED_OWNER_REQUIRED",
                AnalysisStage.SOURCE_ANALYSIS,
                "A valid owner is required for source-locked generation records",
            )
        return normalized

    @staticmethod
    def _identity(owner_id: str, idempotency_key: str, payload_digest: str) -> tuple[str, str]:
        material = canonical_json_bytes(
            {
                "contract_version": "source-locked-generation-identity/v2",
                "owner_id": DurableSourceLockedGenerationStore._owner(owner_id),
                "idempotency_key": idempotency_key,
                "idempotency_payload_digest": payload_digest,
            }
        )
        identity = hashlib.sha256(material).hexdigest()
        return f"slgen_{identity}", f"sla_{identity}"

    def _validated_path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if not candidate.is_relative_to(self.artifact_root):
            raise SourceLockedError(
                "ARTIFACT_PATH_INVALID",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The stored source-locked artifact path is invalid",
            )
        return candidate

    @staticmethod
    def _digest_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    def _response_from_row(
        self,
        row: sqlite3.Row,
        *,
        owner_id: str,
        replay: bool,
    ) -> SourceLockedGenerationResponseV1:
        if row["owner_id"] != owner_id:
            raise SourceLockedError(
                "GENERATION_RECORD_NOT_FOUND",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "Source-locked generation record not found",
            )
        artifact_path = self._validated_path(row["artifact_relative_path"])
        if not artifact_path.is_file():
            raise SourceLockedError(
                "ARTIFACT_MISSING",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The sealed source-locked artifact is missing",
            )
        if artifact_path.stat().st_size != row["artifact_byte_length"]:
            raise SourceLockedError(
                "ARTIFACT_INTEGRITY_FAILURE",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The sealed source-locked artifact size no longer matches its record",
            )
        if self._digest_file(artifact_path) != row["artifact_sha256"]:
            raise SourceLockedError(
                "ARTIFACT_INTEGRITY_FAILURE",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The sealed source-locked artifact digest no longer matches its record",
            )
        response = SourceLockedGenerationResponseV1.model_validate_json(row["response_json"])
        if (
            response.generation_record_id != row["generation_record_id"]
            or response.artifact.artifact_id != row["artifact_id"]
            or response.artifact.sha256 != row["artifact_sha256"]
            or response.artifact.byte_length != row["artifact_byte_length"]
        ):
            raise SourceLockedError(
                "GENERATION_RECORD_INTEGRITY_FAILURE",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The immutable generation response no longer matches its authority record",
            )
        return response.model_copy(update={"idempotent_replay": replay})

    def _cleanup_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        now: datetime,
    ) -> ArtifactLifecycleStats:
        return self.lifecycle.cleanup(connection, now=now)

    def run_lifecycle_maintenance(
        self,
        *,
        now: datetime | None = None,
    ) -> ArtifactLifecycleStats:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                stats = self._cleanup_in_transaction(connection, now=now or utc_now())
                connection.commit()
                return stats
            except Exception:
                connection.rollback()
                raise

    def lookup(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
        idempotency_payload_digest: str,
    ) -> SourceLockedGenerationResponseV1 | None:
        owner = self._owner(owner_id)
        self.run_lifecycle_maintenance()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM generation_records_v2
                WHERE owner_id = ? AND idempotency_key = ?
                """,
                (owner, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        if row["idempotency_payload_digest"] != idempotency_payload_digest:
            raise SourceLockedError(
                "IDEMPOTENCY_KEY_CONFLICT",
                AnalysisStage.SOURCE_ANALYSIS,
                "The idempotency key is already sealed for a different generation payload",
            )
        return self._response_from_row(row, owner_id=owner, replay=True)

    def get(
        self,
        generation_record_id: str,
        *,
        owner_id: str,
    ) -> SourceLockedGenerationResponseV1:
        owner = self._owner(owner_id)
        if not _GENERATION_RECORD_ID.fullmatch(generation_record_id):
            raise SourceLockedError(
                "GENERATION_RECORD_NOT_FOUND",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "Source-locked generation record not found",
            )
        self.run_lifecycle_maintenance()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM generation_records_v2
                WHERE generation_record_id = ? AND owner_id = ?
                """,
                (generation_record_id, owner),
            ).fetchone()
        if row is None:
            raise SourceLockedError(
                "GENERATION_RECORD_NOT_FOUND",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "Source-locked generation record not found",
            )
        return self._response_from_row(row, owner_id=owner, replay=True)

    def list_for_owner(
        self,
        *,
        owner_id: str,
        limit: int = 50,
    ) -> tuple[SourceLockedGenerationResponseV1, ...]:
        owner = self._owner(owner_id)
        bounded_limit = min(max(limit, 1), 200)
        self.run_lifecycle_maintenance()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM generation_records_v2
                WHERE owner_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (owner, bounded_limit),
            ).fetchall()
        return tuple(
            self._response_from_row(row, owner_id=owner, replay=True) for row in rows
        )

    def persist(
        self,
        *,
        owner_id: str,
        response: SourceLockedGenerationResponseV1,
        artifact_bytes: bytes,
    ) -> SourceLockedGenerationResponseV1:
        owner = self._owner(owner_id)
        if not artifact_bytes:
            raise SourceLockedError(
                "ARTIFACT_EMPTY",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The restoration worker produced an empty artifact",
            )
        expected_digest = sha256_digest(artifact_bytes)
        if response.artifact.sha256 != expected_digest or response.artifact.byte_length != len(
            artifact_bytes
        ):
            raise SourceLockedError(
                "ARTIFACT_METADATA_MISMATCH",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "Artifact metadata does not match the generated package",
            )

        record_id, artifact_id = self._identity(
            owner,
            response.analysis_seal.idempotency_key,
            response.analysis_seal.idempotency_payload_digest,
        )
        if response.generation_record_id != record_id or response.artifact.artifact_id != artifact_id:
            raise SourceLockedError(
                "GENERATION_IDENTITY_MISMATCH",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "Generation record identifiers are not derived from the owner-scoped request",
            )

        relative_path = f"artifacts/{artifact_id}.pptx"
        destination = self._validated_path(relative_path)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{artifact_id}-", suffix=".tmp", dir=self.artifact_root
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(artifact_bytes)
                stream.flush()
                os.fsync(stream.fileno())

            now = utc_now()
            self.run_lifecycle_maintenance(now=now)
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    existing = connection.execute(
                        """
                        SELECT * FROM generation_records_v2
                        WHERE owner_id = ? AND idempotency_key = ?
                        """,
                        (owner, response.analysis_seal.idempotency_key),
                    ).fetchone()
                    if existing is not None:
                        if (
                            existing["idempotency_payload_digest"]
                            != response.analysis_seal.idempotency_payload_digest
                        ):
                            raise SourceLockedError(
                                "IDEMPOTENCY_KEY_CONFLICT",
                                AnalysisStage.SOURCE_ANALYSIS,
                                "The idempotency key is already sealed for a different generation payload",
                            )
                        connection.commit()
                        return self._response_from_row(existing, owner_id=owner, replay=True)

                    self.lifecycle.enforce_capacity(
                        connection,
                        owner_id=owner,
                        incoming_bytes=len(artifact_bytes),
                    )
                    created_at = response.artifact.created_at
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    expires_at = self.lifecycle.policy.expires_at(created_at)
                    os.replace(temp_path, destination)
                    connection.execute(
                        """
                        INSERT INTO generation_records_v2 (
                            generation_record_id, owner_id, idempotency_key,
                            idempotency_payload_digest, artifact_id,
                            artifact_relative_path, artifact_sha256,
                            artifact_byte_length, response_json, created_at, expires_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            response.generation_record_id,
                            owner,
                            response.analysis_seal.idempotency_key,
                            response.analysis_seal.idempotency_payload_digest,
                            response.artifact.artifact_id,
                            relative_path,
                            response.artifact.sha256,
                            response.artifact.byte_length,
                            canonical_json_bytes(response).decode("utf-8"),
                            timestamp(created_at),
                            timestamp(expires_at),
                        ),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    if destination.is_file():
                        destination.unlink()
                    raise
            return response
        finally:
            temp_path.unlink(missing_ok=True)

    def artifact_path(
        self,
        artifact_id: str,
        *,
        owner_id: str,
    ) -> tuple[Path, SourceLockedGenerationResponseV1]:
        owner = self._owner(owner_id)
        if not _ARTIFACT_ID.fullmatch(artifact_id):
            raise SourceLockedError(
                "ARTIFACT_NOT_FOUND",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "Source-locked artifact not found",
            )
        self.run_lifecycle_maintenance()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM generation_records_v2
                WHERE artifact_id = ? AND owner_id = ?
                """,
                (artifact_id, owner),
            ).fetchone()
        if row is None:
            raise SourceLockedError(
                "ARTIFACT_NOT_FOUND",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "Source-locked artifact not found",
            )
        response = self._response_from_row(row, owner_id=owner, replay=True)
        return self._validated_path(row["artifact_relative_path"]), response


def generation_identity(owner_id: str, idempotency_key: str, payload_digest: str) -> tuple[str, str]:
    return DurableSourceLockedGenerationStore._identity(owner_id, idempotency_key, payload_digest)
