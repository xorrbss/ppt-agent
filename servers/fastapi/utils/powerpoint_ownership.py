"""Shared blank-application window claim; no Office activation or foreign cleanup."""
from __future__ import annotations

import os
import time
import uuid

#: Interval between PowerPoint ownership polls.
_POLL_INTERVAL_SECONDS = .05


def process_identity(pid: int):
    if os.name != "nt" or pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME))
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return None
        return {"pid": pid, "creation": (created.dwHighDateTime << 32) | created.dwLowDateTime}
    finally:
        kernel.CloseHandle(handle)


def claim_empty_powerpoint(application, *, started_at, identity_reader=None, include_window=False):
    """Bind the exact COM application to its hidden window before opening files.

    PowerPoint's documented Caption property is usable on versions whose
    late-bound HWND member and IOleWindow interface are unavailable.
    """
    identity_reader = identity_reader or process_identity
    import pythoncom
    import win32gui
    import win32process
    if int(application.Presentations.Count) != 0:
        raise ValueError("svg_template_powerpoint_ownership_unproven")
    original = application.Caption
    marker = "Presenton-source-" + uuid.uuid4().hex
    try:
        application.Caption = marker
        if application.Caption != marker:
            raise ValueError("svg_template_powerpoint_ownership_unproven")
        for _ in range(20):
            matches = []
            def visit(hwnd, _context, matches=matches):
                if win32gui.GetClassName(hwnd) == "PPTFrameClass" and marker in win32gui.GetWindowText(hwnd):
                    matches.append(hwnd)
                return True
            win32gui.EnumWindows(visit, None)
            if len(matches) > 1:
                break
            if len(matches) == 1:
                _, pid = win32process.GetWindowThreadProcessId(matches[0])
                identity = identity_reader(int(pid))
                if identity is not None and identity["creation"] >= started_at:
                    if include_window:
                        return {**identity, "hwnd": int(matches[0])}
                    return identity
                break
            pythoncom.PumpWaitingMessages()
            time.sleep(_POLL_INTERVAL_SECONDS)
        raise ValueError("svg_template_powerpoint_ownership_unproven")
    finally:
        application.Caption = original
        if application.Caption != original:
            raise ValueError("svg_template_powerpoint_ownership_unproven")
