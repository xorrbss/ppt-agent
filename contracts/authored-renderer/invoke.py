#!/usr/bin/env python3
"""Invoke the authored render worker through the fixed zero-egress OCI contract.

Two request protocols share one stdin, told apart from the first eight bytes
with no handshake and no argv change (the production worker command is pinned
to exactly ``python invoke.py``, so a subcommand could not be passed anyway):

* **single-shot** -- one bare JSON line, one ``docker compose run --rm``
  container, one response, exit. Unchanged.
* **framed serve** -- an 8-byte big-endian length then that many bytes of JSON,
  per request and per response. One container stays up for the whole stream and
  frames are proxied to it in lock step, so a deck pays one container and one
  Chromium cold start instead of one per slide. ``serve`` may also be passed as
  argv for manual use.

Serve mode changes nothing about containment: the same image, the same compose
service, ``network_mode: none``, ``read_only`` root filesystem, non-root uid,
dropped capabilities, seccomp profile, and the tmpfs that holds the browser
profile. What it does change is the *lifetime* of that tmpfs -- per container
rather than per render -- so the worker inside compensates by giving every
request a fresh browser context and target and disposing them before it
responds, and the parent routes only one deck scope's renders to one container.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import signal
import stat
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, BinaryIO

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
FRAME_HEADER_BYTES = 8


class RequestError(ValueError):
    pass


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_request(prefix: bytes = b"") -> tuple[bytes, int]:
    raw = prefix + sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1 - len(prefix))
    if len(raw) > MAX_REQUEST_BYTES:
        raise RequestError("request-too-large")
    if not raw.endswith(b"\n") or len(raw.splitlines()) != 1:
        raise RequestError("request-must-be-one-json-line")
    try:
        request: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestError("invalid-request-json") from exc
    required = {"schema_version", "html", "timeout"}
    optional = {"width_px", "height_px", "preserved_raster_svg", "system_furniture",
                "font_manifest", "font_manifest_ref", "canonical_svg", "image_bindings"}
    if not isinstance(request, dict) or not required <= set(request) or set(request) - required - optional:
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
        request,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(normalized) > MAX_REQUEST_BYTES:
        raise RequestError("request-too-large")
    return normalized, timeout


def _header_is_frame(header: bytes) -> bool:
    if len(header) != FRAME_HEADER_BYTES:
        return False
    return 0 < int.from_bytes(header, "big", signed=False) <= MAX_REQUEST_BYTES


def _read_frame(stream: BinaryIO, limit: int, header: bytes | None = None) -> bytes | None:
    """Read one length-prefixed frame; ``None`` when the peer closed cleanly."""

    if header is None:
        header = stream.read(FRAME_HEADER_BYTES)
    if not header:
        return None
    if len(header) != FRAME_HEADER_BYTES:
        raise RequestError("frame-header-truncated")
    length = int.from_bytes(header, "big", signed=False)
    if not 0 < length <= limit:
        raise RequestError("frame-length-out-of-range")
    payload = stream.read(length)
    if len(payload) != length:
        raise RequestError("frame-payload-truncated")
    return payload


def _encode_frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(FRAME_HEADER_BYTES, "big", signed=False) + payload


def _normalize_framed_request(payload: bytes) -> tuple[bytes, int]:
    """Validate one framed request before any container sees it."""

    try:
        request: Any = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestError("invalid-request-json") from exc
    if not isinstance(request, dict):
        raise RequestError("invalid-request-shape")
    fields = set(request)
    required = {"schema_version", "html", "timeout"}
    if not required <= fields or not fields <= required | {"max_requests"}:
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
    normalized: dict[str, Any] = {
        "schema_version": REQUEST_SCHEMA,
        "html": request["html"],
        "timeout": timeout,
    }
    if "max_requests" in fields:
        ceiling = request["max_requests"]
        if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 1:
            raise RequestError("max-requests-must-be-a-positive-integer")
        normalized["max_requests"] = ceiling
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) + FRAME_HEADER_BYTES > MAX_REQUEST_BYTES:
        raise RequestError("request-too-large")
    return _encode_frame(encoded), timeout


def _emit_frame_error(code: str) -> int:
    sys.stdout.buffer.write(_encode_frame(json.dumps(
        {"schema_version": RESPONSE_SCHEMA, "ok": False, "error": code},
        separators=(",", ":"),
    ).encode("utf-8")))
    sys.stdout.buffer.flush()
    return 1


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


def _policy_error() -> str | None:
    """The fail-closed checks every protocol runs before any container starts."""

    if _is_true(os.getenv("AUTHORED_RENDERER_KILL_SWITCH")):
        return "renderer-kill-switch-active"
    if os.getenv("AUTHORED_RENDERER_MODE", "isolated").strip().lower() != "isolated":
        return "renderer-isolation-required"
    if _is_true(os.getenv("AUTHORED_RENDERER_LEGACY_ENABLED")):
        return "legacy-renderer-not-allowed"
    if not _production_image_is_valid():
        return "renderer-image-attestation-invalid"
    return None


def _prepare_container(container_name: str, assets_root: Path | None = None) -> tuple[str, dict[str, str], list[str]] | str:
    """Resolve docker, verify the image attestation, and build the run command."""

    docker = shutil.which("docker")
    if docker is None or not COMPOSE_FILE.is_file():
        return "renderer-isolation-unavailable"
    docker_environment = _docker_environment()
    if _requires_image_attestation() and not _verify_image_attestation(
        docker, docker_environment
    ):
        return "renderer-image-attestation-invalid"
    if hasattr(signal, "SIGTERM"):
        def terminate_handler(_signum: int, _frame: Any) -> None:
            _remove_container(docker, container_name, docker_environment)
            raise SystemExit(1)

        signal.signal(signal.SIGTERM, terminate_handler)
    command = [
        docker, "compose", "--ansi", "never", "-f", str(COMPOSE_FILE),
        "run", "--rm", "--no-deps", "-T", "--name", container_name,
    ]
    if assets_root is not None:
        manifest = assets_root / "font-manifest.json"
        if "," in str(manifest):
            return "renderer-font-asset-path-invalid"
        command.extend(["--mount", f"type=bind,source={manifest},target=/tmp/font-manifest.json,readonly"])
    command.append(SERVICE)
    if assets_root is not None:
        command.extend(["--request-assets-root", "/tmp"])
    return docker, docker_environment, command


def _serve_frames(first_header: bytes | None) -> int:
    """Proxy framed requests to one long-lived container, in lock step."""

    policy = _policy_error()
    if policy is not None:
        return _emit_frame_error(policy)
    container_name = f"authored-render-worker-{uuid.uuid4().hex}"
    prepared = _prepare_container(container_name)
    if isinstance(prepared, str):
        return _emit_frame_error(prepared)
    docker, docker_environment, command = prepared
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, shell=False, env=docker_environment,
        )
    except OSError:
        return _emit_error_and_remove(docker, container_name, docker_environment)
    header = first_header
    try:
        while True:
            payload = _read_frame(sys.stdin.buffer, MAX_REQUEST_BYTES, header)
            header = None
            if payload is None:
                return 0
            frame, render_timeout = _normalize_framed_request(payload)
            # The container owns no clock of its own; a request that does not
            # answer inside the same window the single-shot path allows kills
            # the container rather than hanging this launcher.
            watchdog = threading.Timer(
                render_timeout + STARTUP_GRACE_SECONDS,
                _remove_container, (docker, container_name, docker_environment),
            )
            watchdog.daemon = True
            watchdog.start()
            try:
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write(frame)
                process.stdin.flush()
                answer = _read_frame(process.stdout, MAX_RESPONSE_BYTES)
            finally:
                watchdog.cancel()
            if answer is None:
                return _emit_frame_error("renderer-isolation-failed")
            response = _validate_response(answer)
            sys.stdout.buffer.write(_encode_frame(json.dumps(
                response, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")))
            sys.stdout.buffer.flush()
            if not response["ok"]:
                # The worker retires its Chromium after a failure; so does this.
                return 1
    except (RequestError, OSError, BrokenPipeError):
        return _emit_frame_error("renderer-isolation-failed")
    finally:
        _shutdown_container(process, docker, container_name, docker_environment)


def _emit_error_and_remove(
    docker: str, container_name: str, environment: dict[str, str]
) -> int:
    _remove_container(docker, container_name, environment)
    return _emit_frame_error("renderer-isolation-failed")


def _shutdown_container(
    process: "subprocess.Popen[bytes]",
    docker: str,
    container_name: str,
    environment: dict[str, str],
) -> None:
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass
    try:
        process.wait(timeout=STARTUP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        _remove_container(docker, container_name, environment)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["serve"]:
        return _serve_frames(None)
    assets_root = None
    if arguments:
        if len(arguments) != 2 or arguments[0] != "--request-assets-root":
            return _emit_error("unsupported-argument")
        try:
            candidate = Path(arguments[1])
            if not candidate.is_absolute() or candidate.is_symlink():
                return _emit_error("renderer-font-asset-path-invalid")
            assets_root = candidate.resolve(strict=True)
            asset = assets_root / "font-manifest.json"
            info = asset.lstat()
            if not assets_root.is_dir() or not stat.S_ISREG(info.st_mode) or info.st_size > 90 * 1024 * 1024:
                return _emit_error("renderer-font-asset-path-invalid")
        except OSError:
            return _emit_error("renderer-font-asset-path-invalid")
    prefix = sys.stdin.buffer.read(FRAME_HEADER_BYTES)
    if _header_is_frame(prefix):
        if assets_root is not None:
            return _emit_error("renderer-font-assets-require-single-shot")
        return _serve_frames(prefix)
    try:
        request, render_timeout = _read_request(prefix)
    except RequestError as exc:
        return _emit_error(str(exc))

    try:
        parsed_request = json.loads(request)
        if (parsed_request.get("font_manifest_ref") is None) != (assets_root is None):
            return _emit_error("renderer-font-asset-binding-invalid")
    except (ValueError, UnicodeDecodeError):
        return _emit_error("invalid-request-json")
    policy = _policy_error()
    if policy is not None:
        return _emit_error(policy)

    container_name = f"authored-render-worker-{uuid.uuid4().hex}"
    prepared = _prepare_container(container_name, assets_root)
    if isinstance(prepared, str):
        return _emit_error(prepared)
    docker, docker_environment, command = prepared
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
