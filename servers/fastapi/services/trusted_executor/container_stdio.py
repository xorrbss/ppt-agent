"""Bridge a multiplexed container attach stream onto plain stdin/stdout/stderr.

``SupervisedStdioWireTransport._communicate`` expects a process object with a
writable ``stdin`` and readable binary ``stdout``/``stderr`` streams.  Docker's
attach socket (``Tty=false``) instead carries both output channels on one
connection, framed as an 8-byte header (stream type, three reserved bytes, a
big-endian payload length) followed by the payload.  This module demultiplexes
that framing into two anonymous pipes and exposes stdin as a half-closable
writer, so the transport keeps working without knowing which backend it has.
"""

from __future__ import annotations

import os
import struct
import threading
from typing import BinaryIO

from services.trusted_executor.container_runtime import ContainerAttachStream
from constants.timeouts import WATCHER_THREAD_JOIN_SECONDS


_HEADER = struct.Struct(">BxxxI")
_STDOUT = 1
_STDERR = 2
_READ_CHUNK = 64 * 1024


class ContainerStdioError(RuntimeError):
    """The attach stream ended in a way that loses or corrupts worker output."""


class AttachStdin:
    """Write side of the attach stream; ``close`` half-closes so the child sees EOF."""

    def __init__(self, stream: ContainerAttachStream) -> None:
        self._stream = stream
        self._closed = False
        self._lock = threading.Lock()

    def write(self, data: bytes) -> int:
        with self._lock:
            if self._closed:
                raise ValueError("stdin is closed")
            self._stream.write(data)
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._stream.close_write()
            except OSError:
                pass


class AttachDemultiplexer:
    """Copy framed attach output into stdout/stderr pipes on a daemon thread."""

    def __init__(self, stream: ContainerAttachStream, *, name: str) -> None:
        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        self._stream = stream
        self._writers = {
            _STDOUT: os.fdopen(stdout_write, "wb", buffering=0),
            _STDERR: os.fdopen(stderr_write, "wb", buffering=0),
        }
        self.stdout: BinaryIO = os.fdopen(stdout_read, "rb", buffering=0)
        self.stderr: BinaryIO = os.fdopen(stderr_read, "rb", buffering=0)
        self.stdin = AttachStdin(stream)
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._pump, name=name, daemon=True)
        self._thread.start()

    def _read_exact(self, size: int) -> bytes:
        parts: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = self._stream.read(min(remaining, _READ_CHUNK))
            if not chunk:
                break
            parts.append(chunk)
            remaining -= len(chunk)
        return b"".join(parts)

    def _pump(self) -> None:
        try:
            while True:
                header = self._read_exact(_HEADER.size)
                if not header:
                    return
                if len(header) != _HEADER.size:
                    raise ContainerStdioError("attach stream ended inside a frame header")
                stream_type, length = _HEADER.unpack(header)
                payload = self._read_exact(length)
                if len(payload) != length:
                    raise ContainerStdioError("attach stream ended inside a frame payload")
                writer = self._writers.get(stream_type)
                if writer is None:
                    # Stream type 0 is stdin echo, which never appears with Tty=false;
                    # anything else is a protocol violation and must not be silently lost.
                    raise ContainerStdioError(f"unexpected attach stream type {stream_type}")
                try:
                    writer.write(payload)
                except OSError:
                    # The reader closed its end; the remaining output is not wanted.
                    pass
        except BaseException as error:  # noqa: BLE001 - surfaced through ``error``
            self.error = error
        finally:
            for writer in self._writers.values():
                try:
                    writer.close()
                except OSError:
                    pass

    def join(self, timeout: float) -> bool:
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def close(self) -> None:
        self.stdin.close()
        try:
            self._stream.close()
        except OSError:
            pass
        self._thread.join(timeout=WATCHER_THREAD_JOIN_SECONDS)
        for stream in (self.stdout, self.stderr):
            try:
                stream.close()
            except OSError:
                pass


def encode_attach_frame(stream_type: int, payload: bytes) -> bytes:
    """Frame one payload the way the engine does; used by fakes and tests."""

    if stream_type not in {_STDOUT, _STDERR}:
        raise ValueError("stream type must be stdout (1) or stderr (2)")
    return _HEADER.pack(stream_type, len(payload)) + payload


__all__ = [
    "AttachDemultiplexer",
    "AttachStdin",
    "ContainerStdioError",
    "encode_attach_frame",
]
