"""Private no-listener Chrome DevTools pipe transport."""

from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import time
from typing import Any, List, Mapping

from utils.renderer_isolation import build_worker_environment
from utils.slide_capture import _count_runtime, _kill_process_tree

def _runtime_value(result: Mapping[str, Any]) -> Any:
    remote = result.get("result", {})
    return remote.get("value") if isinstance(remote, Mapping) else None


class _SpawnedProcess:
    """Small os.posix_spawn process adapter used to avoid pre-exec hooks."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        waited, status = os.waitpid(self.pid, os.WNOHANG)
        if waited:
            self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def kill(self) -> None:
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("chrome", timeout)
            time.sleep(0.01)
        return b"", b""


def _spawn_chrome_pipe_posix(
    chrome: str,
    args: List[str],
    file_actions: list[tuple[Any, ...]],
) -> _SpawnedProcess:
    """Spawn Chrome with only the renderer allowlist environment."""

    pid = os.posix_spawn(
        chrome,
        args,
        build_worker_environment(os.environ),
        file_actions=file_actions,
        setsid=True,
    )
    return _SpawnedProcess(pid)


class _ChromePipe:
    """Minimal null-delimited CDP transport with no listening network socket."""

    def __init__(self, chrome: str, profile_path: pathlib.Path) -> None:
        child_read, parent_write_raw = os.pipe()
        parent_read_raw, child_write = os.pipe()
        self._buffer = bytearray()
        args = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-pings",
            "--remote-debugging-pipe",
            f"--user-data-dir={profile_path}",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "data:,",
        ]
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": build_worker_environment(os.environ),
        }
        if os.name == "nt":
            import msvcrt

            self._read_fd = parent_read_raw
            self._write_fd = parent_write_raw
            os.set_inheritable(parent_read_raw, False)
            os.set_inheritable(parent_write_raw, False)
            os.set_inheritable(child_read, True)
            os.set_inheritable(child_write, True)
            child_read_handle = msvcrt.get_osfhandle(child_read)
            child_write_handle = msvcrt.get_osfhandle(child_write)
            args.insert(
                -1,
                "--remote-debugging-io-pipes="
                f"{child_read_handle & 0xFFFFFFFF},{child_write_handle & 0xFFFFFFFF}",
            )
            kwargs["close_fds"] = False
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            child_fds = {child_read, child_write}
            try:
                self.process = subprocess.Popen(args, **kwargs)
            finally:
                for fd in child_fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        else:
            # Chromium's POSIX pipe contract is fixed at child fd 3 (read) and
            # fd 4 (write). posix_spawn file actions map those only in the child,
            # avoiding both a child pre-execution hook and clobbering worker fds.
            self._write_fd = parent_write_raw
            self._read_fd = parent_read_raw
            os.set_inheritable(self._read_fd, False)
            os.set_inheritable(self._write_fd, False)
            actions = [
                (os.POSIX_SPAWN_DUP2, child_read, 3),
                (os.POSIX_SPAWN_DUP2, child_write, 4),
            ]
            if parent_write_raw not in {3, 4}:
                actions.append((os.POSIX_SPAWN_CLOSE, parent_write_raw))
            if parent_read_raw not in {3, 4}:
                actions.append((os.POSIX_SPAWN_CLOSE, parent_read_raw))
            if child_read not in {3, 4}:
                actions.append((os.POSIX_SPAWN_CLOSE, child_read))
            if child_write not in {3, 4}:
                actions.append((os.POSIX_SPAWN_CLOSE, child_write))
            try:
                self.process = _spawn_chrome_pipe_posix(chrome, args, actions)
            finally:
                for fd in {child_read, child_write}:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        _count_runtime("chrome_process_launches")

    def command(
        self,
        command_id: int,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> Mapping[str, Any]:
        payload: dict[str, Any] = {
            "id": command_id,
            "method": method,
            "params": dict(params or {}),
        }
        if session_id:
            payload["sessionId"] = session_id
        wire = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\x00"
        offset = 0
        while offset < len(wire):
            offset += os.write(self._write_fd, wire[offset:])
        while True:
            delimiter = self._buffer.find(0)
            if delimiter < 0:
                chunk = os.read(self._read_fd, 65536)
                if not chunk:
                    raise RuntimeError(f"Chrome DevTools pipe closed during {method}")
                self._buffer.extend(chunk)
                continue
            raw = bytes(self._buffer[:delimiter])
            del self._buffer[: delimiter + 1]
            if not raw:
                continue
            response = json.loads(raw)
            if response.get("id") != command_id:
                continue
            if "error" in response:
                raise RuntimeError(
                    f"Chrome DevTools {method} failed: {response['error']}"
                )
            result = response.get("result", {})
            return result if isinstance(result, Mapping) else {}

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                try:
                    self.command(1_000_000, "Browser.close")
                except Exception:
                    _kill_process_tree(self.process)
            try:
                self.process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                _kill_process_tree(self.process)
                self.process.communicate(timeout=5)
        finally:
            os.close(self._read_fd)
            os.close(self._write_fd)

