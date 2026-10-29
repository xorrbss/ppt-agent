"""One-attempt owner-bound source PDF capture in an owned Windows Job Object."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback
import uuid

from services.svg_templates.frame_fonts import artifact_digest, bytes_digest

MAX_PDF_BYTES = 64 * 1024 * 1024
_TYPELIB = "{91493440-5A91-11CF-8700-00AA0060263B}"
_PRESENTATION = "{9149349D-5A91-11CF-8700-00AA0060263B}"
_RANGE = "{9149345F-5A91-11CF-8700-00AA0060263B}"
_NAMES = ("Path", "FixedFormatType", "Intent", "FrameSlides", "HandoutOrder", "OutputType",
          "PrintHiddenSlides", "PrintRange", "RangeType", "SlideShowName", "IncludeDocProperties",
          "KeepIRMSettings", "DocStructureTags", "BitmapMissingFonts", "UseISO19005_1", "ExternalExporter")
_ARGS = ((8, 1), (3, 1), *((3, 49),) * 5, (9, 49), (3, 49), (8, 49),
         *((11, 49),) * 5, (12, 17))
_DEFAULTS = (None, None, 1, 0, 1, 1, 0, 0, 1, "", False, True, True, True, False, None)


def _require(value, reason="svg_template_source_pdf_capture_invalid"):
    if not value:
        raise ValueError(reason)


def _write_once(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())


def _diagnostic(error, stage):
    message = re.sub(r"(?i)[a-z]:[\\/][^\r\n]*", "<path>", str(error))
    message = re.sub(r"(?i)(api.?key|token|password|secret|authorization)\s*[:=]\s*\S+",
                     "<redacted>", message)
    result = {"stage": stage, "exception_class": type(error).__name__, "message_bounded": message[:320],
              "frames": [{"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                         for f in traceback.extract_tb(error.__traceback__)[-8:]]}
    for name in ("hresult", "winerror"):
        value = getattr(error, name, None)
        if type(value) is int:
            result[name] = value
    return result


def _typelib_binding():
    import pythoncom
    from win32com.client.build import DispatchItem
    library = pythoncom.LoadRegTypeLib(_TYPELIB, 2, 12, 0)
    path = Path(pythoncom.QueryPathOfRegTypeLib(_TYPELIB, 2, 12, 0).rstrip("\0"))
    _require(path.is_file(), "svg_template_source_pdf_typelib_unavailable")
    info = next(library.GetTypeInfo(i) for i in range(library.GetTypeInfoCount())
                if library.GetDocumentation(i)[0] == "_Presentation")
    _require(str(info.GetTypeAttr().iid).upper() == _PRESENTATION)
    entry = DispatchItem(info, info.GetTypeAttr()).mapFuncs["ExportAsFixedFormat"]
    args = tuple((int(v[0]), int(v[1])) for v in entry.desc.args)
    _require(entry.desc.memid == 2096 and tuple(entry.desc.rettype[:2]) == (24, 32))
    _require(tuple(entry.names[1:]) == _NAMES and args == _ARGS)
    _require(tuple(v[2] for v in entry.desc.args) == _DEFAULTS)
    _require(str(entry.desc.args[7][3]).upper() == _RANGE)
    return {"typelib_digest": bytes_digest(path.read_bytes()), "arguments": [list(v) for v in args],
            "defaults": list(_DEFAULTS), "names": list(_NAMES), "guid": _TYPELIB,
            "presentation_iid": _PRESENTATION, "print_range_iid": _RANGE,
            "dispid": 2096, "return_type": [24, 32], "version": "2.12"}


def _capture_code_identity():
    from workers import public_svg_pptx_render as renderer
    from utils import powerpoint_ownership
    return {"capture_code_digest": bytes_digest(Path(__file__).read_bytes()),
            "ownership_code_digest": bytes_digest(Path(renderer.__file__).read_bytes()),
            "window_code_digest": bytes_digest(Path(powerpoint_ownership.__file__).read_bytes())}


def _pdf_metadata(data, package):
    import pypdfium2 as pdfium
    _require(0 < len(data) <= MAX_PDF_BYTES and data.startswith(b"%PDF-"))
    pdf = pdfium.PdfDocument(data)
    try:
        physical = package.source_canvas.physical_size
        size = (physical.width_emu / 12700, physical.height_emu / 12700)
        _require(len(pdf) == max(f.source_page for f in package.frames) + 1)
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                _require(page.get_size() == size and page.get_rotation() == 0)
                _require(page.get_mediabox() == (0, 0, *size) and page.get_cropbox() == (0, 0, *size))
            finally:
                page.close()
        return {"pdf_digest": bytes_digest(data), "pdf_bytes": len(data), "page_count": len(pdf)}
    finally:
        pdf.close()


def verify_capture_receipt(operation_id, *, owner_id, tenant_id, expected_digest,
                           expected_pdf_digest, expected_source_digest):
    """Read the exact operation-owned immutable receipt; no caller-selected paths."""
    from utils.get_env import get_app_data_directory_env
    root = get_app_data_directory_env()
    _require(root)
    operation, owner = str(uuid.UUID(str(operation_id))), str(uuid.UUID(str(owner_id)))
    directory = Path(root).resolve() / "svg-template-source-captures" / bytes_digest(str(tenant_id).encode())[7:] / owner / operation
    path = directory / "receipt.json"
    _require(path.is_file() and path.stat().st_size <= 2 * 1024**2)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    _require(artifact_digest(receipt) == expected_digest and receipt.get("status") == "captured")
    _require(receipt.get("operation_id") == operation and receipt.get("owner_id") == owner
             and receipt.get("tenant_digest") == bytes_digest(str(tenant_id).encode())
             and receipt.get("source_digest") == expected_source_digest
             and receipt.get("pdf_digest") == expected_pdf_digest
             and receipt.get("source_unchanged") is True and receipt.get("owned_job_closed") is True)
    pdf = directory / "source.pdf"
    _require(pdf.is_file() and 0 < pdf.stat().st_size <= MAX_PDF_BYTES
             and bytes_digest(pdf.read_bytes()) == expected_pdf_digest)
    return receipt


def capture_source_pdf(source_path, package, *, operation_id, owner_id, tenant_id):
    """Only the admitted analyze operation calls this; existing markers always reject."""
    from utils.get_env import get_app_data_directory_env
    root = get_app_data_directory_env()
    _require(root and os.name == "nt", "svg_template_source_pdf_unavailable")
    operation = str(uuid.UUID(str(operation_id)))
    owner = str(uuid.UUID(str(owner_id)))
    tenant_digest = bytes_digest(str(tenant_id).encode())
    directory = Path(root).resolve() / "svg-template-source-captures" / tenant_digest[7:] / owner / operation
    directory.mkdir(parents=True, exist_ok=True)
    marker = {"schema_version": "svg-template-source-pdf-capture/v1", "operation_id": operation,
              "owner_id": owner, "tenant_digest": tenant_digest, "source_digest": package.source_digest,
              "package_digest": package.digest}
    # Atomic durable fencing precedes every capability check and process creation.
    try:
        _write_once(directory / "once.json", marker)
    except FileExistsError as error:
        raise ValueError("svg_template_source_pdf_already_attempted") from error
    result = {**marker, "status": "failed_no_retry", "export_calls": 0,
              "powerpoint_before": None, "powerpoint_after": None}
    job = child = mutex = None
    acquired = False
    stage = "capability_preconditions"
    try:
        from workers import public_svg_pptx_render as renderer
        from services.svg_templates.source_review import _require_installed_fonts
        from services.svg_templates.frame_glyphs import validate_capture_source_subset
        _require(bytes_digest(Path(source_path).read_bytes()) == package.source_digest)
        validate_capture_source_subset(package, source_path)
        _require_installed_fonts(package.font_manifest)
        _require(renderer._available_memory() >= renderer.MINIMUM_AVAILABLE_MEMORY,
                 "svg_template_source_pdf_memory_unavailable")
        import win32api
        import win32event
        import win32job
        from utils.renderer_worker_policy import build_worker_environment, assert_no_provider_secrets
        mutex = win32event.CreateMutex(None, False, renderer.RENDER_MUTEX_NAME)
        wait = win32event.WaitForSingleObject(mutex, 1000)
        acquired = wait in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED)
        _require(acquired, "svg_template_powerpoint_busy")
        result["powerpoint_before"] = sorted(renderer._powerpoint_pids())
        _require(not result["powerpoint_before"], "svg_template_powerpoint_busy")
        stage = "typed_binding_and_owned_child"
        request = {**marker, "source_path": str(Path(source_path).resolve()),
                   "package": package.model_dump(mode="json"), "typelib": _typelib_binding(),
                   "capture_code": _capture_code_identity(),
                   "jobName": "Local\\svg-template-source-" + uuid.uuid4().hex}
        _write_once(directory / "request.json", request)
        job = win32job.CreateJobObject(None, request["jobName"])
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        environment = build_worker_environment()
        assert_no_provider_secrets(environment)
        child = subprocess.Popen([sys.executable, "-m", "services.svg_templates.source_pdf_worker",
            str(directory / "request.json"), str(directory / "child-receipt.json")],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parents[2], env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        handle = win32api.OpenProcess(0x0100 | 0x0001 | 0x0400, False, child.pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
            _require(win32job.IsProcessInJob(handle, job))
        finally:
            handle.Close()
        child.communicate(input=b"capture\n", timeout=60)
        receipt_path = directory / "child-receipt.json"
        _require(child.returncode == 0 and receipt_path.is_file() and receipt_path.stat().st_size <= 2 * 1024**2)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        result["child_receipt"] = receipt
        result["export_calls"] = receipt.get("export_calls", 0)
        _require(receipt.get("status") == "captured" and receipt.get("export_calls") == 1)
        _require(all(receipt.get(k) == marker[k] for k in marker))
        _require(0 < (directory / "source.pdf").stat().st_size <= MAX_PDF_BYTES)
        pdf_bytes = (directory / "source.pdf").read_bytes()
        stage = "parent_pdf_metadata"
        metadata = _pdf_metadata(pdf_bytes, package)
        _require(metadata["pdf_digest"] == receipt.get("pdf_digest"))
        result.update(metadata, status="captured", export_calls=1,
                      producer_version=receipt["producer_version"], producer_build=receipt["producer_build"])
    except BaseException as error:
        result["failure_class"] = type(error).__name__
        result["failure"] = _diagnostic(error, stage)
        raise ValueError("svg_template_source_pdf_capture_failed") from error
    finally:
        cleanup_failures = []
        try:
            if job is not None:
                import win32job
                try:
                    win32job.TerminateJobObject(job, 1)
                finally:
                    job.Close()
                result["owned_job_closed"] = True
        except BaseException as error:
            cleanup_failures.append(_diagnostic(error, "terminate_close_owned_job"))
        try:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
        except BaseException as error:
            cleanup_failures.append(_diagnostic(error, "wait_owned_child"))
        try:
            result["source_unchanged"] = bytes_digest(Path(source_path).read_bytes()) == package.source_digest
            output = directory / "source.pdf"
            if output.is_file() and 0 < output.stat().st_size <= MAX_PDF_BYTES:
                result.update(raw_pdf_digest=bytes_digest(output.read_bytes()), raw_pdf_bytes=output.stat().st_size)
            if result["powerpoint_before"] is not None:
                result["powerpoint_after"] = sorted(renderer._powerpoint_pids())
        except BaseException as error:
            cleanup_failures.append(_diagnostic(error, "source_pdf_and_process_readback"))
        try:
            if mutex is not None:
                import win32event
                if acquired:
                    try:
                        win32event.ReleaseMutex(mutex)
                    finally:
                        mutex.Close()
                else:
                    mutex.Close()
        except BaseException as error:
            cleanup_failures.append(_diagnostic(error, "release_close_mutex"))
        finally:
            if (cleanup_failures or not result.get("source_unchanged") or not result.get("owned_job_closed")
                    or result["powerpoint_before"] != [] or result["powerpoint_after"] != []):
                result["status"] = "failed_no_retry"
                result["cleanup_failures"] = cleanup_failures
            _write_once(directory / "receipt.json", result)
    _require(result["status"] == "captured" and result["source_unchanged"] and result.get("owned_job_closed"))
    return pdf_bytes, result


def _child(request_path, receipt_path):
    from models.svg_template_frame import SvgTemplatePackage
    from services.svg_templates.frame_native_text import validated_parts
    from workers import public_svg_pptx_render as renderer
    import pythoncom
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    package = SvgTemplatePackage.model_validate(request["package"])
    result = {k: request[k] for k in ("schema_version", "operation_id", "owner_id", "tenant_digest",
                                      "source_digest", "package_digest")}
    result.update(status="failed_no_retry", export_calls=0, ownership=[])
    result.update(typelib=request["typelib"], capture_code=request["capture_code"])
    application = document = None
    stage = "child_gate"
    pythoncom.CoInitialize()
    try:
        _require(sys.stdin.buffer.readline() == b"capture\n")
        _require(_typelib_binding() == request["typelib"] and _capture_code_identity() == request["capture_code"])
        path = Path(request["source_path"])
        _require(bytes_digest(path.read_bytes()) == package.source_digest)
        _require(not renderer._powerpoint_pids() and renderer._available_memory() >= 8 * 1024**3)
        binding = {}
        def guard(stage):
            renderer._assert_com_owned(application, pid, request["jobName"], binding)
            result["ownership"].append({"stage": stage, "pid": pid, **binding["identity"],
                "hwnd": binding["hwnd"], "job": request["jobName"], "checked": True})
        with renderer._owned_powerpoint(request) as pid:
            stage = "attach_owned_application"
            application = renderer._attach_powerpoint(pid, 30, job_name=request["jobName"], binding=binding)
            try:
                guard("before_open")
                stage = "open_readonly"
                document = application.Presentations.Open(str(path), ReadOnly=True, Untitled=False, WithWindow=False)
                guard("before_metadata")
                stage = "producer_and_source_metadata"
                _require(int(document.ReadOnly) != 0)
                result["producer_version"], result["producer_build"] = application.Version, application.Build
                _require(all(type(result[k]) is str and result[k] for k in ("producer_version", "producer_build")))
                physical = package.source_canvas.physical_size
                _require(float(document.PageSetup.SlideWidth) == physical.width_emu / 12700)
                _require(float(document.PageSetup.SlideHeight) == physical.height_emu / 12700)
                _require(int(document.Slides.Count) == max(f.source_page for f in package.frames) + 1)
                for frame in package.frames:
                    guard("before_shapes")
                    slide = document.Slides.Item(frame.source_page + 1)
                    shapes = {}
                    for i in range(1, int(slide.Shapes.Count) + 1):
                        guard("before_shape_item")
                        candidate = slide.Shapes.Item(i)
                        identity = str(candidate.Id)
                        _require(identity not in shapes)
                        shapes[identity] = candidate
                    for element in frame.elements:
                        if element.native_fixed_text is None:
                            continue
                        record, _sp, body, bounds = validated_parts(element.native_fixed_text,
                            frame.source_canvas, frame.font_manifest)
                        shape = shapes.get(element.source_shape_id)
                        _require(shape is not None)
                        guard("before_shape_metadata")
                        stage = "shape_and_font_metadata"
                        import ctypes
                        expected = [bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1]]
                        _require([float(getattr(shape, name)) for name in ("Left", "Top", "Width", "Height")]
                                 == [ctypes.c_float(v / 12700).value for v in expected])
                        _require(float(shape.Rotation) == 0)
                        text = shape.TextFrame.TextRange
                        from services.svg_templates.frame_native_text import NS
                        _require(text.Text == "".join(n.text or "" for n in body.findall(".//a:t", NS)))
                        props = body.find("a:p/a:r/a:rPr", NS)
                        guard("before_shape_font")
                        _require(text.Font.Name == props.find("a:latin", NS).get("typeface")
                                 and float(text.Font.Size) == int(props.get("sz")) / 100
                                 and int(text.Font.Bold) == (-1 if props.get("b") == "1" else 0)
                                 and int(text.Font.Italic) == 0)
                output = Path(request_path).parent / "source.pdf"
                _require(not output.exists() and bytes_digest(path.read_bytes()) == package.source_digest)
                guard("before_export")
                stage = "typed_source_pdf_export"
                result["export_calls"] = 1
                document._oleobj_.InvokeTypes(2096, 0, pythoncom.DISPATCH_METHOD, (24, 32), _ARGS,
                    str(output.resolve()), 2, 2, 0, 2, 1, 0, None, 1, "",
                    False, False, True, False, False, pythoncom.Missing)
                _require(0 < output.stat().st_size <= MAX_PDF_BYTES)
                result.update(raw_pdf_digest=bytes_digest(output.read_bytes()), raw_pdf_bytes=output.stat().st_size)
                stage = "child_pdf_metadata"
                result.update(_pdf_metadata(output.read_bytes(), package))
                result["status"] = "captured"
            finally:
                result["com_cleanup"] = "owned_job_fallback"
                try:
                    guard("before_close")
                    if document is not None:
                        document.Close()
                    guard("before_quit")
                    _require(int(application.Presentations.Count) == 0)
                    application.Quit()
                    result["com_cleanup"] = "owned_close_quit"
                except BaseException as error:
                    result["cleanup_failure"] = _diagnostic(error, "guarded_close_quit")
    except BaseException as error:
        result.update(status="failed_no_retry", failure_class=type(error).__name__)
        result["failure"] = _diagnostic(error, stage)
    finally:
        application = document = None
        pythoncom.CoUninitialize()
        _write_once(receipt_path, result)


if __name__ == "__main__":
    _child(sys.argv[1], sys.argv[2])
