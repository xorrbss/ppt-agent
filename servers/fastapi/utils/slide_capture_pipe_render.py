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
from typing import Any, TypeVar
import urllib.parse

from utils.renderer_isolation import AdmittedHtml
from utils.slide_capture_fit import fit_marker_poll_js
from utils.slide_capture_glyph_probe import probe_glyph_clipping_sync
from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX

SLIDE_W = DEFAULT_CANVAS_WIDTH_PX
SLIDE_H = DEFAULT_CANVAS_HEIGHT_PX
METRICS_ELEMENT_ID = "__codex_authored_dom_metrics"
T = TypeVar("T")
PageCommand = Callable[..., Mapping[str, Any]]

def render_admitted_html_over_pipe(
    admitted: AdmittedHtml, timeout: int, *,
    width_px: int = SLIDE_W, height_px: int = SLIDE_H,
    blocked_urls: Sequence[str],
    chrome_pipe: Callable[[str, pathlib.Path], Any],
    find_chrome: Callable[[], str | None],
    instrument_html: Callable[..., tuple[str, str]],
    runtime_value: Callable[[Mapping[str, Any]], Any],
) -> tuple[bytes, Mapping[str, Any] | None]:
    return render_admitted_htmls_over_pipe(
        (admitted,),
        timeout,
        width_px=width_px,
        height_px=height_px,
        blocked_urls=blocked_urls,
        chrome_pipe=chrome_pipe,
        find_chrome=find_chrome,
        instrument_html=instrument_html,
        runtime_value=runtime_value,
    )[0]


def render_admitted_htmls_over_pipe(
    admitted_htmls: Sequence[AdmittedHtml],
    timeout: int,
    *,
    width_px: int = SLIDE_W,
    height_px: int = SLIDE_H,
    blocked_urls: Sequence[str],
    chrome_pipe: Callable[[str, pathlib.Path], Any],
    find_chrome: Callable[[], str | None],
    instrument_html: Callable[..., tuple[str, str]],
    runtime_value: Callable[[Mapping[str, Any]], Any],
) -> list[tuple[bytes, Mapping[str, Any] | None]]:
    """Render one operation batch through one Chrome CDP pipe.

    Every slide still gets a disposable BrowserContext, target/session, denied
    downloads, and fail-closed network policy; only the supervised Chromium
    process is reused for the exact batch.
    """

    from PIL import Image

    def capture(
        page_command: PageCommand,
        frame_id: str,
        admitted: AdmittedHtml,
        metric_result: Mapping[str, Any],
    ) -> tuple[bytes, Mapping[str, Any] | None]:
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
            {
                "format": "png",
                "fromSurface": True,
                "captureBeyondViewport": False,
            },
        )
        image_bytes = base64.b64decode(str(shot.get("data") or ""), validate=True)
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB").crop(
            (0, 0, width_px, height_px)
        )
        output = io.BytesIO()
        image.save(output, format="PNG")
        if metrics is not None:
            from utils.slide_capture_local_fit import measured_html
            metrics = probe_glyph_clipping_sync(
                measured_html(admitted.html, metrics), metrics, output.getvalue(),
                command=page_command, frame_id=frame_id,
                width_px=width_px, height_px=height_px,
            )
        return output.getvalue(), metrics

    return drive_admitted_htmls_over_pipe(
        admitted_htmls, timeout, width_px=width_px, height_px=height_px,
        blocked_urls=blocked_urls, chrome_pipe=chrome_pipe,
        find_chrome=find_chrome, instrument_html=instrument_html,
        capture=capture,
    )


def drive_admitted_htmls_over_pipe(
    admitted_htmls: Sequence[AdmittedHtml],
    timeout: int,
    *,
    width_px: int,
    height_px: int,
    blocked_urls: Sequence[str],
    chrome_pipe: Callable[[str, pathlib.Path], Any],
    find_chrome: Callable[[], str | None],
    instrument_html: Callable[..., tuple[str, str]],
    capture: Callable[[PageCommand, str, AdmittedHtml, Mapping[str, Any]], T],
) -> list[T]:
    """Load each admitted document in its own isolated page, then ``capture``.

    Shared by the screenshot and print-to-PDF paths so both get the identical
    context/target/session setup, network policy and font/image readiness
    wait; only the final capture command differs. ``capture`` receives the
    page command, the frame id, the admitted document, and the readiness
    ``Runtime.evaluate`` result.
    """

    if not admitted_htmls:
        raise ValueError("at least one admitted HTML document is required")
    if timeout <= 0:
        raise ValueError("render timeout must be positive")
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

        def render_one(admitted: AdmittedHtml) -> T:
            context_id = ""
            target_id = ""
            try:
                created_context = command(
                    "Target.createBrowserContext",
                    {"disposeOnDetach": True},
                )
                context_id = str(created_context.get("browserContextId") or "")
                if not context_id:
                    raise RuntimeError("Chrome DevTools pipe context is missing")
                command(
                    "Browser.setDownloadBehavior",
                    {"behavior": "deny", "browserContextId": context_id},
                )
                target_id = str(
                    command(
                        "Target.createTarget",
                        {
                            "url": "data:,",
                            "browserContextId": context_id,
                        },
                    ).get("targetId")
                    or ""
                )
                if not target_id:
                    raise RuntimeError("Chrome DevTools pipe target is missing")
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
                        "width": width_px,
                        "height": height_px,
                        "deviceScaleFactor": 1,
                        "mobile": False,
                    },
                )
                frame_tree = page_command("Page.getFrameTree")
                tree = frame_tree.get("frameTree", {})
                frame = tree.get("frame", {}) if isinstance(tree, Mapping) else {}
                frame_id = (
                    str(frame.get("id") or "") if isinstance(frame, Mapping) else ""
                )
                if not frame_id:
                    raise RuntimeError("Chrome DevTools pipe frame id is missing")
                instrumented, _key = instrument_html(
                    admitted.html,
                    script_nonce=admitted.nonce,
                    issue_capability=False,
                    width_px=width_px,
                    height_px=height_px,
                )
                page_command(
                    "Page.setDocumentContent",
                    {"frameId": frame_id, "html": instrumented},
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
{fit_marker_poll_js(METRICS_ELEMENT_ID, indent='                  ')}
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
                        if (
                            "Execution context was destroyed" not in str(error)
                            or attempt == 2
                        ):
                            raise
                        time.sleep(0.05)
                if metric_result is None:
                    raise RuntimeError(
                        "Chrome DevTools metrics evaluation did not complete"
                    )
                return capture(page_command, frame_id, admitted, metric_result)
            finally:
                target_error: BaseException | None = None
                if target_id:
                    try:
                        command("Target.closeTarget", {"targetId": target_id})
                    except BaseException as error:
                        target_error = error
                if context_id:
                    try:
                        command(
                            "Target.disposeBrowserContext",
                            {"browserContextId": context_id},
                        )
                    except BaseException as error:
                        raise RuntimeError(
                            "Chrome DevTools pipe context disposal failed"
                        ) from error
                if target_error is not None and not context_id:
                    raise target_error

        try:
            return [render_one(admitted) for admitted in admitted_htmls]
        finally:
            transport.close()

