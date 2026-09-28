import sys

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

