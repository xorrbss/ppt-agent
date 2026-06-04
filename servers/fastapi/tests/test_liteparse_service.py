import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from services.liteparse_service import _DEFAULT_NUM_WORKERS, LiteParseService


def _ok_process(
    stdout: str = "ok",
    returncode: int = 0,
    stderr: str = "",
):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _completed_process(command, stdout: str = "ok", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=command, returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestLiteParseService:
    def test_parse_uses_safe_defaults(self):
        # Default ("plain") bridge spawns Node via Popen in
        # _run_plain_bridge_to_text. Mock that method so the test asserts the
        # command/args building (the real intent) without depending on a real
        # node runner being installed at a Docker path.
        with patch.dict(
            "os.environ",
            {
                "LITEPARSE_DPI": "",
                "LITEPARSE_NUM_WORKERS": "",
            },
            clear=False,
        ), patch.object(
            LiteParseService,
            "check_runtime_ready",
            return_value=(True, "ok"),
        ), patch.object(
            LiteParseService,
            "_run_plain_bridge_to_text",
            side_effect=lambda command: _completed_process(command),
        ) as mock_run:
            service = LiteParseService(timeout_seconds=30)
            r = service.parse("/tmp/sample.pdf", ocr_enabled=True, ocr_language="eng")
            assert r["ok"] is True
            assert r["text"] == "ok"

        command = mock_run.call_args.args[0]
        assert "--dpi" in command
        assert command[command.index("--dpi") + 1] == "120"
        assert "--num-workers" in command
        # Empty LITEPARSE_NUM_WORKERS falls back to the computed default, which
        # depends on os.cpu_count(); assert against the actual default rather
        # than a core-count-specific literal so this holds on any machine.
        assert command[command.index("--num-workers") + 1] == str(_DEFAULT_NUM_WORKERS)
        assert command[command.index("--python-bridge") + 1] == "plain"

    def test_parse_uses_env_overrides(self):
        with patch.dict(
            "os.environ",
            {
                "LITEPARSE_DPI": "96",
                "LITEPARSE_NUM_WORKERS": "2",
            },
            clear=False,
        ), patch.object(
            LiteParseService,
            "check_runtime_ready",
            return_value=(True, "ok"),
        ), patch.object(
            LiteParseService,
            "_run_plain_bridge_to_text",
            side_effect=lambda command: _completed_process(command),
        ) as mock_run:
            service = LiteParseService(timeout_seconds=30)
            service.parse("/tmp/sample.pdf", ocr_enabled=True, ocr_language="eng")

        command = mock_run.call_args.args[0]
        assert command[command.index("--dpi") + 1] == "96"
        assert command[command.index("--num-workers") + 1] == "2"

    def test_parse_clamps_invalid_env_values(self):
        with patch.dict(
            "os.environ",
            {
                "LITEPARSE_DPI": "-1",
                "LITEPARSE_NUM_WORKERS": "0",
            },
            clear=False,
        ), patch.object(
            LiteParseService,
            "check_runtime_ready",
            return_value=(True, "ok"),
        ), patch.object(
            LiteParseService,
            "_run_plain_bridge_to_text",
            side_effect=lambda command: _completed_process(command),
        ) as mock_run:
            service = LiteParseService(timeout_seconds=30)
            service.parse("/tmp/sample.pdf", ocr_enabled=True, ocr_language="eng")

        command = mock_run.call_args.args[0]
        assert command[command.index("--dpi") + 1] == "72"
        assert command[command.index("--num-workers") + 1] == "1"

    def test_parse_json_bridge_env(self):
        with patch.dict(
            "os.environ",
            {"LITEPARSE_RUNNER_OUTPUT": "json"},
            clear=False,
        ), patch.object(
            LiteParseService,
            "check_runtime_ready",
            return_value=(True, "ok"),
        ), patch(
            "services.liteparse_service.subprocess.run",
            return_value=_ok_process(stdout='{"ok": true, "text": "legacy"}\n'),
        ) as mock_run:
            service = LiteParseService(timeout_seconds=30)
            r = service.parse("/tmp/sample.pdf", ocr_enabled=True, ocr_language="eng")
            assert r["text"] == "legacy"

        command = mock_run.call_args.args[0]
        assert command[command.index("--python-bridge") + 1] == "json"
