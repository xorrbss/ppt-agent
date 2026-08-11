"""Gate 7 proof against the production ASGI app and external worker process."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any, Callable
import uuid

from alembic.config import Config
from alembic.script import ScriptDirectory
import httpx


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until(
    predicate: Callable[[], Any],
    *,
    timeout: float = 30.0,
    interval: float = 0.05,
) -> Any:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (httpx.HTTPError, json.JSONDecodeError, sqlite3.OperationalError) as error:
            last_error = error
        time.sleep(interval)
    raise AssertionError(f"Timed out waiting for production process state: {last_error}")


def _spawn_process(
    command: list[str],
    *,
    server_root: Path,
    env: dict[str, str],
    log_path: Path,
) -> subprocess.Popen[str]:
    options: dict[str, Any] = {
        "cwd": str(server_root),
        "env": env,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    log_stream = log_path.open("w", encoding="utf-8")
    try:
        return subprocess.Popen(command, stdout=log_stream, **options)
    finally:
        log_stream.close()


def _process_log(log_path: Path) -> str:
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _assert_process_running(
    process: subprocess.Popen[str],
    *,
    log_path: Path,
    marker: str,
    timeout: float = 60.0,
) -> None:
    def started() -> bool:
        if process.poll() is not None:
            raise AssertionError(
                f"Process exited before {marker!r} ({process.returncode}):\n"
                f"{_process_log(log_path)}"
            )
        return marker in _process_log(log_path)

    _wait_until(started, timeout=timeout, interval=0.1)


def _kill_process_tree(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=15,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        # ``tasklist`` can take several seconds per poll on a loaded Windows
        # runner.  Querying the process handle directly keeps the crash/restart
        # assertion deterministic without weakening the process-boundary proof.
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _start_api(
    *,
    server_root: Path,
    env: dict[str, str],
    log_path: Path,
) -> tuple[subprocess.Popen[str], str]:
    port = _free_tcp_port()
    process = _spawn_process(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
            "--no-access-log",
        ],
        server_root=server_root,
        env=env,
        log_path=log_path,
    )
    base_url = f"http://127.0.0.1:{port}"

    def ready() -> bool:
        if process.poll() is not None:
            raise AssertionError(
                f"Production API exited during lifespan ({process.returncode}):\n"
                f"{_process_log(log_path)}"
            )
        response = httpx.get(f"{base_url}/api/v1/auth/status", timeout=0.5)
        return response.status_code == 200 and response.json()["configured"] is True

    _wait_until(ready, timeout=90.0, interval=0.1)
    return process, base_url


def _start_crash_probe_worker(
    *,
    server_root: Path,
    env: dict[str, str],
    log_path: Path,
    marker_path: Path,
    mode: str,
) -> subprocess.Popen[str]:
    worker_env = {
        **env,
        "WORKSPACE_CRASH_PROBE_MARKER": str(marker_path),
        "WORKSPACE_CRASH_PROBE_MODE": mode,
    }
    process = _spawn_process(
        [
            sys.executable,
            str(server_root / "tests" / "support" / "workspace_crash_probe_worker.py"),
        ],
        server_root=server_root,
        env=worker_env,
        log_path=log_path,
    )
    _assert_process_running(
        process,
        log_path=log_path,
        marker="Workspace operation external worker started",
        timeout=90.0,
    )
    return process


def _alembic_head(server_root: Path) -> str:
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


def _database_revision(database_path: Path) -> str:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def _raw_operation_id(connection: sqlite3.Connection, operation_id: uuid.UUID) -> str:
    for (candidate,) in connection.execute("SELECT id FROM workspace_operations"):
        if str(candidate).replace("-", "").lower() == operation_id.hex:
            return str(candidate)
    raise AssertionError(f"Operation {operation_id} was not persisted")


def _queue_crash_probe_operation(
    database_path: Path,
    *,
    operation_id: uuid.UUID,
) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with sqlite3.connect(database_path, timeout=10) as connection:
        raw_id = _raw_operation_id(connection, operation_id)
        # Keep every unrelated operation out of the external worker's claim set.
        connection.execute(
            "UPDATE workspace_operations SET state = 'awaiting_approval', "
            "next_attempt_at = NULL WHERE id <> ? AND state = 'queued'",
            (raw_id,),
        )
        updated = connection.execute(
            "UPDATE workspace_operations SET kind = 'test.crash_probe', "
            "state = 'queued', request_payload = '{}', expected_revision = 0, "
            "result_revision = NULL, attempt_number = 0, attempt_token = NULL, "
            "fencing_token = 0, lease_expires_at = NULL, heartbeat_at = NULL, "
            "next_attempt_at = NULL, retry_count = 0, max_attempts = 3, "
            "backoff_seconds = 0, cancel_requested_at = NULL, cancelled_at = NULL, "
            "progress = NULL, result = NULL, error_code = NULL, error_detail = NULL, "
            "started_at = NULL, completed_at = NULL, updated_at = ?, "
            "version = version + 1 "
            "WHERE id = ?",
            (now, raw_id),
        ).rowcount
        connection.commit()
    assert updated == 1
    return raw_id


def _operation_row(database_path: Path, raw_operation_id: str) -> dict[str, Any]:
    with sqlite3.connect(database_path, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT state, attempt_number, attempt_token, fencing_token, "
            "lease_expires_at, heartbeat_at, retry_count, error_code "
            "FROM workspace_operations WHERE id = ?",
            (raw_operation_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def _sqlite_datetime(value: object) -> datetime:
    assert value is not None
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=None)


def _claim_marker(marker_path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(marker_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _read_sse_event(
    client: httpx.Client,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    current: dict[str, str] = {}
    with client.stream(
        "GET",
        path,
        params={"stream": "true"},
        headers=headers,
        timeout=20.0,
    ) as response:
        if response.status_code != 200:
            response.read()
        assert response.status_code == 200, response.text
        for line in response.iter_lines():
            if not line:
                if current.get("event") == "workspace_event" and "data" in current:
                    data = json.loads(current["data"])
                    event = {"id": current.get("id"), "data": data}
                    if predicate is None or predicate(event):
                        return event
                current = {}
                continue
            field, _, value = line.partition(":")
            current[field] = value.lstrip()
    raise AssertionError("Workspace SSE stream ended before the expected event")


def _operation_item(timeline: dict[str, Any], operation_id: uuid.UUID) -> dict[str, Any]:
    return next(
        item
        for item in timeline["items"]
        if item["anchor_type"] == "operation"
        and item["operation_id"] == str(operation_id)
    )


def test_production_api_external_worker_restart_and_sse_backfill(
    tmp_path: Path,
) -> None:
    """Exercise migrations, API lifespan, process recovery and public wire contracts."""

    server_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "workspace-production-lifecycle.db"
    env = os.environ.copy()
    for inherited_name in (
        "CHAT_FIRST_WORKSPACE_KILL_SWITCH",
        "RESET_AUTH",
        "DISABLE_AUTH",
    ):
        env.pop(inherited_name, None)
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "APP_DATA_DIRECTORY": str(tmp_path / "app-data"),
            # TempFileService clears its configured root when each production
            # process imports it. Keep that startup cleanup inside this test
            # run instead of racing another Windows process in /tmp/presenton.
            "TEMP_DIRECTORY": str((tmp_path / "temp").resolve()),
            "USER_CONFIG_PATH": str(tmp_path / "userConfig.json"),
            "AUTH_USERNAME": "gate7_admin",
            "AUTH_PASSWORD": "gate7-password",
            "AUTH_OVERRIDE_FROM_ENV": "true",
            # This lifecycle proof does not mutate model/provider settings.
            # Disabling that middleware work avoids repeated user-config ACL
            # rewrites on Windows while preserving the production auth path.
            "CAN_CHANGE_KEYS": "false",
            # Use a valid offline provider selection so production lifespan
            # validation remains active without contacting an external model.
            "LLM": "codex",
            "DISABLE_IMAGE_GENERATION": "true",
            "DISABLE_ANONYMOUS_TRACKING": "true",
            "ENABLE_TEMPLATE_V2": "false",
            "CHAT_FIRST_WORKSPACE_ENABLED": "true",
            "CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE": "sqlite-local",
            "CHAT_FIRST_WORKSPACE_ROLLOUT_PERCENT": "100",
            "CHAT_FIRST_WORKSPACE_SHADOW_ENABLED": "false",
            "WORKSPACE_ASSET_MALWARE_SCAN_MODE": "disabled",
            "WORKSPACE_OPERATION_WORKER_MODE": "external",
            "WORKSPACE_OPERATION_POLL_SECONDS": "0.1",
            "WORKSPACE_OPERATION_LEASE_SECONDS": "4",
            "WORKSPACE_OPERATION_HEARTBEAT_SECONDS": "1",
            "TEMPLATE_V2_DEPLOYMENT_TIER": "local",
            "ENVIRONMENT": "test",
            "MIGRATE_DATABASE_ON_STARTUP": "true",
            "PYTHONUNBUFFERED": "1",
            "LOG_LEVEL": "INFO",
        }
    )

    api_process: subprocess.Popen[str] | None = None
    first_worker: subprocess.Popen[str] | None = None
    second_worker: subprocess.Popen[str] | None = None
    api_log = tmp_path / "api.log"
    first_worker_log = tmp_path / "worker-1.log"
    second_worker_log = tmp_path / "worker-2.log"
    first_worker_marker = tmp_path / "worker-1-claim.json"
    second_worker_marker = tmp_path / "worker-2-claim.json"
    try:
        api_process, base_url = _start_api(
            server_root=server_root,
            env=env,
            log_path=api_log,
        )
        # This is the migrated production database, not SQLModel.create_all alone.
        assert _database_revision(database_path) == _alembic_head(server_root)

        with httpx.Client(base_url=base_url, timeout=15.0) as client:
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "gate7_admin", "password": "gate7-password"},
            )
            assert login.status_code == 200, login.text
            assert login.json()["authenticated"] is True

            presentation_id = uuid.uuid4()
            create = client.post(
                "/api/v1/ppt/presentation/create",
                headers={"Idempotency-Key": "gate7-create"},
                json={
                    "content": "Gate 7 production lifecycle contract",
                    "presentation_id": str(presentation_id),
                    "n_slides": 3,
                    "language": "en",
                    "include_title_slide": True,
                },
            )
            assert create.status_code == 200, create.text
            assert create.json()["id"] == str(presentation_id)

            timeline_path = (
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/timeline"
            )
            events_path = (
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/events"
            )
            bootstrap_path = (
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/bootstrap"
            )
            commands_path = (
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/commands"
            )

            initial_timeline = client.get(timeline_path)
            assert initial_timeline.status_code == 200, initial_timeline.text
            generation_operation = next(
                item
                for item in initial_timeline.json()["items"]
                if item["anchor_type"] == "operation"
                and item["operation_type"] == "presentation.generate"
            )
            generation_operation_id = uuid.UUID(generation_operation["operation_id"])

            bootstrap_payload = {
                "idempotency_key": "gate7-bootstrap-quality",
                "prompt": "Review the presentation quality.",
                "operation_type": "quality.review",
                "expected_revision": 0,
                "generation_settings": {},
                "asset_ids": [],
                "attachments": [],
            }
            bootstrap = client.post(bootstrap_path, json=bootstrap_payload)
            assert bootstrap.status_code == 200, bootstrap.text
            assert bootstrap.json()["duplicate"] is False
            duplicate_bootstrap = client.post(bootstrap_path, json=bootstrap_payload)
            assert duplicate_bootstrap.status_code == 200, duplicate_bootstrap.text
            assert duplicate_bootstrap.json()["duplicate"] is True
            assert duplicate_bootstrap.json()["operation_id"] == bootstrap.json()[
                "operation_id"
            ]

            # Consume one real streaming response and close it to model a network
            # disconnect. The next stream must backfill from this durable cursor.
            before_disconnect = _read_sse_event(client, events_path)
            reconnect_cursor = before_disconnect["id"]
            assert reconnect_cursor is not None

            command_payload = {
                "command": "Tighten the opening slide.",
                "command_type": "presentation.edit",
                "idempotency_key": "gate7-edit",
                "expected_revision": 0,
                # WorkspaceChatPanel always sends this top-level field, even
                # when the current command has no pending attachments.
                "asset_ids": [],
                "context": {"source": "gate7-process-test"},
            }
            command = client.post(commands_path, json=command_payload)
            assert command.status_code == 200, command.text
            command_body = command.json()
            assert command_body["duplicate"] is False
            command_operation_id = uuid.UUID(command_body["operation_id"])
            duplicate_command = client.post(commands_path, json=command_payload)
            assert duplicate_command.status_code == 200, duplicate_command.text
            assert duplicate_command.json()["duplicate"] is True
            assert duplicate_command.json()["operation_id"] == str(command_operation_id)

            backfilled = _read_sse_event(
                client,
                events_path,
                headers={"Last-Event-ID": reconnect_cursor},
                predicate=lambda event: event["data"].get("operation_id")
                == str(command_operation_id),
            )
            assert int(backfilled["id"]) > int(reconnect_cursor)

            stale = client.post(
                commands_path,
                json={
                    **command_payload,
                    "command": "Apply a stale revision edit.",
                    "idempotency_key": "gate7-stale-edit",
                    "expected_revision": 999,
                },
            )
            assert stale.status_code == 409, stale.text
            assert stale.json()["detail"] == "revision_conflict"
            assert stale.json()["error"]["code"] == "revision_conflict"
            assert stale.json()["error"]["retryable"] is True

            raw_operation_id = _queue_crash_probe_operation(
                database_path,
                operation_id=generation_operation_id,
            )
            first_worker = _start_crash_probe_worker(
                server_root=server_root,
                env=env,
                log_path=first_worker_log,
                marker_path=first_worker_marker,
                mode="block",
            )
            first_claim = _wait_until(
                lambda: _claim_marker(first_worker_marker),
                timeout=30.0,
                interval=0.05,
            )
            first_row = _operation_row(database_path, raw_operation_id)
            assert first_row["state"] == "running"
            assert first_row["attempt_number"] == first_claim["attempt_number"] == 1
            assert first_row["attempt_token"] == first_claim["attempt_token"]
            assert first_row["fencing_token"] == first_claim["fencing_token"] >= 1
            first_heartbeat = _sqlite_datetime(first_row["heartbeat_at"])
            first_lease = _sqlite_datetime(first_row["lease_expires_at"])

            def renewed_by_first_owner() -> dict[str, Any] | None:
                row = _operation_row(database_path, raw_operation_id)
                if (
                    row["state"] == "running"
                    and _sqlite_datetime(row["heartbeat_at"]) > first_heartbeat
                    and _sqlite_datetime(row["lease_expires_at"]) > first_lease
                ):
                    return row
                return None

            renewed_row = _wait_until(
                renewed_by_first_owner,
                timeout=10.0,
                interval=0.05,
            )
            first_worker_pid = first_worker.pid
            lease_owner_pid = int(first_claim["pid"])
            assert _pid_is_running(lease_owner_pid)
            # Hard-kill the process that owns and has renewed the durable lease.
            _kill_process_tree(first_worker)
            first_worker = None
            _wait_until(
                lambda: not _pid_is_running(lease_owner_pid),
                timeout=15.0,
                interval=0.1,
            )

            def naturally_expired_lease() -> dict[str, Any] | None:
                row = _operation_row(database_path, raw_operation_id)
                if (
                    row["state"] == "running"
                    and row["attempt_token"] == renewed_row["attempt_token"]
                    and _sqlite_datetime(row["lease_expires_at"])
                    <= datetime.now(timezone.utc).replace(tzinfo=None)
                ):
                    return row
                return None

            _wait_until(naturally_expired_lease, timeout=12.0, interval=0.05)

            second_worker = _start_crash_probe_worker(
                server_root=server_root,
                env=env,
                log_path=second_worker_log,
                marker_path=second_worker_marker,
                mode="succeed",
            )
            assert second_worker.pid != first_worker_pid
            second_claim = _wait_until(
                lambda: _claim_marker(second_worker_marker),
                timeout=30.0,
                interval=0.05,
            )
            assert _pid_is_running(int(second_claim["pid"]))
            assert second_claim["attempt_number"] >= 2
            assert second_claim["attempt_token"] != first_claim["attempt_token"]
            assert second_claim["fencing_token"] > first_claim["fencing_token"]

            def recovered_operation() -> tuple[dict[str, Any], dict[str, Any]] | None:
                response = client.get(timeline_path)
                response.raise_for_status()
                operation = _operation_item(response.json(), generation_operation_id)
                recovered_event = next(
                    (
                        event
                        for event in operation["events"]
                        if event["type"] == "operation.error"
                        and event["error"] == "operation_recovery_required"
                    ),
                    None,
                )
                result_event = next(
                    (
                        event
                        for event in operation["events"]
                        if event["type"] == "operation.result"
                    ),
                    None,
                )
                if operation["status"] == "succeeded" and recovered_event and result_event:
                    return operation, recovered_event
                return None

            operation, recovered = _wait_until(
                recovered_operation,
                timeout=30.0,
                interval=0.1,
            )
            assert recovered["retryable"] is True
            final_row = _operation_row(database_path, raw_operation_id)
            assert final_row["state"] == "succeeded"
            assert final_row["attempt_number"] >= 2
            assert final_row["fencing_token"] == second_claim["fencing_token"]
            assert final_row["attempt_token"] is None
            assert api_process.poll() is None
            assert client.get(timeline_path).status_code == 200
    finally:
        _kill_process_tree(second_worker)
        _kill_process_tree(first_worker)
        _kill_process_tree(api_process)
