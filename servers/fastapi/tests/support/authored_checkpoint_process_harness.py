"""Bounded child-process and SQLite observation for checkpoint restart tests."""

from __future__ import annotations

from collections import deque
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import threading
import time

import httpx

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
_PROCESS_OUTPUT: dict[int, deque[str]] = {}
_PROCESS_READERS: dict[int, threading.Thread] = {}

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
        f"Timed out after {timeout:.0f}s waiting for {description} (last transport error: {last_error!r})"
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
            with closing(sqlite3.connect(database_path, timeout=_SQLITE_BUSY_TIMEOUT)) as connection:
                return [tuple(row) for row in connection.execute(sql)]
        except sqlite3.OperationalError as error:
            if time.monotonic() >= deadline:
                raise AssertionError(f"SQLite stayed unreadable for {sql!r}: {error}") from error
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
        "bufsize": 1,
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
        output = deque(maxlen=200)
        _PROCESS_OUTPUT[process.pid] = output

        def drain(process=process, output=output) -> None:
            if process.stdout is not None:
                for line in process.stdout:
                    output.append(line)

        reader = threading.Thread(target=drain, daemon=True)
        _PROCESS_READERS[process.pid] = reader
        reader.start()
        base_url = f"http://127.0.0.1:{port}"

        def ready(process=process, reader=reader, output=output, base_url=base_url):
            if process.poll() is not None:
                reader.join(timeout=2)
                captured = "".join(output)[-16000:]
                if "10048" in captured or "bind on address" in captured:
                    raise _AddressInUse(captured)
                raise AssertionError(f"Uvicorn subprocess exited early ({process.returncode}): {captured}")
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

    raise AssertionError(f"{description} never bound a free port; last bind failure: {last_bind_failure!r}")


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
        process.wait(timeout=_KILL_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=_KILL_TIMEOUT)
    reader = _PROCESS_READERS.pop(process.pid, None)
    if reader is not None:
        reader.join(timeout=5)
    return "".join(_PROCESS_OUTPUT.pop(process.pid, ()))[-16000:]
