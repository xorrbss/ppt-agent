"""Synchronous CDP-pipe rendering over an already admitted HTML source."""

from __future__ import annotations

import base64
import html as html_lib
import io
import json
import pathlib
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any
import urllib.parse

from utils.renderer_isolation import AdmittedHtml

SLIDE_W = 1280
SLIDE_H = 720
METRICS_ELEMENT_ID = "__codex_authored_dom_metrics"

def render_admitted_html_over_pipe(
    admitted: AdmittedHtml, timeout: int, *,
    blocked_urls: Sequence[str],
    chrome_pipe: Callable[[str, pathlib.Path], Any],
    find_chrome: Callable[[], str | None],
    instrument_html: Callable[..., tuple[str, str]],
    runtime_value: Callable[[Mapping[str, Any]], Any],
) -> tuple[bytes, Mapping[str, Any] | None]:
    from PIL import Image

    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("No chrome/chromium found for isolated slide capture")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        transport = chrome_pipe(chrome, pathlib.Path(temp_dir) / "profile")
        command_id = 0

        def command(
            method: str,
            params: Mapping[str, Any] | None = None,
            *,
            session_id: str | None = None,
        ) -> Mapping[str, Any]:
            nonlocal command_id
            command_id += 1
            return transport.command(
                command_id, method, params, session_id=session_id
            )

        try:
            target_id = str(
                command("Target.createTarget", {"url": "data:,"}).get("targetId")
                or ""
            )
            attached = command(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            session_id = str(attached.get("sessionId") or "")
            if not session_id:
                raise RuntimeError("Chrome DevTools pipe session is missing")

            def page_command(
                method: str, params: Mapping[str, Any] | None = None
            ) -> Mapping[str, Any]:
                return command(method, params, session_id=session_id)

            page_command("Page.enable")
            page_command("Runtime.enable")
            page_command("Network.enable")
            page_command(
                "Network.setBlockedURLs", {"urls": list(blocked_urls)}
            )
            page_command("Page.setBypassCSP", {"enabled": False})
            page_command(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": SLIDE_W,
                    "height": SLIDE_H,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            frame_tree = page_command("Page.getFrameTree")
            tree = frame_tree.get("frameTree", {})
            frame = tree.get("frame", {}) if isinstance(tree, Mapping) else {}
            frame_id = str(frame.get("id") or "") if isinstance(frame, Mapping) else ""
            if not frame_id:
                raise RuntimeError("Chrome DevTools pipe frame id is missing")
            instrumented, _key = instrument_html(
                admitted.html,
                script_nonce=admitted.nonce,
                issue_capability=False,
            )
            page_command(
                "Page.setDocumentContent", {"frameId": frame_id, "html": instrumented}
            )
            metric_params = {
                "expression": f"""(async () => {{
                  if (document.fonts && document.fonts.ready) await document.fonts.ready;
                  await Promise.all(Array.from(document.images).map(image => {{
                    if (image.complete) return image.decode ? image.decode().catch(() => undefined) : Promise.resolve();
                    return new Promise(resolve => {{
                      image.addEventListener('load', resolve, {{once:true}});
                      image.addEventListener('error', resolve, {{once:true}});
                    }});
                  }}));
                  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                  const marker = document.getElementById('{METRICS_ELEMENT_ID}');
                  return marker ? marker.getAttribute('content') : null;
                }})()""",
                "awaitPromise": True,
                "returnByValue": True,
            }
            metric_result: Mapping[str, Any] | None = None
            for attempt in range(3):
                try:
                    metric_result = page_command("Runtime.evaluate", metric_params)
                    break
                except RuntimeError as error:
                    if "Execution context was destroyed" not in str(error) or attempt == 2:
                        raise
                    time.sleep(0.05)
            if metric_result is None:
                raise RuntimeError("Chrome DevTools metrics evaluation did not complete")
            encoded_metrics = runtime_value(metric_result)
            metrics: Mapping[str, Any] | None = None
            if isinstance(encoded_metrics, str) and encoded_metrics:
                try:
                    parsed = json.loads(
                        urllib.parse.unquote(html_lib.unescape(encoded_metrics))
                    )
                    metrics = parsed if isinstance(parsed, Mapping) else None
                except (TypeError, ValueError):
                    metrics = None
            shot = page_command(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
            )
            image_bytes = base64.b64decode(str(shot.get("data") or ""), validate=True)
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB").crop(
                (0, 0, SLIDE_W, SLIDE_H)
            )
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue(), metrics
        finally:
            transport.close()

