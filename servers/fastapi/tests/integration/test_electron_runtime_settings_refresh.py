import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

from fastapi import FastAPI
import pytest
import uvicorn

from api.middlewares import RuntimeSettingsContextMiddleware
from utils.runtime_settings import get_process_runtime_settings, resolve_runtime_settings
from utils.user_config import refresh_process_runtime_settings


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
ELECTRON_REFRESH_CLIENT = (
    REPOSITORY_ROOT
    / "electron"
    / "app_dist"
    / "utils"
    / "runtime-settings-refresh.js"
)


def _get_json(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        return response.status, json.loads(body)
    finally:
        connection.close()


def test_compiled_electron_client_refreshes_the_live_fastapi_snapshot(
    monkeypatch,
    tmp_path,
):
    node = shutil.which("node")
    if node is None or not ELECTRON_REFRESH_CLIENT.is_file():
        pytest.skip("build Electron TypeScript first to run the cross-runtime smoke")

    config_path = tmp_path / "userConfig.json"
    refresh_token = "cross-runtime-control-token"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CAN_CHANGE_KEYS", "true")
    monkeypatch.setenv("PRESENTON_RUNTIME_SETTINGS_REFRESH_TOKEN", refresh_token)
    monkeypatch.setattr(
        "utils.user_config.read_user_config_file",
        lambda _path: json.loads(config_path.read_text(encoding="utf-8")),
    )
    config_path.write_text(
        json.dumps(
            {
                "LLM": "openai",
                "OPENAI_API_KEY": "old-provider-key",
                "OPENAI_MODEL": "old-model",
            }
        ),
        encoding="utf-8",
    )
    refresh_process_runtime_settings()

    app = FastAPI()
    app.add_middleware(RuntimeSettingsContextMiddleware)

    @app.get("/probe")
    async def probe():
        settings = resolve_runtime_settings()
        return {
            "provider": settings.get("LLM"),
            "model": settings.get("GOOGLE_MODEL"),
        }

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="critical",
            access_log=False,
            lifespan="off",
        )
    )
    server_thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    server_thread.start()

    try:
        deadline = time.monotonic() + 10
        while not server.started and server_thread.is_alive():
            if time.monotonic() >= deadline:
                pytest.fail("FastAPI refresh smoke server did not start")
            time.sleep(0.01)
        assert server_thread.is_alive()

        config_path.write_text(
            json.dumps(
                {
                    "LLM": "google",
                    "GOOGLE_API_KEY": "new-provider-key",
                    "GOOGLE_MODEL": "new-model",
                }
            ),
            encoding="utf-8",
        )
        node_environment = {
            key: os.environ[key]
            for key in (
                "PATH",
                "PATHEXT",
                "SYSTEMROOT",
                "SystemRoot",
                "TEMP",
                "TMP",
                "WINDIR",
            )
            if key in os.environ
        }
        node_environment["PRESENTON_TEST_REFRESH_TOKEN"] = refresh_token
        script = """
const [modulePath, portText] = process.argv.slice(1);
const { requestRuntimeSettingsRefresh } = require(modulePath);
requestRuntimeSettingsRefresh(Number(portText), process.env.PRESENTON_TEST_REFRESH_TOKEN)
  .catch((error) => {
    process.stderr.write(error.message);
    process.exitCode = 1;
  });
"""
        result = subprocess.run(
            [node, "-e", script, str(ELECTRON_REFRESH_CLIENT), str(port)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=node_environment,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        published = get_process_runtime_settings()
        assert published.get("LLM") == "google"
        assert published.get("GOOGLE_API_KEY") == "new-provider-key"
        assert published.get("GOOGLE_MODEL") == "new-model"

        status, payload = _get_json(port, "/probe")
        assert status == 200
        assert payload == {"provider": "google", "model": "new-model"}
        assert "new-provider-key" not in repr(payload)
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
        listener.close()

    assert not server_thread.is_alive()
