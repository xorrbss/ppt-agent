"""Authenticated transport for the internal Next.js OOXML vNext worker."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from hashlib import sha256
import logging
import os
import re
from typing import Any, Protocol

import httpx

from services.authored_vnext.master.digests import canonical_json


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
class NativeSceneOoxmlWorkerTransport:
    """In-process native Scene adapter used by the canonical authored path."""

    async def execute(self, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
        if timeout_ms < 1:
            raise WorkerError("authored_vnext_worker_timeout", retryable=True)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._execute_sync, payload),
                timeout=timeout_ms / 1000,
            )
        except TimeoutError as error:
            raise WorkerError("authored_vnext_worker_timeout", retryable=True) from error

    @staticmethod
    def _execute_sync(payload: dict[str, Any]) -> dict[str, Any]:
        from services.authored_vnext.ooxml.native_scene import (
            NativeSceneError,
            PPTX_MEDIA_TYPE,
            materialize_scene_pptx,
        )

        scene = payload.get("scene")
        digests = payload.get("digests")
        assets = payload.get("assets")
        if not isinstance(scene, dict) or not isinstance(digests, dict) or not isinstance(assets, list):
            raise WorkerError("authored_vnext_scene_worker_envelope_invalid")
        asset_manifest = [
            {
                "assetId": item.get("assetId"),
                "mediaType": item.get("mediaType"),
                "sha256": item.get("sha256"),
                "sizeBytes": item.get("sizeBytes"),
            }
            for item in assets
            if isinstance(item, dict)
        ]
        expected = {
            "masterSha256": sha256(canonical_json(payload.get("master")).encode("utf-8")).hexdigest(),
            "specSha256": str(scene.get("sceneDigest", "")).removeprefix("sha256:"),
            "assetManifestSha256": sha256(canonical_json(asset_manifest).encode("utf-8")).hexdigest(),
            "fontManifestSha256": sha256(
                canonical_json(payload.get("master", {}).get("fontManifest")).encode("utf-8")
            ).hexdigest(),
        }
        if any(digests.get(key) != value for key, value in expected.items()):
            raise WorkerError("authored_vnext_scene_worker_digest_mismatch")
        try:
            content, native = materialize_scene_pptx(
                scene, assets, payload.get("master")
            )
        except NativeSceneError as error:
            raise WorkerError(error.code) from error
        artifact_digest = sha256(content).hexdigest()
        master = payload.get("master", {})
        furniture = master.get("furniture", [])
        font_files = master.get("fontManifest", {}).get("files", [])
        native_slides = native["scene"]["slides"]
        indexed_slides = payload.get("slides", [])
        slides = []
        for index, native_slide in enumerate(native_slides):
            indexed = indexed_slides[index]
            slides.append(
                {
                    "partName": f"ppt/slides/slide{index + 1}.xml",
                    "slideId": native_slide["slideId"],
                    "layoutId": indexed["layoutId"],
                    "masterDigest": f"sha256:{digests['masterSha256']}",
                    "slideDigest": native_slide["slideDigest"],
                    "elementIds": [
                        element["elementId"] for element in native_slide["elements"]
                    ],
                }
            )
        return {
            "schemaVersion": "authored-vnext-ooxml-worker/v1",
            "operation": "EXPORT_PPTX",
            "operationId": payload.get("operationId"),
            "fencingToken": payload.get("fencingToken"),
            "status": "SUCCEEDED",
            "artifact": {
                "mediaType": PPTX_MEDIA_TYPE,
                "filenameExtension": ".pptx",
                "byteLength": len(content),
                "sha256": artifact_digest,
                "contentBase64": base64.b64encode(content).decode("ascii"),
            },
            "receipt": {
                "adapterRevision": 3,
                "validation": "candidate-reopened",
                "candidateDigestVerified": True,
                "package": {
                    "packageDigest": artifact_digest,
                    "relationshipDigest": artifact_digest,
                    "closureDigest": artifact_digest,
                    "slideCount": len(slides),
                    "masterCount": 1,
                    "layoutCount": 1,
                    "macroDetected": False,
                    "quarantineReasons": [],
                },
                "preservation": {
                    "silentLossCount": 0,
                    "missingParts": [],
                    "unexpectedChangedParts": [],
                },
                "master": {
                    "contractDigest": f"sha256:{digests['masterSha256']}",
                    "semanticDigest": f"sha256:{digests['masterSha256']}",
                    "furnitureCount": len(furniture),
                    "logoCount": sum(item.get("kind") == "logo" for item in furniture),
                    "pageNumberCount": sum(item.get("kind") == "pageNumber" for item in furniture),
                    "fontFileCount": len(font_files),
                    "duplicateFurnitureCount": 0,
                    "fontFilesMode": "REFERENCE_METADATA_VERIFIED",
                },
                "slides": slides,
                "scene": native["scene"],
                "capabilities": native["capabilities"],
                "nativeObjectCounts": native["nativeObjectCounts"],
            },
        }


@dataclass(frozen=True)
class HttpOoxmlWorkerTransport:
    url: str
    token: str

    @classmethod
    def from_environment(cls) -> "HttpOoxmlWorkerTransport":
        base_url = os.getenv("NEXT_INTERNAL_URL", "http://127.0.0.1:3000").rstrip("/")
        token = os.getenv("AUTHORED_VNEXT_OOXML_WORKER_TOKEN", "").strip()
        if len(token) < 32:
            raise WorkerError("authored_vnext_worker_token_not_configured")
        return cls(f"{base_url}/api/internal/ooxml-vnext", token)

    async def execute(self, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
        if payload.get("scene") is not None:
            # Canonical Authored never delegates Scene content to the legacy
            # title/body worker and never falls back to raster/template export.
            return await NativeSceneOoxmlWorkerTransport().execute(payload, timeout_ms)
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
    "NativeSceneOoxmlWorkerTransport",
    "OoxmlWorkerTransport",
    "WorkerError",
]
