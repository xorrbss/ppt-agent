"""Length-prefixed framing for the authored renderer worker boundary.

A frame is an 8-byte big-endian unsigned payload length followed by exactly
that many bytes of UTF-8 JSON. Framing is what lets one worker process serve
many renders: the legacy protocol carried a single bare JSON payload that the
worker could only terminate by reading to EOF, which made "one render per
process" a property of the wire format itself.

Compatibility is decided from the first eight bytes, with no handshake and no
new flag on the wire. A legacy payload begins with ASCII JSON -- ``{"schema``
is 0x7b22736368656d61, about 8.9e18 -- which is many orders of magnitude above
any admissible frame length, so a header whose value lands inside
``1..limit`` is unambiguously a frame and anything else is the first eight
bytes of a legacy payload. Callers that detect legacy must therefore keep
those bytes and prepend them to the rest of the stream.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Mapping

from utils.renderer_isolation import (
    MAX_HTML_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    RendererPolicyError,
)

FRAME_HEADER_BYTES = 8
REQUEST_SCHEMA = "authored-render-request/v1"
#: Default ceiling on renders served by one worker process. Overridden per
#: session by ``max_requests`` on the first framed request.
DEFAULT_MAX_REQUESTS_PER_PROCESS = 200
MAX_REQUESTS_PER_PROCESS_CEILING = 5000


class RendererFrameError(RuntimeError):
    """A framed exchange violated the length-prefixed wire format."""


def frame_length(header: bytes) -> int:
    """Return the payload length encoded by ``header`` (which must be 8 bytes)."""

    if len(header) != FRAME_HEADER_BYTES:
        raise RendererFrameError("renderer frame header is truncated")
    return int.from_bytes(header, "big", signed=False)


def header_is_frame(header: bytes, *, limit: int = MAX_REQUEST_BYTES) -> bool:
    """Tell a frame header apart from the first bytes of a legacy payload."""

    if len(header) != FRAME_HEADER_BYTES:
        return False
    return 0 < int.from_bytes(header, "big", signed=False) <= limit


def encode_frame(payload: bytes, *, limit: int) -> bytes:
    if not payload:
        raise RendererFrameError("renderer frame payload is empty")
    if len(payload) > limit:
        raise RendererFrameError("renderer frame exceeds its wire limit")
    return len(payload).to_bytes(FRAME_HEADER_BYTES, "big", signed=False) + payload


def read_frame_sync(
    read_exact: Callable[[int], bytes],
    *,
    limit: int = MAX_REQUEST_BYTES,
    header: bytes | None = None,
) -> bytes | None:
    """Read one frame using a blocking ``read_exact``; ``None`` on clean EOF.

    ``header`` supplies eight bytes already consumed from the stream (the
    protocol-detection peek), so the first frame of a session is not lost.
    """

    if header is None:
        header = read_exact(FRAME_HEADER_BYTES)
    if not header:
        return None
    length = frame_length(header)
    if not 0 < length <= limit:
        raise RendererFrameError("renderer frame length is out of range")
    payload = read_exact(length)
    if len(payload) != length:
        raise RendererFrameError("renderer frame payload is truncated")
    return payload


async def read_frame_async(
    stream: asyncio.StreamReader, *, limit: int = MAX_RESPONSE_BYTES
) -> bytes | None:
    """Read one frame from ``stream``; ``None`` when the peer closed cleanly."""

    try:
        header = await stream.readexactly(FRAME_HEADER_BYTES)
    except asyncio.IncompleteReadError as incomplete:
        if not incomplete.partial:
            return None
        raise RendererFrameError("renderer frame header is truncated") from incomplete
    length = frame_length(header)
    if not 0 < length <= limit:
        raise RendererFrameError("renderer frame length is out of range")
    try:
        return await stream.readexactly(length)
    except asyncio.IncompleteReadError as incomplete:
        raise RendererFrameError("renderer frame payload is truncated") from incomplete


def encode_json_frame(value: Mapping[str, Any], *, limit: int) -> bytes:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return encode_frame(payload, limit=limit)


def encode_framed_request(html: str, timeout: int, *, max_requests: int | None = None) -> bytes:
    """Encode one framed render request.

    ``max_requests`` is meaningful only on a session's first request: it tells
    the worker how many renders it may serve before it exits, so the parent
    keeps the ceiling and the worker enforces it.
    """

    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise RendererPolicyError("renderer HTML limit exceeded")
    request: dict[str, Any] = {
        "schema_version": REQUEST_SCHEMA,
        "html": html,
        "timeout": timeout,
    }
    if max_requests is not None:
        request["max_requests"] = clamp_max_requests(max_requests)
    try:
        return encode_json_frame(request, limit=MAX_REQUEST_BYTES)
    except RendererFrameError as exc:
        raise RendererPolicyError("renderer request wire limit exceeded") from exc


def clamp_max_requests(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RendererPolicyError("renderer max_requests must be an integer")
    return max(1, min(MAX_REQUESTS_PER_PROCESS_CEILING, value))


__all__ = [
    "DEFAULT_MAX_REQUESTS_PER_PROCESS",
    "FRAME_HEADER_BYTES",
    "MAX_REQUESTS_PER_PROCESS_CEILING",
    "RendererFrameError",
    "clamp_max_requests",
    "encode_frame",
    "encode_framed_request",
    "encode_json_frame",
    "frame_length",
    "header_is_frame",
    "read_frame_async",
    "read_frame_sync",
]
