"""stdin/stdout worker for the authored renderer boundary.

Two wire protocols are served on the same stdin, told apart from the first
eight bytes without a handshake (see ``utils.renderer_frame_codec``):

* **framed** -- ``<8-byte big-endian length><JSON>`` per request and per
  response. The process keeps one Chromium alive and serves requests until
  stdin EOF, ``max_requests`` renders, or the first failure. Each response
  carries ``request_ordinal`` alongside the fields the single-shot protocol
  already returns.
* **legacy single-shot** -- one bare JSON payload terminated by EOF, one
  response, exit. Preserved byte-for-byte for the pinned launcher and for any
  caller that has not moved to frames.

Production launches this module inside the network-none, non-root, read-only
renderer container.  The local command exists for development and focused
tests; ``AUTHORED_RENDERER_PRODUCTION_STRICT=1`` forbids that implicit local
fallback. Reusing the process never reuses renderer state: every request opens
a fresh browser context and target and disposes them before responding, and a
process only ever serves one caller-chosen deck scope.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from typing import Any, Mapping

from services.authored_vnext.capability_registry import capability_metadata
from utils.renderer_frame_codec import (
    DEFAULT_MAX_REQUESTS_PER_PROCESS,
    FRAME_HEADER_BYTES,
    clamp_max_requests,
    encode_frame,
    header_is_frame,
    read_frame_sync,
)
from utils.renderer_isolation import (
    MAX_HTML_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    RendererPolicyError,
    admit_authored_html,
    admit_static_output_html,
    assert_no_provider_secrets,
)

RESPONSE_SCHEMA = "authored-render-response/v1"


def _validate_request(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RendererPolicyError("renderer request must be a JSON object")
    fields = set(value)
    if not {"schema_version", "html", "timeout"} <= fields or not fields <= {
        "schema_version",
        "html",
        "timeout",
        "max_requests",
        "width_px",
        "height_px",
    }:
        raise RendererPolicyError("renderer request has an invalid shape")
    if value.get("schema_version") != "authored-render-request/v1":
        raise RendererPolicyError("renderer request schema is unsupported")
    html = value.get("html")
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise RendererPolicyError("renderer HTML limit exceeded")
    timeout = value.get("timeout")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 120:
        raise RendererPolicyError("renderer timeout must be between 1 and 120 seconds")
    if "max_requests" in fields:
        clamp_max_requests(value["max_requests"])
    for field_name, default in (("width_px", 1280), ("height_px", 720)):
        dimension = value.get(field_name, default)
        if (
            not isinstance(dimension, int)
            or isinstance(dimension, bool)
            or not 1 <= dimension <= 4096
        ):
            raise RendererPolicyError("renderer canvas dimensions are invalid")
    return value


def _parse_request(payload: bytes) -> Mapping[str, Any]:
    if not payload or len(payload) > MAX_REQUEST_BYTES:
        raise RendererPolicyError("renderer request wire limit exceeded")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RendererPolicyError("renderer request is not valid JSON") from exc
    return _validate_request(value)


def _read_legacy_request(prefix: bytes) -> Mapping[str, Any]:
    payload = prefix + sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1 - len(prefix))
    request = _parse_request(payload)
    if "max_requests" in request:
        raise RendererPolicyError("renderer request has an invalid shape")
    return request


async def _render(
    request: Mapping[str, Any], session: Any = None, *, ordinal: int | None = None
) -> Mapping[str, Any]:
    # Import only after the worker environment and wire request are validated.  This
    # also prevents importing the network author/provider layer in the renderer.
    from utils import slide_capture

    source = str(request["html"])
    try:
        admitted = admit_authored_html(source)
    except RendererPolicyError:
        admitted = admit_static_output_html(source)
    timeout = int(request["timeout"])
    width_px = int(request.get("width_px", 1280))
    height_px = int(request.get("height_px", 720))
    if session is None:
        png, metrics = await slide_capture._render_admitted_html_in_worker(
            admitted, timeout=timeout, width_px=width_px, height_px=height_px
        )
    else:
        png, metrics = session.render(
            admitted, timeout, width_px=width_px, height_px=height_px
        )
    metrics = dict(metrics)
    metrics["capabilityRegistry"] = capability_metadata(consumer="chromium")
    response: dict[str, Any] = {
        "schema_version": RESPONSE_SCHEMA,
        "ok": True,
        "png_base64": base64.b64encode(png).decode("ascii"),
        "metrics": metrics,
        "source_digest": admitted.source_digest,
        "asset_digests": list(admitted.asset_digests),
    }
    if ordinal is not None:
        response["request_ordinal"] = ordinal
    return response


def _encode_response(value: Mapping[str, Any], *, framed: bool) -> bytes:
    body = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(body) + (FRAME_HEADER_BYTES if framed else 1) > MAX_RESPONSE_BYTES:
        replacement: dict[str, Any] = {
            "schema_version": RESPONSE_SCHEMA,
            "ok": False,
            "error": "renderer response limit exceeded",
        }
        if "request_ordinal" in value:
            replacement["request_ordinal"] = value["request_ordinal"]
        body = json.dumps(replacement, separators=(",", ":")).encode("utf-8")
    if framed:
        return encode_frame(body, limit=MAX_RESPONSE_BYTES - FRAME_HEADER_BYTES)
    return body + b"\n"


def _write_response(value: Mapping[str, Any], *, framed: bool = False) -> None:
    sys.stdout.buffer.write(_encode_response(value, framed=framed))
    sys.stdout.buffer.flush()


def _failure_response(exc: BaseException, ordinal: int | None) -> dict[str, Any]:
    # Only the exception class and bounded policy message cross the boundary;
    # request HTML, environment values, paths, and Chrome stderr never do.
    message = str(exc).replace("\r", " ").replace("\n", " ")[:256]
    response: dict[str, Any] = {
        "schema_version": RESPONSE_SCHEMA,
        "ok": False,
        "error_type": type(exc).__name__,
        "error": message or "authored renderer failed",
    }
    if ordinal is not None:
        response["request_ordinal"] = ordinal
    return response


def _serve_framed(first_header: bytes) -> int:
    """Serve framed requests over one Chromium until EOF, the ceiling, or a failure."""

    from utils.authored_renderer_session import RendererChromiumSession

    session = RendererChromiumSession()
    max_requests = DEFAULT_MAX_REQUESTS_PER_PROCESS
    ordinal = 0
    header: bytes | None = first_header
    try:
        while ordinal < max_requests:
            payload = read_frame_sync(
                sys.stdin.buffer.read, limit=MAX_REQUEST_BYTES, header=header
            )
            header = None
            if payload is None:
                return 0
            ordinal += 1
            request = _parse_request(payload)
            if ordinal == 1 and "max_requests" in request:
                max_requests = clamp_max_requests(request["max_requests"])
            response = asyncio.run(_render(request, session, ordinal=ordinal))
            _write_response(response, framed=True)
        return 0
    except Exception as exc:
        # A failed exchange leaves no guarantee that this Chromium is still
        # usable, so the process stops after reporting it rather than serving
        # the next slide from a possibly poisoned browser.  The parent retires
        # the session on the same signal and opens a fresh worker.
        _write_response(_failure_response(exc, ordinal or None), framed=True)
        return 1
    finally:
        try:
            session.close()
        except Exception:
            pass


def main() -> int:
    framed = False
    try:
        if os.getenv("AUTHORED_RENDERER_WORKER") != "1":
            raise RendererPolicyError("renderer worker boundary marker is missing")
        assert_no_provider_secrets(os.environ)
        header = sys.stdin.buffer.read(FRAME_HEADER_BYTES)
        if header_is_frame(header, limit=MAX_REQUEST_BYTES):
            framed = True
            return _serve_framed(header)
        request = _read_legacy_request(header)
        _write_response(asyncio.run(_render(request)))
        return 0
    except Exception as exc:
        _write_response(_failure_response(exc, None), framed=framed)
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
