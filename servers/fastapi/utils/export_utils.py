import json
import logging
import os
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx
from pathvalidate import sanitize_filename
from pydantic import ValidationError

from models.presentation_and_path import PresentationAndPath
from models.sql.presentation_export_attempt import (
    PresentationExportCoverageManifest,
)
from services.export_task_service import EXPORT_TASK_SERVICE
from utils.asset_directory_utils import get_exports_directory
from utils.filename_utils import safe_export_basename
from utils.get_env import get_app_data_directory_env
from utils.runtime_limits import log_memory
from utils.simple_auth import SESSION_COOKIE_NAME


LOGGER = logging.getLogger(__name__)
_AUTHORED_HYBRID_EXPORT_TIMEOUT_SECONDS = 30 * 60
_MAX_AUTHORED_HYBRID_EXPORT_BYTES = 512 * 1024 * 1024


class AuthoredHybridPresentationAndPath(PresentationAndPath):
    """Hybrid artifact plus the validated, client-visible coverage evidence."""

    coverage_manifest: dict
    renderer_source_sha256: str


def _validated_hybrid_coverage(
    result: dict,
    *,
    expected_revision: int,
) -> dict:
    metadata = result.get("exportMetadata")
    raw_coverage = (
        metadata.get("coverageManifest") if isinstance(metadata, dict) else None
    )
    try:
        coverage = PresentationExportCoverageManifest.model_validate(raw_coverage)
    except ValidationError as error:
        raise RuntimeError("authored_hybrid_export_coverage_invalid") from error
    if coverage.source.presentation_revision != expected_revision:
        raise RuntimeError("authored_hybrid_export_coverage_revision_mismatch")

    quality = result.get("quality")
    if not isinstance(quality, dict):
        raise RuntimeError("authored_hybrid_export_quality_invalid")
    expected_counts = {
        "nativeTextElements": coverage.native_text,
        "nativeImageElements": coverage.native_image,
        "nativeShapeElements": coverage.native_shape,
        "rasterFallbackElements": coverage.raster_remaining,
    }
    if any(quality.get(key) != value for key, value in expected_counts.items()):
        raise RuntimeError("authored_hybrid_export_coverage_quality_mismatch")
    if coverage.raster_remaining > 0 and quality.get("status") == "fully-editable":
        raise RuntimeError("authored_hybrid_export_editability_overstated")
    return coverage.model_dump(mode="json", by_alias=True)


def _require_persisted_hybrid_coverage(path: Path, coverage: dict) -> None:
    coverage_path = Path(f"{path}.coverage.json")
    try:
        stored = json.loads(coverage_path.read_text(encoding="utf-8"))
        validated = PresentationExportCoverageManifest.model_validate(stored)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise RuntimeError("authored_hybrid_export_coverage_not_persisted") from error
    if validated.model_dump(mode="json", by_alias=True) != coverage:
        raise RuntimeError("authored_hybrid_export_coverage_sidecar_mismatch")


def _persist_hybrid_coverage(path: Path, coverage: dict) -> None:
    coverage_path = Path(f"{path}.coverage.json")
    temporary = coverage_path.with_name(
        f"{coverage_path.name}.{uuid.uuid4().hex}.exporting"
    )
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(coverage, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, coverage_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _get_next_public_url() -> str:
    return (
        (os.getenv("NEXT_PUBLIC_URL") or "").strip()
        or (os.getenv("NEXT_INTERNAL_URL") or "").strip()
        or "http://127.0.0.1"
    )


def _get_next_public_fastapi_url() -> str | None:
    value = (os.getenv("NEXT_PUBLIC_FAST_API") or "").strip()
    return value or None


def _extract_session_token(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE_NAME and value:
            return value
    return None


def _build_presentation_export_url(
    presentation_id: uuid.UUID,
    export_as: Literal["pptx", "pdf"],
    session_token: str | None = None,
    expected_revision: int | None = None,
    legacy_path_only_rolling_facade: bool = False,
) -> tuple[str, str | None]:
    params = {"id": str(presentation_id)}
    if legacy_path_only_rolling_facade:
        if expected_revision is not None:
            raise ValueError("legacy path-only export cannot bind an expected revision")
    else:
        if expected_revision is None:
            raise ValueError("expected_revision is required for canonical export")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise ValueError("expected_revision must be an integer")
        if expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")
        params.update(
            {
                "format": export_as,
                "render_contract": "presenton.render-artifact/v1",
                "expected_revision": str(expected_revision),
            }
        )
    fastapi_url = _get_next_public_fastapi_url()
    if fastapi_url:
        params["fastapiUrl"] = fastapi_url
    # Seed the browser cookie jar via the Next.js `exportSession` handler
    # (proxy.ts Set-Cookie + redirect). The export runtime injects the session
    # as a CDP extra Cookie header, which newer Chromium silently drops on the
    # wire; the jar cookie survives and authenticates the render's data fetch.
    if session_token:
        params["exportSession"] = session_token
    return (
        f"{_get_next_public_url().rstrip('/')}/pdf-maker?{urlencode(params)}",
        fastapi_url,
    )


async def export_presentation(
    presentation_id: uuid.UUID,
    title: str,
    export_as: Literal["pptx", "pdf"],
    cookie_header: str | None = None,
    expected_revision: int | None = None,
    legacy_path_only_rolling_facade: bool = False,
) -> PresentationAndPath:
    log_memory(
        LOGGER,
        "presentation.export.start",
        presentation_id=str(presentation_id),
        export_as=export_as,
    )
    session_token = _extract_session_token(cookie_header)
    export_url, fastapi_url = _build_presentation_export_url(
        presentation_id,
        export_as,
        session_token,
        expected_revision,
        legacy_path_only_rolling_facade,
    )
    name = (title or "").strip() or str(uuid.uuid4())
    export_result = await EXPORT_TASK_SERVICE.export_from_url(
        url=export_url,
        title=safe_export_basename(sanitize_filename(name)),
        export_as=export_as,
        fastapi_url=fastapi_url,
        cookie_header=cookie_header,
    )
    log_memory(
        LOGGER,
        "presentation.export.finish",
        presentation_id=str(presentation_id),
        export_as=export_as,
    )
    return PresentationAndPath(
        presentation_id=presentation_id,
        path=export_result.path,
    )


def _resolve_hybrid_export_path(download_path: str) -> Path | None:
    parsed = urlparse(download_path)
    if parsed.path != "/api/export-presentation/file":
        return None
    names = parse_qs(parsed.query).get("name", [])
    if len(names) != 1 or not names[0]:
        return None
    app_data_directory = get_app_data_directory_env()
    if not app_data_directory:
        return None
    exports_directory = (Path(app_data_directory) / "exports").resolve()
    candidate = (exports_directory / names[0]).resolve()
    try:
        candidate.relative_to(exports_directory)
    except ValueError:
        return None
    return candidate


async def export_authored_hybrid_presentation(
    presentation_id: uuid.UUID,
    title: str,
    *,
    cookie_header: str | None,
    expected_revision: int,
    font_embedding: bool = False,
    workspace_operation_id: uuid.UUID | None = None,
) -> PresentationAndPath:
    """Run the canonical authored-hybrid exporter instead of rasterizing HTML."""

    if isinstance(expected_revision, bool) or expected_revision < 0:
        raise ValueError("expected_revision must be non-negative")
    base_url = _get_next_public_url().rstrip("/")
    headers = {"cookie": cookie_header} if cookie_header else {}
    timeout = httpx.Timeout(
        _AUTHORED_HYBRID_EXPORT_TIMEOUT_SECONDS,
        connect=20.0,
    )
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        response = await client.post(
            f"{base_url}/api/export-presentation",
            json={
                "id": str(presentation_id),
                "format": "pptx",
                "title": title,
                "pptxMode": "hybrid",
                "fontEmbedding": font_embedding,
                "expectedPresentationRevision": expected_revision,
                **(
                    {"operationId": str(workspace_operation_id)}
                    if workspace_operation_id is not None
                    else {}
                ),
            },
        )
        response.raise_for_status()
        result = response.json()
        download_path = result.get("path") if isinstance(result, dict) else None
        if (
            not isinstance(result, dict)
            or result.get("success") is not True
            or not isinstance(download_path, str)
        ):
            raise RuntimeError("authored_hybrid_export_invalid_response")
        quality = result.get("quality")
        if (
            not isinstance(quality, dict)
            or quality.get("status") == "image-only"
            or not isinstance(quality.get("editableSlides"), int)
            or quality["editableSlides"] <= 0
        ):
            raise RuntimeError("authored_hybrid_export_not_editable")
        coverage_manifest = _validated_hybrid_coverage(
            result,
            expected_revision=expected_revision,
        )
        renderer_source_sha256 = coverage_manifest["source"]["sourceDigest"]

        shared_path = _resolve_hybrid_export_path(download_path)
        if shared_path is not None and shared_path.is_file():
            _require_persisted_hybrid_coverage(shared_path, coverage_manifest)
            return AuthoredHybridPresentationAndPath(
                presentation_id=presentation_id,
                path=str(shared_path),
                coverage_manifest=coverage_manifest,
                renderer_source_sha256=renderer_source_sha256,
            )

        safe_title = safe_export_basename(
            sanitize_filename((title or "").strip() or str(presentation_id))
        )
        destination = Path(get_exports_directory()) / (
            f"{safe_title}-{str(presentation_id)[:8]}-hybrid.pptx"
        )
        temporary = destination.with_name(
            f"{destination.name}.{uuid.uuid4().hex}.exporting"
        )
        total = 0
        try:
            async with client.stream(
                "GET",
                urljoin(f"{base_url}/", download_path.lstrip("/")),
            ) as download:
                download.raise_for_status()
                with temporary.open("xb") as stream:
                    async for chunk in download.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_AUTHORED_HYBRID_EXPORT_BYTES:
                            raise RuntimeError("authored_hybrid_export_too_large")
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.replace(temporary, destination)
            _persist_hybrid_coverage(destination, coverage_manifest)
        except BaseException:
            temporary.unlink(missing_ok=True)
            Path(f"{destination}.coverage.json").unlink(missing_ok=True)
            raise
        return AuthoredHybridPresentationAndPath(
            presentation_id=presentation_id,
            path=str(destination),
            coverage_manifest=coverage_manifest,
            renderer_source_sha256=renderer_source_sha256,
        )


async def export_authored_fidelity_presentation(
    presentation_id: uuid.UUID,
    title: str,
    *,
    cookie_header: str | None,
    expected_revision: int,
) -> PresentationAndPath:
    """Capture Authored HTML as revision-sealed full-slide raster backplates."""

    if isinstance(expected_revision, bool) or expected_revision < 0:
        raise ValueError("expected_revision must be non-negative")
    base_url = _get_next_public_url().rstrip("/")
    headers = {"cookie": cookie_header} if cookie_header else {}
    timeout = httpx.Timeout(_AUTHORED_HYBRID_EXPORT_TIMEOUT_SECONDS, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        response = await client.post(
            f"{base_url}/api/export-presentation",
            json={
                "id": str(presentation_id),
                "format": "pptx",
                "title": title,
                "pptxMode": "fidelity",
                "expectedPresentationRevision": expected_revision,
            },
        )
        response.raise_for_status()
        result = response.json()
        download_path = result.get("path") if isinstance(result, dict) else None
        if (
            not isinstance(result, dict)
            or result.get("success") is not True
            or not isinstance(download_path, str)
        ):
            raise RuntimeError("authored_fidelity_export_invalid_response")
        shared_path = _resolve_hybrid_export_path(download_path)
        if shared_path is None or not shared_path.is_file():
            raise RuntimeError("authored_fidelity_export_artifact_unavailable")
        return PresentationAndPath(
            presentation_id=presentation_id,
            path=str(shared_path),
        )
