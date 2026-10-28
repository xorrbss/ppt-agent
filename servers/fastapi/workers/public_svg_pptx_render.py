"""Read-only final Office renders, isolated from the API and source converter."""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from time import monotonic, sleep
import uuid

SCHEMA = "svg-final-pptx-office-render/v1"
MINIMUM_AVAILABLE_MEMORY = 8 * 1024**3
RENDER_MUTEX_NAME = "Local\\PresentonSvgFinalPptxRender"
MINIMUM_RENDER_BUDGET_SECONDS = 60
LOCK_WAIT_SLICE_SECONDS = 0.1
SHARED_INSTANCE_REASON = "svg_final_pptx_office_instance_busy"
ACTIVATION_TIMEOUT_SECONDS = 30
_RENDER_STAGES = frozenset(
    {"create", "assign_job", "resume", "get_active_object", "hwnd_pid_match", "open", "export", "close"}
)
_RENDER_STAGE_CALLBACK = ContextVar("svg_render_stage_callback", default=None)


class SharedPowerPointInstance(ValueError):
    """A desktop, orphan or other worker owns the account's Office server."""


class PowerPointActivationUnavailable(RuntimeError):
    """The owned Office process did not become available for automation."""


def _unavailable(reason, *, retryable=False):
    result = dict(schemaVersion=SCHEMA, status="unavailable", reasonCode=reason)
    if retryable:
        result["retryable"] = True
    return result


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


def _powerpoint_pids():
    import pywintypes
    import win32api
    import win32process

    pids = set()
    for pid in win32process.EnumProcesses():
        process = None
        try:
            process = win32api.OpenProcess(0x0400 | 0x0010, False, pid)
            if Path(win32process.GetModuleFileNameEx(process, 0)).name.lower() == "powerpnt.exe":
                pids.add(pid)
        except (OSError, pywintypes.error):
            # Protected/system processes may not be inspectable. Ownership is
            # still proved by the retained launch handle and exact COM PID.
            continue
        finally:
            if process is not None:
                process.Close()
    return pids


def _powerpoint_executable():
    import winreg

    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\POWERPNT.EXE"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                    executable = Path(winreg.QueryValueEx(key, None)[0].strip('"'))
                if executable.is_file():
                    return str(executable)
            except OSError:
                continue
    raise PowerPointActivationUnavailable("PowerPoint executable unavailable")


def _normalize_hwnd(value):
    """Convert PowerPoint's signed 32-bit COM HWND value to a Python int."""

    try:
        hwnd = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("svg_final_pptx_hwnd_unavailable") from error
    # Application.HWND is exposed by Office as a 32-bit signed Long.  A valid
    # handle whose high bit is set therefore arrives sign-extended as negative.
    return hwnd & 0xFFFFFFFF if hwnd < 0 else hwnd


def _mark_render_stage(stage, callback=None):
    callback = callback or _RENDER_STAGE_CALLBACK.get()
    if callback is not None:
        callback(stage)


@contextmanager
def _owned_powerpoint(request, *, stage_callback=None):
    """Contain the explicitly created process before its first instruction."""
    import win32event
    import win32job
    import win32process

    if _powerpoint_pids():
        raise SharedPowerPointInstance(SHARED_INSTANCE_REASON)
    executable = _powerpoint_executable()
    job = win32job.OpenJobObject(win32job.JOB_OBJECT_ASSIGN_PROCESS | win32job.JOB_OBJECT_QUERY,
        False, request["jobName"])
    process = thread = None
    try:
        startup = win32process.STARTUPINFO()
        startup.dwFlags |= 1  # STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        _mark_render_stage("create", stage_callback)
        process, thread, pid, _ = win32process.CreateProcess(executable,
            subprocess.list2cmdline([executable, "/automation"]), None, None, False,
            0x00000004 | 0x08000000, None, None, startup)  # suspended, no console
        # The renderer child normally passes its existing job to descendants.
        if not win32job.IsProcessInJob(process, job):
            _mark_render_stage("assign_job", stage_callback)
            win32job.AssignProcessToJobObject(job, process)
        job.Close()
        job = None
        _mark_render_stage("resume", stage_callback)
        win32process.ResumeThread(thread)
        yield pid
    finally:
        try:
            if process is not None:
                try:
                    if win32event.WaitForSingleObject(process, 0) == win32event.WAIT_TIMEOUT:
                        win32process.TerminateProcess(process, 1)
                    win32event.WaitForSingleObject(process, 5000)
                finally:
                    process.Close()
        finally:
            if thread is not None:
                thread.Close()
            if job is not None:
                job.Close()


def _attach_powerpoint(pid, timeout, *, stage_callback=None):
    import pythoncom
    import win32com.client
    import win32process

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        try:
            # GetActiveObject cannot activate an unowned DCOM server. Never
            # fall back to Dispatch/DispatchEx if registration is unavailable.
            _mark_render_stage("get_active_object", stage_callback)
            application = win32com.client.GetActiveObject("PowerPoint.Application")
            _mark_render_stage("hwnd_pid_match", stage_callback)
            hwnd = _normalize_hwnd(application.HWND)
            _, attached_pid = win32process.GetWindowThreadProcessId(hwnd)
            if attached_pid != pid:
                raise SharedPowerPointInstance(SHARED_INSTANCE_REASON)
            return application
        except pythoncom.com_error:
            sleep(LOCK_WAIT_SLICE_SECONDS)
    raise PowerPointActivationUnavailable("PowerPoint activation deadline exceeded")


def _render(request, *, stage_callback=None):
    import pythoncom
    from PIL import Image

    available = _available_memory()
    if available < MINIMUM_AVAILABLE_MEMORY:
        raise ValueError("svg_final_pptx_memory_insufficient")
    target = Path(request["pptx"])
    before = _sha(target)
    stage = stage_callback or (lambda _name: None)
    token = _RENDER_STAGE_CALLBACK.set(stage)
    _mark_render_stage("create")
    pythoncom.CoInitialize()
    try:
        with _owned_powerpoint(request) as pid:
            application = _attach_powerpoint(pid, request["activationTimeout"])
            document = None
            try:
                (Path(request["outputDirectory"]) / "activated").write_text(str(pid), encoding="ascii")
                _mark_render_stage("open")
                document = application.Presentations.Open(str(target.resolve()),
                    ReadOnly=True, Untitled=False, WithWindow=False)
                office_version = str(application.Version)
                if document.Slides.Count != request["pageCount"]:
                    raise ValueError("svg_final_pptx_page_count_mismatch")
                width, height = request["widthPx"], request["heightPx"]
                pages = []
                for index in range(request["pageCount"]):
                    output = Path(request["outputDirectory"]) / f"page-{index:03d}.png"
                    _mark_render_stage("export")
                    document.Slides(index + 1).Export(str(output.resolve()), "PNG", width, height)
                    with Image.open(output) as rendered:
                        if rendered.format != "PNG" or rendered.size != (width, height):
                            raise ValueError("svg_final_pptx_render_size_mismatch")
                        rendered.verify()
                    pages.append(dict(index=index, pngSha256=_sha(output), widthPx=width, heightPx=height))
            finally:
                _mark_render_stage("close")
                try:
                    if document is not None:
                        document.Close()
                finally:
                    application.Quit()
    finally:
        application = document = None
        pythoncom.CoUninitialize()
        _RENDER_STAGE_CALLBACK.reset(token)
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


def render_final_pptx(target, output_directory, *, page_count, width_px, height_px, timeout,
                      lock_wait_timeout=None, cancellation_event=None):
    """Bound lock admission separately within the total review render deadline."""
    if os.name != "nt":
        return _unavailable("svg_final_pptx_office_unavailable")
    if timeout <= 0:
        return _unavailable("svg_final_pptx_render_deadline_exhausted")
    mutex = None
    acquired = False
    started = monotonic()
    render_deadline = started + timeout
    lock_deadline = started + min(timeout, timeout if lock_wait_timeout is None else lock_wait_timeout)
    try:
        import win32event
        # One account/session Office server serves every API/controller process.
        mutex = win32event.CreateMutex(None, False, RENDER_MUTEX_NAME)
        if cancellation_event is not None and cancellation_event.is_set():
            return _unavailable("svg_final_pptx_render_cancelled", retryable=True)
        state = win32event.WaitForSingleObject(mutex, 0)
        contended = state == win32event.WAIT_TIMEOUT
        while state == win32event.WAIT_TIMEOUT:
            if cancellation_event is not None and cancellation_event.is_set():
                return _unavailable("svg_final_pptx_render_cancelled", retryable=True)
            lock_remaining = lock_deadline - monotonic()
            if lock_remaining <= 0:
                return _unavailable("svg_final_pptx_render_busy", retryable=True)
            state = win32event.WaitForSingleObject(mutex,
                max(1, int(min(lock_remaining, LOCK_WAIT_SLICE_SECONDS) * 1000)))
        if state not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
            raise OSError("Office render mutex unavailable")
        acquired = True
        if cancellation_event is not None and cancellation_event.is_set():
            return _unavailable("svg_final_pptx_render_cancelled", retryable=True)
        remaining = render_deadline - monotonic()
        # A contending review must defer rather than launch a starved render.
        if remaining <= 0 or (contended and remaining < min(MINIMUM_RENDER_BUDGET_SECONDS, timeout)):
            return _unavailable("svg_final_pptx_render_busy", retryable=True)
        result = _render_serialized(target, output_directory, page_count=page_count,
            width_px=width_px, height_px=height_px, timeout=remaining)
        if contended and result.get("reasonCode") == "svg_final_pptx_render_timeout":
            return {**result, "retryable": True}
        return result
    except (ImportError, OSError):
        return _unavailable("svg_final_pptx_office_isolation_unavailable")
    finally:
        if mutex is not None:
            try:
                if acquired:
                    win32event.ReleaseMutex(mutex)
            finally:
                mutex.Close()


def _render_serialized(target, output_directory, *, page_count, width_px, height_px, timeout):
    unavailable = dict(schemaVersion=SCHEMA, status="unavailable")
    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    request = dict(pptx=str(target.resolve()), outputDirectory=str(output_directory.resolve()),
        pageCount=page_count, widthPx=width_px, heightPx=height_px,
        activationTimeout=min(ACTIVATION_TIMEOUT_SECONDS, timeout),
        rendererVersion=_sha(__file__), jobName=f"Local\\svg-final-review-{uuid.uuid4()}")
    request_path, result_path = output_directory / "request.json", output_directory / "result.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    activated_path = output_directory / "activated"
    activated_path.unlink(missing_ok=True)
    result_path.unlink(missing_ok=True)
    child = job = watchdog = None
    activation_timed_out = threading.Event()
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
            cwd=Path(__file__).resolve().parents[1],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        process = win32api.OpenProcess(0x0100 | 0x0001, False, child.pid)
        try:
            win32job.AssignProcessToJobObject(job, process)
        finally:
            process.Close()
        def expire_activation():
            if not activated_path.is_file():
                activation_timed_out.set()
                win32job.TerminateJobObject(job, 1)

        # An out-of-process deadline covers even a hung COM property read.
        watchdog = threading.Timer(request["activationTimeout"], expire_activation)
        watchdog.daemon = True
        watchdog.start()
        # Child cannot activate Office until both processes can join this job.
        child.communicate(input=b"render\n", timeout=timeout)
        if activation_timed_out.is_set():
            return _unavailable("svg_final_pptx_render_timeout", retryable=True)
        if child.returncode != 0 or not result_path.is_file():
            return _unavailable("svg_final_pptx_render_failed", retryable=not activated_path.is_file())
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("status") == "unavailable":
            return result
        if result.get("artifactDigest") != "sha256:" + _sha(target):
            return {**unavailable, "reasonCode": "svg_final_pptx_render_binding_invalid"}
        return result
    except subprocess.TimeoutExpired:
        return _unavailable("svg_final_pptx_render_timeout", retryable=not activated_path.is_file())
    except (ImportError, OSError):
        return {**unavailable, "reasonCode": "svg_final_pptx_office_isolation_unavailable"}
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join()
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


def _child_diagnostic(stage, error):
    diagnostic = {"stage": stage, "exceptionClass": type(error).__name__}
    for name in ("hresult", "winerror"):
        value = getattr(error, name, None)
        if type(value) is int:
            diagnostic[name] = value
    return diagnostic


def _child_failure(reason, error, *, stage, retryable=True):
    result = _unavailable(reason, retryable=retryable)
    result["diagnostic"] = _child_diagnostic(stage, error)
    return result


def _render_child(request_path, result_path):
    import pythoncom

    stage_holder = ["create"]

    def mark_stage(stage):
        if stage in _RENDER_STAGES:
            stage_holder[0] = stage

    try:
        token = _RENDER_STAGE_CALLBACK.set(mark_stage)
        try:
            result = _render(json.loads(Path(request_path).read_text(encoding="utf-8")))
        finally:
            _RENDER_STAGE_CALLBACK.reset(token)
    except SharedPowerPointInstance as error:
        result = _child_failure(SHARED_INSTANCE_REASON, error, stage=stage_holder[0])
    except pythoncom.com_error as error:
        result = _child_failure("svg_final_pptx_render_failed", error, stage=stage_holder[0])
    except (PowerPointActivationUnavailable, OSError, TypeError, ValueError) as error:
        result = _child_failure("svg_final_pptx_render_failed", error, stage=stage_holder[0])
    except Exception as error:
        # Keep the child result boundary total: a future COM/pywin32 exception
        # must not disappear as a nonzero child exit with no diagnostic.
        result = _child_failure("svg_final_pptx_render_failed", error, stage=stage_holder[0])
    Path(result_path).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    if sys.stdin.buffer.readline() != b"render\n":
        raise SystemExit(1)
    _render_child(sys.argv[1], sys.argv[2])
