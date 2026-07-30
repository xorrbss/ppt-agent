import importlib.util
import sys
import types
from pathlib import Path


def load_server_module(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *args, **kwargs: calls.append((args, kwargs))))
    monkeypatch.setitem(sys.modules, "api.main", types.SimpleNamespace(app=object()))
    path = Path(__file__).parents[2] / "server.py"
    spec = importlib.util.spec_from_file_location("testable_server", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


def test_server_cli_uses_explicit_log_level_and_propagates_it(monkeypatch):
    module, calls = load_server_module(monkeypatch)
    args = module.parse_args(["--port", "8123", "--reload", "false", "--log-level", "debug"])

    module.run_server(args)

    assert calls == [
        (("api.main:app",), {"host": "127.0.0.1", "port": 8123, "log_level": "debug", "reload": False})
    ]
    assert module.os.environ["NEXT_PUBLIC_FAST_API"] == "http://127.0.0.1:8123"
    assert module.os.environ["LOG_LEVEL"] == "DEBUG"


def test_server_cli_uses_log_level_environment_default(monkeypatch):
    module, _ = load_server_module(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "warning")

    assert module.parse_args(["--port", "8000"]).log_level == "warning"
