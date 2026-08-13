#!/usr/bin/env python3
"""Invoke the authored render worker through the fixed zero-egress OCI contract."""

from __future__ import annotations

import json
import hashlib
import os
import re
import signal
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

MAX_HTML_BYTES = 2 * 1_048_576
# JSON escaping can expand an otherwise valid 2 MiB HTML string by up to 6x.
MAX_REQUEST_BYTES = 12 * 1_048_576 + 4096
MAX_RESPONSE_BYTES = 32 * 1_048_576
MAX_TIMEOUT_SECONDS = 120.0
STARTUP_GRACE_SECONDS = 15.0
COMPOSE_FILE = Path(__file__).with_name("compose.authored-renderer.json")
SERVICE = "authored-render-worker"
REQUEST_SCHEMA = "authored-render-request/v1"
RESPONSE_SCHEMA = "authored-render-response/v1"
IMMUTABLE_IMAGE = re.compile(
    r"^(?:sha256:[0-9a-f]{64}|[^\s@]+@sha256:[0-9a-f]{64})$"
)
SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class RequestError(ValueError):
    pass


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_request() -> tuple[bytes, int]:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise RequestError("request-too-large")
    if not raw.endswith(b"\n") or len(raw.splitlines()) != 1:
        raise RequestError("request-must-be-one-json-line")
    try:
        request: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestError("invalid-request-json") from exc
    if not isinstance(request, dict) or set(request) != {"schema_version", "html", "timeout"}:
        raise RequestError("invalid-request-shape")
    if request["schema_version"] != REQUEST_SCHEMA:
        raise RequestError("unsupported-request-schema")
    if not isinstance(request["html"], str):
        raise RequestError("html-must-be-string")
    if len(request["html"].encode("utf-8")) > MAX_HTML_BYTES:
        raise RequestError("html-too-large")
    timeout = request["timeout"]
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise RequestError("timeout-must-be-integer")
    if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise RequestError("timeout-out-of-range")
    normalized = json.dumps(
        {"schema_version": REQUEST_SCHEMA, "html": request["html"], "timeout": timeout},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(normalized) > MAX_REQUEST_BYTES:
        raise RequestError("request-too-large")
    return normalized, timeout


def _emit_error(code: str) -> int:
    sys.stdout.write(json.dumps(
        {"schema_version": RESPONSE_SCHEMA, "ok": False, "error": code},
        separators=(",", ":"),
    ) + "\n")
    return 1


def _validate_response(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RequestError("response-too-large")
    try:
        response = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestError("invalid-worker-response") from exc
    if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
        raise RequestError("invalid-worker-response")
    if response.get("schema_version") != RESPONSE_SCHEMA:
        raise RequestError("invalid-worker-response")
    if response["ok"]:
        if not isinstance(response.get("png_base64"), str):
            raise RequestError("invalid-worker-response")
    elif not isinstance(response.get("error"), str):
        raise RequestError("invalid-worker-response")
    return response


def _docker_environment() -> dict[str, str]:
    allowed = {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "HOME",
        "USERPROFILE", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
        "AUTHORED_RENDERER_IMAGE_REF", "AUTHORED_RENDERER_SOURCE_REVISION",
        "AUTHORED_RENDERER_SOURCE_TREE_DIGEST", "AUTHORED_RENDERER_CONTRACT_DIGEST",
        "AUTHORED_RENDERER_DEPLOYMENT_ATTESTATION",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _production_image_is_valid() -> bool:
    strict = (
        os.getenv("TEMPLATE_V2_DEPLOYMENT_TIER", "").strip().lower() == "production"
        or _is_true(os.getenv("AUTHORED_RENDERER_PRODUCTION"))
        or _is_true(os.getenv("AUTHORED_RENDERER_PRODUCTION_STRICT"))
    )
    if not strict:
        return True
    return bool(
        IMMUTABLE_IMAGE.fullmatch(os.getenv("AUTHORED_RENDERER_IMAGE_REF", "").strip())
        and SOURCE_REVISION.fullmatch(
            os.getenv("AUTHORED_RENDERER_SOURCE_REVISION", "").strip()
        )
        and SHA256.fullmatch(os.getenv("AUTHORED_RENDERER_SOURCE_TREE_DIGEST", "").strip())
        and SHA256.fullmatch(os.getenv("AUTHORED_RENDERER_CONTRACT_DIGEST", "").strip())
        and SHA256.fullmatch(os.getenv("AUTHORED_RENDERER_DEPLOYMENT_ATTESTATION", "").strip())
    )


def _requires_image_attestation() -> bool:
    return bool(
        os.getenv("TEMPLATE_V2_DEPLOYMENT_TIER", "").strip().lower() == "production"
        or _is_true(os.getenv("AUTHORED_RENDERER_PRODUCTION"))
        or _is_true(os.getenv("AUTHORED_RENDERER_PRODUCTION_STRICT"))
        or any(os.getenv(name) for name in (
            "AUTHORED_RENDERER_SOURCE_TREE_DIGEST",
            "AUTHORED_RENDERER_CONTRACT_DIGEST",
            "AUTHORED_RENDERER_DEPLOYMENT_ATTESTATION",
        ))
    )


def _verify_image_attestation(docker: str, environment: dict[str, str]) -> bool:
    image_ref = environment.get("AUTHORED_RENDERER_IMAGE_REF", "")
    try:
        completed = subprocess.run(
            [docker, "image", "inspect", image_ref], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
            timeout=15, shell=False, env=environment,
        )
        values = json.loads(completed.stdout)
        if completed.returncode != 0 or not isinstance(values, list) or len(values) != 1:
            return False
        image = values[0]
        image_id = str(image.get("Id") or "")
        labels = (image.get("Config") or {}).get("Labels") or {}
        revision = environment["AUTHORED_RENDERER_SOURCE_REVISION"]
        source_tree = environment["AUTHORED_RENDERER_SOURCE_TREE_DIGEST"]
        contract = environment["AUTHORED_RENDERER_CONTRACT_DIGEST"]
        payload = json.dumps({
            "contract_digest": contract, "image_id": image_id,
            "source_revision": revision, "source_tree_digest": source_tree,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = "sha256:" + hashlib.sha256(payload).hexdigest()
        reference_matches = image_ref == image_id or image_ref in (image.get("RepoDigests") or [])
        return bool(
            reference_matches
            and labels.get("org.opencontainers.image.revision") == revision
            and labels.get("io.presenton.authored-renderer.source-tree-digest") == source_tree
            and labels.get("io.presenton.authored-renderer.contract-digest") == contract
            and environment["AUTHORED_RENDERER_DEPLOYMENT_ATTESTATION"] == expected
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        return False


def _remove_container(
    docker: str, container_name: str, environment: dict[str, str]
) -> None:
    try:
        subprocess.run(
            [docker, "rm", "--force", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
            shell=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Cleanup failure never authorizes a fallback or changes the error into success.
        pass


def main() -> int:
    try:
        request, render_timeout = _read_request()
    except RequestError as exc:
        return _emit_error(str(exc))

    if _is_true(os.getenv("AUTHORED_RENDERER_KILL_SWITCH")):
        return _emit_error("renderer-kill-switch-active")
    if os.getenv("AUTHORED_RENDERER_MODE", "isolated").strip().lower() != "isolated":
        return _emit_error("renderer-isolation-required")
    if _is_true(os.getenv("AUTHORED_RENDERER_LEGACY_ENABLED")):
        return _emit_error("legacy-renderer-not-allowed")
    if not _production_image_is_valid():
        return _emit_error("renderer-image-attestation-invalid")

    docker = shutil.which("docker")
    if docker is None or not COMPOSE_FILE.is_file():
        return _emit_error("renderer-isolation-unavailable")
    container_name = f"authored-render-worker-{uuid.uuid4().hex}"
    docker_environment = _docker_environment()
    if _requires_image_attestation() and not _verify_image_attestation(
        docker, docker_environment
    ):
        return _emit_error("renderer-image-attestation-invalid")
    if hasattr(signal, "SIGTERM"):
        def terminate_handler(_signum: int, _frame: Any) -> None:
            _remove_container(docker, container_name, docker_environment)
            raise SystemExit(1)

        signal.signal(signal.SIGTERM, terminate_handler)
    command = [
        docker,
        "compose",
        "--ansi",
        "never",
        "-f",
        str(COMPOSE_FILE),
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--name",
        container_name,
        SERVICE,
    ]
    try:
        completed = subprocess.run(
            command,
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=render_timeout + STARTUP_GRACE_SECONDS,
            shell=False,
            env=docker_environment,
        )
        if completed.returncode != 0:
            _remove_container(docker, container_name, docker_environment)
            return _emit_error("renderer-isolation-failed")
        response = _validate_response(completed.stdout)
    except subprocess.TimeoutExpired:
        _remove_container(docker, container_name, docker_environment)
        return _emit_error("renderer-isolation-timeout")
    except (OSError, RequestError):
        return _emit_error("renderer-isolation-failed")

    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
