"""Authenticated transport for the internal Next.js OOXML vNext worker."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Protocol

import httpx

from utils.env_settings.authored_vnext import (
    get_authored_vnext_ooxml_worker_token,
    get_authored_vnext_ooxml_worker_url,
)


_SAFE_WORKER_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_WORKER_REASON = re.compile(r"^[A-Za-z0-9_.\[\] ]{1,160}$")
logger = logging.getLogger(__name__)


class WorkerError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class OoxmlWorkerTransport(Protocol):
    async def execute(self, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]: ...


@dataclass(frozen=True)
class HttpOoxmlWorkerTransport:
    url: str
    token: str

    @classmethod
    def from_environment(cls) -> "HttpOoxmlWorkerTransport":
        token = get_authored_vnext_ooxml_worker_token()
        if len(token) < 32:
            raise WorkerError("authored_vnext_worker_token_not_configured")
        return cls(get_authored_vnext_ooxml_worker_url(), token)

    async def execute(self, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
        if payload.get("scene") is not None:
            # Canonical Authored never delegates Scene content to the legacy
            # title/body worker and never falls back to raster/template export.
            # Its in-process native Scene adapter was removed with authored
            # generation (P3 B4c), so a Scene payload is refused.
            raise WorkerError("authored_vnext_scene_worker_removed")
        try:
            timeout = httpx.Timeout(timeout_ms / 1000, connect=min(5.0, timeout_ms / 1000))
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(
                    self.url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as error:
            raise WorkerError("authored_vnext_worker_timeout", retryable=True) from error
        except httpx.HTTPError as error:
            raise WorkerError("authored_vnext_worker_unavailable", retryable=True) from error
        if response.status_code != 200:
            retryable = response.status_code >= 500 or response.status_code in {408, 429}
            code = f"authored_vnext_worker_http_{response.status_code}"
            try:
                body = response.json()
                worker_code = body.get("error", {}).get("code") if isinstance(body, dict) else None
                worker_reason = body.get("error", {}).get("message") if isinstance(body, dict) else None
                if isinstance(worker_code, str) and _SAFE_WORKER_CODE.fullmatch(worker_code):
                    code = f"{code}_{worker_code.lower()}"
                if isinstance(worker_reason, str) and _SAFE_WORKER_REASON.fullmatch(worker_reason):
                    logger.warning(
                        "Authored OOXML worker rejected a request status=%s code=%s reason=%s",
                        response.status_code,
                        worker_code if isinstance(worker_code, str) else "unknown",
                        worker_reason,
                    )
            except ValueError:
                pass
            raise WorkerError(code, retryable=retryable)
        try:
            result = response.json()
        except ValueError as error:
            raise WorkerError("authored_vnext_worker_response_invalid") from error
        if not isinstance(result, dict):
            raise WorkerError("authored_vnext_worker_response_invalid")
        return result


__all__ = [
    "HttpOoxmlWorkerTransport",
    "OoxmlWorkerTransport",
    "WorkerError",
]
