import os
import logging
from typing import Literal
from urllib.parse import urlencode
import uuid

from pathvalidate import sanitize_filename

from models.presentation_and_path import PresentationAndPath
from utils.filename_utils import safe_export_basename
from services.export_task_service import EXPORT_TASK_SERVICE
from utils.runtime_limits import log_memory
from utils.simple_auth import SESSION_COOKIE_NAME


LOGGER = logging.getLogger(__name__)


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
    presentation_id: uuid.UUID, session_token: str | None = None
) -> tuple[str, str | None]:
    params = {"id": str(presentation_id)}
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
) -> PresentationAndPath:
    log_memory(
        LOGGER,
        "presentation.export.start",
        presentation_id=str(presentation_id),
        export_as=export_as,
    )
    session_token = _extract_session_token(cookie_header)
    export_url, fastapi_url = _build_presentation_export_url(
        presentation_id, session_token
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
