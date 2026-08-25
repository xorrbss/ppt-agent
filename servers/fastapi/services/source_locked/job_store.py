from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid

from services.source_locked.contracts import (
    AdmissionContractV1,
    AnalysisStage,
    ProgressStageV1,
    SourceLockedErrorV1,
    SourceLockedGenerationJobV1,
    SourceLockedGenerationResponseV1,
    SourceLockedJobStatus,
    StageStatus,
)
from services.source_locked.digests import canonical_json_bytes, sha256_digest
from services.source_locked.errors import SourceLockedError
from utils.get_env import get_app_data_directory_env


_JOB_ID = re.compile(r"^sljob_[0-9a-f]{64}$")
_LEASE_SECONDS = 90
_ORPHAN_GRACE_SECONDS = 3600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _initial_progress() -> tuple[ProgressStageV1, ...]:
    return tuple(
        ProgressStageV1(stage=stage, status=StageStatus.PENDING)
        for stage in AnalysisStage
    )


class DurableSourceLockedJobStore:
    """Owner-scoped durable queue independent from the central model registry."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.input_root = (self.root / "job-inputs").resolve()
        self.database_path = self.root / "source_locked_jobs.sqlite3"
        self.input_root.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.cleanup_orphan_inputs()

    @classmethod
    def from_app_data(cls) -> "DurableSourceLockedJobStore":
        app_data = (get_app_data_directory_env() or "").strip()
        if not app_data:
            raise SourceLockedError(
                "SOURCE_LOCKED_STORAGE_UNAVAILABLE",
                AnalysisStage.SOURCE_ANALYSIS,
                "APP_DATA_DIRECTORY is required for source-locked jobs",
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
                CREATE TABLE IF NOT EXISTS generation_jobs_v1 (
                    job_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    idempotency_payload_digest TEXT NOT NULL,
                    source_filename TEXT NOT NULL,
                    source_relative_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    source_byte_length INTEGER NOT NULL,
                    admission_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_json TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    commit_started INTEGER NOT NULL DEFAULT 0,
                    runner_token TEXT,
                    lease_expires_at TEXT,
                    result_json TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_id, idempotency_key)
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(generation_jobs_v1)")
            }
            if "runner_token" not in columns:
                connection.execute("ALTER TABLE generation_jobs_v1 ADD COLUMN runner_token TEXT")
            if "lease_expires_at" not in columns:
                connection.execute(
                    "ALTER TABLE generation_jobs_v1 ADD COLUMN lease_expires_at TEXT"
                )

    @staticmethod
    def _identity(owner_id: str, idempotency_key: str, payload_digest: str) -> str:
        material = canonical_json_bytes(
            {
                "contract_version": "source-locked-job-identity/v1",
                "owner_id": owner_id,
                "idempotency_key": idempotency_key,
                "idempotency_payload_digest": payload_digest,
            }
        )
        return "sljob_" + hashlib.sha256(material).hexdigest()

    def _validated_input_path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if not candidate.is_relative_to(self.input_root):
            raise SourceLockedError(
                "SOURCE_LOCKED_JOB_INPUT_PATH_INVALID",
                AnalysisStage.SOURCE_ANALYSIS,
                "The stored source-locked job input path is invalid",
            )
        return candidate

    @staticmethod
    def _progress_json(progress: tuple[ProgressStageV1, ...]) -> str:
        return canonical_json_bytes(
            {"progress": [item.model_dump(mode="json") for item in progress]}
        ).decode("utf-8")

    @staticmethod
    def _progress_from_row(row: sqlite3.Row) -> tuple[ProgressStageV1, ...]:
        return tuple(
            ProgressStageV1.model_validate(item)
            for item in json.loads(row["progress_json"])["progress"]
        )

    def _snapshot(self, row: sqlite3.Row, *, replay: bool = False) -> SourceLockedGenerationJobV1:
        result = (
            SourceLockedGenerationResponseV1.model_validate_json(row["result_json"])
            if row["result_json"]
            else None
        )
        error = SourceLockedErrorV1.model_validate_json(row["error_json"]) if row["error_json"] else None
        return SourceLockedGenerationJobV1(
            job_id=row["job_id"],
            status=SourceLockedJobStatus(row["status"]),
            source_filename=row["source_filename"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            progress_stages=self._progress_from_row(row),
            cancel_requested=bool(row["cancel_requested"]),
            idempotent_replay=replay,
            result=result,
            error=error,
        )

    @staticmethod
    def _row_for_owner(connection: sqlite3.Connection, job_id: str, owner_id: str):
        return connection.execute(
            "SELECT * FROM generation_jobs_v1 WHERE job_id = ? AND owner_id = ?",
            (job_id, owner_id),
        ).fetchone()

    def create(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
        idempotency_payload_digest: str,
        admission_contract: AdmissionContractV1,
        source_bytes: bytes,
    ) -> tuple[SourceLockedGenerationJobV1, bool]:
        if not owner_id:
            raise ValueError("owner_id is required")
        if not source_bytes:
            raise SourceLockedError(
                "SOURCE_EMPTY",
                AnalysisStage.SOURCE_ANALYSIS,
                "The uploaded source file is empty",
            )
        source_digest = sha256_digest(source_bytes)
        job_id = self._identity(owner_id, idempotency_key, idempotency_payload_digest)
        relative_path = f"job-inputs/{job_id}.source"
        destination = self._validated_input_path(relative_path)
        fd, temp_name = tempfile.mkstemp(prefix=f".{job_id}-", suffix=".tmp", dir=self.input_root)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(source_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM generation_jobs_v1 WHERE owner_id = ? AND idempotency_key = ?",
                    (owner_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    connection.rollback()
                    if existing["idempotency_payload_digest"] != idempotency_payload_digest:
                        raise SourceLockedError(
                            "IDEMPOTENCY_KEY_CONFLICT",
                            AnalysisStage.SOURCE_ANALYSIS,
                            "The idempotency key is already sealed for a different job payload",
                        )
                    return self._snapshot(existing, replay=True), False
                now = _now().isoformat()
                os.replace(temp_path, destination)
                connection.execute(
                    """
                    INSERT INTO generation_jobs_v1 (
                        job_id, owner_id, idempotency_key, idempotency_payload_digest,
                        source_filename, source_relative_path, source_sha256,
                        source_byte_length, admission_json, status, progress_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        owner_id,
                        idempotency_key,
                        idempotency_payload_digest,
                        admission_contract.source_filename,
                        relative_path,
                        source_digest,
                        len(source_bytes),
                        canonical_json_bytes(admission_contract).decode("utf-8"),
                        SourceLockedJobStatus.QUEUED.value,
                        self._progress_json(_initial_progress()),
                        now,
                        now,
                    ),
                )
                connection.commit()
                row = self._row_for_owner(connection, job_id, owner_id)
                assert row is not None
                return self._snapshot(row), True
        finally:
            temp_path.unlink(missing_ok=True)

    def get(self, *, owner_id: str, job_id: str) -> SourceLockedGenerationJobV1 | None:
        if not _JOB_ID.fullmatch(job_id):
            return None
        with self._connect() as connection:
            row = self._row_for_owner(connection, job_id, owner_id)
        return self._snapshot(row) if row is not None else None

    def list_for_owner(
        self,
        *,
        owner_id: str,
        limit: int = 50,
    ) -> tuple[SourceLockedGenerationJobV1, ...]:
        bounded_limit = min(max(limit, 1), 200)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM generation_jobs_v1
                WHERE owner_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (owner_id, bounded_limit),
            ).fetchall()
        return tuple(self._snapshot(row) for row in rows)

    def _delete_input(self, relative_path: str) -> None:
        try:
            self._validated_input_path(relative_path).unlink(missing_ok=True)
        except OSError:
            # The DB terminal state is authoritative. Maintenance retries orphan cleanup.
            pass

    def cleanup_orphan_inputs(
        self,
        *,
        now: datetime | None = None,
        grace_seconds: int = _ORPHAN_GRACE_SECONDS,
    ) -> int:
        cutoff = (now or _now()).timestamp() - max(grace_seconds, 0)
        with self._connect() as connection:
            active = {
                row["source_relative_path"]
                for row in connection.execute(
                    """
                    SELECT source_relative_path FROM generation_jobs_v1
                    WHERE status IN (?, ?)
                    """,
                    (
                        SourceLockedJobStatus.QUEUED.value,
                        SourceLockedJobStatus.RUNNING.value,
                    ),
                )
            }
        removed = 0
        for path in self.input_root.iterdir():
            if not path.is_file() or path.stat().st_mtime > cutoff:
                continue
            relative = path.relative_to(self.root).as_posix()
            if relative in active:
                continue
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
        return removed

    def recover_stale(
        self,
        *,
        owner_id: str,
        job_id: str,
        now: datetime | None = None,
    ) -> tuple[SourceLockedGenerationJobV1 | None, bool]:
        current = now or _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row_for_owner(connection, job_id, owner_id)
            if row is None:
                connection.rollback()
                return None, False
            lease = (
                datetime.fromisoformat(row["lease_expires_at"])
                if row["lease_expires_at"]
                else None
            )
            stale = (
                row["status"] == SourceLockedJobStatus.RUNNING.value
                and (lease is None or lease <= current)
            )
            if not stale:
                connection.rollback()
                return self._snapshot(row), False
            connection.execute(
                """
                UPDATE generation_jobs_v1
                SET status = ?, progress_json = ?, commit_started = 0,
                    runner_token = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ? AND owner_id = ?
                """,
                (
                    SourceLockedJobStatus.QUEUED.value,
                    self._progress_json(_initial_progress()),
                    current.isoformat(),
                    job_id,
                    owner_id,
                ),
            )
            connection.commit()
            updated = self._row_for_owner(connection, job_id, owner_id)
            assert updated is not None
            return self._snapshot(updated), True

    def read_input(
        self, *, owner_id: str, job_id: str
    ) -> tuple[AdmissionContractV1, bytes, str, str]:
        with self._connect() as connection:
            row = self._row_for_owner(connection, job_id, owner_id)
        if row is None:
            raise SourceLockedError(
                "GENERATION_JOB_NOT_FOUND",
                AnalysisStage.SOURCE_ANALYSIS,
                "Source-locked generation job not found",
            )
        path = self._validated_input_path(row["source_relative_path"])
        if not path.is_file():
            raise SourceLockedError(
                "SOURCE_LOCKED_JOB_INPUT_MISSING",
                AnalysisStage.SOURCE_ANALYSIS,
                "The durable source-locked job input is missing",
            )
        data = path.read_bytes()
        if len(data) != row["source_byte_length"] or sha256_digest(data) != row["source_sha256"]:
            raise SourceLockedError(
                "SOURCE_LOCKED_JOB_INPUT_INTEGRITY_FAILURE",
                AnalysisStage.SOURCE_ANALYSIS,
                "The durable source-locked job input failed integrity verification",
            )
        return (
            AdmissionContractV1.model_validate_json(row["admission_json"]),
            data,
            row["idempotency_key"],
            row["idempotency_payload_digest"],
        )

    def claim(self, *, owner_id: str, job_id: str) -> str | None:
        runner_token = uuid.uuid4().hex
        lease_expires_at = (_now() + timedelta(seconds=_LEASE_SECONDS)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row_for_owner(connection, job_id, owner_id)
            if row is None or row["status"] != SourceLockedJobStatus.QUEUED.value:
                connection.rollback()
                return None
            progress = list(self._progress_from_row(row))
            progress[0] = progress[0].model_copy(update={"status": StageStatus.RUNNING})
            connection.execute(
                """
                UPDATE generation_jobs_v1
                SET status = ?, progress_json = ?, runner_token = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    SourceLockedJobStatus.RUNNING.value,
                    self._progress_json(tuple(progress)),
                    runner_token,
                    lease_expires_at,
                    _now().isoformat(),
                    job_id,
                ),
            )
            connection.commit()
            return runner_token

    def heartbeat(self, *, owner_id: str, job_id: str, runner_token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE generation_jobs_v1
                SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND owner_id = ? AND runner_token = ? AND status = ?
                """,
                (
                    (_now() + timedelta(seconds=_LEASE_SECONDS)).isoformat(),
                    _now().isoformat(),
                    job_id,
                    owner_id,
                    runner_token,
                    SourceLockedJobStatus.RUNNING.value,
                ),
            )
        return cursor.rowcount == 1

    def cancel(self, *, owner_id: str, job_id: str) -> tuple[SourceLockedGenerationJobV1 | None, bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row_for_owner(connection, job_id, owner_id)
            if row is None:
                connection.rollback()
                return None, False
            terminal = row["status"] in {
                SourceLockedJobStatus.SUCCEEDED.value,
                SourceLockedJobStatus.FAILED.value,
                SourceLockedJobStatus.CANCELLED.value,
            }
            if terminal or row["commit_started"]:
                connection.rollback()
                return self._snapshot(row), False
            progress = list(self._progress_from_row(row))
            for index, item in enumerate(progress):
                if item.status in {StageStatus.PENDING, StageStatus.RUNNING}:
                    progress[index] = item.model_copy(update={"status": StageStatus.CANCELLED})
            connection.execute(
                """
                UPDATE generation_jobs_v1
                SET status = ?, cancel_requested = 1, progress_json = ?, updated_at = ?
                WHERE job_id = ? AND owner_id = ?
                """,
                (
                    SourceLockedJobStatus.CANCELLED.value,
                    self._progress_json(tuple(progress)),
                    _now().isoformat(),
                    job_id,
                    owner_id,
                ),
            )
            input_path = row["source_relative_path"]
            connection.commit()
            updated = self._row_for_owner(connection, job_id, owner_id)
            assert updated is not None
            snapshot = self._snapshot(updated)
        self._delete_input(input_path)
        return snapshot, True

    def advance(
        self,
        *,
        owner_id: str,
        job_id: str,
        runner_token: str,
        completed_stage: AnalysisStage,
    ) -> bool:
        stages = list(AnalysisStage)
        completed_index = stages.index(completed_stage)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row_for_owner(connection, job_id, owner_id)
            if (
                row is None
                or row["status"] != SourceLockedJobStatus.RUNNING.value
                or row["runner_token"] != runner_token
            ):
                connection.rollback()
                return False
            progress = list(self._progress_from_row(row))
            progress[completed_index] = progress[completed_index].model_copy(
                update={"status": StageStatus.COMPLETED}
            )
            if completed_index + 1 < len(progress):
                progress[completed_index + 1] = progress[completed_index + 1].model_copy(
                    update={"status": StageStatus.RUNNING}
                )
            connection.execute(
                "UPDATE generation_jobs_v1 SET progress_json = ?, updated_at = ? WHERE job_id = ?",
                (self._progress_json(tuple(progress)), _now().isoformat(), job_id),
            )
            connection.commit()
            return True

    def prepare_commit(self, *, owner_id: str, job_id: str, runner_token: str) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row_for_owner(connection, job_id, owner_id)
            if (
                row is None
                or row["status"] != SourceLockedJobStatus.RUNNING.value
                or row["runner_token"] != runner_token
            ):
                connection.rollback()
                return False
            connection.execute(
                "UPDATE generation_jobs_v1 SET commit_started = 1, updated_at = ? WHERE job_id = ?",
                (_now().isoformat(), job_id),
            )
            connection.commit()
            return True

    def succeed(
        self,
        *,
        owner_id: str,
        job_id: str,
        runner_token: str,
        result: SourceLockedGenerationResponseV1,
    ) -> SourceLockedGenerationJobV1:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row_for_owner(connection, job_id, owner_id)
            if (
                row is None
                or row["status"] != SourceLockedJobStatus.RUNNING.value
                or row["runner_token"] != runner_token
                or not row["commit_started"]
                or row["cancel_requested"]
            ):
                connection.rollback()
                raise SourceLockedError(
                    "GENERATION_JOB_STATE_CONFLICT",
                    AnalysisStage.MISSING_CONTENT_VALIDATION,
                    "The generation job cannot transition to success",
                )
            progress = tuple(
                item.model_copy(update={"status": StageStatus.COMPLETED})
                for item in self._progress_from_row(row)
            )
            connection.execute(
                """
                UPDATE generation_jobs_v1
                SET status = ?, progress_json = ?, result_json = ?, updated_at = ?
                WHERE job_id = ? AND owner_id = ?
                """,
                (
                    SourceLockedJobStatus.SUCCEEDED.value,
                    self._progress_json(progress),
                    canonical_json_bytes(result).decode("utf-8"),
                    _now().isoformat(),
                    job_id,
                    owner_id,
                ),
            )
            input_path = row["source_relative_path"]
            connection.commit()
            updated = self._row_for_owner(connection, job_id, owner_id)
            assert updated is not None
            snapshot = self._snapshot(updated)
        self._delete_input(input_path)
        return snapshot

    def fail(
        self,
        *,
        owner_id: str,
        job_id: str,
        runner_token: str,
        error: SourceLockedErrorV1,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row_for_owner(connection, job_id, owner_id)
            if row is None or row["runner_token"] != runner_token or row["status"] in {
                SourceLockedJobStatus.SUCCEEDED.value,
                SourceLockedJobStatus.FAILED.value,
                SourceLockedJobStatus.CANCELLED.value,
            }:
                connection.rollback()
                return
            progress = list(self._progress_from_row(row))
            error_index = list(AnalysisStage).index(error.stage)
            progress[error_index] = progress[error_index].model_copy(update={"status": StageStatus.FAILED})
            connection.execute(
                """
                UPDATE generation_jobs_v1
                SET status = ?, progress_json = ?, error_json = ?, updated_at = ?
                WHERE job_id = ? AND owner_id = ?
                """,
                (
                    SourceLockedJobStatus.FAILED.value,
                    self._progress_json(tuple(progress)),
                    canonical_json_bytes(error).decode("utf-8"),
                    _now().isoformat(),
                    job_id,
                    owner_id,
                ),
            )
            input_path = row["source_relative_path"]
            connection.commit()
        self._delete_input(input_path)


def source_locked_job_identity(owner_id: str, idempotency_key: str, payload_digest: str) -> str:
    return DurableSourceLockedJobStore._identity(owner_id, idempotency_key, payload_digest)
