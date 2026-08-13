"""Single-request stdin/stdout worker for the authored renderer boundary.

Production launches this module inside the network-none, non-root, read-only
renderer container.  The local command exists for development and focused tests;
``AUTHORED_RENDERER_PRODUCTION_STRICT=1`` forbids that implicit local fallback.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from typing import Any, Mapping

from utils.renderer_isolation import (
    MAX_HTML_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    RendererPolicyError,
    admit_authored_html,
    admit_static_output_html,
    assert_no_provider_secrets,
)


def _read_request() -> Mapping[str, Any]:
    payload = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if not payload or len(payload) > MAX_REQUEST_BYTES:
        raise RendererPolicyError("renderer request wire limit exceeded")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RendererPolicyError("renderer request is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise RendererPolicyError("renderer request must be a JSON object")
    if set(value) != {"schema_version", "html", "timeout"}:
        raise RendererPolicyError("renderer request has an invalid shape")
    if value.get("schema_version") != "authored-render-request/v1":
        raise RendererPolicyError("renderer request schema is unsupported")
    html = value.get("html")
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise RendererPolicyError("renderer HTML limit exceeded")
    timeout = value.get("timeout")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 120:
        raise RendererPolicyError("renderer timeout must be between 1 and 120 seconds")
    return value


async def _render(request: Mapping[str, Any]) -> Mapping[str, Any]:
    # Import only after the worker environment and wire request are validated.  This
    # also prevents importing the network author/provider layer in the renderer.
    from utils import slide_capture

    source = str(request["html"])
    try:
        admitted = admit_authored_html(source)
    except RendererPolicyError:
        admitted = admit_static_output_html(source)
    png, metrics = await slide_capture._render_admitted_html_in_worker(
        admitted, timeout=int(request["timeout"])
    )
    return {
        "schema_version": "authored-render-response/v1",
        "ok": True,
        "png_base64": base64.b64encode(png).decode("ascii"),
        "metrics": metrics,
        "source_digest": admitted.source_digest,
        "asset_digests": list(admitted.asset_digests),
    }


def _write_response(value: Mapping[str, Any]) -> None:
    output = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(output) > MAX_RESPONSE_BYTES:
        output = json.dumps(
            {
                "schema_version": "authored-render-response/v1",
                "ok": False,
                "error": "renderer response limit exceeded",
            },
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()


def main() -> int:
    try:
        if os.getenv("AUTHORED_RENDERER_WORKER") != "1":
            raise RendererPolicyError("renderer worker boundary marker is missing")
        assert_no_provider_secrets(os.environ)
        request = _read_request()
        response = asyncio.run(_render(request))
        _write_response(response)
        return 0
    except Exception as exc:
        # Only the exception class and bounded policy message cross the boundary;
        # request HTML, environment values, paths, and Chrome stderr never do.
        message = str(exc).replace("\r", " ").replace("\n", " ")[:256]
        _write_response(
            {
                "schema_version": "authored-render-response/v1",
                "ok": False,
                "error_type": type(exc).__name__,
                "error": message or "authored renderer failed",
            }
        )
        return 1
    finally:
        module = sys.modules.get("utils.slide_capture")
        if module is not None:
            try:
                module._close_reusable_chrome()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
