from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from services.source_locked.contracts import (
    AnalysisStage,
    SourceLockedGenerationResponseV1,
)
from services.source_locked.digests import canonical_json_bytes, sha256_digest
from services.source_locked.errors import SourceLockedError
from utils.get_env import get_app_data_directory_env


_ARTIFACT_ID = re.compile(r"^sla_[0-9a-f]{64}$")


class DurableSourceLockedGenerationStore:
    """Dedicated durable Phase B store, independent from the central model registry."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.artifact_root = (self.root / "artifacts").resolve()
        self.database_path = self.root / "source_locked.sqlite3"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
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
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_records_v1 (
                    generation_record_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    idempotency_payload_digest TEXT NOT NULL,
                    artifact_id TEXT NOT NULL UNIQUE,
                    artifact_relative_path TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    artifact_byte_length INTEGER NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _identity(idempotency_key: str, payload_digest: str) -> tuple[str, str]:
        material = canonical_json_bytes(
            {
                "contract_version": "source-locked-generation-identity/v1",
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

    def _response_from_row(self, row: sqlite3.Row, *, replay: bool) -> SourceLockedGenerationResponseV1:
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
        return response.model_copy(update={"idempotent_replay": replay})

    def lookup(
        self,
        *,
        idempotency_key: str,
        idempotency_payload_digest: str,
    ) -> SourceLockedGenerationResponseV1 | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM generation_records_v1 WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        if row["idempotency_payload_digest"] != idempotency_payload_digest:
            raise SourceLockedError(
                "IDEMPOTENCY_KEY_CONFLICT",
                AnalysisStage.SOURCE_ANALYSIS,
                "The idempotency key is already sealed for a different generation payload",
            )
        return self._response_from_row(row, replay=True)

    def persist(
        self,
        *,
        response: SourceLockedGenerationResponseV1,
        artifact_bytes: bytes,
    ) -> SourceLockedGenerationResponseV1:
        if not artifact_bytes:
            raise SourceLockedError(
                "ARTIFACT_EMPTY",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The restoration worker produced an empty artifact",
            )
        expected_digest = sha256_digest(artifact_bytes)
        if response.artifact.sha256 != expected_digest or response.artifact.byte_length != len(artifact_bytes):
            raise SourceLockedError(
                "ARTIFACT_METADATA_MISMATCH",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "Artifact metadata does not match the generated package",
            )

        record_id, artifact_id = self._identity(
            response.analysis_seal.idempotency_key,
            response.analysis_seal.idempotency_payload_digest,
        )
        if response.generation_record_id != record_id or response.artifact.artifact_id != artifact_id:
            raise SourceLockedError(
                "GENERATION_IDENTITY_MISMATCH",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "Generation record identifiers are not derived from the sealed request",
            )

        relative_path = f"artifacts/{artifact_id}.pptx"
        destination = self._validated_path(relative_path)
        fd, temp_name = tempfile.mkstemp(prefix=f".{artifact_id}-", suffix=".tmp", dir=self.artifact_root)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(artifact_bytes)
                stream.flush()
                os.fsync(stream.fileno())

            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM generation_records_v1 WHERE idempotency_key = ?",
                    (response.analysis_seal.idempotency_key,),
                ).fetchone()
                if existing is not None:
                    connection.rollback()
                    if existing["idempotency_payload_digest"] != response.analysis_seal.idempotency_payload_digest:
                        raise SourceLockedError(
                            "IDEMPOTENCY_KEY_CONFLICT",
                            AnalysisStage.SOURCE_ANALYSIS,
                            "The idempotency key is already sealed for a different generation payload",
                        )
                    return self._response_from_row(existing, replay=True)

                os.replace(temp_path, destination)
                connection.execute(
                    """
                    INSERT INTO generation_records_v1 (
                        generation_record_id, idempotency_key, idempotency_payload_digest,
                        artifact_id, artifact_relative_path, artifact_sha256,
                        artifact_byte_length, response_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        response.generation_record_id,
                        response.analysis_seal.idempotency_key,
                        response.analysis_seal.idempotency_payload_digest,
                        response.artifact.artifact_id,
                        relative_path,
                        response.artifact.sha256,
                        response.artifact.byte_length,
                        canonical_json_bytes(response).decode("utf-8"),
                        response.artifact.created_at.isoformat(),
                    ),
                )
                connection.commit()
            return response
        finally:
            temp_path.unlink(missing_ok=True)

    def artifact_path(self, artifact_id: str) -> tuple[Path, SourceLockedGenerationResponseV1]:
        if not _ARTIFACT_ID.fullmatch(artifact_id):
            raise SourceLockedError(
                "ARTIFACT_ID_INVALID",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "Invalid source-locked artifact identifier",
            )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM generation_records_v1 WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise SourceLockedError(
                "ARTIFACT_NOT_FOUND",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "Source-locked artifact not found",
            )
        response = self._response_from_row(row, replay=True)
        return self._validated_path(row["artifact_relative_path"]), response


def generation_identity(idempotency_key: str, payload_digest: str) -> tuple[str, str]:
    return DurableSourceLockedGenerationStore._identity(idempotency_key, payload_digest)
