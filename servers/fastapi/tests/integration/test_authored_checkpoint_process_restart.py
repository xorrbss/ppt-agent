from __future__ import annotations

from collections import Counter
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


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _wait_until(predicate, *, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            last_error = error
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for subprocess state: {last_error}")


def _start_server(*, python: Path, port: int, env: dict[str, str]):
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
            raise AssertionError(
                f"Uvicorn subprocess exited early ({process.returncode}): {output}"
            )
        response = httpx.get(f"{base_url}/openapi.json", timeout=0.5)
        return response.status_code == 200

    _wait_until(ready, timeout=15)
    return process, base_url


def _kill_process_tree(process: subprocess.Popen) -> str:
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    try:
        output, _ = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=5)
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
            "AUTHORED_SUBPROCESS_BLOCK_RENDER_SLIDE": "2"
        }
        first, first_url = _start_server(
            python=python,
            port=_free_tcp_port(),
            env=first_env,
        )
        first_runtime_pid = next(
            event["pid"]
            for event in _events(event_log)
            if event["event"] == "startup"
        )
        response = httpx.post(
            f"{first_url}/api/v1/ppt/presentation/generate/async",
            json=payload,
            headers=headers,
            timeout=5,
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["id"]

        _wait_until(
            lambda: any(
                event["event"] == "render_blocked"
                and event.get("slide_index") == 2
                for event in _events(event_log)
            )
        )
        running = httpx.get(
            f"{first_url}/api/v1/ppt/presentation/status/{task_id}",
            timeout=2,
        ).json()
        assert running["status"] == "running"
        assert running["progress"]["completed_slides"] == 2
        assert running["progress"]["current_stage"] == "render"
        with sqlite3.connect(database_path) as connection:
            assert connection.execute(
                "SELECT author_status, illustrate_status, render_status, "
                "persist_status FROM authored_slide_checkpoints "
                "WHERE slide_index = 2"
            ).fetchone() == ("completed", "skipped", "running", "pending")

        first_pid = first.pid
        first_output = _kill_process_tree(first)
        first = None

        with sqlite3.connect(database_path) as connection:
            crashed_job = connection.execute(
                "SELECT state, attempt_number, completed_slides "
                "FROM presentation_generation_jobs"
            ).fetchone()
            assert crashed_job == ("running", 1, 2)
            assert connection.execute(
                "SELECT COUNT(*) FROM presentations"
            ).fetchone()[0] == 0

        # The test app uses a two-second lease and a sixty-second heartbeat.
        time.sleep(2.5)
        second_env = common_env | {
            "AUTHORED_SUBPROCESS_BLOCK_RENDER_SLIDE": "-1"
        }
        second, second_url = _start_server(
            python=python,
            port=_free_tcp_port(),
            env=second_env,
        )
        second_runtime_pid = [
            event["pid"]
            for event in _events(event_log)
            if event["event"] == "startup"
        ][-1]
        assert second.pid != first_pid
        assert second_runtime_pid != first_runtime_pid

        pending = httpx.get(
            f"{second_url}/api/v1/ppt/presentation/status/{task_id}",
            timeout=2,
        ).json()
        assert pending["status"] == "pending"
        assert "Idempotency-Key" in pending["message"]
        with sqlite3.connect(database_path) as connection:
            assert connection.execute(
                "SELECT state FROM presentation_generation_jobs"
            ).fetchone()[0] == "awaiting_resume"

        resumed = httpx.post(
            f"{second_url}/api/v1/ppt/presentation/generate/async",
            json=payload,
            headers=headers,
            timeout=5,
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["id"] == task_id

        def completed_status():
            status_response = httpx.get(
                f"{second_url}/api/v1/ppt/presentation/status/{task_id}",
                timeout=2,
            )
            status_response.raise_for_status()
            body = status_response.json()
            return body if body["status"] == "completed" else None

        completed = _wait_until(completed_status, timeout=30)
        assert completed["progress"]["completed_slides"] == 6
        assert completed["progress"]["percent"] == 100
        assert completed["failure"] is None
        output_path = Path(completed["data"]["path"])
        assert output_path.exists()
        assert len(PptxPresentation(output_path).slides) == 6

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

        with sqlite3.connect(database_path) as connection:
            published_job = connection.execute(
                "SELECT state, attempt_number, completed_slides, current_stage "
                "FROM presentation_generation_jobs"
            ).fetchone()
            assert published_job == ("published", 2, 6, "completed")
            assert connection.execute(
                "SELECT COUNT(*) FROM authored_slide_checkpoints "
                "WHERE persist_status = 'completed'"
            ).fetchone()[0] == 6
            assert connection.execute(
                "SELECT lifecycle_status, n_slides FROM presentations"
            ).fetchone() == ("published", 6)
            assert connection.execute(
                "SELECT COUNT(*) FROM slides"
            ).fetchone()[0] == 6
    except Exception as error:
        raise AssertionError(
            f"subprocess recovery failed; first={first_output!r}, "
            f"second={second_output!r}"
        ) from error
    finally:
        if first is not None:
            _kill_process_tree(first)
        if second is not None:
            _kill_process_tree(second)
