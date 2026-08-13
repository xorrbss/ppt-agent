from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_ROOT = REPOSITORY_ROOT / "contracts" / "authored-renderer"


def _load_verifier():
    path = CONTRACT_ROOT / "verify_contract.py"
    spec = importlib.util.spec_from_file_location("authored_renderer_contract_verifier", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_renderer_contract_is_statically_hardened() -> None:
    checks = _load_verifier().verify()
    assert checks
    assert all(item["pass"] for item in checks)


def _invoke(payload: bytes, *, environment: dict[str, str] | None = None):
    return subprocess.run(
        [sys.executable, str(CONTRACT_ROOT / "invoke.py")],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )


def test_launcher_rejects_malformed_input_before_container_runtime() -> None:
    completed = _invoke(b'{"html":"safe"}\n')
    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {
        "schema_version": "authored-render-response/v1",
        "ok": False,
        "error": "invalid-request-shape",
    }
    assert completed.stderr == b""


def test_kill_switch_rejects_new_work_before_container_runtime() -> None:
    environment = {
        "AUTHORED_RENDERER_MODE": "isolated",
        "AUTHORED_RENDERER_KILL_SWITCH": "true",
        "AUTHORED_RENDERER_LEGACY_ENABLED": "false",
    }
    completed = _invoke(
        b'{"schema_version":"authored-render-request/v1","html":"<main>safe</main>","timeout":1}\n',
        environment=environment,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {
        "schema_version": "authored-render-response/v1",
        "ok": False,
        "error": "renderer-kill-switch-active",
    }
    assert completed.stderr == b""


def test_production_launcher_requires_immutable_image_and_source_revision() -> None:
    environment = {
        "AUTHORED_RENDERER_MODE": "isolated",
        "AUTHORED_RENDERER_KILL_SWITCH": "false",
        "AUTHORED_RENDERER_LEGACY_ENABLED": "false",
        "AUTHORED_RENDERER_PRODUCTION_STRICT": "true",
        "AUTHORED_RENDERER_IMAGE_REF": "presenton-authored-render-worker:mutable",
        "AUTHORED_RENDERER_SOURCE_REVISION": "unknown",
    }
    completed = _invoke(
        b'{"schema_version":"authored-render-request/v1","html":"<main>safe</main>","timeout":1}\n',
        environment=environment,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["error"] == "renderer-image-attestation-invalid"


def test_launcher_refuses_legacy_mode_without_downgrade() -> None:
    environment = {
        "AUTHORED_RENDERER_MODE": "legacy",
        "AUTHORED_RENDERER_KILL_SWITCH": "false",
        "AUTHORED_RENDERER_LEGACY_ENABLED": "true",
    }
    completed = _invoke(
        b'{"schema_version":"authored-render-request/v1","html":"<main>safe</main>","timeout":1}\n',
        environment=environment,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {
        "schema_version": "authored-render-response/v1",
        "ok": False,
        "error": "renderer-isolation-required",
    }
    assert completed.stderr == b""
