#!/usr/bin/env python3
"""Bounded, local-only probes for the current FastAPI authored renderer.

The probe never contacts an external address.  It uses a temporary file canary
and an ephemeral 127.0.0.1 HTTP sink, then invokes the production one-shot
renderer because that renderer is the file:// fallback used by the current
authored path.  The canary payload itself is never written to the result.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import pathlib
import secrets
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


HERE = pathlib.Path(__file__).resolve()
REPO_ROOT = HERE.parents[5]
FASTAPI_ROOT = REPO_ROOT / "servers" / "fastapi"
sys.path.insert(0, str(FASTAPI_ROOT))

# Force the file-backed production fallback and prevent result-cache reuse.
os.environ.setdefault("AUTHORED_CHROME_REUSE", "0")
os.environ.setdefault("AUTHORED_RENDER_CACHE_SIZE", "0")

from PIL import Image  # noqa: E402
from utils import slide_capture  # noqa: E402


PNG_SIZE = 64
FILE_CANARY_RGB = (217, 35, 172)
HTTP_CANARY_RGB = (22, 163, 74)
ABSENT_IPC_RGB = (22, 163, 74)
PRESENT_IPC_RGB = (220, 38, 38)


def png_bytes(rgb: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (PNG_SIZE, PNG_SIZE), rgb)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def sampled_rgb(rendered: bytes, x: int = 32, y: int = 32) -> tuple[int, int, int]:
    return Image.open(io.BytesIO(rendered)).convert("RGB").getpixel((x, y))


def close_color(
    observed: tuple[int, int, int], expected: tuple[int, int, int], tolerance: int = 8
) -> bool:
    return all(abs(left - right) <= tolerance for left, right in zip(observed, expected))


class LocalSink:
    def __init__(self, response_png: bytes) -> None:
        self._response_png = response_png
        self._requests: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
                owner._requests.append(
                    {
                        "method": "GET",
                        "path_sha256": hashlib.sha256(
                            self.path.encode("utf-8", "replace")
                        ).hexdigest(),
                    }
                )
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(owner._response_png)))
                self.end_headers()
                self.wfile.write(owner._response_png)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/renderer-canary.png"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return list(self._requests)

    def __enter__(self) -> "LocalSink":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def image_document(source: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:1280px;height:720px;background:#fff;overflow:hidden}}
#canary{{position:fixed;left:0;top:0;width:{PNG_SIZE}px;height:{PNG_SIZE}px}}
</style></head><body><img id="canary" src="{source}" alt=""></body></html>"""


def ipc_surface_document() -> str:
    absent = "rgb(22, 163, 74)"
    present = "rgb(220, 38, 38)"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:1280px;height:720px;background:#fff}}
#marker{{width:{PNG_SIZE}px;height:{PNG_SIZE}px;background:{absent}}}
</style></head><body><div id="marker"></div><script>
if (window.electron || window.ipcRenderer || window.require || window.process) {{
  document.getElementById('marker').style.background = '{present}';
}}
</script></body></html>"""


async def render(html: str, timeout: int) -> bytes:
    return await slide_capture._render_html_to_png_subprocess(html, timeout=timeout)


async def measure(timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    secret = f"AUTHORED_LOCAL_SECRET_{secrets.token_hex(16)}"
    errors: list[dict[str, str]] = []
    observations: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="authored-security-canary-") as temp:
        temp_root = pathlib.Path(temp)
        host_canary = temp_root / "host-read-canary.png"
        host_canary.write_bytes(png_bytes(FILE_CANARY_RGB))

        try:
            rendered = await render(image_document(host_canary.as_uri()), timeout)
            pixel = sampled_rgb(rendered)
            observations["host_file_read"] = {
                "attempts": 1,
                "successful_reads": int(close_color(pixel, FILE_CANARY_RGB)),
                "evidence": "temporary PNG outside renderer-created work directory",
                "sample_rgb": list(pixel),
            }
        except Exception as error:  # fail closed, but retain bounded evidence
            errors.append({"probe": "host_file_read", "error": type(error).__name__})
            observations["host_file_read"] = {
                "attempts": 1,
                "successful_reads": None,
                "status": "inconclusive",
            }

        with LocalSink(png_bytes(HTTP_CANARY_RGB)) as sink:
            try:
                rendered = await render(image_document(sink.url), timeout)
                pixel = sampled_rgb(rendered)
                observations["loopback_egress"] = {
                    "attempts": len(sink.requests),
                    "successful_responses": int(close_color(pixel, HTTP_CANARY_RGB)),
                    "sink_requests": sink.requests,
                    "sample_rgb": list(pixel),
                }
            except Exception as error:
                errors.append({"probe": "loopback_egress", "error": type(error).__name__})
                observations["loopback_egress"] = {
                    "attempts": len(sink.requests),
                    "successful_responses": None,
                    "status": "inconclusive",
                }

        try:
            rendered = await render(ipc_surface_document(), timeout)
            pixel = sampled_rgb(rendered)
            observations["renderer_privileged_surface"] = {
                "ipc_or_node_surface_exposed": close_color(pixel, PRESENT_IPC_RGB),
                "surface_absent": close_color(pixel, ABSENT_IPC_RGB),
                "sample_rgb": list(pixel),
                "scope": "standalone FastAPI Chrome only; Electron not executed",
            }
        except Exception as error:
            errors.append(
                {"probe": "renderer_privileged_surface", "error": type(error).__name__}
            )
            observations["renderer_privileged_surface"] = {
                "ipc_or_node_surface_exposed": None,
                "status": "inconclusive",
            }

    serialized = json.dumps({"errors": errors, "observations": observations}, sort_keys=True)
    return {
        "schema_version": "authored-renderer-baseline-v1",
        "subject": "servers/fastapi/utils/slide_capture.py::_render_html_to_png_subprocess",
        "network_policy": "local-only; no external DNS/HTTP or metadata address used",
        "chrome": slide_capture.find_chrome(),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "observations": observations,
        "secret_log": {
            "marker_present_in_result": secret in serialized,
            "raw_marker_persisted": False,
            "scope": "probe result only; OS-wide/browser-internal logs not inspected",
        },
        "process_escape": {
            "successful_children": None,
            "status": "inconclusive",
            "reason": "host-safe run does not attempt process creation or OS syscall tracing",
        },
        "external_egress": {
            "attempts": None,
            "status": "inconclusive",
            "reason": "actual external network and DNS are prohibited by test policy",
        },
        "metadata_egress": {
            "attempts": None,
            "status": "inconclusive",
            "reason": "metadata-shaped targets are static-policy-only and never requested",
        },
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--timeout", type=int, default=25)
    args = parser.parse_args()
    if args.timeout < 5 or args.timeout > 30:
        parser.error("--timeout must be between 5 and 30 seconds")
    result = asyncio.run(measure(args.timeout))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
