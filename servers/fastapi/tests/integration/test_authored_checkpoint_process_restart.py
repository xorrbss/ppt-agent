from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import time

import httpx
from pptx import Presentation as PptxPresentation


# Every constant below is a deadline for a polled condition, never a duration
# the test expects the machine to need. A contended full-suite host slows
# subprocess startup, HTTP round trips and SQLite writes by roughly an order of
# magnitude compared with running this file alone, so the budgets are
# deliberately generous: an idle host never spends them because each wait
# returns the moment its condition holds.
_HTTP_TIMEOUT = 30.0
_SERVER_READY_TIMEOUT = 180.0
_CONDITION_TIMEOUT = 120.0
_LEASE_EXPIRY_TIMEOUT = 120.0
_COMPLETION_TIMEOUT = 300.0
_KILL_TIMEOUT = 60.0
_SQLITE_BUSY_TIMEOUT = 30.0
_POLL_INTERVAL = 0.05

# The crashed runtime keeps a short lease so the observable expiry this test
# polls for arrives quickly; the resuming runtime keeps a long one so a
# contended host cannot evict a healthy attempt while it finishes the deck.
# Only expiry of the *crashed* lease is what allows the takeover, and that is
# asserted directly against the persisted row.
_CRASHED_LEASE_SECONDS = "8"
_RESUMING_LEASE_SECONDS = "90"


class _AddressInUse(RuntimeError):
    """The ephemeral port was taken between probing it and binding it."""


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    events: list[dict] = []
    for position, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # A live subprocess may be appending while the parent reads, so the
            # final line can be a partial record. Anything earlier is real
            # corruption and must still fail loudly.
            if position == len(lines) - 1:
                break
            raise
    return events


def _wait_until(predicate, *, timeout: float, description: str):
    """Poll ``predicate`` until it reports success or ``timeout`` elapses.

    Transport-level failures are treated as "not ready yet": under suite load a
    single HTTP round trip or SQLite read can exceed any fixed per-call budget,
    and retrying is what distinguishes a slow host from a broken one.
    """

    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while True:
        try:
            result = predicate()
            if result is not None and result is not False:
                return result
        except (
            httpx.HTTPError,
            json.JSONDecodeError,
            sqlite3.OperationalError,
        ) as error:
            last_error = error
        if time.monotonic() >= deadline:
            break
        time.sleep(_POLL_INTERVAL)
    raise AssertionError(
        f"Timed out after {timeout:.0f}s waiting for {description}"
        f" (last transport error: {last_error!r})"
    )


def _request(method: str, url: str, *, description: str, **kwargs) -> httpx.Response:
    """Issue one logical request, retrying only transport-level failures.

    Both generate calls carry the same ``Idempotency-Key`` -- the very contract
    under test -- so replaying a request whose response was lost to a timeout
    cannot create a second attempt.
    """

    return _wait_until(
        lambda: httpx.request(method, url, timeout=_HTTP_TIMEOUT, **kwargs),
        timeout=_CONDITION_TIMEOUT,
        description=description,
    )


def _query(database_path: Path, sql: str) -> list[tuple]:
    """Read the durable state, tolerating a concurrently written WAL."""

    deadline = time.monotonic() + _CONDITION_TIMEOUT
    while True:
        try:
            with closing(
                sqlite3.connect(database_path, timeout=_SQLITE_BUSY_TIMEOUT)
            ) as connection:
                return [tuple(row) for row in connection.execute(sql)]
        except sqlite3.OperationalError as error:
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"SQLite stayed unreadable for {sql!r}: {error}"
                ) from error
            time.sleep(_POLL_INTERVAL)


def _one(database_path: Path, sql: str) -> tuple:
    rows = _query(database_path, sql)
    assert len(rows) == 1, f"expected exactly one row from {sql!r}, got {rows!r}"
    return rows[0]


def _as_utc(value: str) -> datetime:
    """Read a SQLite ``DateTime`` column back as an aware UTC instant.

    SQLite stores these naive; the service writes and compares them in UTC.
    """

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _start_server(*, python: Path, env: dict[str, str], description: str):
    options = {
        "cwd": str(Path(__file__).resolve().parents[2]),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True

    last_bind_failure = ""
    for _ in range(5):
        port = _free_tcp_port()
        process = subprocess.Popen(
            [
                str(python),
                "-m",
                "uvicorn",
                "tests.authored_subprocess_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            **options,
        )
        base_url = f"http://127.0.0.1:{port}"

        def ready():
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                if "10048" in output or "bind on address" in output:
                    raise _AddressInUse(output)
                raise AssertionError(
                    f"Uvicorn subprocess exited early ({process.returncode}): {output}"
                )
            # The probe itself must not impose a tighter budget than the host
            # can meet: a 500ms read timeout never succeeds on a saturated
            # machine even once the server is fully up.
            response = httpx.get(f"{base_url}/openapi.json", timeout=_HTTP_TIMEOUT)
            return response.status_code == 200

        try:
            _wait_until(
                ready,
                timeout=_SERVER_READY_TIMEOUT,
                description=f"{description} to serve {base_url}",
            )
        except _AddressInUse as error:
            # Another process claimed the port between _free_tcp_port() and
            # uvicorn's bind. That is a harness collision, not a product fault.
            last_bind_failure = str(error)
            _kill_process_tree(process)
            continue
        except BaseException:
            _kill_process_tree(process)
            raise
        return process, base_url

    raise AssertionError(
        f"{description} never bound a free port; last bind failure: "
        f"{last_bind_failure!r}"
    )


def _kill_process_tree(process: subprocess.Popen) -> str:
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=_KILL_TIMEOUT,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    try:
        output, _ = process.communicate(timeout=_KILL_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=_KILL_TIMEOUT)
    return output or ""


def test_hard_process_kill_expires_lease_and_resumes_same_job(tmp_path):
    """A new OS process resumes durable checkpoints without duplicate success."""

    server_root = Path(__file__).resolve().parents[2]
    python = server_root / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(os.environ.get("PYTHON", "python"))

    database_path = tmp_path / "process-restart.db"
    event_log = tmp_path / "external-calls.jsonl"
    common_env = os.environ.copy()
    common_env.update(
        {
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "APP_DATA_DIRECTORY": str(tmp_path / "app-data"),
            "AUTHORED_CHECKPOINT_DIRECTORY": str(tmp_path / "checkpoints"),
            "AUTHORED_SUBPROCESS_EVENT_LOG": str(event_log),
            "AUTHORED_SUBPROCESS_SLIDES": "6",
            "AUTHORED_TEST_FAILURES_ENABLED": "false",
            "ENVIRONMENT": "test",
            "MIGRATE_DATABASE_ON_STARTUP": "false",
            "PYTHONUNBUFFERED": "1",
        }
    )
    payload = {
        "content": "hard process checkpoint recovery deck",
        "template": "authored",
        "strategy": "authored",
        "n_slides": 6,
        "include_title_slide": False,
        "vision_qa": False,
        "export_as": "pptx",
    }
    headers = {
        "Idempotency-Key": "hard-process-authored-resume",
        "Cookie": "test_session=process-restart",
    }

    first = None
    second = None
    first_runtime_pid = None
    second_runtime_pid = None
    first_output = ""
    second_output = ""
    try:
        first_env = common_env | {
            "AUTHORED_SUBPROCESS_BLOCK_RENDER_SLIDE": "2",
            "AUTHORED_SUBPROCESS_LEASE_SECONDS": _CRASHED_LEASE_SECONDS,
            "WORKSPACE_OPERATION_LEASE_SECONDS": _CRASHED_LEASE_SECONDS,
            "WORKSPACE_OPERATION_HEARTBEAT_SECONDS": "1",
        }
        first, first_url = _start_server(
            python=python,
            env=first_env,
            description="the crashing runtime",
        )
        first_runtime_pid = _wait_until(
            lambda: next(
                (
                    event["pid"]
                    for event in _events(event_log)
                    if event["event"] == "startup"
                ),
                None,
            ),
            timeout=_CONDITION_TIMEOUT,
            description="the crashing runtime to log its startup pid",
        )
        response = _request(
            "POST",
            f"{first_url}/api/v1/ppt/presentation/generate/async",
            json=payload,
            headers=headers,
            description="the initial authored generate request",
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["id"]

        _wait_until(
            lambda: any(
                event["event"] == "render_blocked"
                and event.get("slide_index") == 2
                for event in _events(event_log)
            ),
            timeout=_CONDITION_TIMEOUT,
            description="slide 2 to block inside render",
        )

        def running_render_status():
            status_response = httpx.get(
                f"{first_url}/api/v1/ppt/presentation/status/{task_id}",
                timeout=_HTTP_TIMEOUT,
            )
            status_response.raise_for_status()
            body = status_response.json()
            progress = body.get("progress") or {}
            if (
                body.get("status") == "running"
                and progress.get("completed_slides") == 2
                and progress.get("current_stage") == "render"
            ):
                return body
            return None

        # The subprocess event log and the durable status projection are
        # independent writes, so the render-blocked marker can become visible
        # one scheduler tick before the HTTP projection catches up.
        _wait_until(
            running_render_status,
            timeout=_CONDITION_TIMEOUT,
            description="the durable status projection to report two rendered slides",
        )
        assert _one(
            database_path,
            "SELECT author_status, illustrate_status, render_status, "
            "persist_status FROM authored_slide_checkpoints "
            "WHERE slide_index = 2",
        ) == ("completed", "skipped", "running", "pending")

        first_pid = first.pid
        first_output = _kill_process_tree(first)
        first = None

        crashed_job = _one(
            database_path,
            "SELECT state, attempt_number, completed_slides "
            "FROM presentation_generation_jobs",
        )
        # A forced process-tree termination normally leaves the leased row
        # running. On Windows the parent can briefly process task cancellation
        # while taskkill tears down its children, in which case the same
        # attempt is already placed in the safe resume state. Both boundaries
        # must preserve checkpoints and require the same authenticated
        # idempotent resume below.
        assert crashed_job in {
            ("running", 1, 2),
            ("awaiting_resume", 1, 2),
        }
        assert _one(
            database_path,
            "SELECT lifecycle_status, n_slides FROM presentations",
        ) == ("staging", 6)

        def lease_is_recoverable():
            state, job_lease_expires_at, operation_lease_expires_at = _one(
                database_path,
                "SELECT jobs.state, jobs.lease_expires_at, "
                "operations.lease_expires_at "
                "FROM presentation_generation_jobs AS jobs "
                "JOIN workspace_operations AS operations "
                "ON operations.id = jobs.workspace_operation_id",
            )
            # The workspace operation is the canonical lease and the linked
            # generation row mirrors it for checkpoint fencing. Both must be
            # reclaimable before the new process performs startup recovery.
            now = datetime.now(timezone.utc)
            leases = (job_lease_expires_at, operation_lease_expires_at)
            return state if all(
                value is None or _as_utc(value) <= now for value in leases
            ) else None

        # Waiting for the recorded expiry instant -- instead of sleeping for a
        # duration that merely tends to exceed the lease -- is what makes the
        # takeover below deterministic. No heartbeat can renew the lease: the
        # process tree that owned it is gone.
        _wait_until(
            lease_is_recoverable,
            timeout=_LEASE_EXPIRY_TIMEOUT,
            description="the killed attempt's lease to become reclaimable",
        )

        second_env = common_env | {
            "AUTHORED_SUBPROCESS_BLOCK_RENDER_SLIDE": "-1",
            "AUTHORED_SUBPROCESS_LEASE_SECONDS": _RESUMING_LEASE_SECONDS,
            "WORKSPACE_OPERATION_LEASE_SECONDS": _RESUMING_LEASE_SECONDS,
            "WORKSPACE_OPERATION_HEARTBEAT_SECONDS": "1",
        }
        second, second_url = _start_server(
            python=python,
            env=second_env,
            description="the resuming runtime",
        )
        second_runtime_pid = _wait_until(
            lambda: (
                [
                    event["pid"]
                    for event in _events(event_log)
                    if event["event"] == "startup"
                ]
                + [None]
            )[1],
            timeout=_CONDITION_TIMEOUT,
            description="the resuming runtime to log its startup pid",
        )
        assert second.pid != first_pid
        assert second_runtime_pid != first_runtime_pid

        def completed_status():
            status_response = httpx.get(
                f"{second_url}/api/v1/ppt/presentation/status/{task_id}",
                timeout=_HTTP_TIMEOUT,
            )
            status_response.raise_for_status()
            body = status_response.json()
            return body if body["status"] == "completed" else None

        try:
            completed = _wait_until(
                completed_status,
                timeout=_COMPLETION_TIMEOUT,
                description="the resumed attempt to publish the deck",
            )
        except AssertionError as error:
            stalled_job = _one(
                database_path,
                "SELECT state, attempt_number, completed_slides, "
                "current_stage, last_error FROM presentation_generation_jobs",
            )
            raise AssertionError(
                f"{error}; durable job row={stalled_job!r}"
            ) from error
        assert completed["progress"]["completed_slides"] == 6
        assert completed["progress"]["percent"] == 100
        assert completed["failure"] is None
        storage_key = _one(
            database_path, "SELECT storage_key FROM workspace_artifacts"
        )[0]
        assert completed["data"]["path"].replace("\\", "/").endswith(
            f"/{storage_key}"
        )
        output_path = Path(common_env["APP_DATA_DIRECTORY"]) / storage_key
        assert output_path.exists()
        assert len(PptxPresentation(output_path).slides) == 6

        # Replaying the original idempotency key after recovery must resolve to
        # the same durable task instead of replacing the historical row.
        replayed = _request(
            "POST",
            f"{second_url}/api/v1/ppt/presentation/generate/async",
            json=payload,
            headers=headers,
            description="the completed idempotent replay",
        )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["id"] == task_id
        assert replayed.json()["status"] == "completed"

        second_output = _kill_process_tree(second)
        second = None

        recorded = _events(event_log)
        assert Counter(
            event["slide_index"]
            for event in recorded
            if event["event"] == "author_success"
        ) == Counter({index: 1 for index in range(6)})
        assert Counter(
            event["slide_index"]
            for event in recorded
            if event["event"] == "render_success"
        ) == Counter({index: 1 for index in range(6)})
        assert sum(event["event"] == "brief_success" for event in recorded) == 1
        assert sum(event["event"] == "outline_success" for event in recorded) == 1
        assert {
            event["pid"] for event in recorded if event["event"] == "startup"
        } == {first_runtime_pid, second_runtime_pid}

        published_job = _one(
            database_path,
            "SELECT state, attempt_number, completed_slides, current_stage "
            "FROM presentation_generation_jobs",
        )
        assert published_job == ("published", 2, 6, "completed")
        assert _one(
            database_path,
            "SELECT COUNT(*) FROM authored_slide_checkpoints "
            "WHERE persist_status = 'completed'",
        )[0] == 6
        assert _one(
            database_path, "SELECT lifecycle_status, n_slides FROM presentations"
        ) == ("published", 6)
        assert _one(database_path, "SELECT COUNT(*) FROM slides")[0] == 6
    except Exception as error:
        # Preserve subprocess diagnostics even when the failure happens before
        # the normal hard-kill points (for example, during the initial POST).
        if first is not None:
            first_output = _kill_process_tree(first)
            first = None
        if second is not None:
            second_output = _kill_process_tree(second)
            second = None
        raise AssertionError(
            f"subprocess recovery failed; first={first_output!r}, "
            f"second={second_output!r}"
        ) from error
    finally:
        if first is not None:
            _kill_process_tree(first)
        if second is not None:
            _kill_process_tree(second)
