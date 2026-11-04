"""Fail-closed client for the externally isolated renderer worker."""
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import json
import hashlib
import tempfile
from pathlib import Path
import os
import signal
import subprocess
from typing import Any, Mapping

from utils.get_env_programme import (
    get_authored_renderer_lease_max_renders,
    get_authored_renderer_lease_pool_size,
    is_authored_renderer_framed_protocol_enabled,
    is_authored_renderer_lease_enabled,
)
from utils.process_kill_on_parent_death import bind_to_parent_lifetime
from utils.renderer_isolation import (
    MAX_RESPONSE_BYTES,
    build_worker_environment, encode_request, worker_command,
)
from utils.renderer_worker_policy import renderer_requires_external_worker
from utils.renderer_worker_response import (
    RendererIsolationError,
    decode_worker_response as _decode_worker_response,
    source_digest_for,
)
from utils.slide_capture import _admit_render_source, _cache_browser_metrics
from utils.slide_capture_worker_lease import acquire_lease
from utils.slide_capture_worker_session import RendererWorkerSession
from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX, is_default_landscape
from constants.timeouts import CAPTURE_WORKER_TERMINATE_GRACE_SECONDS

async def _render_html_in_isolated_worker(  # noqa: ASYNC109
    html: str, timeout: int, *, width_px: int = DEFAULT_CANVAS_WIDTH_PX, height_px: int = DEFAULT_CANVAS_HEIGHT_PX,
    preserved_raster_svg: str | None = None, system_furniture: dict | None = None,
    font_manifest: dict | None = None, canonical_svg: str | None = None,
    image_bindings: dict[str, str] | None = None,
    fixed_glyph_preview: dict | None = None,
    fixed_glyph_display_digest: str | None = None,
    fixed_glyph_render_digest: str | None = None,
    fixed_glyph_projection_digest: str | None = None,
    fixed_glyph_font_css_digest: str | None = None,
    private_diagnostic_profile: str | None = None,
) -> tuple[bytes, Mapping[str, Any] | None]:
    if private_diagnostic_profile is not None:
        if (private_diagnostic_profile != "fixed-glyph-dpr2/v1"
                or fixed_glyph_preview is None or canonical_svg is None
                or (width_px, height_px) != (1280, 720)):
            raise ValueError("svg_template_glyph_diagnostic_profile_invalid")
    if font_manifest is None:
        if fixed_glyph_preview is not None:
            raise ValueError("svg_template_glyph_font_manifest_missing")
        return await _render_html_in_isolated_worker_impl(html, timeout, width_px=width_px, height_px=height_px,
            preserved_raster_svg=preserved_raster_svg, system_furniture=system_furniture,
            canonical_svg=canonical_svg, image_bindings=image_bindings)
    from models.svg_template_frame import SvgTemplateFontManifest
    from services.svg_templates.frame_fonts import MAX_FONT_BYTES, resolve_font_manifest
    manifest = SvgTemplateFontManifest.model_validate(font_manifest)
    entries = resolve_font_manifest(manifest)
    if sum(len(payload) for _asset, payload in entries) > MAX_FONT_BYTES:
        raise ValueError("svg_template_font_manifest_bytes_exceeded")
    encoded = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    if len(encoded) > 90 * 1024 * 1024:
        raise ValueError("svg_template_font_manifest_bytes_exceeded")
    with tempfile.TemporaryDirectory(prefix="presenton-render-font-") as temporary:
        root = Path(await asyncio.to_thread(os.path.realpath, temporary))
        path = root / "font-manifest.json"
        await asyncio.to_thread(path.write_bytes, encoded)
        if os.name != "nt":
            os.chmod(path, 0o444)
        reference = {"name": "font-manifest.json", "sha256": "sha256:" + hashlib.sha256(encoded).hexdigest()}
        glyph_reference = None
        if fixed_glyph_preview is not None:
            from models.svg_template_glyph import SvgTemplateFixedGlyphPreview
            from utils.svg_template_glyph_materialization import materializer_code_digest
            glyph = SvgTemplateFixedGlyphPreview.model_validate(fixed_glyph_preview)
            if (glyph.materializer_code_digest != materializer_code_digest()
                    or not isinstance(fixed_glyph_display_digest, str)
                    or len(fixed_glyph_display_digest) != 71
                    or not fixed_glyph_display_digest.startswith("sha256:")
                    or not isinstance(fixed_glyph_render_digest, str)
                    or not isinstance(fixed_glyph_projection_digest, str)
                    or not isinstance(fixed_glyph_font_css_digest, str)
                    or any(len(value) != 71 or not value.startswith("sha256:") for value in
                           (fixed_glyph_render_digest, fixed_glyph_projection_digest,
                            fixed_glyph_font_css_digest))):
                raise ValueError("svg_template_glyph_render_binding_invalid")
            glyph_payload = json.dumps(glyph.model_dump(mode="json"), ensure_ascii=False,
                                       sort_keys=True, separators=(",", ":")).encode()
            if len(glyph_payload) > 16 * 1024 * 1024:
                raise ValueError("svg_template_glyph_authority_size_invalid")
            glyph_path = root / "fixed-glyph-preview.json"
            glyph_path.write_bytes(glyph_payload)
            if os.name != "nt":
                os.chmod(glyph_path, 0o444)
            glyph_reference = {"name": "fixed-glyph-preview.json",
                               "sha256": "sha256:" + hashlib.sha256(glyph_payload).hexdigest()}
        return await _render_html_in_isolated_worker_impl(html, timeout, width_px=width_px, height_px=height_px,
            preserved_raster_svg=preserved_raster_svg, system_furniture=system_furniture,
            canonical_svg=canonical_svg, image_bindings=image_bindings,
            font_manifest_ref=reference, request_assets_root=str(root),
            fixed_glyph_preview_ref=glyph_reference,
            fixed_glyph_display_digest=fixed_glyph_display_digest,
            fixed_glyph_render_digest=fixed_glyph_render_digest,
            fixed_glyph_projection_digest=fixed_glyph_projection_digest,
            fixed_glyph_font_css_digest=fixed_glyph_font_css_digest,
            private_diagnostic_profile=private_diagnostic_profile,
            fixed_glyph_authority_digest=glyph.authority_digest if fixed_glyph_preview is not None else None,
            fixed_glyph_code_digest=glyph.materializer_code_digest if fixed_glyph_preview is not None else None)


async def _render_html_in_isolated_worker_impl(  # noqa: ASYNC109
    html: str, timeout: int, *, width_px: int = DEFAULT_CANVAS_WIDTH_PX, height_px: int = DEFAULT_CANVAS_HEIGHT_PX,
    preserved_raster_svg: str | None = None,
    system_furniture: dict | None = None,
    font_manifest: dict | None = None,
    canonical_svg: str | None = None,
    image_bindings: dict[str, str] | None = None,
    font_manifest_ref: dict | None = None,
    request_assets_root: str | None = None,
    fixed_glyph_preview_ref: dict | None = None,
    fixed_glyph_display_digest: str | None = None,
    fixed_glyph_render_digest: str | None = None,
    fixed_glyph_projection_digest: str | None = None,
    fixed_glyph_font_css_digest: str | None = None,
    private_diagnostic_profile: str | None = None,
    fixed_glyph_authority_digest: str | None = None,
    fixed_glyph_code_digest: str | None = None,
) -> tuple[bytes, Mapping[str, Any] | None]:
    if preserved_raster_svg is not None:
        from services.svg_renderer import admit_preserved_raster_preview
        admitted, measured_html = admit_preserved_raster_preview(
            html, preserved_raster_svg, width=width_px, height=height_px,
            **({"system_furniture": system_furniture} if system_furniture is not None else {}))
    else:
        admitted = _admit_render_source(html)
        measured_html = html
    command = worker_command()
    if request_assets_root is not None:
        command = [*command, "--request-assets-root", request_assets_root]
    request = encode_request(
        html, timeout, width_px=width_px, height_px=height_px,
        **({"preserved_raster_svg": preserved_raster_svg} if preserved_raster_svg is not None else {}),
        **({"system_furniture": system_furniture} if system_furniture is not None else {}),
        **({"font_manifest": font_manifest} if font_manifest is not None else {}),
        **({"font_manifest_ref": font_manifest_ref} if font_manifest_ref is not None else {}),
        **({"canonical_svg": canonical_svg} if canonical_svg is not None else {}),
        **({"image_bindings": image_bindings} if image_bindings is not None else {}),
        **({"fixed_glyph_preview_ref": fixed_glyph_preview_ref} if fixed_glyph_preview_ref is not None else {}),
        **({"fixed_glyph_display_digest": fixed_glyph_display_digest} if fixed_glyph_display_digest is not None else {}),
        **({"private_diagnostic_profile": private_diagnostic_profile} if private_diagnostic_profile is not None else {}),
    )
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
    # Bind the one-shot worker to this process's lifetime; a force-killed owner
    # otherwise strands the worker and its Chromium. No-op off Windows.
    bind_to_parent_lifetime(process)

    async def terminate_process_tree() -> None:
        if process.returncode is None:
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), timeout=CAPTURE_WORKER_TERMINATE_GRACE_SECONDS)
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
    expected_digest = source_digest_for(measured_html)
    # Admission is intentionally repeated in the worker.  The parent digest check
    # binds the returned artifact to the exact request across an external launcher.
    response = json.loads(stdout)
    if admitted.source_digest != expected_digest or response.get("source_digest") != expected_digest:
        raise RendererIsolationError("authored renderer response identity mismatch")
    if process.returncode != 0:
        raise RendererIsolationError("authored renderer worker failed")
    if fixed_glyph_preview_ref is not None:
        fixed = (metrics or {}).get("fixedGlyphPreview")
        if (not isinstance(fixed, dict)
                or fixed.get("displayDigest") != fixed_glyph_display_digest
                or fixed.get("renderDigest") != fixed_glyph_render_digest
                or fixed.get("projectionDigest") != fixed_glyph_projection_digest
                or fixed.get("fontCssDigest") != fixed_glyph_font_css_digest
                or fixed.get("authorityDigest") != fixed_glyph_authority_digest
                or fixed.get("materializerCodeDigest") != fixed_glyph_code_digest):
            raise RendererIsolationError("svg template glyph renderer identity mismatch")
    if private_diagnostic_profile is not None:
        from PIL import Image
        import io
        from utils.slide_capture_pipe_render import private_diagnostic_pipe_code_digest

        diagnostic = (metrics or {}).get("privateDiagnosticRaster")
        with Image.open(io.BytesIO(png)) as raster:
            raster_size = raster.size
        if (private_diagnostic_profile != "fixed-glyph-dpr2/v1"
                or not isinstance(diagnostic, dict)
                or diagnostic.get("profile") != private_diagnostic_profile
                or diagnostic.get("logicalCanvasPx") != [1280, 720]
                or diagnostic.get("rasterCanvasPx") != [2560, 1440]
                or diagnostic.get("devicePixelRatio") != 2
                or diagnostic.get("pipeCodeDigest") != private_diagnostic_pipe_code_digest()
                or diagnostic.get("canonicalDigest") != "sha256:" + hashlib.sha256(canonical_svg.encode()).hexdigest()
                or diagnostic.get("displayDigest") != fixed_glyph_display_digest
                or diagnostic.get("renderDigest") != fixed_glyph_render_digest
                or diagnostic.get("projectionDigest") != fixed_glyph_projection_digest
                or diagnostic.get("fontCssDigest") != fixed_glyph_font_css_digest
                or diagnostic.get("authorityDigest") != fixed_glyph_authority_digest
                or diagnostic.get("materializerCodeDigest") != fixed_glyph_code_digest
                or diagnostic.get("pngDigest") != "sha256:" + hashlib.sha256(png).hexdigest()
                or raster_size != (2560, 1440)):
            raise RendererIsolationError("svg template glyph diagnostic raster mismatch")
    if metrics is not None and fixed_glyph_preview_ref is None:
        _cache_browser_metrics(
            measured_html, metrics, width_px=width_px, height_px=height_px
        )
    return png, metrics


async def render_with_lease(  # noqa: ASYNC109
    deck_scope: str,
    html: str,
    timeout: int = 60,
    *,
    render_fn: Any = None,
    width_px: int = DEFAULT_CANVAS_WIDTH_PX,
    height_px: int = DEFAULT_CANVAS_HEIGHT_PX,
) -> tuple[bytes, Mapping[str, Any] | None]:
    """Render one authored slide, batching same-deck renders behind a lease.

    Uses a :class:`~utils.slide_capture_worker_lease.RendererWorkerLease` keyed
    by the opaque ``deck_scope`` when ``AUTHORED_RENDERER_LEASE_ENABLED`` is on
    and this process requires the external isolated worker boundary (see
    ``renderer_worker_policy.renderer_requires_external_worker``). When the
    lease knob is off, or this process is not in "requires external worker"
    mode (development's reusable-Chrome path already amortizes cold starts on
    its own), this degrades to exactly ``_render_html_in_isolated_worker``.

    How the lease renders depends on the framed-protocol knob:

    * ``AUTHORED_RENDERER_FRAMED_PROTOCOL_ENABLED`` on -- the lease owns up to
      ``AUTHORED_RENDERER_LEASE_POOL_SIZE`` framed worker sessions, each
      keeping one worker process and one Chromium alive across its renders.
      This is where the batching actually saves a cold start; it requires a
      launcher and worker image that speak the framed protocol
      (``utils.renderer_frame_codec``), which is why it is opt-in.
    * off -- the lease performs the same one-process-per-render isolated-worker
      exchange as before, so it only groups bookkeeping.

    Either way each render is admitted, digest-bound to its response, and
    served by a fresh browser context and target inside the worker.

    ``render_fn`` overrides the single-shot exchange this falls back to (and
    the one a non-framed lease performs). ``utils.slide_capture`` passes its
    own module-level ``_render_html_in_isolated_worker`` so the renderer's
    isolation tests keep their existing monkeypatch seam.
    """

    exchange = _render_html_in_isolated_worker if render_fn is None else render_fn

    async def dimensioned_exchange(value: str, value_timeout: int):
        if is_default_landscape(width_px, height_px):
            return await exchange(value, value_timeout)
        return await exchange(
            value, value_timeout, width_px=width_px, height_px=height_px
        )
    if not (is_authored_renderer_lease_enabled() and renderer_requires_external_worker()):
        return await dimensioned_exchange(html, timeout)
    if is_authored_renderer_framed_protocol_enabled():
        lease = await acquire_lease(
            deck_scope,
            dimensioned_exchange,
            session_factory=lambda: RendererWorkerSession(
                max_requests=get_authored_renderer_lease_max_renders(),
                width_px=width_px,
                height_px=height_px,
            ),
            pool_size=get_authored_renderer_lease_pool_size(),
        )
    else:
        lease = await acquire_lease(deck_scope, dimensioned_exchange)
    return await lease.render(html, timeout)
