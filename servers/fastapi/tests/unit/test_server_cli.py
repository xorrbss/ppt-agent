import importlib.util
import logging
import sys
import types
from pathlib import Path


def load_server_module(monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        types.SimpleNamespace(
            run=lambda *args, **kwargs: calls.append((args, kwargs))
        ),
    )
    monkeypatch.setitem(sys.modules, "api.main", types.SimpleNamespace(app=object()))
    path = Path(__file__).parents[2] / "server.py"
    spec = importlib.util.spec_from_file_location("testable_server", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


def test_server_cli_uses_explicit_log_level_and_propagates_it(monkeypatch):
    monkeypatch.delenv("APP_DATA_DIRECTORY", raising=False)
    module, calls = load_server_module(monkeypatch)
    args = module.parse_args(
        ["--port", "8123", "--reload", "false", "--log-level", "debug"]
    )

    module.run_server(args)

    assert calls == [
        (
            ("api.main:app",),
            {
                "host": "127.0.0.1",
                "port": 8123,
                "log_level": "debug",
                "reload": False,
            },
        )
    ]
    assert module.os.environ["NEXT_PUBLIC_FAST_API"] == "http://127.0.0.1:8123"
    assert module.os.environ["LOG_LEVEL"] == "DEBUG"


def test_server_cli_uses_log_level_environment_default(monkeypatch):
    module, _ = load_server_module(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "warning")

    assert module.parse_args(["--port", "8000"]).log_level == "warning"


def test_configure_file_logging_writes_rotating_backend_log(monkeypatch, tmp_path):
    module, _ = load_server_module(monkeypatch)
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))

    handler = module.configure_file_logging("info")
    assert handler is not None

    try:
        logging.getLogger("services.decompose_job_service").error(
            "document_decompose_failed job_id=test-job exception_type=RuntimeError"
        )
        handler.flush()

        log_path = tmp_path / "logs" / "backend.log"
        log_content = log_path.read_text(encoding="utf-8")
        assert "document_decompose_failed" in log_content
        assert "job_id=test-job" in log_content
        assert handler.maxBytes == module.BACKEND_LOG_MAX_BYTES
        assert handler.backupCount == module.BACKEND_LOG_BACKUP_COUNT
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_configure_file_logging_keeps_console_and_file_sinks(
    monkeypatch, tmp_path, capsys
):
    module, _ = load_server_module(monkeypatch)
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    root_logger = logging.getLogger()
    monkeypatch.setattr(root_logger, "handlers", [])
    original_level = root_logger.level

    try:
        file_handler = module.configure_file_logging("info")
        assert file_handler is not None
        console_handlers = [
            handler
            for handler in root_logger.handlers
            if isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, module.RotatingFileHandler)
        ]
        assert len(console_handlers) == 1

        logging.getLogger("services.dual_sink_probe").info("dual_sink_probe")
        for handler in root_logger.handlers:
            handler.flush()

        assert "dual_sink_probe" in capsys.readouterr().err
        assert "dual_sink_probe" in (
            tmp_path / "logs" / "backend.log"
        ).read_text(encoding="utf-8")
    finally:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()
        root_logger.setLevel(original_level)
