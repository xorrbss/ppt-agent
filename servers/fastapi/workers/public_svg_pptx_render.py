"""Read-only final Office renders, isolated from the API and source converter."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

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
    import win32api
    import win32job
    import win32process
    from PIL import Image

    available = _available_memory()
    if available < MINIMUM_AVAILABLE_MEMORY:
        raise ValueError("svg_final_pptx_memory_insufficient")
    target = Path(request["pptx"])
    before = _sha(target)
    pythoncom.CoInitialize()
    document = application = None
    owns_application = False
    existing_processes = set(win32process.EnumProcesses())
    try:
        application = win32com.client.DispatchEx("PowerPoint.Application")
        _, pid = win32process.GetWindowThreadProcessId(application.HWND)
        # DispatchEx can return a shared Office server. Never open, quit or kill it.
        if pid in existing_processes:
            raise ValueError("svg_final_pptx_office_isolation_unavailable")
        owns_application = True
        job = win32job.OpenJobObject(win32job.JOB_OBJECT_ASSIGN_PROCESS, False, request["jobName"])
        process = win32api.OpenProcess(0x0100 | 0x0001, False, pid)
        try:
            win32job.AssignProcessToJobObject(job, process)
        finally:
            process.Close()
            job.Close()
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
            try:
                if owns_application:
                    application.Quit()
            finally:
                application = document = None
                pythoncom.CoUninitialize()
    after = _sha(target)
    if before != after:
        raise ValueError("svg_final_pptx_hash_changed")
    return dict(schemaVersion=SCHEMA, status="measured", artifactDigest="sha256:" + before,
        artifactDigestAfter="sha256:" + after, hashInvariant=True,
        openReadOnly=True, openWithWindow=False, availablePhysicalMemoryBytes=available,
        renderIsolation="windows-job-object", rendererQuit=True,
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
        rendererVersion=_sha(__file__), jobName=f"Local\\svg-final-review-{uuid.uuid4()}")
    request_path, result_path = output_directory / "request.json", output_directory / "result.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    child = job = None
    try:
        import win32api
        import win32job
        job = win32job.CreateJobObject(None, request["jobName"])
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        child = subprocess.Popen([sys.executable, "-m", "workers.public_svg_pptx_render",
            str(request_path), str(result_path)], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        process = win32api.OpenProcess(0x0100 | 0x0001, False, child.pid)
        try:
            win32job.AssignProcessToJobObject(job, process)
        finally:
            process.Close()
        # Child cannot activate Office until both processes can join this job.
        child.communicate(input=b"render\n", timeout=timeout)
        if child.returncode != 0 or not result_path.is_file():
            return {**unavailable, "reasonCode": "svg_final_pptx_render_failed"}
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("artifactDigest") != "sha256:" + _sha(target):
            return {**unavailable, "reasonCode": "svg_final_pptx_render_binding_invalid"}
        return result
    except subprocess.TimeoutExpired:
        return {**unavailable, "reasonCode": "svg_final_pptx_render_timeout"}
    except (ImportError, OSError):
        return {**unavailable, "reasonCode": "svg_final_pptx_office_isolation_unavailable"}
    finally:
        try:
            if job is not None:
                try:
                    win32job.TerminateJobObject(job, 1)
                finally:
                    job.Close()
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.wait()
                if child.stdin is not None:
                    child.stdin.close()


if __name__ == "__main__":
    if sys.stdin.buffer.readline() != b"render\n":
        raise SystemExit(1)
    result = _render(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    Path(sys.argv[2]).write_text(json.dumps(result), encoding="utf-8")
