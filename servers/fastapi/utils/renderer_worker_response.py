"""Fail-closed decoding of one authored renderer worker response.

Extracted from ``utils.slide_capture_worker_client`` so the framed multi-render
session transport can share the exact same validation without importing the
single-shot client (which would close an import cycle). The field sets are
strict on purpose: an isolated renderer may return only the artifact and its
identity, never extra channels back into the parent process.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Mapping

from utils.renderer_isolation import MAX_RESPONSE_BYTES, RendererPolicyError
from utils.hashing import sha256_prefixed

SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
RESPONSE_SCHEMA = "authored-render-response/v1"
_FAILURE_FIELDS = {"schema_version", "ok", "error", "error_type"}
_SUCCESS_FIELDS = {
    "schema_version",
    "ok",
    "png_base64",
    "metrics",
    "source_digest",
    "asset_digests",
}


class RendererIsolationError(RuntimeError):
    """The isolated authored render boundary failed without a publishable artifact."""


def source_digest_for(html: str) -> str:
    return sha256_prefixed(html.encode("utf-8"))


def decode_worker_response(
    payload: bytes, *, allow_ordinal: bool = False
) -> tuple[bytes, Mapping[str, Any] | None]:
    """Validate one worker response and return its PNG and browser metrics.

    ``allow_ordinal`` admits the framed protocol's ``request_ordinal`` field,
    which binds a response to its position in a multi-render session.
    """

    if not payload or len(payload) > MAX_RESPONSE_BYTES:
        raise RendererIsolationError("authored renderer response limit exceeded")
    try:
        response = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RendererIsolationError("authored renderer returned invalid JSON") from exc
    if not isinstance(response, Mapping):
        raise RendererIsolationError("authored renderer returned a non-object response")
    if response.get("schema_version") != RESPONSE_SCHEMA:
        raise RendererIsolationError("authored renderer response schema is unsupported")
    ok = response.get("ok")
    if not isinstance(ok, bool):
        raise RendererIsolationError("authored renderer response is missing status")
    ordinal_field = {"request_ordinal"} if allow_ordinal else set()
    if not ok:
        if not set(response).issubset(_FAILURE_FIELDS | ordinal_field):
            raise RendererIsolationError("authored renderer failure response has invalid fields")
        message = response.get("error")
        safe_message = (
            str(message).replace("\r", " ").replace("\n", " ")[:256]
            if isinstance(message, str)
            else "isolated authored renderer failed"
        )
        if response.get("error_type") in {"RendererPolicyError", "RendererDisabledError"}:
            raise RendererPolicyError(safe_message)
        raise RendererIsolationError(safe_message)
    if set(response) != _SUCCESS_FIELDS | ordinal_field:
        raise RendererIsolationError("authored renderer success response has invalid fields")
    encoded_png = response.get("png_base64")
    if not isinstance(encoded_png, str):
        raise RendererIsolationError("authored renderer response has no PNG")
    try:
        png = base64.b64decode(encoded_png, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RendererIsolationError("authored renderer PNG is not valid base64") from exc
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RendererIsolationError("authored renderer returned a non-PNG artifact")
    if len(png) > MAX_RESPONSE_BYTES:
        raise RendererIsolationError("authored renderer PNG exceeds response limit")
    metrics = response.get("metrics")
    if metrics is not None and not isinstance(metrics, Mapping):
        raise RendererIsolationError("authored renderer metrics are invalid")
    digest = response.get("source_digest")
    assets = response.get("asset_digests")
    if not isinstance(digest, str) or not SHA256_DIGEST.fullmatch(digest):
        raise RendererIsolationError("authored renderer source digest is invalid")
    if not isinstance(assets, list) or any(
        not isinstance(item, str) or not SHA256_DIGEST.fullmatch(item) for item in assets
    ):
        raise RendererIsolationError("authored renderer asset digests are invalid")
    return png, metrics


def response_ordinal(payload: bytes) -> int | None:
    """Return the ``request_ordinal`` of an already-validated framed response."""

    value = json.loads(payload).get("request_ordinal")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def assert_response_identity(payload: bytes, html: str) -> None:
    """Bind the returned artifact to the exact request across an external launcher."""

    expected = source_digest_for(html)
    if json.loads(payload).get("source_digest") != expected:
        raise RendererIsolationError("authored renderer response identity mismatch")


__all__ = [
    "RendererIsolationError",
    "assert_response_identity",
    "decode_worker_response",
    "response_ordinal",
    "source_digest_for",
]
