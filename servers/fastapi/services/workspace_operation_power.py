"""One process-wide Windows power request for locally owned operation attempts."""

from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import logging
import os
import threading

LOGGER = logging.getLogger(__name__)
SYSTEM_REQUIRED = 1


def _create_request():
    if os.name != "nt":
        LOGGER.debug("Operation power request unavailable on this platform")
        return None

    class DetailedReason(ctypes.Structure):
        _fields_ = [("module", wintypes.HMODULE), ("id", wintypes.ULONG),
                    ("count", wintypes.ULONG), ("strings", ctypes.POINTER(wintypes.LPWSTR))]

    class Reason(ctypes.Union):
        _fields_ = [("detailed", DetailedReason), ("simple", wintypes.LPWSTR)]

    class Context(ctypes.Structure):
        _anonymous_ = ("reason",)
        _fields_ = [("version", wintypes.ULONG), ("flags", wintypes.DWORD), ("reason", Reason)]

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.PowerCreateRequest.argtypes = [ctypes.POINTER(Context)]
    api.PowerCreateRequest.restype = wintypes.HANDLE
    for name in ("PowerSetRequest", "PowerClearRequest"):
        function = getattr(api, name)
        function.argtypes = [wintypes.HANDLE, ctypes.c_int]
        function.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    context = Context(version=0, flags=1, simple="Presenton operation lease is active")
    handle = api.PowerCreateRequest(ctypes.byref(context))
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    if not api.PowerSetRequest(handle, SYSTEM_REQUIRED):
        error = ctypes.WinError(ctypes.get_last_error())
        api.CloseHandle(handle)
        raise error
    return api, handle


class OperationPowerHold:
    """Attempt tokens make release idempotent across runner and heartbeat threads.

    A handle, unlike SetThreadExecutionState, is independent of its calling
    thread. Windows closes it on process death. Explicit sleep and Modern
    Standby battery policy can override this request; no power policy is edited.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._attempts = set()
        self._request = None

    @staticmethod
    def _key(claim):
        return claim.operation_id, claim.attempt_token

    def acquire(self, claim):
        with self._lock:
            key = self._key(claim)
            if key in self._attempts:
                return
            if not self._attempts:
                try:
                    self._request = _create_request()
                except Exception:
                    LOGGER.warning("Operation power request unavailable", exc_info=True)
            self._attempts.add(key)
            LOGGER.debug("Operation power hold acquired: leases=%d", len(self._attempts))

    def release(self, claim):
        with self._lock:
            key = self._key(claim)
            if key not in self._attempts:
                return
            self._attempts.remove(key)
            if not self._attempts:
                self._clear()
            LOGGER.debug("Operation power hold released: leases=%d", len(self._attempts))

    def _clear(self):
        request, self._request = self._request, None
        if request is None:
            return
        api, handle = request
        try:
            try:
                if not api.PowerClearRequest(handle, SYSTEM_REQUIRED):
                    LOGGER.warning("Operation power request clear failed")
            finally:
                api.CloseHandle(handle)
        except Exception:
            LOGGER.warning("Operation power request cleanup failed", exc_info=True)

    def close(self):
        with self._lock:
            self._attempts.clear()
            self._clear()


operation_power_hold = OperationPowerHold()
atexit.register(operation_power_hold.close)
