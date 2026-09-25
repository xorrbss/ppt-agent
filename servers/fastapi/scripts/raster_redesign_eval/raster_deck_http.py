"""Minimal stdlib HTTP JSON client used by the raster-redesign product-path
driver (generate_raster_deck.py): a plain urllib wrapper with a structured
`ApiError` that carries the backend's own error envelope `code`.

Split out of the original generate_raster_deck.py (2026-09-25 eval suite) to
keep each module under the repo's file-size limit; behavior is unchanged.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from typing import Any, Optional


class ApiError(RuntimeError):
    """An HTTP error from the backend, with the error envelope's `code` (if any)
    pulled out separately so callers can report an exact code instead of a
    raw body dump."""

    def __init__(self, method: str, path: str, status_code: int, code: Optional[str], raw: str):
        self.method = method
        self.path = path
        self.status_code = status_code
        self.code = code
        self.raw = raw[:500]
        super().__init__(f"{method} {path} -> HTTP {status_code} code={code}")


def _urlopen_json(req: urllib.request.Request, *, method: str, path: str, timeout: int) -> Any:
    """Shared response handling: parse JSON on success, raise ApiError with the
    server's error code on failure. Never buffers a large response body --
    callers that expect a big payload (artifact downloads) don't use this."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        code = None
        try:
            parsed = json.loads(raw)
            detail = parsed.get("detail") if isinstance(parsed, dict) else None
            if isinstance(detail, dict):
                code = detail.get("code")
            elif isinstance(detail, str):
                code = detail
            elif isinstance(parsed, dict):
                code = parsed.get("code") or (parsed.get("error") or {}).get("code")
        except (json.JSONDecodeError, AttributeError):
            pass
        raise ApiError(method, path, exc.code, code, raw) from exc


def _encode_multipart(fields: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    lines = []
    for name, value in fields.items():
        if isinstance(value, tuple):  # (filename, bytes, content_type)
            filename, content, content_type = value
            lines.append(f"--{boundary}".encode())
            lines.append(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode()
            )
            lines.append(f"Content-Type: {content_type}".encode())
            lines.append(b"")
            lines.append(content)
        else:
            lines.append(f"--{boundary}".encode())
            lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
            lines.append(b"")
            lines.append(str(value).encode())
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


def request(base: str, method: str, path: str, *, json_body: Optional[dict] = None,
            multipart: Optional[dict] = None, headers: Optional[dict] = None) -> Any:
    url = f"{base}{path}"
    hdrs = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif multipart is not None:
        data, content_type = _encode_multipart(multipart)
        hdrs["Content-Type"] = content_type
    else:
        data = None
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    return _urlopen_json(req, method=method, path=path, timeout=120)
