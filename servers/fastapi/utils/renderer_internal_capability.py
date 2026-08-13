"""Process-local, source-bound one-shot capabilities for renderer instrumentation."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from collections import OrderedDict

from utils.renderer_worker_policy import RendererPolicyError


_KEY = secrets.token_bytes(32)
_MAX_ISSUED = 128
_LOCK = threading.Lock()
_ISSUED: "OrderedDict[str, str]" = OrderedDict()


def issue_internal_render_capability(source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    nonce = secrets.token_hex(24)
    signature = hmac.new(
        _KEY, f"{nonce}:{digest}".encode("ascii"), hashlib.sha256
    ).hexdigest()
    token = f"{nonce}.{signature}"
    with _LOCK:
        _ISSUED[token] = digest
        while len(_ISSUED) > _MAX_ISSUED:
            _ISSUED.popitem(last=False)
    return token


def consume_internal_render_capability(source: str, token: str) -> None:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    try:
        nonce, supplied = token.split(".", 1)
    except ValueError as exc:
        raise RendererPolicyError("internal renderer capability is malformed") from exc
    expected = hmac.new(
        _KEY, f"{nonce}:{digest}".encode("ascii"), hashlib.sha256
    ).hexdigest()
    with _LOCK:
        issued_digest = _ISSUED.pop(token, None)
    if issued_digest != digest or not hmac.compare_digest(expected, supplied):
        raise RendererPolicyError(
            "internal renderer capability is invalid or already consumed"
        )
