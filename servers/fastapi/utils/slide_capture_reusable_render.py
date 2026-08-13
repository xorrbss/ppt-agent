"""Disposable-page rendering inside a reusable isolated Chrome process."""
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import base64
import html as html_lib
import io
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any
import urllib.parse

import aiohttp

SLIDE_W = 1280
SLIDE_H = 720

async def render_html_in_reusable_chrome(  # noqa: ASYNC109
    html: str, timeout: int, *, script_nonce: str | None = None,
    cache_html: str | None = None, blocked_urls: Sequence[str],
    cache_metrics: Callable[..., None], cdp_command: Callable[..., Any],
    count_runtime: Callable[[str], None], discard_runtime: Callable[[Any], None],
    enrich_contrast: Callable[..., Mapping[str, Any]], get_runtime: Callable[[], Any],
    instrument_html: Callable[..., tuple[str, str]], metrics_element_id: str,
    pixel_probe_document: Callable[[str], str], runtime_value: Callable[[Mapping[str, Any]], Any],
) -> tuple[bytes, Mapping[str, Any] | None]:
    """Render through a disposable CDP page in the shared isolated process."""

    from PIL import Image

    runtime = await asyncio.to_thread(get_runtime)
    timeout_config = aiohttp.ClientTimeout(total=max(5, timeout))
    target_id = ""
    page_closed = False
    try:
        async with aiohttp.ClientSession(timeout=timeout_config) as session:
            create_url = f"{runtime.base_url}/json/new?{urllib.parse.quote('about:blank', safe='')}"
            async with session.put(create_url) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"Chrome DevTools target creation failed: HTTP {response.status}"
                    )
                target = await response.json()
            target_id = str(target.get("id") or "")
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            if not target_id or not websocket_url:
                raise RuntimeError("Chrome DevTools target metadata is incomplete")
            count_runtime("page_sessions_opened")
            async with session.ws_connect(websocket_url, max_msg_size=16 * 1024 * 1024) as ws:
                command_id = 0

                async def command(
                    method: str, params: Mapping[str, Any] | None = None
                ) -> Mapping[str, Any]:
                    nonlocal command_id
                    command_id += 1
                    return await cdp_command(ws, command_id, method, params)

                await command("Page.enable")
                await command("Runtime.enable")
                await command("Network.enable")
                await command(
                    "Network.setBlockedURLs",
                    {"urls": list(blocked_urls)},
                )
                await command("Page.setBypassCSP", {"enabled": False})
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": SLIDE_W,
                        "height": SLIDE_H,
                        "deviceScaleFactor": 1,
                        "mobile": False,
                    },
                )
                frame_tree = await command("Page.getFrameTree")
                frame = frame_tree.get("frameTree", {})
                frame = frame.get("frame", {}) if isinstance(frame, Mapping) else {}
                frame_id = str(frame.get("id") or "")
                if not frame_id:
                    raise RuntimeError("Chrome DevTools frame id is missing")
                instrumented, _ = instrument_html(
                    html,
                    script_nonce=script_nonce,
                    issue_capability=False,
                )
                await command(
                    "Page.setDocumentContent", {"frameId": frame_id, "html": instrumented}
                )
                metrics_result = await command(
                    "Runtime.evaluate",
                    {
                        "expression": f"""(async () => {{
                          if (document.fonts && document.fonts.ready) await document.fonts.ready;
                          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                          const marker = document.getElementById('{metrics_element_id}');
                          return marker ? marker.getAttribute('content') : null;
                        }})()""",
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                )
                encoded_metrics = runtime_value(metrics_result)
                metrics: Mapping[str, Any] | None = None
                if isinstance(encoded_metrics, str) and encoded_metrics:
                    try:
                        decoded = urllib.parse.unquote(html_lib.unescape(encoded_metrics))
                        parsed = json.loads(decoded)
                        metrics = parsed if isinstance(parsed, Mapping) else None
                    except (TypeError, ValueError):
                        metrics = None
                screenshot = await command(
                    "Page.captureScreenshot",
                    {
                        "format": "png",
                        "fromSurface": True,
                        "captureBeyondViewport": False,
                    },
                )
                image_bytes = base64.b64decode(str(screenshot.get("data") or ""))
                if not image_bytes:
                    raise RuntimeError("Chrome DevTools screenshot is empty")
                image = Image.open(io.BytesIO(image_bytes)).convert("RGB").crop(
                    (0, 0, SLIDE_W, SLIDE_H)
                )

                if metrics is not None and any(
                    isinstance(item, Mapping)
                    and bool(item.get("requires_pixel_contrast"))
                    for item in metrics.get("elements", ())
                ):
                    await command(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": SLIDE_W,
                            "height": SLIDE_H * 2,
                            "deviceScaleFactor": 1,
                            "mobile": False,
                        },
                    )
                    await command(
                        "Page.setDocumentContent",
                        {
                            "frameId": frame_id,
                            "html": pixel_probe_document(html),
                        },
                    )
                    await command(
                        "Runtime.evaluate",
                        {
                            "expression": """(async () => {
                              const frames = Array.from(document.querySelectorAll('iframe'));
                              await Promise.all(frames.map(frame => new Promise(resolve => {
                                if (frame.contentDocument?.readyState === 'complete') resolve();
                                else frame.addEventListener('load', resolve, {once:true});
                              })));
                              await Promise.all(frames.map(frame =>
                                frame.contentDocument?.fonts?.ready || Promise.resolve()));
                              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                              return frames.length;
                            })()""",
                            "awaitPromise": True,
                            "returnByValue": True,
                        },
                    )
                    probe_shot = await command(
                        "Page.captureScreenshot",
                        {
                            "format": "png",
                            "fromSurface": True,
                            "captureBeyondViewport": False,
                        },
                    )
                    probe_bytes = base64.b64decode(
                        str(probe_shot.get("data") or "")
                    )
                    probe_image = Image.open(io.BytesIO(probe_bytes)).convert("RGB")
                    if probe_image.size[0] < SLIDE_W or probe_image.size[1] < SLIDE_H * 2:
                        raise RuntimeError(
                            f"Chrome pixel probe has unexpected size {probe_image.size}"
                        )
                    metrics = enrich_contrast(metrics, image, probe_image)
                if metrics is not None:
                    cache_metrics(cache_html or html, metrics)
                output = io.BytesIO()
                image.save(output, format="PNG")

            close_url = f"{runtime.base_url}/json/close/{target_id}"
            for _attempt in range(2):
                try:
                    async with session.get(close_url) as response:
                        if response.status == 200:
                            page_closed = True
                            break
                except aiohttp.ClientError:
                    await asyncio.sleep(0)
            if not page_closed:
                raise RuntimeError("Chrome DevTools page cleanup failed")
            count_runtime("page_sessions_closed")
            return output.getvalue(), metrics
    except Exception:
        # A protocol failure can leave state behind.  Retire the shared process;
        # the caller falls back to the proven one-shot renderer and the next
        # request starts a fresh isolated runtime.
        await asyncio.to_thread(discard_runtime, runtime)
        raise
