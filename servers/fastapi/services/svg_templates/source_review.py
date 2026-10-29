"""Owner-bound source/frame review evidence from actual rendered images."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import subprocess
import sys
import os
from pathlib import Path
import platform
import tempfile
import time
import uuid
from io import BytesIO

from PIL import Image, ImageChops, ImageStat

from utils.powerpoint_ownership import claim_empty_powerpoint, process_identity as _process_identity

from services.svg_templates.frame_fonts import artifact_digest, bytes_digest, resolve_font_manifest


def _require_installed_fonts(manifest) -> None:
    # PowerPoint resolves system-installed faces; never change the user's font registry.
    if os.name != "nt":
        raise ValueError("svg_template_powerpoint_unavailable")
    import winreg
    needed = {asset.digest for asset, _ in resolve_font_manifest(manifest)}
    found = set()
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            key = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        except OSError:
            continue
        with key:
            for index in range(winreg.QueryInfoKey(key)[1]):
                try:
                    _name, raw_path, _kind = winreg.EnumValue(key, index)
                    path = Path(raw_path)
                    if not path.is_absolute():
                        path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / path
                    if path.is_file() and path.stat().st_size <= 64 * 1024 * 1024:
                        digest = bytes_digest(path.read_bytes())
                        if digest in needed:
                            found.add(digest)
                except (OSError, TypeError):
                    continue
    if needed != found:
        raise ValueError("svg_template_powerpoint_fonts_unavailable")


def _existing_powerpoint_processes():
    """Include hidden processes so another user's automation is never adopted."""
    import ctypes
    from ctypes import wintypes
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t), ("module", wintypes.DWORD),
            ("threads", wintypes.DWORD), ("parent", wintypes.DWORD),
            ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
            ("name", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    for name in ("Process32FirstW", "Process32NextW"):
        getattr(kernel, name).argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
        getattr(kernel, name).restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ValueError("svg_template_powerpoint_ownership_unproven")
    try:
        entry = ProcessEntry(size=ctypes.sizeof(ProcessEntry))
        present = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while present:
            if entry.name.casefold() == "powerpnt.exe":
                return True
            present = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ValueError("svg_template_powerpoint_ownership_unproven")
        return False
    finally:
        kernel.CloseHandle(snapshot)


def _claim_empty_powerpoint(application, *, started_at):
    # Preserve the existing source-worker identity payload and injectable reader.
    return claim_empty_powerpoint(application, started_at=started_at, identity_reader=_process_identity)


def _render_source(path: Path, package, *, ownership_path=None):
    if bytes_digest(path.read_bytes()) != package.source_digest:
        raise ValueError("svg_template_source_digest_mismatch")
    _require_installed_fonts(package.font_manifest)
    from services.presentation_export_powerpoint_qa import _powerpoint_executable_available
    if not _powerpoint_executable_available():
        raise ValueError("svg_template_powerpoint_unavailable")
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    application = deck = None
    owned = False
    try:
        if _existing_powerpoint_processes():
            raise ValueError("svg_template_powerpoint_busy")
        started_at = time.time_ns() // 100 + 116444736000000000
        application = win32com.client.DispatchEx("PowerPoint.Application")
        identity = _claim_empty_powerpoint(application, started_at=started_at)
        owned = True
        if ownership_path is not None:
            marker = Path(ownership_path)
            temporary_marker = marker.with_suffix(".tmp")
            temporary_marker.write_text(json.dumps(identity), encoding="utf-8")
            os.replace(temporary_marker, marker)
        application.DisplayAlerts = 1
        version = str(application.Version)
        deck = application.Presentations.Open(str(path), True, False, False)
        images = {}
        with tempfile.TemporaryDirectory(prefix="svg-template-review-") as directory:
            for page in sorted({frame.source_page for frame in package.frames}):
                if page >= int(deck.Slides.Count):
                    raise ValueError("svg_template_source_page_missing")
                output = Path(directory) / f"page-{page}.png"
                deck.Slides(page + 1).Export(str(output), "PNG", package.source_canvas.width_px,
                                             package.source_canvas.height_px)
                images[page] = output.read_bytes()
        return version, images
    finally:
        if owned and deck is not None:
            try:
                deck.Close()
            except Exception:
                pass
        if owned and application is not None:
            try:
                if int(application.Presentations.Count) == 0:
                    application.Quit()
                elif ownership_path is not None:
                    # A newly opened user document revokes process cleanup.
                    Path(ownership_path).write_text('{"revoked":true}', encoding="utf-8")
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _terminate_owned_powerpoint(identity):
    if not isinstance(identity, dict) or set(identity) != {"pid", "creation"}:
        return
    pid = identity.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or _process_identity(pid) != identity:
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.TerminateProcess.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1001, False, pid)
    if not handle:
        return
    try:
        # Check on the same process handle to avoid PID reuse between lookup and termination.
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME))
        if (kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel_time), ctypes.byref(user_time)) and
                ((created.dwHighDateTime << 32) | created.dwLowDateTime) == identity["creation"]):
            kernel.TerminateProcess(handle, 1)
    finally:
        kernel.CloseHandle(handle)


async def _render_source_isolated(path, package, frame):
    with tempfile.TemporaryDirectory(prefix="svg-template-source-worker-") as directory:
        ownership = Path(directory) / "owned.json"
        request = {"path": str(path), "source_digest": package.source_digest,
            "canvas": package.source_canvas.model_dump(mode="json"),
            "frame": frame.model_dump(mode="json"), "ownership_path": str(ownership)}
        options = {"stdin": asyncio.subprocess.PIPE, "stdout": asyncio.subprocess.PIPE,
                   "stderr": asyncio.subprocess.DEVNULL,
                   "cwd": str(Path(__file__).resolve().parents[2])}
        if os.name == "nt":
            options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = await asyncio.create_subprocess_exec(sys.executable, "-m",
            "services.svg_templates.source_review_worker", **options)
        try:
            output, _ = await asyncio.wait_for(process.communicate(json.dumps(request).encode()), timeout=60)
            if process.returncode != 0 or len(output) > 48 * 1024 * 1024:
                raise ValueError("svg_template_review_render_unavailable")
            value = json.loads(output)
            if not isinstance(value, dict) or value.get("status") != "ok":
                raise ValueError(value.get("reason", "svg_template_review_render_unavailable"))
            return value["version"], base64.b64decode(value["png"], validate=True)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            try:
                identity = json.loads(ownership.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                identity = None
            if identity is not None:
                _terminate_owned_powerpoint(identity)

def _compare_fixed_regions(source: bytes, rendered: bytes, frame):
    with Image.open(BytesIO(source)) as a, Image.open(BytesIO(rendered)) as b:
        source_image = a.convert("RGB")
        frame_image = b.convert("RGB")
    if source_image.size != frame_image.size:
        raise ValueError("svg_template_review_image_size_mismatch")
    width, height = source_image.size
    results = []
    for element in frame.elements:
        if element.layer != "foreground":
            continue
        rect = element.bounds
        left = max(0, int(rect.x))
        top = max(0, int(rect.y))
        right = min(width, int(rect.x + rect.width + 0.999999))
        bottom = min(height, int(rect.y + rect.height + 0.999999))
        if right <= left or bottom <= top:
            raise ValueError("svg_template_review_region_invalid")
        diff = ImageChops.difference(source_image.crop((left, top, right, bottom)),
                                     frame_image.crop((left, top, right, bottom)))
        mean = ImageStat.Stat(diff).mean
        results.append({"element_id": element.element_id,
                        "bounds": rect.model_dump(mode="json"),
                        "mean_absolute_channel_error": sum(mean) / 3})
    if not results:
        raise ValueError("svg_template_review_regions_missing")
    return {"method": "fixed-region-pixel-diff/v1", "regions": results}


def _pdf_rasterizer_identity():
    import inspect
    import pypdfium2 as pdfium
    import pypdfium2_raw.bindings as raw_bindings
    import PIL
    from PIL import ImageFile, PngImagePlugin, _imaging
    library = Path(raw_bindings.__file__).resolve().parent / "pdfium.dll"
    if not library.is_file():
        raise ValueError("svg_template_source_pdf_rasterizer_unavailable")
    return {"kind": "pdfium", "version": str(pdfium.PDFIUM_INFO.version),
            "helper_version": str(pdfium.PYPDFIUM_INFO.version),
            "pillow_version": PIL.__version__,
            "library_digest": bytes_digest(library.read_bytes()),
            "helper_digest": bytes_digest(Path(inspect.getfile(pdfium.PdfPage)).read_bytes()),
            "bitmap_helper_digest": bytes_digest(Path(inspect.getfile(pdfium.PdfBitmap)).read_bytes()),
            "raw_binding_digest": bytes_digest(Path(raw_bindings.__file__).read_bytes()),
            "png_encoder_digest": bytes_digest(Path(PngImagePlugin.__file__).read_bytes()),
            "image_file_digest": bytes_digest(Path(ImageFile.__file__).read_bytes()),
            "image_core_digest": bytes_digest(Path(_imaging.__file__).read_bytes()),
            "flags": {"rotation": 0, "crop": [0, 0, 0, 0], "may_draw_forms": False,
                "fill_color": [255, 255, 255, 255], "grayscale": False, "draw_annots": False,
                "optimize_mode": None, "no_smoothtext": False, "no_smoothimage": False,
                "no_smoothpath": False, "force_halftone": False, "limit_image_cache": False,
                "rev_byteorder": False, "prefer_bgrx": False, "maybe_alpha": False,
                "color_scheme": None, "fill_to_stroke": False,
                "extra_flags": 0, "force_bitmap_format": int(pdfium.raw.FPDFBitmap_BGRA)},
            "png_encoder": {"format": "PNG", "optimize": False, "compress_level": 6,
                            "icc_profile": None, "pnginfo": None},
            "code_digest": bytes_digest(Path(__file__).read_bytes())}


def _render_pdf_source(pdf_bytes, frame):
    import pypdfium2 as pdfium
    from services.svg_templates.frame_glyphs import validate_frame_glyph_authority
    authority = validate_frame_glyph_authority(frame)
    if bytes_digest(pdf_bytes) != authority.source_pdf_digest:
        raise ValueError("svg_template_source_pdf_digest_mismatch")
    identity = _pdf_rasterizer_identity()
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        page = pdf[frame.source_page]
        bitmap = image = None
        try:
            bitmap = page.render(scale=frame.source_canvas.width_px / authority.physical_canvas_pt[0],
                                 bitmap_maker=pdfium.PdfBitmap.new_native, **identity["flags"])
            image = bitmap.to_pil()
            if image.size != (frame.source_canvas.width_px, frame.source_canvas.height_px):
                raise ValueError("svg_template_review_image_size_mismatch")
            output = BytesIO()
            image.save(output, **identity["png_encoder"])
            return output.getvalue(), identity
        finally:
            if image is not None:
                image.close()
            if bitmap is not None:
                bitmap.close()
            page.close()
    finally:
        pdf.close()


async def prepare_source_review(session, package, source_path, operation_id, *, captured_pdf=None, capture_receipt=None):
    """Render exact source and frame pairs; only a later human decision can approve them."""
    from services.svg_templates.catalog import principal
    from services.artifact_store import configured_public_artifact_service
    from services.svg_renderer import render_template_frame_for_review
    owner_id, tenant_id = principal()
    prepared = []
    blocked = []
    source_images = {}
    versions = set()
    glyph_preview = package.schema_version == "svg-template-package/v3"
    rasterizer = None
    if glyph_preview:
        if (not isinstance(captured_pdf, bytes) or not isinstance(capture_receipt, dict)
                or capture_receipt.get("status") != "captured"
                or capture_receipt.get("source_digest") != package.source_digest
                or capture_receipt.get("pdf_digest") != bytes_digest(captured_pdf)
                or str(capture_receipt.get("operation_id")) != str(operation_id)):
            raise ValueError("svg_template_source_pdf_capture_binding_mismatch")
    for frame in package.frames:
        try:
            if glyph_preview:
                from services.svg_templates.frame_glyphs import validate_frame_glyph_authority
                authority = validate_frame_glyph_authority(frame)
                if authority.capture_receipt_digest != artifact_digest(capture_receipt):
                    raise ValueError("svg_template_source_pdf_capture_binding_mismatch")
                source_image, current_rasterizer = await asyncio.to_thread(_render_pdf_source, captured_pdf, frame)
                if rasterizer is not None and rasterizer != current_rasterizer:
                    raise ValueError("svg_template_review_environment_changed")
                rasterizer = current_rasterizer
                version = capture_receipt["producer_version"]
            else:
                version, source_image = await _render_source_isolated(Path(source_path), package, frame)
            image = await render_template_frame_for_review(frame)
            comparison = _compare_fixed_regions(source_image, image, frame)
            source_images[frame.frame_id] = source_image
            prepared.append((frame, image, comparison))
            versions.add(version)
        except Exception as error:
            safe_codes = {"svg_template_powerpoint_unavailable", "svg_template_powerpoint_fonts_unavailable", "svg_template_powerpoint_busy",
                "svg_template_powerpoint_ownership_unproven", "svg_template_source_digest_mismatch",
                "svg_template_source_page_missing", "svg_template_review_image_size_mismatch",
                "svg_template_review_region_invalid", "svg_template_review_regions_missing"}
            code = str(error) if str(error) in safe_codes else "svg_template_review_render_unavailable"
            blocked.append({"frame_id": frame.frame_id, "frame_digest": frame.digest,
                "font_manifest_digest": frame.font_manifest.digest, "status": "blocked", "reason": code})
    if not prepared:
        return {"status": "blocked", "frames": blocked, "reason": "svg_template_review_render_unavailable"}, {}
    if len(versions) != 1:
        return {"status": "blocked", "frames": blocked, "reason": "svg_template_review_environment_changed"}, {}
    version = next(iter(versions))

    service = configured_public_artifact_service()
    source_refs = {}
    previews = {}
    for frame_id, payload in source_images.items():
        ref = await service.persist_bytes(session, owner_id=owner_id, tenant_id=tenant_id,
            kind="image-derivative", payload=payload, media_type="image/png",
            created_by_operation_id=operation_id, retention_class="published")
        source_refs[frame_id] = ref
        source_page = next(frame.source_page for frame in package.frames if frame.frame_id == frame_id)
        previews[str(source_page)] = "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
    frames = list(blocked)
    for frame, image, comparison in prepared:
        ref = await service.persist_bytes(session, owner_id=owner_id, tenant_id=tenant_id,
            kind="image-derivative", payload=image, media_type="image/png",
            created_by_operation_id=operation_id, retention_class="published")
        source_digest = bytes_digest(source_images[frame.frame_id])
        frame_digest = bytes_digest(image)
        previews["frame:" + frame.frame_id] = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
        region_digest = artifact_digest({"frame_digest": frame.digest,
            "regions": [e.bounds.model_dump(mode="json") for e in frame.elements if e.layer == "foreground"],
            "source_image_digest": source_digest, "frame_image_digest": frame_digest})
        entry = {"frame_id": frame.frame_id, "frame_digest": frame.digest,
            "font_manifest_digest": frame.font_manifest.digest,
            "source_environment_digest": artifact_digest({"system": platform.platform(),
                "powerpoint_version": version, "font_manifest_digest": frame.font_manifest.digest}),
            "source_image_digest": source_digest, "frame_image_digest": frame_digest,
            "source_image_ref": {"artifact_id": str(source_refs[frame.frame_id].artifact_id),
                                 "sha256": source_refs[frame.frame_id].sha256},
            "frame_image_ref": {"artifact_id": str(ref.artifact_id), "sha256": ref.sha256}, "region_review_digest": region_digest,
            "comparison": comparison, "review": {"method": "human-source-frame-comparison",
                "classification_approved": False, "preservation_approved": False,
                "reviewed_image_pair_digest": artifact_digest({"source_image_digest": source_digest,
                    "frame_image_digest": frame_digest}), "decision_id": ""}}
        if glyph_preview:
            authority = frame.fixed_glyph_preview
            entry.update(source_image_origin="captured-source-pdf", source_page=frame.source_page,
                source_pdf_digest=authority.source_pdf_digest, capture_receipt_digest=authority.capture_receipt_digest,
                fixed_glyph_preview_digest=authority.authority_digest,
                source_canvas_digest=authority.source_canvas_digest, rasterizer_digest=artifact_digest(rasterizer),
                raster_scale=frame.source_canvas.width_px / authority.physical_canvas_pt[0])
        frames.append(entry)
    evidence = {"schema_version": "svg-template-review/v1", "source_digest": package.source_digest,
        "package_digest": package.digest, "renderer": {"kind": "powerpoint", "version": version,
        "environment_digest": artifact_digest({"system": platform.platform(), "powerpoint_version": version,
            "font_manifest_digest": package.font_manifest.digest}),
        "font_manifest_digest": package.font_manifest.digest}, "frames": frames}
    if glyph_preview:
        authority = package.frames[0].fixed_glyph_preview
        evidence.update(schema_version="svg-template-review/v3", source_review_domain="powerpoint-pdf/pdfium/v1")
        evidence["renderer"] = {"kind": "powerpoint-pdf", "version": version,
            "producer_build": capture_receipt["producer_build"], "source_pdf_digest": authority.source_pdf_digest,
            "capture_receipt_digest": authority.capture_receipt_digest, "capture_operation_id": str(operation_id),
            "rasterizer": rasterizer, "font_manifest_digest": package.font_manifest.digest,
            "source_canvas_digest": artifact_digest(package.source_canvas)}
        evidence["renderer"]["environment_digest"] = artifact_digest(evidence["renderer"])
        from services.svg_templates.frame_review import review_image_pair_digest
        for entry in frames:
            if entry.get("status") != "blocked":
                entry["source_environment_digest"] = evidence["renderer"]["environment_digest"]
                entry["review"]["reviewed_image_pair_digest"] = review_image_pair_digest(evidence, entry)
    evidence["digest"] = artifact_digest(evidence)
    return evidence, previews


async def verify_source_review_assets(session, evidence):
    """Resolve each exact image against the current owner and tenant before publication."""
    from services.svg_templates.catalog import principal
    from services.artifact_store import configured_public_artifact_service

    owner_id, tenant_id = principal()
    if evidence.get("schema_version") == "svg-template-review/v3":
        from services.svg_templates.frame_review import validate_pdf_review_provenance
        from services.svg_templates.source_pdf_worker import verify_capture_receipt
        for entry in evidence.get("frames", []):
            if isinstance(entry, dict) and entry.get("status") != "blocked":
                validate_pdf_review_provenance(evidence, entry)
        renderer = evidence.get("renderer", {})
        receipt = verify_capture_receipt(renderer.get("capture_operation_id"), owner_id=owner_id, tenant_id=tenant_id,
            expected_digest=renderer.get("capture_receipt_digest"),
            expected_pdf_digest=renderer.get("source_pdf_digest"), expected_source_digest=evidence.get("source_digest"))
        if (renderer.get("version") != receipt.get("producer_version")
                or renderer.get("producer_build") != receipt.get("producer_build")):
            raise ValueError("svg_template_source_pdf_review_producer_mismatch")
    service = configured_public_artifact_service()
    frames = evidence.get("frames") if isinstance(evidence, dict) else None
    if not isinstance(frames, list) or not frames:
        raise ValueError("svg_template_review_image_evidence_missing")
    checked = set()
    for frame in frames:
        if isinstance(frame, dict) and frame.get("status") == "blocked":
            continue
        if not isinstance(frame, dict):
            raise ValueError("svg_template_review_image_evidence_missing")
        for role in ("source", "frame"):
            image_ref = frame.get(role + "_image_ref")
            expected = frame.get(role + "_image_digest")
            if (not isinstance(image_ref, dict) or set(image_ref) != {"artifact_id", "sha256"}
                    or not isinstance(expected, str) or expected != "sha256:" + str(image_ref.get("sha256"))):
                raise ValueError("svg_template_review_image_evidence_missing")
            try:
                artifact_id = uuid.UUID(image_ref["artifact_id"])
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError("svg_template_review_image_evidence_missing") from error
            key = (artifact_id, expected)
            if key in checked:
                continue
            ref = await service.resolve_ref(session, artifact_id=artifact_id, owner_id=owner_id,
                tenant_id=tenant_id, expected_digest=image_ref["sha256"], check_object=False)
            if (evidence.get("schema_version") == "svg-template-review/v3"
                    and str(ref.created_by_operation_id) != str(evidence["renderer"]["capture_operation_id"])):
                raise ValueError("svg_template_source_pdf_review_artifact_lineage_mismatch")
            if ref.kind != "image-derivative" or ref.media_type != "image/png":
                raise ValueError("svg_template_review_image_evidence_missing")
            payload, _proof = await service.read_verified_ref_bytes(ref, max_bytes=32 * 1024 * 1024)
            if bytes_digest(payload) != expected:
                raise ValueError("svg_template_review_image_digest_mismatch")
            try:
                with Image.open(BytesIO(payload)) as image:
                    if image.format != "PNG":
                        raise ValueError("svg_template_review_image_invalid")
                    image.verify()
            except (OSError, SyntaxError) as error:
                raise ValueError("svg_template_review_image_invalid") from error
            checked.add(key)
