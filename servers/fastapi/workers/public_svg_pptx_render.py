"""Read-only final Office renders, isolated from the API and source converter."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

SCHEMA = "svg-final-pptx-office-render/v1"
MINIMUM_AVAILABLE_MEMORY = 8 * 1024**3


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _available_memory():
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    *[(name, ctypes.c_ulonglong) for name in (
                        "totalPhysical", "availablePhysical", "totalPage", "availablePage",
                        "totalVirtual", "availableVirtual", "availableExtended")]]
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ValueError("svg_final_pptx_memory_unavailable")
    return status.availablePhysical


def _render(request):
    import pythoncom
    import win32com.client
    from PIL import Image

    available = _available_memory()
    if available < MINIMUM_AVAILABLE_MEMORY:
        raise ValueError("svg_final_pptx_memory_insufficient")
    target = Path(request["pptx"])
    before = _sha(target)
    pythoncom.CoInitialize()
    document = application = None
    try:
        application = win32com.client.DispatchEx("PowerPoint.Application")
        document = application.Presentations.Open(str(target.resolve()),
            ReadOnly=True, Untitled=False, WithWindow=False)
        office_version = str(application.Version)
        if document.Slides.Count != request["pageCount"]:
            raise ValueError("svg_final_pptx_page_count_mismatch")
        width, height = request["widthPx"], request["heightPx"]
        pages = []
        for index in range(request["pageCount"]):
            output = Path(request["outputDirectory"]) / f"page-{index:03d}.png"
            document.Slides(index + 1).Export(str(output.resolve()), "PNG", width, height)
            with Image.open(output) as rendered:
                if rendered.format != "PNG" or rendered.size != (width, height):
                    raise ValueError("svg_final_pptx_render_size_mismatch")
                rendered.verify()
            pages.append(dict(index=index, pngSha256=_sha(output), widthPx=width, heightPx=height))
    finally:
        try:
            if document is not None:
                document.Close()
        finally:
            # The COM server may share a user's PowerPoint instance.
            application = document = None
            pythoncom.CoUninitialize()
    after = _sha(target)
    if before != after:
        raise ValueError("svg_final_pptx_hash_changed")
    return dict(schemaVersion=SCHEMA, status="measured", artifactDigest="sha256:" + before,
        artifactDigestAfter="sha256:" + after, hashInvariant=True,
        openReadOnly=True, openWithWindow=False, availablePhysicalMemoryBytes=available,
        renderer="Microsoft.PowerPoint", rendererVersion=office_version,
        checkerCodeDigest=str(request["rendererVersion"]),
        pageCount=len(pages), pages=pages)


def render_final_pptx(target, output_directory, *, page_count, width_px, height_px, timeout):
    unavailable = dict(schemaVersion=SCHEMA, status="unavailable")
    if os.name != "nt":
        return {**unavailable, "reasonCode": "svg_final_pptx_office_unavailable"}
    if timeout <= 0:
        return {**unavailable, "reasonCode": "svg_final_pptx_render_deadline_exhausted"}
    output_directory.mkdir(parents=True, exist_ok=True)
    request = dict(pptx=str(target.resolve()), outputDirectory=str(output_directory.resolve()),
        pageCount=page_count, widthPx=width_px, heightPx=height_px,
        rendererVersion=_sha(__file__))
    request_path, result_path = output_directory / "request.json", output_directory / "result.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    try:
        child = subprocess.run([sys.executable, "-m", "workers.public_svg_pptx_render",
            str(request_path), str(result_path)], capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if child.returncode != 0 or not result_path.is_file():
            return {**unavailable, "reasonCode": "svg_final_pptx_render_failed"}
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("artifactDigest") != "sha256:" + _sha(target):
            return {**unavailable, "reasonCode": "svg_final_pptx_render_binding_invalid"}
        return result
    except subprocess.TimeoutExpired:
        return {**unavailable, "reasonCode": "svg_final_pptx_render_timeout"}


if __name__ == "__main__":
    result = _render(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    Path(sys.argv[2]).write_text(json.dumps(result), encoding="utf-8")
