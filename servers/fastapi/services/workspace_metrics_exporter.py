"""Bounded Prometheus exporter for the standalone workspace worker.

The API process already exposes its own in-memory metrics.  External workers
run in a different process, so their runner observations need a separate
scrape target instead of being incorrectly attributed to the API process.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os

from services.workspace_metrics import WORKSPACE_METRICS


LOGGER = logging.getLogger(__name__)
_HOST_ENV = "WORKSPACE_OPERATION_METRICS_HOST"
_PORT_ENV = "WORKSPACE_OPERATION_METRICS_PORT"
_DEFAULT_HOST = "127.0.0.1"
_MAX_REQUEST_BYTES = 8_192
_READ_TIMEOUT_SECONDS = 5.0


def workspace_worker_metrics_endpoint() -> tuple[str, int] | None:
    """Return the explicitly configured worker scrape endpoint.

    An unset port keeps the listener disabled.  A configured but malformed
    endpoint fails startup so an operator cannot unknowingly deploy an
    unobservable external worker.
    """

    raw_port = os.getenv(_PORT_ENV)
    if raw_port is None or not raw_port.strip():
        return None
    try:
        port = int(raw_port)
    except ValueError as error:
        raise RuntimeError("workspace_operation_metrics_port_invalid") from error
    if port < 1 or port > 65_535:
        raise RuntimeError("workspace_operation_metrics_port_invalid")
    host = os.getenv(_HOST_ENV, _DEFAULT_HOST).strip()
    if not host:
        raise RuntimeError("workspace_operation_metrics_host_invalid")
    return host, port


def _response(status: str, body: bytes, content_type: str) -> bytes:
    return (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n"
        "X-Content-Type-Options: nosniff\r\n"
        "\r\n"
    ).encode("ascii") + body


async def _handle_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        request = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"),
            timeout=_READ_TIMEOUT_SECONDS,
        )
        if len(request) > _MAX_REQUEST_BYTES:
            raise asyncio.LimitOverrunError("request headers too large", len(request))
        request_line = request.split(b"\r\n", 1)[0]
        parts = request_line.split(b" ")
        if len(parts) != 3 or parts[0] != b"GET":
            payload = _response("405 Method Not Allowed", b"method not allowed\n", "text/plain")
        elif parts[1] == b"/metrics":
            body = WORKSPACE_METRICS.prometheus_text().encode("utf-8")
            payload = _response(
                "200 OK",
                body,
                "text/plain; version=0.0.4; charset=utf-8",
            )
        elif parts[1] == b"/healthz":
            payload = _response("200 OK", b"ok\n", "text/plain; charset=utf-8")
        else:
            payload = _response("404 Not Found", b"not found\n", "text/plain; charset=utf-8")
        writer.write(payload)
        await writer.drain()
    except (
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
        asyncio.TimeoutError,
    ):
        # A malformed scrape must never affect operation execution.
        try:
            writer.write(
                _response(
                    "400 Bad Request",
                    b"bad request\n",
                    "text/plain; charset=utf-8",
                )
            )
            await writer.drain()
        except (ConnectionError, OSError):
            # Prometheus (or a health checker) may disconnect after the
            # request timeout.  Scrape transport failures are non-authoritative.
            pass
    except (ConnectionError, OSError):
        # A reset peer must never escape the exporter task or stop the worker.
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


@dataclass
class WorkspaceWorkerMetricsExporter:
    server: asyncio.AbstractServer
    host: str
    port: int

    async def close(self) -> None:
        self.server.close()
        await self.server.wait_closed()


async def start_workspace_worker_metrics_exporter(
) -> WorkspaceWorkerMetricsExporter | None:
    endpoint = workspace_worker_metrics_endpoint()
    if endpoint is None:
        return None
    host, port = endpoint
    server = await asyncio.start_server(
        _handle_request,
        host=host,
        port=port,
        limit=_MAX_REQUEST_BYTES,
    )
    sockets = server.sockets or []
    bound_port = int(sockets[0].getsockname()[1]) if sockets else port
    LOGGER.info(
        "Workspace worker metrics exporter started at http://%s:%s/metrics",
        host,
        bound_port,
    )
    return WorkspaceWorkerMetricsExporter(server=server, host=host, port=bound_port)
