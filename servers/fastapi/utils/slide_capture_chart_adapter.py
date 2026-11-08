"""Chart-only capture callback over the unchanged isolated Chromium pipe driver.

The default renderer and its diagnostic source identity stay byte-preserved in
``slide_capture_pipe_render``. Only manifest-bearing captures enter this adapter.
"""
from __future__ import annotations

import base64
import html as html_lib
import io
import json
import pathlib
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX
from utils.renderer_isolation import AdmittedHtml
from utils.slide_capture_glyph_probe import probe_glyph_clipping_sync
from utils.slide_capture_chart_timing import new_capture_timing, timing_phase
from utils.slide_capture_pipe_render import (
    PageCommand,
    drive_admitted_htmls_over_pipe,
    private_diagnostic_pipe_code_digest,
)


def _wait_svg_materials(command: PageCommand, *, settle: bool = True) -> None:
    """Finish SVG image/font decoding before the one emitted chart frame."""
    result = command("Runtime.evaluate", {"expression": """(async()=>{
      if(document.fonts)await document.fonts.ready;
      const nodes=Array.from(document.querySelectorAll('svg image'));
      const loaded=await Promise.all(nodes.map(async node=>{
        const image=new Image();image.src=node.href.baseVal;
        try{await image.decode();return image.complete&&image.naturalWidth>0;}
        catch(_){return false;}
      }));
      if(__SETTLE__)await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      return {svgMaterialsReady:loaded.every(Boolean)};
    })()""".replace("__SETTLE__", "true" if settle else "false"), "returnByValue": True, "awaitPromise": True})
    if result.get("exceptionDetails") or result.get("result", {}).get("value") != {"svgMaterialsReady": True}:
        raise RuntimeError("chart SVG material decoding incomplete")


class _SharedChartPipe:
    """One synchronous transport with IDs shared by original and sibling drives."""

    def __init__(self, transport: Any, *, timeout: int = 60) -> None:
        self.transport = transport
        self.command_id = 0
        self.closed = False
        self.timeout = timeout
        self.primary_ids: set[str] = set()
        self.sibling_ids: dict[str, str] = {}
        self.plane_active = False
        self.poisoned = False
        self.document_reset = False
        self.expected_html: str | None = None
        self.frame_id: str | None = None

    def command(
        self, method: str, params: Mapping[str, Any] | None = None, *,
        session_id: str | None = None,
    ) -> Mapping[str, Any]:
        if self.closed:
            raise RuntimeError("chart diagnostic transport is closed")
        self.command_id += 1
        return self.transport.command(
            self.command_id, method, params, session_id=session_id,
        )

    def owner_command(self, method: str, params: Mapping[str, Any] | None = None, *, session_id: str | None = None) -> Mapping[str, Any]:
        result = self.command(method, params, session_id=session_id)
        for key in ("browserContextId", "targetId", "sessionId"):
            value = result.get(key)
            if isinstance(value, str):
                if value in self.sibling_ids.values():
                    raise RuntimeError("chart primary and sibling identities overlap")
                self.primary_ids.add(value)
        return result

    def begin_plane(self) -> None:
        if self.closed or self.plane_active or self.poisoned:
            raise RuntimeError("chart sibling lifecycle unavailable")
        self.plane_active = True
        self.document_reset = False
        self.expected_html = None
        self.frame_id = None

    def end_plane(self, success: bool) -> None:
        if not self.plane_active:
            self.poisoned = True
            raise RuntimeError("chart sibling plane is not active")
        self.plane_active = False
        if not success:
            self.poisoned = True
        elif self.poisoned or not self.document_reset or self.expected_html is None:
            self.poisoned = True
            raise RuntimeError("chart sibling plane did not complete safely")

    def expect_document(self, html: str) -> None:
        if not self.plane_active or not self.document_reset or self.expected_html is not None or not isinstance(html, str):
            self.poisoned = True
            raise RuntimeError("chart sibling document registration invalid")
        self.expected_html = html

    def _fresh_document(self, session_id: str) -> Mapping[str, Any]:
        def page(method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
            return self.command(method, params, session_id=session_id)

        # Emulation overrides survive navigation; alpha must never leak to the
        # following plane's all-on screenshot.
        page("Emulation.setDefaultBackgroundColorOverride", {})
        old_tree = page("Page.getFrameTree")
        old_frame = old_tree.get("frameTree", {}).get("frame", {})
        old_loader = old_frame.get("loaderId")
        old_frame_id = old_frame.get("id")
        if not isinstance(old_loader, str) or not old_loader or not isinstance(old_frame_id, str) or not old_frame_id:
            raise RuntimeError("chart sibling prior document identity unavailable")
        old_context = page("Page.createIsolatedWorld", {"frameId": old_frame_id, "worldName": "chart-plane-reset/v1"}).get("executionContextId")
        marked = page("Runtime.evaluate", {"expression": "(()=>{globalThis.__chartPlanePriorDocument=true;const n=document.createElement('i');n.id='chart-plane-prior-document';(document.body||document.documentElement).appendChild(n);return true})()", "returnByValue": True})
        if marked.get("exceptionDetails") or marked.get("result", {}).get("value") is not True:
            raise RuntimeError("chart sibling prior document sentinel unavailable")
        navigation = page("Page.navigate", {"url": "data:,"})
        new_loader = navigation.get("loaderId")
        if navigation.get("errorText") or not isinstance(new_loader, str) or not new_loader or new_loader == old_loader:
            raise RuntimeError("chart sibling navigation identity invalid")
        deadline = time.monotonic() + self.timeout
        while True:
            tree = page("Page.getFrameTree")
            frame = tree.get("frameTree", {}).get("frame", {})
            if frame.get("loaderId") == new_loader:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("chart sibling blank navigation did not commit")
            time.sleep(0.01)
        frame_id = frame.get("id")
        if not isinstance(frame_id, str) or not frame_id or frame.get("url") != "data:,":
            raise RuntimeError("chart sibling committed frame invalid")
        while True:
            try:
                ready = page("Runtime.evaluate", {"expression": "({chartPlaneReadyV1:document.readyState==='complete'&&location.href==='data:,'})", "returnByValue": True})
            except RuntimeError as exc:
                if not any(message in str(exc) for message in (
                    "Execution context was destroyed", "Cannot find default execution context",
                    "Cannot find context with specified id",
                )):
                    raise
                ready = None
            if ready is not None:
                if ready.get("exceptionDetails"):
                    raise RuntimeError("chart sibling blank readiness evaluation failed")
                state = ready.get("result", {}).get("value")
                if state == {"chartPlaneReadyV1": True}:
                    break
                if state != {"chartPlaneReadyV1": False}:
                    raise RuntimeError("chart sibling blank readiness invalid")
            if time.monotonic() >= deadline:
                raise RuntimeError("chart sibling blank document did not become ready")
            time.sleep(0.01)
        new_context = page("Page.createIsolatedWorld", {"frameId": frame_id, "worldName": "chart-plane-reset/v1"}).get("executionContextId")
        # CDP does not promise distinct context ids across a navigation (Chrome for
        # Testing 153 reuses them), so the id only has to exist; the loader change
        # and the blank-document sentinel check below prove the reset.
        if type(old_context) is not int or type(new_context) is not int:
            raise RuntimeError("chart sibling execution context was not replaced")
        clean = page("Runtime.evaluate", {"expression": "(async()=>{if(document.readyState!=='complete')await new Promise(resolve=>window.addEventListener('load',resolve,{once:true}));return {chartPlaneBlankV1:document.readyState==='complete'&&location.href==='data:,'&&typeof globalThis.__chartPlanePriorDocument==='undefined'&&typeof globalThis.__chartPaint==='undefined'&&!document.getElementById('chart-plane-prior-document')&&!document.querySelector('svg')&&!document.getElementById('__codex_authored_dom_metrics')&&document.styleSheets.length===0&&document.adoptedStyleSheets.length===0&&(!document.fonts||document.fonts.size===0)&&(!getSelection()||getSelection().rangeCount===0)&&(!document.body||document.body.childNodes.length===0)}})()", "returnByValue": True, "awaitPromise": True})
        if clean.get("exceptionDetails") or clean.get("result", {}).get("value") != {"chartPlaneBlankV1": True}:
            raise RuntimeError("chart sibling blank document retained prior state")
        self.document_reset = True
        self.frame_id = frame_id
        return tree

    def sibling_command(self, method: str, params: Mapping[str, Any] | None = None, *, session_id: str | None = None) -> Mapping[str, Any]:
        params = dict(params or {})
        # The frozen nested driver's cleanup is deferred to the capture owner.
        if method in ("Target.closeTarget", "Target.disposeBrowserContext"):
            key = "targetId" if method == "Target.closeTarget" else "browserContextId"
            if params.get(key) != self.sibling_ids.get(key) or key not in self.sibling_ids:
                self.poisoned = True
                raise RuntimeError("chart sibling cleanup identity mismatch")
            return {"success": True} if method == "Target.closeTarget" else {}
        if not self.plane_active or self.poisoned:
            raise RuntimeError("chart sibling command outside active plane")
        try:
            create = {"Target.createBrowserContext": "browserContextId", "Target.createTarget": "targetId", "Target.attachToTarget": "sessionId"}
            if method in create:
                key = create[method]
                if method == "Target.createTarget" and (params.get("browserContextId") != self.sibling_ids.get("browserContextId") or params.get("url") != "data:,"):
                    raise RuntimeError("chart sibling target identity mismatch")
                if method == "Target.attachToTarget" and (params.get("targetId") != self.sibling_ids.get("targetId") or params.get("flatten") is not True):
                    raise RuntimeError("chart sibling attachment identity mismatch")
                if key not in self.sibling_ids:
                    result = self.command(method, params, session_id=session_id)
                    value = result.get(key)
                    if not isinstance(value, str) or not value or value in self.primary_ids or value in self.sibling_ids.values():
                        raise RuntimeError("chart sibling identity invalid")
                    self.sibling_ids[key] = value
                    return result
                return {key: self.sibling_ids[key]}
            if session_id is not None and session_id != self.sibling_ids.get("sessionId"):
                raise RuntimeError("chart sibling session identity mismatch")
            if method == "Browser.setDownloadBehavior" and params.get("browserContextId") != self.sibling_ids.get("browserContextId"):
                raise RuntimeError("chart sibling context identity mismatch")
            if method == "Page.getFrameTree" and not self.document_reset:
                if session_id is None:
                    raise RuntimeError("chart sibling reset session missing")
                return self._fresh_document(session_id)
            if method == "Page.setDocumentContent":
                if not self.document_reset or self.expected_html is None or params.get("frameId") != self.frame_id or params.get("html") != self.expected_html:
                    raise RuntimeError("chart sibling admitted document mismatch")
            return self.command(method, params, session_id=session_id)
        except BaseException:
            self.poisoned = True
            raise

    def close(self) -> None:
        if self.closed:
            return
        cleanup_error: BaseException | None = None
        try:
            for method, key in (("Target.closeTarget", "targetId"), ("Target.disposeBrowserContext", "browserContextId")):
                if key in self.sibling_ids:
                    try:
                        result = self.command(method, {key: self.sibling_ids[key]})
                        if method == "Target.closeTarget" and result.get("success") is not True:
                            raise RuntimeError("chart sibling target cleanup incomplete")
                    except BaseException as exc:
                        cleanup_error = cleanup_error or exc
        finally:
            self.closed = True
            try:
                self.transport.close()
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise RuntimeError("chart sibling cleanup failed") from cleanup_error


class _ChartPipeView:
    """Driver facade; only the original drive owns the shared transport close."""

    def __init__(self, shared: _SharedChartPipe, *, owner: bool) -> None:
        self.shared = shared
        self.owner = owner

    def command(
        self, _command_id: int, method: str, params: Mapping[str, Any] | None = None, *,
        session_id: str | None = None,
    ) -> Mapping[str, Any]:
        # Each nested driver starts its own numbering at one. Remap both drives
        # through one allocator so synchronous replies cannot alias another ID.
        command = self.shared.owner_command if self.owner else self.shared.sibling_command
        return command(method, params, session_id=session_id)

    def close(self) -> None:
        if self.owner:
            self.shared.close()


def render_admitted_html_over_pipe(
    admitted: AdmittedHtml, timeout: int, *,
    chart_witness_manifest: dict,
    width_px: int = DEFAULT_CANVAS_WIDTH_PX,
    height_px: int = DEFAULT_CANVAS_HEIGHT_PX,
    private_diagnostic_profile: str | None = None,
    blocked_urls: Sequence[str],
    chrome_pipe: Callable[[str, pathlib.Path], Any],
    find_chrome: Callable[[], str | None],
    instrument_html: Callable[..., tuple[str, str]],
    runtime_value: Callable[[Mapping[str, Any]], Any],
) -> tuple[bytes, Mapping[str, Any] | None]:
    from PIL import Image

    if not isinstance(chart_witness_manifest, dict):
        raise ValueError("chart capture requires a witness manifest")
    if private_diagnostic_profile not in (None, "fixed-glyph-dpr2/v1"):
        raise ValueError("renderer private diagnostic profile invalid")
    if private_diagnostic_profile is not None and (width_px, height_px) != (DEFAULT_CANVAS_WIDTH_PX, DEFAULT_CANVAS_HEIGHT_PX):
        raise ValueError("renderer private diagnostic canvas invalid")
    raster_scale = 2 if private_diagnostic_profile is not None else 1
    timing = new_capture_timing()
    if timing is not None:
        try:
            from utils.slide_capture_chart_paint import _targets
            timing.set_target_count(len(_targets(chart_witness_manifest)))
        except Exception:
            pass
        timing.emit(0)
    shared: _SharedChartPipe | None = None

    def owner_pipe(chrome: str, profile_path: pathlib.Path) -> _ChartPipeView:
        nonlocal shared
        if shared is not None:
            raise RuntimeError("chart capture transport already exists")
        shared = _SharedChartPipe(chrome_pipe(chrome, profile_path), timeout=timeout)
        return _ChartPipeView(shared, owner=True)

    def sibling_pipe(_chrome: str, _profile_path: pathlib.Path) -> _ChartPipeView:
        if shared is None or shared.closed:
            raise RuntimeError("chart diagnostic transport is unavailable")
        return _ChartPipeView(shared, owner=False)

    def read_metrics(metric_result: Mapping[str, Any]) -> Mapping[str, Any] | None:
        encoded_metrics = runtime_value(metric_result)
        if isinstance(encoded_metrics, str) and encoded_metrics:
            try:
                parsed = json.loads(urllib.parse.unquote(html_lib.unescape(encoded_metrics)))
                return parsed if isinstance(parsed, Mapping) else None
            except (TypeError, ValueError):
                pass
        return None

    def diagnostic_capture(callback: Callable[[PageCommand, Mapping[str, Any] | None], Any]) -> Any:
        """Run one counterfactual callback in a fresh sibling document.

        The unchanged driver repeats its normal admitted-source instrumentation,
        readiness on one capture-owned sibling. Only browser orchestration is
        reused; documents, measurements and screenshots are independently made.
        The outer worker supervisor retains the operation's hard timeout.
        """
        def capture_sibling(
            command: PageCommand, _frame_id: str, _admitted: AdmittedHtml,
            metric_result: Mapping[str, Any],
        ) -> Any:
            with timing_phase(timing, "material_wait"):
                _wait_svg_materials(command, settle=False)
            with timing_phase(timing, "callback"):
                return callback(command, read_metrics(metric_result))

        if shared is None:
            raise RuntimeError("chart diagnostic transport is unavailable")
        def instrument_sibling(*args: Any, **kwargs: Any) -> tuple[str, str]:
            instrumented = instrument_html(*args, **kwargs)
            shared.expect_document(instrumented[0])
            return instrumented

        shared.begin_plane()
        succeeded = False
        try:
            with timing_phase(timing, "diagnostic_total"):
                result = drive_admitted_htmls_over_pipe(
                    (admitted,), timeout, width_px=width_px, height_px=height_px,
                    blocked_urls=blocked_urls, chrome_pipe=sibling_pipe,
                    find_chrome=find_chrome, instrument_html=instrument_sibling,
                    capture=capture_sibling, device_scale_factor=raster_scale,
                )[0]
            succeeded = True
        finally:
            shared.end_plane(succeeded)
        if timing is not None:
            timing.plane_completed()
        return result

    def capture(
        page_command: PageCommand,
        frame_id: str,
        admitted: AdmittedHtml,
        metric_result: Mapping[str, Any],
    ) -> tuple[bytes, Mapping[str, Any] | None]:
        with timing_phase(timing, "material_wait"):
            _wait_svg_materials(page_command)
        metrics = read_metrics(metric_result)
        if private_diagnostic_profile is not None:
            if metrics is None or any(
                isinstance(item, Mapping) and item.get("range_clip_suspect") is True
                for item in metrics.get("elements", ())
            ):
                raise RuntimeError("diagnostic raster requires complete logical metrics without glyph probe")
            # Keep the CSS viewport unchanged. A prior internal probe can reset
            # emulation, so set and read back DPR directly before final capture.
            page_command("Emulation.setDeviceMetricsOverride", {
                "width": width_px, "height": height_px,
                "deviceScaleFactor": raster_scale, "mobile": False,
            })
            state = page_command("Runtime.evaluate", {
                "expression": "({dpr:window.devicePixelRatio,width:window.innerWidth,height:window.innerHeight})",
                "returnByValue": True,
            }).get("result", {}).get("value")
            if state != {"dpr": 2, "width": width_px, "height": height_px}:
                raise RuntimeError("diagnostic raster device metrics mismatch")
        if chart_witness_manifest is not None:
            # Check the actual viewport/DPR before cropping could hide a stale frame.
            state = page_command("Runtime.evaluate", {
                "expression": "({dpr:window.devicePixelRatio,width:window.innerWidth,height:window.innerHeight})",
                "returnByValue": True,
            }).get("result", {}).get("value")
            if state != {"dpr": raster_scale, "width": width_px, "height": height_px}:
                raise RuntimeError("chart paint raster device metrics mismatch")
        shot = page_command(
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "captureBeyondViewport": False,
            },
        )
        image_bytes = base64.b64decode(str(shot.get("data") or ""), validate=True)
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        if chart_witness_manifest is not None and image.size != (width_px * raster_scale, height_px * raster_scale):
            raise RuntimeError("chart paint raster screenshot size mismatch")
        if private_diagnostic_profile is not None:
            if image.size != (width_px * raster_scale, height_px * raster_scale):
                raise RuntimeError("diagnostic raster screenshot size mismatch")
        else:
            image = image.crop((0, 0, width_px, height_px))
        output = io.BytesIO()
        image.save(output, format="PNG")
        if chart_witness_manifest is not None:
            from utils.slide_capture_chart_paint import collect_chart_paint_proof
            proof = collect_chart_paint_proof(
                command=page_command, manifest=chart_witness_manifest, source_html=admitted.html,
                wrapper_digest=admitted.source_digest,
                final_png=output.getvalue(), metrics=metrics, width_px=width_px, height_px=height_px,
                device_scale_factor=raster_scale,
                diagnostic_capture=diagnostic_capture,
                timing=timing,
            )
            metrics = dict(metrics or {})
            metrics["nativeChartPaintProof"] = proof
        if metrics is not None and private_diagnostic_profile is None:
            from utils.slide_capture_local_fit import measured_html
            metrics = probe_glyph_clipping_sync(
                measured_html(admitted.html, metrics), metrics, output.getvalue(),
                command=page_command, frame_id=frame_id,
                width_px=width_px, height_px=height_px,
            )
        elif metrics is not None:
            metrics = dict(metrics)
            metrics["privateDiagnosticRaster"] = {
                "profile": private_diagnostic_profile,
                "logicalCanvasPx": [width_px, height_px],
                "rasterCanvasPx": [image.width, image.height],
                "devicePixelRatio": raster_scale,
                "pipeCodeDigest": private_diagnostic_pipe_code_digest(),
            }
        return output.getvalue(), metrics

    try:
        return drive_admitted_htmls_over_pipe(
            (admitted,), timeout, width_px=width_px, height_px=height_px,
            blocked_urls=blocked_urls, chrome_pipe=owner_pipe,
            find_chrome=find_chrome, instrument_html=instrument_html,
            capture=capture, device_scale_factor=raster_scale,
        )[0]
    finally:
        if timing is not None:
            timing.finish()
