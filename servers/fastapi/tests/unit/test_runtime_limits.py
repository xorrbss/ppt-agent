import asyncio
import os
import sys

from services.export_task_service import ExportTaskService
from services.liteparse_service import LiteParseService
from utils.runtime_limits import BoundedTextBuffer, cap_text_by_env, merge_node_options


def test_bounded_text_buffer_keeps_only_tail():
    buffer = BoundedTextBuffer(limit=5)
    buffer.append("abcdef")
    buffer.append("gh")

    value = buffer.get()

    assert "defgh" in value
    assert "truncated 3 chars" in value


def test_merge_node_options_preserves_existing_limit():
    assert (
        merge_node_options("--trace-warnings --max-old-space-size=2048", 1024)
        == "--trace-warnings --max-old-space-size=2048"
    )


def test_cap_text_by_env(monkeypatch):
    monkeypatch.setenv("PRESENTON_MAX_EXTRACTED_TEXT_CHARS", "1000")

    assert cap_text_by_env("x" * 2000) == "x" * 1000


def test_export_service_reads_concurrency_from_env(monkeypatch):
    monkeypatch.setenv("PRESENTON_EXPORT_CONCURRENCY", "2")

    service = ExportTaskService()

    assert service.concurrency == 2


def test_liteparse_service_reads_concurrency_from_env(monkeypatch):
    monkeypatch.setenv("PRESENTON_LITEPARSE_CONCURRENCY", "2")

    service = LiteParseService()

    assert service.concurrency == 2


def test_liteparse_plain_bridge_caps_stdout_and_bounds_stderr(monkeypatch, tmp_path):
    monkeypatch.setenv("PRESENTON_MAX_EXTRACTED_TEXT_CHARS", "1024")
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
    assert len(process.stdout) == 1024
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
