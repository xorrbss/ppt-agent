#!/usr/bin/env python3
"""Inject one owned workspace-worker crash after a real authored checkpoint.

The target API/Next/proxy stack is never started or stopped here.  This runner
owns only the external workspace worker processes it launches, requires an
isolated SQLite/app-data root, drives public HTTP endpoints, and emits the
release-gate crash evidence contract only after durable DB/outbox/fence audit.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid
import io
import zipfile
MANIFEST_SCHEMA = "public-service-crash-recovery-manifest/v1"
EVIDENCE_SCHEMA = "public-service-crash-recovery-evidence/v1"
TERMINAL = {"completed", "error", "failed", "cancelled", "canceled"}
RC_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value
def child_path(repo: Path, raw: str, label: str) -> Path:
    path = (repo / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    try:
        path.relative_to(repo)
    except ValueError as error:
        raise ValueError(f"{label} must stay below the repository root") from error
    return path
def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
def verify_source_manifest(
    path: Path, repo: Path, release_candidate: str, runner_path: str
) -> dict[str, str]:
    try:
        path.relative_to(repo)
    except ValueError as error:
        raise ValueError("source manifest must stay below the RC worktree") from error
    exact_digest = sha256_file(path)
    if exact_digest != release_candidate:
        raise ValueError("source manifest exact-byte digest does not equal releaseCandidate")
    source = read_json(path)
    if source.get("schemaVersion") != "public-service-source-manifest/v1":
        raise ValueError("source manifest schema is invalid")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    if source.get("sourceTreeCommit") != head:
        raise ValueError("source manifest sourceTreeCommit does not equal HEAD")
    entries = source.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("source manifest files must be a non-empty array")
    seen: set[str] = set()
    runner_digest: str | None = None
    for entry in entries:
        relative = str(entry.get("path", "")).replace("\\", "/") if isinstance(entry, dict) else ""
        expected = str(entry.get("sha256", "")) if isinstance(entry, dict) else ""
        if not relative or relative in seen or not RC_ID.fullmatch(expected):
            raise ValueError("source manifest contains an invalid or duplicate file entry")
        absolute = child_path(repo, relative, "source manifest file")
        if relative.startswith(("artifacts/", "app_data/", ".git/")):
            raise ValueError("source manifest file escaped the source boundary")
        if sha256_file(absolute) != expected:
            raise ValueError(f"source manifest file digest mismatch: {relative}")
        seen.add(relative)
        if relative == runner_path:
            runner_digest = expected
    if runner_digest is None:
        raise ValueError(f"source manifest omits evidence runner: {runner_path}")
    return {
        "runnerPath": runner_path,
        "runnerSha256": runner_digest,
        "sourceManifestDigest": exact_digest,
    }
def load_manifest(
    path: Path, source_manifest_path: Path, repo: Path
) -> tuple[dict[str, Any], Path, Path, dict[str, str]]:
    value = read_json(path)
    if value.get("schemaVersion") != MANIFEST_SCHEMA:
        raise ValueError(f"schemaVersion must be {MANIFEST_SCHEMA}")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    if not RC_ID.fullmatch(str(value.get("releaseCandidate", ""))):
        raise ValueError("releaseCandidate must be a sha256:<64 lowercase hex> source-manifest id")
    if value.get("sourceCommit") != head:
        raise ValueError("sourceCommit must equal the checked-out HEAD")
    provenance = verify_source_manifest(
        source_manifest_path,
        repo,
        str(value["releaseCandidate"]),
        "scripts/public-service-crash-recovery.py",
    )
    if value.get("evidenceClass") != "product-path":
        raise ValueError("evidenceClass must be product-path")
    if value.get("confirmExclusiveWorker") != "PUBLIC_SERVICE_CRASH_OWNED_WORKER_V1":
        raise ValueError("confirmExclusiveWorker acknowledgement is required")
    base_url = str(value.get("baseUrl", "")).rstrip("/")
    if not base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise ValueError("baseUrl must be an explicit loopback HTTP URL")
    value["baseUrl"] = base_url
    command = value.get("workerCommand")
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
        raise ValueError("workerCommand must be a non-empty argv array")
    if not Path(command[0]).is_absolute():
        raise ValueError("workerCommand executable must be absolute")
    worker_cwd = Path(str(value.get("workerCwd", ""))).resolve()
    expected_cwd = repo / "servers" / "fastapi"
    if worker_cwd != expected_cwd:
        raise ValueError("workerCwd must be this RC's servers/fastapi directory")
    value["workerCwd"] = str(worker_cwd)
    database_path = Path(str(value.get("sqliteDatabasePath", ""))).resolve()
    isolated_root = Path(str(value.get("isolatedRoot", ""))).resolve()
    app_data = Path(os.environ.get("APP_DATA_DIRECTORY", "")).resolve()
    for checked, label in ((database_path, "sqliteDatabasePath"), (app_data, "APP_DATA_DIRECTORY")):
        try:
            checked.relative_to(isolated_root)
        except ValueError as error:
            raise ValueError(f"{label} must stay below isolatedRoot") from error
    configured_db = os.environ.get("DATABASE_URL", "").replace("\\", "/")
    if database_path.as_posix().lower() not in configured_db.lower():
        raise ValueError("DATABASE_URL must identify sqliteDatabasePath")
    if os.environ.get("WORKSPACE_OPERATION_WORKER_MODE", "").lower() != "external":
        raise ValueError("WORKSPACE_OPERATION_WORKER_MODE=external is required")
    if os.environ.get("PPT_PROCESS_PROFILE", "").lower() != "generation":
        raise ValueError("PPT_PROCESS_PROFILE=generation is required")
    if not isinstance(value.get("deck"), dict) or not str(value["deck"].get("content", "")).strip():
        raise ValueError("deck.content is required")
    timeout = value.get("timeoutSeconds", 1800)
    lease = value.get("leaseSeconds", 8)
    if not isinstance(timeout, int) or timeout < 60 or timeout > 3600:
        raise ValueError("timeoutSeconds must be 60..3600")
    if not isinstance(lease, int) or lease < 2 or lease > 120:
        raise ValueError("leaseSeconds must be 2..120")
    output = child_path(repo, str(value.get("outputDirectory", "")), "outputDirectory")
    if output == repo:
        raise ValueError("outputDirectory must be a child directory")
    return value, output, database_path, provenance
def issue_session_token(repo: Path) -> str:
    server_root = repo / "servers" / "fastapi"
    sys.path.insert(0, str(server_root))
    import asyncio
    import secrets
    from sqlalchemy import select
    from api.v1.auth.users import PASSWORD_HELPER, get_jwt_strategy
    from models.sql.user import User
    from services.database import async_session_maker

    async def issue() -> str:
        username = "release-validation-crash-recovery"
        user_id = uuid.uuid5(uuid.NAMESPACE_URL, f"presenton:{username}")
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(
                    id=user_id,
                    username=username,
                    hashed_password=PASSWORD_HELPER.hash(secrets.token_urlsafe(32)),
                    is_active=True,
                    is_superuser=False,
                    is_verified=True,
                )
                session.add(user)
                await session.flush()
            elif (
                user.id != user_id or not user.is_active
                or user.is_superuser or not user.is_verified
            ):
                raise RuntimeError("crash validation fixture user state is invalid")
            token = await get_jwt_strategy().write_token(user)
            await session.commit()
            return token

    return asyncio.run(issue())
def headers(session_token: str, json_body: bool = False) -> dict[str, str]:
    result = {"Accept": "application/json"}
    if json_body:
        result["Content-Type"] = "application/json"
    result["Cookie"] = f"presenton_session={session_token}"
    return result
def http_json(
    method: str, url: str, body: dict[str, Any] | None, timeout: float, session_token: str
) -> dict[str, Any]:
    payload = json.dumps(body).encode() if body is not None else None
    request = Request(
        url, data=payload, method=method,
        headers=headers(session_token, body is not None),
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read())
    except HTTPError as error:
        detail = error.read(800).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {detail}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"non-object JSON from {url}")
    return value
def http_bytes(url: str, timeout: float, session_token: str) -> bytes:
    request = Request(url, headers=headers(session_token))
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def wait_for_export_artifact(
    *, base_url: str, presentation_id: str, operation_id: str,
    timeout: float, session_token: str,
) -> tuple[dict[str, Any], str]:
    def terminal_export() -> tuple[dict[str, Any], str] | None:
        timeline = http_json(
            "GET",
            f"{base_url}/api/v1/ppt/workspace/presentations/"
            f"{quote(presentation_id)}/timeline?limit=200",
            None,
            min(30, timeout),
            session_token,
        )
        items = timeline.get("items")
        if not isinstance(items, list):
            raise RuntimeError("workspace timeline did not return an item array")
        operation = next(
            (
                item for item in items
                if isinstance(item, dict)
                and str(item.get("operation_id") or item.get("operationId") or "")
                == operation_id
            ),
            None,
        )
        if operation is None:
            return None
        status = str(operation.get("status") or operation.get("state") or "").lower()
        if status in {"failed", "cancelled", "canceled", "dead_letter"}:
            raise RuntimeError(f"export operation ended as {status}")
        if status != "succeeded":
            return None
        result = parse_payload(operation.get("result"))
        artifact_id = str(
            operation.get("artifact_id") or operation.get("artifactId")
            or result.get("artifact_id") or result.get("artifactId") or ""
        )
        if not artifact_id:
            raise RuntimeError("successful export operation returned no artifact id")
        return operation, artifact_id

    return wait_until(terminal_export, timeout, "workspace export operation completion")
def contains_token(path: Path, token: bytes) -> bool:
    overlap = len(token) - 1
    tail = b""
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                observed = tail + chunk
                if token in observed:
                    return True
                tail = observed[-overlap:] if overlap > 0 else b""
    except (OSError, PermissionError) as error:
        raise RuntimeError("session token leak scan could not read isolated artifact") from error
    return False
def authentication_receipt(
    *, token: str, output: Path, app_data: Path, evidence: dict[str, Any]
) -> dict[str, Any]:
    token_bytes = token.encode("utf-8")
    if any(token in argument for argument in sys.argv):
        raise RuntimeError("session token present in crash runner argv")
    if any(token in value for value in os.environ.values()):
        raise RuntimeError("session token present in crash runner environment")
    encoded_evidence = json.dumps(
        evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if token_bytes in encoded_evidence:
        raise RuntimeError("session token persisted in crash evidence payload")
    scanned_files = 0
    for root in {output.resolve(), app_data.resolve()}:
        for candidate in root.rglob("*"):
            if candidate.is_file():
                scanned_files += 1
                if contains_token(candidate, token_bytes):
                    raise RuntimeError("session token persisted in crash validation output")
    return {
        "schemaVersion": "public-service-crash-auth-fixture/v1",
        "principalCount": 1,
        "principalRole": "non-admin",
        "transport": "signed-session-cookie",
        "tokenInArgv": False,
        "tokenInEnvironment": False,
        "tokenInEvidence": False,
        "tokenForwardedToWorkerEnvironment": False,
        "isolatedArtifactTokenByteScan": "passed",
        "scannedFileCount": scanned_files,
    }
def wait_until(predicate: Callable[[], Any], timeout: float, description: str) -> Any:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, sqlite3.Error, URLError, json.JSONDecodeError) as error:
            last_error = error
        time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {description}; last error={last_error!r}")
def db_rows(path: Path, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql, parameters)]
def generation_state(path: Path, task_id: str) -> dict[str, Any] | None:
    rows = db_rows(
        path,
        "SELECT id, workspace_operation_id, state, attempt_number, attempt_token, "
        "lease_expires_at, completed_slides, total_slides, current_stage "
        "FROM presentation_generation_jobs WHERE task_id = ?",
        (task_id,),
    )
    return rows[0] if rows else None
def raw_id(path: Path, table: str, target: str) -> str:
    for row in db_rows(path, f"SELECT id FROM {table}"):
        if str(row["id"]).replace("-", "").lower() == target.replace("-", "").lower():
            return str(row["id"])
    raise RuntimeError(f"{table} row not found")


def operation_state(path: Path, operation_id: str) -> dict[str, Any]:
    raw = raw_id(path, "workspace_operations", operation_id)
    return db_rows(
        path,
        "SELECT id, owner_id, owner_scope, conversation_id, presentation_id, kind, request_payload, "
        "expected_revision, state, attempt_number, attempt_token, fencing_token, lease_expires_at, "
        "result_revision, version FROM workspace_operations WHERE id = ?",
        (raw,),
    )[0]


def checkpoint_snapshot(path: Path, job_id: str) -> list[dict[str, Any]]:
    raw = raw_id(path, "presentation_generation_jobs", job_id)
    return db_rows(
        path,
        "SELECT slide_index, author_status, illustrate_status, render_status, persist_status, "
        "render_sha256, attempt_token FROM authored_slide_checkpoints WHERE job_id = ? ORDER BY slide_index",
        (raw,),
    )


def parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def start_worker(manifest: dict[str, Any], output: Path, sequence: int) -> tuple[subprocess.Popen[Any], Path]:
    log_path = output / f"owned-worker-{sequence}.log"
    log = log_path.open("w", encoding="utf-8")
    options: dict[str, Any] = {
        "cwd": manifest["workerCwd"], "env": os.environ.copy(), "stdout": log,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(manifest["workerCommand"], **options)
    log.close()

    def ready() -> bool:
        if process.poll() is not None:
            raise RuntimeError(f"worker exited early ({process.returncode}): {log_path.read_text(errors='replace')[-2000:]}")
        return "Workspace operation external worker started" in log_path.read_text(encoding="utf-8", errors="replace")

    wait_until(ready, 90, f"worker {sequence} readiness")
    return process, log_path


def kill_owned_worker(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        raise RuntimeError("owned worker exited before crash injection")
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
            text=True, timeout=30, check=False,
        )
        if result.returncode not in {0, 128}:
            raise RuntimeError(f"taskkill failed for owned pid {process.pid}: {result.stderr[-500:]}")
    else:
        os.killpg(process.pid, 9)
    process.wait(timeout=30)


def stop_owned_worker(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    kill_owned_worker(process)


def request_body(deck: dict[str, Any]) -> dict[str, Any]:
    body = {
        "content": deck["content"], "n_slides": int(deck.get("nSlides", 5)),
        "template": "authored", "strategy": "authored", "export_as": "pptx",
        "include_title_slide": deck.get("includeTitleSlide", True),
        "include_table_of_contents": deck.get("includeTableOfContents", False),
        "web_search": deck.get("webSearch", False), "vision_qa": deck.get("visionQa", False),
        "quality_profile": deck.get("qualityProfile", "development"),
    }
    for source, target in (("language", "language"), ("style", "authored_style"), ("instructions", "instructions")):
        if deck.get(source) is not None:
            body[target] = deck[source]
    return body


def stale_fence_probe(database_path: Path, job: dict[str, Any], operation: dict[str, Any]) -> tuple[bool, bool]:
    server_root = Path(__file__).resolve().parents[1] / "servers" / "fastapi"
    sys.path.insert(0, str(server_root))
    import asyncio
    from services.authored_checkpoint_service import start_stage
    from services.database import async_session_maker
    from services.workspace_operation_runner import append_operation_event
    from utils.owner_context import reset_current_owner_id, set_current_owner_id

    async def run() -> tuple[bool, bool]:
        owner = operation.get("owner_id")
        owner_token = set_current_owner_id(uuid.UUID(str(owner)) if owner else None)
        try:
            checkpoint_rejected = False
            try:
                await start_stage(
                    job_id=uuid.UUID(str(job["id"])), slide_index=0, stage="persist",
                    attempt_token=str(job["attempt_token"]),
                )
            except RuntimeError:
                checkpoint_rejected = True
            async with async_session_maker() as session:
                event = await append_operation_event(
                    session,
                    operation_id=uuid.UUID(str(operation["id"])),
                    event_type="operation.result",
                    payload={"state": "succeeded"},
                    operation_kind=str(operation["kind"]),
                    attempt_token=str(operation["attempt_token"]),
                    allowed_states=("running",),
                )
                if event is None:
                    await session.rollback()
            return checkpoint_rejected, event is None
        finally:
            reset_current_owner_id(owner_token)

    return asyncio.run(run())


def parse_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
def audit_checkpoint_reuse_event(
    database_path: Path,
    operation_id: str,
    successor_attempt: int,
    minimum_reuse_count: int,
) -> tuple[bool, int, int | None]:
    raw = raw_id(database_path, "workspace_operations", operation_id)
    rows = db_rows(
        database_path,
        "SELECT cursor, payload FROM workspace_operation_events WHERE operation_id = ? "
        "AND event_type = 'run.status' ORDER BY cursor",
        (raw,),
    )
    matches = []
    for row in rows:
        payload = parse_payload(row["payload"])
        if (
            payload.get("state") == "running"
            and payload.get("attempt_number") == successor_attempt
            and isinstance(payload.get("checkpoint_reuse_count"), int)
            and payload["checkpoint_reuse_count"] >= minimum_reuse_count
        ):
            matches.append((int(payload["checkpoint_reuse_count"]), int(row["cursor"])))
    return bool(matches), (matches[-1][0] if matches else 0), (matches[-1][1] if matches else None)
def audit_outbox(
    database_path: Path,
    operation_id: str,
    stale_operation: dict[str, Any],
) -> tuple[bool, int, int | None]:
    raw = raw_id(database_path, "workspace_operations", operation_id)
    recovery = db_rows(
        database_path,
        "SELECT cursor, payload FROM workspace_operation_events WHERE operation_id = ? "
        "AND event_type = 'operation.error' AND payload LIKE '%operation_recovery_required%'",
        (raw,),
    )
    expected_attempt_hash = hashlib.sha256(
        str(stale_operation["attempt_token"]).encode("utf-8")
    ).hexdigest()
    causal = [
        row for row in recovery
        if (
            parse_payload(row["payload"]).get("attempt_number") == stale_operation["attempt_number"]
            and parse_payload(row["payload"]).get("fencing_token") == stale_operation["fencing_token"]
            and parse_payload(row["payload"]).get("reclaimed_attempt_token_sha256") == expected_attempt_hash
        )
    ]
    if not causal:
        return False, 0, None
    cursors = {row["cursor"] for row in causal}
    outbox = db_rows(
        database_path,
        "SELECT event_cursor, status FROM workspace_outbox WHERE operation_id = ?",
        (raw,),
    )
    bound = [row for row in outbox if row["event_cursor"] in cursors]
    verified = bool(bound) and all(row["status"] in {"pending", "delivering", "delivered"} for row in bound)
    return verified, len(bound), (int(bound[-1]["event_cursor"]) if bound else None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest, output, database_path, provenance = load_manifest(
        args.manifest.resolve(), args.source_manifest.resolve(), repo
    )
    output.mkdir(parents=True, exist_ok=True)
    evidence_path = output / "crash-recovery-evidence.json"
    first: subprocess.Popen[Any] | None = None
    second: subprocess.Popen[Any] | None = None
    session_token: str | None = None
    started = time.monotonic()
    evidence: dict[str, Any] = {
        "schemaVersion": EVIDENCE_SCHEMA,
        "releaseCandidate": manifest["releaseCandidate"],
        "sourceDigest": manifest["sourceCommit"],
        "provenance": provenance,
        "evidenceClass": "product-path",
        "injectedCrashCount": 0,
        "recoveredCrashCount": 0,
        "automaticConvergenceRate": 0,
        "checkpointResumeVerified": False,
        "staleWorkerCheckpointAccepted": 0,
        "staleWorkerPublicationAccepted": 0,
        "dbOutboxRecoveryAuthorityVerified": False,
        "status": "FAIL",
        "startedAt": utc_now(),
    }
    try:
        if os.environ.get("PRESENTON_BENCH_USER") or os.environ.get("PRESENTON_BENCH_PASSWORD"):
            raise ValueError("Basic-auth benchmark credentials are forbidden for crash validation")
        session_token = issue_session_token(repo)
        first, _ = start_worker(manifest, output, 1)
        admitted = http_json(
            "POST", f"{manifest['baseUrl']}/api/v1/ppt/presentation/generate/async",
            request_body(manifest["deck"]), 60, session_token,
        )
        task_id = str(admitted.get("id", ""))
        operation_id = str(admitted.get("operation_id") or admitted.get("operationId") or "")
        if not task_id or not operation_id:
            raise RuntimeError("admission did not return task and workspace operation ids")

        def persisted_checkpoint() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]] | None:
            job = generation_state(database_path, task_id)
            if not job or job["state"] != "running" or not job["attempt_token"]:
                return None
            checkpoints = checkpoint_snapshot(database_path, str(job["id"]))
            completed = [row for row in checkpoints if row["persist_status"] == "completed" or row["render_status"] == "completed"]
            operation = operation_state(database_path, operation_id)
            if completed and operation["state"] == "running" and operation["attempt_token"]:
                return job, operation, completed
            return None

        stale_job, stale_operation, before_checkpoints = wait_until(
            persisted_checkpoint, manifest["timeoutSeconds"], "one durable authored checkpoint",
        )
        first_pid = first.pid
        kill_owned_worker(first)
        first = None
        evidence["injectedCrashCount"] = 1
        evidence["crashedWorkerPid"] = first_pid

        def leases_expired() -> bool:
            job = generation_state(database_path, task_id)
            operation = operation_state(database_path, operation_id)
            now = datetime.now(timezone.utc)
            return bool(
                job and job["state"] in {"running", "awaiting_resume"}
                and operation["state"] == "running"
                and job["lease_expires_at"] and operation["lease_expires_at"]
                and parse_time(job["lease_expires_at"]) <= now
                and parse_time(operation["lease_expires_at"]) <= now
            )

        wait_until(leases_expired, manifest["leaseSeconds"] + 30, "both durable leases to expire")
        second, _ = start_worker(manifest, output, 2)

        def successor_active() -> tuple[dict[str, Any], dict[str, Any]] | None:
            job = generation_state(database_path, task_id)
            operation = operation_state(database_path, operation_id)
            if (
                job and job["state"] == "running"
                and operation["state"] == "running"
                and job["attempt_number"] > stale_job["attempt_number"]
                and operation["attempt_number"] > stale_operation["attempt_number"]
                and operation["fencing_token"] > stale_operation["fencing_token"]
                and job["attempt_token"] != stale_job["attempt_token"]
                and operation["attempt_token"] != stale_operation["attempt_token"]
            ):
                return job, operation
            return None

        active_job, active_operation = wait_until(
            successor_active, manifest["timeoutSeconds"], "successor attempt to become active"
        )
        # Probe both old fences while the successor still owns a live attempt;
        # terminal state rejection alone would not prove attempt fencing.
        checkpoint_rejected, publication_rejected = stale_fence_probe(
            database_path, stale_job, stale_operation
        )
        if not checkpoint_rejected or not publication_rejected:
            raise RuntimeError("stale attempt mutation was accepted while successor was active")

        def completed() -> dict[str, Any] | None:
            status = http_json(
                "GET", f"{manifest['baseUrl']}/api/v1/ppt/presentation/status/{quote(task_id)}",
                None, 30, session_token,
            )
            return status if str(status.get("status", "")).lower() in TERMINAL else None

        terminal = wait_until(completed, manifest["timeoutSeconds"], "recovered product operation completion")
        if terminal.get("status") != "completed":
            raise RuntimeError(f"recovered operation ended as {terminal.get('status')}")
        data = terminal.get("data") or {}
        presentation_id = str(data.get("presentation_id") or data.get("presentationId") or "")
        if not presentation_id:
            raise RuntimeError("recovered generation result has no presentation id")
        final_operation = operation_state(database_path, operation_id)
        result_revision = final_operation.get("result_revision")
        if not isinstance(result_revision, int) or result_revision < 0:
            raise RuntimeError("recovered generation operation has no result revision")
        export_request = {
            "format": "pptx",
            "id": presentation_id,
            "title": "crash recovery evidence",
            "pptxMode": manifest["deck"].get("pptxMode", "fidelity"),
            "expectedPresentationRevision": result_revision,
        }
        exported = http_json(
            "POST", f"{manifest['baseUrl']}/api/export-presentation",
            export_request, manifest["timeoutSeconds"], session_token,
        )
        export_operation_id = str(exported.get("operationId") or "")
        if exported.get("success") is not True or not export_operation_id:
            raise RuntimeError("PPTX export admission failed after recovery")
        replayed = http_json(
            "POST", f"{manifest['baseUrl']}/api/export-presentation",
            export_request, manifest["timeoutSeconds"], session_token,
        )
        if (
            replayed.get("success") is not True
            or replayed.get("duplicate") is not True
            or str(replayed.get("operationId") or "") != export_operation_id
        ):
            raise RuntimeError("PPTX export single-flight replay mismatch after recovery")
        export_operation, export_artifact_id = wait_for_export_artifact(
            base_url=manifest["baseUrl"], presentation_id=presentation_id,
            operation_id=export_operation_id, timeout=manifest["timeoutSeconds"],
            session_token=session_token,
        )
        download_route = (
            "/api/v1/ppt/workspace/artifacts/"
            f"{quote(export_artifact_id)}/download"
        )
        payload = http_bytes(
            f"{manifest['baseUrl']}{download_route}",
            manifest["timeoutSeconds"], session_token,
        )
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if "ppt/presentation.xml" not in archive.namelist():
                raise RuntimeError("recovered artifact is not a PPTX package")

        final_job = generation_state(database_path, task_id)
        final_checkpoints = checkpoint_snapshot(database_path, str(final_job["id"])) if final_job else []
        reuse_verified, checkpoint_reuse_count, reuse_event_cursor = audit_checkpoint_reuse_event(
            database_path,
            operation_id,
            active_operation["attempt_number"],
            len(before_checkpoints),
        )
        outbox_verified, outbox_rows, reclaim_event_cursor = audit_outbox(
            database_path, operation_id, stale_operation
        )
        converged = bool(
            final_job and final_job["state"] == "succeeded"
            and final_job["attempt_number"] > stale_job["attempt_number"]
            and final_operation["state"] == "succeeded"
            and final_operation["attempt_number"] > stale_operation["attempt_number"]
            and final_operation["fencing_token"] > stale_operation["fencing_token"]
        )
        evidence.update({
            "recoveredCrashCount": 1 if converged else 0,
            "automaticConvergenceRate": 1 if converged else 0,
            "checkpointResumeVerified": reuse_verified and bool(before_checkpoints),
            "checkpointReuseEventCount": checkpoint_reuse_count,
            "checkpointReuseEventCursor": reuse_event_cursor,
            "staleWorkerCheckpointAccepted": 0 if checkpoint_rejected else 1,
            "staleWorkerPublicationAccepted": 0 if publication_rejected else 1,
            "dbOutboxRecoveryAuthorityVerified": outbox_verified,
            "recoveryOutboxRowCount": outbox_rows,
            "reclaimEventCursor": reclaim_event_cursor,
            "preservedCheckpointCount": len(before_checkpoints),
            "recoveredArtifactSha256": hashlib.sha256(payload).hexdigest(),
            "recoveredArtifactBytes": len(payload),
            "exportLineage": {
                "presentationId": presentation_id,
                "generationOperationId": operation_id,
                "generationResultRevision": result_revision,
                "exportOperationId": export_operation_id,
                "exportArtifactId": export_artifact_id,
                "exportTerminalState": str(
                    export_operation.get("status") or export_operation.get("state") or ""
                ),
                "singleFlightReplayVerified": True,
                "downloadRoute": download_route,
                "downloadAuthorization": "owner-scoped-signed-session-cookie",
                "mediaType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "packageReadback": "ppt/presentation.xml",
            },
            "attempts": {
                "before": {"job": stale_job["attempt_number"], "operation": stale_operation["attempt_number"], "fencing": stale_operation["fencing_token"]},
                "after": {"job": final_job["attempt_number"], "operation": final_operation["attempt_number"], "fencing": final_operation["fencing_token"]},
            },
        })
        passed = (
            converged and evidence["checkpointResumeVerified"]
            and checkpoint_rejected and publication_rejected and outbox_verified
        )
        evidence["status"] = "PASS" if passed else "FAIL"
        evidence["completedAt"] = utc_now()
        evidence["wallTimeSeconds"] = time.monotonic() - started
        evidence["authentication"] = authentication_receipt(
            token=session_token,
            output=output,
            app_data=Path(os.environ["APP_DATA_DIRECTORY"]),
            evidence=evidence,
        )
        atomic_json(evidence_path, evidence)
        print(json.dumps({"status": evidence["status"], "evidencePath": str(evidence_path), "wallTimeSeconds": evidence["wallTimeSeconds"]}))
        return 0 if passed else 2
    except BaseException as error:
        evidence["status"] = "FAIL"
        evidence["failureCode"] = type(error).__name__
        evidence["failureDetail"] = str(error)[:800]
        evidence["completedAt"] = utc_now()
        evidence["wallTimeSeconds"] = time.monotonic() - started
        if session_token is not None:
            try:
                evidence["authentication"] = authentication_receipt(
                    token=session_token,
                    output=output,
                    app_data=Path(os.environ["APP_DATA_DIRECTORY"]),
                    evidence=evidence,
                )
            except RuntimeError as scan_error:
                evidence["authentication"] = {
                    "schemaVersion": "public-service-crash-auth-fixture/v1",
                    "transport": "signed-session-cookie",
                    "isolatedArtifactTokenByteScan": "failed",
                }
                evidence["failureDetail"] = str(scan_error)[:800]
        atomic_json(evidence_path, evidence)
        raise
    finally:
        stop_owned_worker(second)
        stop_owned_worker(first)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, TimeoutError, HTTPError, URLError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f"public-service crash recovery failed: {error}", file=sys.stderr)
        raise SystemExit(1)
