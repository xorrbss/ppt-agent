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
from utils.slide_capture_fit import fit_marker_poll_js
from utils.slide_capture_glyph_probe import probe_glyph_clipping_async
from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX

SLIDE_W = DEFAULT_CANVAS_WIDTH_PX
SLIDE_H = DEFAULT_CANVAS_HEIGHT_PX

async def _close_owned_target_with_verification(session, close_url: str, target_id: str) -> bool:
    import aiohttp
    if not isinstance(target_id, str) or not target_id or not close_url.endswith("/json/close/" + target_id):
        return False
    request_timeout = aiohttp.ClientTimeout(total=1.5)
    for _ in range(2):
        try:
            async with session.get(close_url, timeout=request_timeout, allow_redirects=False) as response:
                if response.status in (200, 404):
                    return True
        except (aiohttp.ClientError, TimeoutError):
            continue
    list_url = close_url.rsplit("/json/close/", 1)[0] + "/json/list"
    try:
        async with session.get(list_url, timeout=request_timeout, allow_redirects=False) as response:
            if response.status != 200:
                return False
            targets = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError):
        return False
    if not isinstance(targets, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] for item in targets):
        return False
    return all(item["id"] != target_id for item in targets)


async def render_html_in_reusable_chrome(  # noqa: ASYNC109
    html: str, timeout: int, *, script_nonce: str | None = None,
    cache_html: str | None = None, blocked_urls: Sequence[str],
    cache_metrics: Callable[..., None], cdp_command: Callable[..., Any],
    count_runtime: Callable[[str], None], discard_runtime: Callable[[Any], None],
    release_runtime: Callable[[Any], None],
    enrich_contrast: Callable[..., Mapping[str, Any]], get_runtime: Callable[[], Any],
    instrument_html: Callable[..., tuple[str, str]], metrics_element_id: str,
    pixel_probe_document: Callable[[str], str], runtime_value: Callable[[Mapping[str, Any]], Any],
    width_px: int = SLIDE_W, height_px: int = SLIDE_H,
) -> tuple[bytes, Mapping[str, Any] | None]:
    """Render through a disposable CDP page in the shared isolated process."""

    from PIL import Image

    runtime = None
    acquisition = asyncio.create_task(asyncio.to_thread(get_runtime))
    timeout_config = aiohttp.ClientTimeout(total=max(5, timeout))
    target_id = ""
    failed = False
    try:
        # ClientTimeout does not bound WebSocket receive/Runtime.evaluate.
        async with (
            asyncio.timeout(max(0.001, timeout)),
            aiohttp.ClientSession(timeout=timeout_config) as session,
        ):
            runtime = await asyncio.shield(acquisition)
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
                        "width": width_px,
                        "height": height_px,
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
                    width_px=width_px,
                    height_px=height_px,
                    identity_html=cache_html,
                )
                await command(
                    "Page.setDocumentContent", {"frameId": frame_id, "html": instrumented}
                )
                metrics_result = await command(
                    "Runtime.evaluate",
                    {
                        "expression": f"""(async () => {{
                          if (document.fonts && document.fonts.ready) await document.fonts.ready;
                          await Promise.all(Array.from(document.images).map(image => image.complete
                            ? (image.decode ? image.decode().catch(() => undefined) : Promise.resolve())
                            : new Promise(resolve => {{image.addEventListener('load',resolve,{{once:true}});
                                image.addEventListener('error',resolve,{{once:true}});}})));
                          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
{fit_marker_poll_js(metrics_element_id, indent='                          ')}
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
                    (0, 0, width_px, height_px)
                )

                if metrics is not None and any(
                    isinstance(item, Mapping)
                    and bool(item.get("requires_pixel_contrast"))
                    for item in metrics.get("elements", ())
                ):
                    from utils.slide_capture_local_fit import measured_html
                    await command(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": width_px,
                            "height": height_px * 2,
                            "deviceScaleFactor": 1,
                            "mobile": False,
                        },
                    )
                    await command(
                        "Page.setDocumentContent",
                        {
                            "frameId": frame_id,
                            "html": pixel_probe_document(
                                measured_html(html, metrics),
                                width_px=width_px, height_px=height_px,
                            ),
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
                    if probe_image.size[0] < width_px or probe_image.size[1] < height_px * 2:
                        raise RuntimeError(
                            f"Chrome pixel probe has unexpected size {probe_image.size}"
                        )
                    metrics = enrich_contrast(metrics, image, probe_image)
                output = io.BytesIO()
                image.save(output, format="PNG")
                if metrics is not None:
                    from utils.slide_capture_local_fit import measured_html
                    metrics = await probe_glyph_clipping_async(
                        measured_html(html, metrics), metrics, output.getvalue(), command=command, frame_id=frame_id,
                        width_px=width_px, height_px=height_px,
                    )
                    cache_metrics(
                        cache_html or html, metrics,
                        width_px=width_px, height_px=height_px,
                    )

            return output.getvalue(), metrics
    except BaseException:
        failed = True
        raise
    finally:
        async def cleanup() -> None:
            owned_runtime = runtime
            if owned_runtime is None:
                # A cancelled to_thread acquisition still runs. Recover and
                # release its reference instead of abandoning a Chrome process.
                acquired = await asyncio.gather(acquisition, return_exceptions=True)
                if isinstance(acquired[0], BaseException):
                    return
                owned_runtime = acquired[0]
            cleanup_failed = False
            try:
                if failed:
                    await asyncio.to_thread(discard_runtime, owned_runtime)
                if target_id:
                    page_closed = False
                    try:
                        async with asyncio.timeout(5), aiohttp.ClientSession(
                            timeout=aiohttp.ClientTimeout(total=5)
                        ) as cleanup_session:
                                close_url = f"{owned_runtime.base_url}/json/close/{target_id}"
                                page_closed = await _close_owned_target_with_verification(cleanup_session, close_url, target_id)
                    except (aiohttp.ClientError, TimeoutError):
                        pass
                    cleanup_failed = not page_closed
                    if page_closed:
                        count_runtime("page_sessions_closed")
                    else:
                        await asyncio.to_thread(discard_runtime, owned_runtime)
            finally:
                await asyncio.to_thread(release_runtime, owned_runtime)
            if cleanup_failed and not failed:
                raise RuntimeError("Chrome DevTools page cleanup failed")

        cleanup_task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
            raise
