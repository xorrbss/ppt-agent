import asyncio
import os
import sys

from services.export_task_service import (
    DEFAULT_EXPORT_TASK_TIMEOUT_SECONDS,
    MIN_EXPORT_TASK_TIMEOUT_SECONDS,
    ExportTaskService,
)
from services.liteparse_service import LiteParseService
from utils.runtime_limits import BoundedTextBuffer


def test_bounded_text_buffer_keeps_only_tail():
    buffer = BoundedTextBuffer(limit=5)
    buffer.append("abcdef")
    buffer.append("gh")

    value = buffer.get()

    assert "defgh" in value
    assert "truncated 3 chars" in value


def test_liteparse_plain_bridge_keeps_stdout_and_bounds_stderr(tmp_path):
    service = LiteParseService(timeout_seconds=10)
    service._npm_project_root = str(tmp_path)

    process = service._run_plain_bridge_to_text(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x' * 5000); sys.stderr.write('e' * 10000)",
        ]
    )

    assert process.returncode == 0
    assert len(process.stdout) == 5000
    assert "truncated" in process.stderr


def test_export_child_output_is_bounded(tmp_path):
    service = ExportTaskService(timeout_seconds=10)

    result = asyncio.run(
        service._run_bounded_child(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('o' * 10000); sys.stderr.write('e' * 10000); sys.exit(7)",
            ],
            cwd=str(tmp_path),
            env=os.environ.copy(),
            timeout=10,
        )
    )

    assert result["returncode"] == 7
    assert "truncated" in str(result["stdout"])
    assert "truncated" in str(result["stderr"])


def test_export_timeout_defaults_to_large_deck_budget(monkeypatch):
    monkeypatch.delenv("EXPORT_TASK_TIMEOUT_SECONDS", raising=False)

    service = ExportTaskService()

    assert service.timeout_seconds == DEFAULT_EXPORT_TASK_TIMEOUT_SECONDS


def test_export_timeout_can_be_configured_with_a_safe_floor(monkeypatch):
    monkeypatch.setenv("EXPORT_TASK_TIMEOUT_SECONDS", "30")

    service = ExportTaskService()

    assert service.timeout_seconds == MIN_EXPORT_TASK_TIMEOUT_SECONDS


def test_export_runtime_receives_absolute_data_directories(monkeypatch, tmp_path):
    process_directory = tmp_path / "servers" / "fastapi"
    process_directory.mkdir(parents=True)
    monkeypatch.chdir(process_directory)
    monkeypatch.setenv("APP_DATA_DIRECTORY", "../../app_data")
    monkeypatch.setenv("TEMP_DIRECTORY", "../../runtime-temp")
    monkeypatch.setenv("NEXT_PUBLIC_FAST_API", "http://127.0.0.1:8000")
    monkeypatch.setenv("PUPPETEER_EXECUTABLE_PATH", "test-browser")

    service = ExportTaskService(timeout_seconds=10)
    environment = service._build_node_env()

    assert environment["APP_DATA_DIRECTORY"] == str(
        (tmp_path / "app_data").resolve()
    )
    assert environment["TEMP_DIRECTORY"] == str(
        (tmp_path / "runtime-temp").resolve()
    )
