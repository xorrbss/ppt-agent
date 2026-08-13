"""Fail-closed client for the externally isolated renderer worker."""
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import signal
import subprocess
from typing import Any, Mapping

from utils.renderer_isolation import (
    MAX_RESPONSE_BYTES, RendererPolicyError,
    build_worker_environment, encode_request, worker_command,
)
from utils.slide_capture import _admit_render_source, _cache_browser_metrics

class RendererIsolationError(RuntimeError):
    """The isolated authored render boundary failed without a publishable artifact."""


def _decode_worker_response(payload: bytes) -> tuple[bytes, Mapping[str, Any] | None]:
    if not payload or len(payload) > MAX_RESPONSE_BYTES:
        raise RendererIsolationError("authored renderer response limit exceeded")
    try:
        response = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RendererIsolationError("authored renderer returned invalid JSON") from exc
    if not isinstance(response, Mapping):
        raise RendererIsolationError("authored renderer returned a non-object response")
    if response.get("schema_version") != "authored-render-response/v1":
        raise RendererIsolationError("authored renderer response schema is unsupported")
    ok = response.get("ok")
    if not isinstance(ok, bool):
        raise RendererIsolationError("authored renderer response is missing status")
    if not ok:
        allowed = {"schema_version", "ok", "error", "error_type"}
        if not set(response).issubset(allowed):
            raise RendererIsolationError("authored renderer failure response has invalid fields")
        message = response.get("error")
        safe_message = (
            str(message).replace("\r", " ").replace("\n", " ")[:256]
            if isinstance(message, str)
            else "isolated authored renderer failed"
        )
        if response.get("error_type") in {
            "RendererPolicyError",
            "RendererDisabledError",
        }:
            raise RendererPolicyError(safe_message)
        raise RendererIsolationError(safe_message)
    allowed = {
        "schema_version",
        "ok",
        "png_base64",
        "metrics",
        "source_digest",
        "asset_digests",
    }
    if set(response) != allowed:
        raise RendererIsolationError("authored renderer success response has invalid fields")
    encoded_png = response.get("png_base64")
    if not isinstance(encoded_png, str):
        raise RendererIsolationError("authored renderer response has no PNG")
    try:
        png = base64.b64decode(encoded_png, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RendererIsolationError("authored renderer PNG is not valid base64") from exc
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RendererIsolationError("authored renderer returned a non-PNG artifact")
    if len(png) > MAX_RESPONSE_BYTES:
        raise RendererIsolationError("authored renderer PNG exceeds response limit")
    metrics = response.get("metrics")
    if metrics is not None and not isinstance(metrics, Mapping):
        raise RendererIsolationError("authored renderer metrics are invalid")
    digest = response.get("source_digest")
    assets = response.get("asset_digests")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RendererIsolationError("authored renderer source digest is invalid")
    if not isinstance(assets, list) or any(
        not isinstance(item, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", item)
        for item in assets
    ):
        raise RendererIsolationError("authored renderer asset digests are invalid")
    return png, metrics


async def _render_html_in_isolated_worker(  # noqa: ASYNC109
    html: str, timeout: int
) -> tuple[bytes, Mapping[str, Any] | None]:
    admitted = _admit_render_source(html)
    command = worker_command()
    request = encode_request(html, timeout)
    environment = build_worker_environment()
    kwargs: dict[str, Any] = {
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.DEVNULL,
        "env": environment,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    process = await asyncio.create_subprocess_exec(*command, **kwargs)

    async def terminate_process_tree() -> None:
        if process.returncode is None:
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), timeout=12)
                    except asyncio.TimeoutError:
                        os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except (ProcessLookupError, PermissionError):
                pass
        await process.wait()

    async def exchange() -> bytes:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(request)
        await process.stdin.drain()
        process.stdin.close()
        try:
            await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass
        try:
            output = await process.stdout.readexactly(MAX_RESPONSE_BYTES + 1)
        except asyncio.IncompleteReadError as incomplete:
            output = incomplete.partial
        if len(output) > MAX_RESPONSE_BYTES:
            await terminate_process_tree()
        else:
            await process.wait()
        return output

    try:
        # The production launcher owns a timeout+15s Docker cleanup window.  The
        # author process must outlive that window; otherwise killing the launcher
        # first can orphan its uniquely named one-shot container.
        stdout = await asyncio.wait_for(
            exchange(), timeout=max(25, timeout + 25)
        )
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        await terminate_process_tree()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise RendererIsolationError("authored renderer worker timed out") from exc
    if len(stdout) > MAX_RESPONSE_BYTES:
        raise RendererIsolationError("authored renderer response limit exceeded")
    png, metrics = _decode_worker_response(stdout)
    expected_digest = "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest()
    # Admission is intentionally repeated in the worker.  The parent digest check
    # binds the returned artifact to the exact request across an external launcher.
    response = json.loads(stdout)
    if admitted.source_digest != expected_digest or response.get("source_digest") != expected_digest:
        raise RendererIsolationError("authored renderer response identity mismatch")
    if process.returncode != 0:
        raise RendererIsolationError("authored renderer worker failed")
    if metrics is not None:
        _cache_browser_metrics(html, metrics)
    return png, metrics
