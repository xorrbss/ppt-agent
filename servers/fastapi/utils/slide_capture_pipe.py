"""Private no-listener Chrome DevTools pipe transport."""

from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import time
from typing import Any, List, Mapping

from utils.process_kill_on_parent_death import bind_to_parent_lifetime
from utils.renderer_isolation import build_worker_environment
from utils.slide_capture import _count_runtime, _kill_process_tree
from constants.timeouts import BUSY_WAIT_POLL_INTERVAL_SECONDS, PROCESS_REAP_TIMEOUT_SECONDS

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
            time.sleep(BUSY_WAIT_POLL_INTERVAL_SECONDS)
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


def _posix_spawn_setsid_supported() -> bool:
    """Whether this interpreter's ``os.posix_spawn`` accepts ``setsid=True``.

    CPython defines ``os.POSIX_SPAWN_SETSID`` only when the C library provides
    setsid support for posix_spawn. uv's python-build-standalone does not, and
    there ``posix_spawn(setsid=True)`` raises ``NotImplementedError``.
    """
    return hasattr(os, "POSIX_SPAWN_SETSID")


def _spawn_chrome_pipe_popen(
    chrome: str,
    args: List[str],
    child_read: int,
    child_write: int,
) -> subprocess.Popen:
    """Fallback spawn for interpreters without ``POSIX_SPAWN_SETSID``.

    Keeps the same contract as ``_spawn_chrome_pipe_posix``: the child becomes
    a session and process-group leader (so ``os.killpg(pid)`` still reaches the
    whole tree), gets only the renderer allowlist environment, and sees the CDP
    pipe ends as fd 3 (read) and fd 4 (write). ``subprocess`` has no fd-remap
    argument, so the mapping runs in ``preexec_fn`` in the child only.
    """
    import fcntl

    def _map_pipe_fds() -> None:
        # Duplicate to fds >= 5 first, so a source fd that is itself 3 or 4
        # is not clobbered before it is copied.
        read_tmp = fcntl.fcntl(child_read, fcntl.F_DUPFD, 5)
        write_tmp = fcntl.fcntl(child_write, fcntl.F_DUPFD, 5)
        os.dup2(read_tmp, 3)
        os.dup2(write_tmp, 4)
        os.close(read_tmp)
        os.close(write_tmp)

    return subprocess.Popen(
        args,
        executable=chrome,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=build_worker_environment(os.environ),
        start_new_session=True,
        preexec_fn=_map_pipe_fds,
        # Keep False: with close_fds=True, subprocess closes fds 3 and 4 after
        # preexec_fn has mapped the pipes there. The parent's pipe ends are
        # non-inheritable (os.pipe), so inheriting here matches posix_spawn.
        close_fds=False,
    )


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
            # fd 4 (write). The preferred posix_spawn path maps those with file
            # actions, which needs no child pre-execution hook. Interpreters
            # without POSIX_SPAWN_SETSID fall back to Popen, which maps them in
            # preexec_fn instead (see _spawn_chrome_pipe_popen).
            self._write_fd = parent_write_raw
            self._read_fd = parent_read_raw
            os.set_inheritable(self._read_fd, False)
            os.set_inheritable(self._write_fd, False)
            try:
                if _posix_spawn_setsid_supported():
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
                    self.process = _spawn_chrome_pipe_posix(chrome, args, actions)
                else:
                    self.process = _spawn_chrome_pipe_popen(
                        chrome, args, child_read, child_write
                    )
            finally:
                for fd in {child_read, child_write}:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        # Bind the pipe-transport Chrome to this process's lifetime so a
        # force-killed owner cannot strand it. No-op off Windows.
        bind_to_parent_lifetime(self.process)
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
                self.process.communicate(timeout=PROCESS_REAP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                _kill_process_tree(self.process)
                self.process.communicate(timeout=PROCESS_REAP_TIMEOUT_SECONDS)
        finally:
            os.close(self._read_fd)
            os.close(self._write_fd)

