"""Docker Engine adapter for ``ContainerRuntimeClient`` (imports ``docker`` lazily).

The ``docker`` SDK is not a declared dependency of this service yet; this
adapter is only instantiated when ``PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND=container``
composes the runtime, and it fails with a clear ``ContainerRuntimeError`` when
the SDK is absent.  Unit tests never import it; they inject a fake client.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from typing import Any

from services.trusted_executor.container_runtime import (
    ContainerAttachStream,
    ContainerCreateRequest,
    ContainerHandle,
    ContainerRuntimeError,
    as_environment_list,
)


_WAIT_SLICE_SECONDS = 60.0


class _DockerAttachStream(ContainerAttachStream):
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def read(self, size: int) -> bytes:
        data = self._raw.read(size)
        return data or b""

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = self._raw.write(view)
            if not written:
                raise ContainerRuntimeError("attach stream rejected stdin bytes")
            view = view[written:]

    def close_write(self) -> None:
        underlying = getattr(self._raw, "_sock", None)
        if underlying is None or not hasattr(underlying, "shutdown"):
            raise ContainerRuntimeError("attach stream cannot half-close stdin")
        underlying.shutdown(socket.SHUT_WR)

    def close(self) -> None:
        try:
            self._raw.close()
        except OSError:
            pass


class DockerContainerHandle(ContainerHandle):
    def __init__(self, runtime: "DockerSdkContainerRuntime", container_id: str) -> None:
        self._runtime = runtime
        self._id = container_id

    @property
    def id(self) -> str:
        return self._id

    def inspect(self) -> Mapping[str, object]:
        return self._runtime._call(self._runtime.api.inspect_container, self._id)

    def attach(self) -> ContainerAttachStream:
        raw = self._runtime._call(
            self._runtime.api.attach_socket,
            self._id,
            params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1},
        )
        return _DockerAttachStream(raw)

    def start(self) -> None:
        self._runtime._call(self._runtime.api.start, self._id)

    def wait(self, *, timeout: float | None) -> int:
        if timeout is not None:
            result = self._runtime._call_wait(self._id, timeout)
        else:
            while True:
                try:
                    result = self._runtime._call_wait(self._id, _WAIT_SLICE_SECONDS)
                    break
                except TimeoutError:
                    continue
        code = result.get("StatusCode") if isinstance(result, Mapping) else None
        if not isinstance(code, int):
            raise ContainerRuntimeError("container wait returned no exit code")
        return code

    def kill(self) -> None:
        self._runtime._call(self._runtime.api.kill, self._id)

    def stats(self) -> Mapping[str, object]:
        result = self._runtime._call(
            self._runtime.api.stats, self._id, stream=False, one_shot=True
        )
        return result if isinstance(result, Mapping) else {}

    def remove(self) -> None:
        self._runtime._call(self._runtime.api.remove_container, self._id, v=True, force=True)


class DockerSdkContainerRuntime:
    """``ContainerRuntimeClient`` over ``docker.APIClient`` (low-level API)."""

    def __init__(self, api_client: Any, *, error_types: tuple[type[BaseException], ...]) -> None:
        self.api = api_client
        self._error_types = error_types
        self._timeout_types = tuple(
            error for error in error_types if error.__name__ in {"Timeout", "ReadTimeout"}
        )

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "DockerSdkContainerRuntime":
        source = os.environ if environment is None else environment
        try:
            import docker
            import docker.errors
            import requests.exceptions
        except ImportError as error:
            raise ContainerRuntimeError(
                "the docker SDK is not installed; add `docker` to the service dependencies"
            ) from error
        base_url = source.get("DOCKER_HOST", "").strip() or "unix://var/run/docker.sock"
        api = docker.APIClient(base_url=base_url, version="auto")
        return cls(
            api,
            error_types=(
                docker.errors.DockerException,
                requests.exceptions.RequestException,
                requests.exceptions.ReadTimeout,
                OSError,
            ),
        )

    def _call(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except self._error_types as error:
            raise ContainerRuntimeError(f"docker {getattr(method, '__name__', 'call')} failed") from error

    def _call_wait(self, container_id: str, timeout: float) -> Any:
        try:
            return self.api.wait(container_id, timeout=timeout)
        except self._timeout_types as error:
            raise TimeoutError("container wait timed out") from error
        except self._error_types as error:
            raise ContainerRuntimeError("docker wait failed") from error

    def create(self, request: ContainerCreateRequest) -> ContainerHandle:
        config = self._call(
            self.api.create_container_config,
            image=request.image,
            command=list(request.command),
            environment=as_environment_list(request.environment),
            working_dir=request.working_dir,
            user=request.user,
            labels=dict(request.labels),
            stdin_open=request.open_stdin,
            tty=request.tty,
            host_config=dict(request.host_config),
        )
        config["StdinOnce"] = request.stdin_once
        config["AttachStdin"] = request.open_stdin
        config["AttachStdout"] = True
        config["AttachStderr"] = True
        config.update(dict(request.extra))
        created = self._call(self.api.create_container_from_config, config, request.name)
        container_id = created.get("Id") if isinstance(created, Mapping) else None
        if not isinstance(container_id, str) or not container_id:
            raise ContainerRuntimeError("docker create returned no container id")
        return DockerContainerHandle(self, container_id)


__all__ = ["DockerContainerHandle", "DockerSdkContainerRuntime"]
