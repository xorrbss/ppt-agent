import hashlib
import os
import logging
from typing import Literal
from urllib.parse import urlencode
import uuid
from pathvalidate import sanitize_filename
from models.presentation_and_path import PresentationAndPath
from services.export_task_service import EXPORT_TASK_SERVICE
from utils.runtime_limits import log_memory

LOGGER = logging.getLogger(__name__)


def _get_next_public_url() -> str:
    return (os.getenv("NEXT_PUBLIC_URL") or "").strip() or "http://127.0.0.1"


def _get_next_public_fastapi_url() -> str | None:
    value = (os.getenv("NEXT_PUBLIC_FAST_API") or "").strip()
    return value or None


def _build_presentation_export_url(presentation_id: uuid.UUID) -> tuple[str, str | None]:
    params = {"id": str(presentation_id)}
    fastapi_url = _get_next_public_fastapi_url()
    if fastapi_url:
        params["fastapiUrl"] = fastapi_url
    return (
        f"{_get_next_public_url().rstrip('/')}/pdf-maker?{urlencode(params)}",
        fastapi_url,
    )


def _safe_filename(name: str, max_bytes: int = 200) -> str:
    """Truncate filename to avoid OSError on long names (e.g. Japanese characters).
    
    On most Linux/macOS systems, the filename limit is 255 bytes.
    Japanese characters take 3 bytes each in UTF-8, so a short-looking
    title can easily exceed this limit and raise an OSError.
    """
    encoded = name.encode('utf-8')
    if len(encoded) <= max_bytes:
        return name
    hash_suffix = hashlib.md5(encoded).hexdigest()[:8]
    truncated = encoded[:max_bytes - 9].decode('utf-8', errors='ignore')
    return f"{truncated}_{hash_suffix}"


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
    export_url, fastapi_url = _build_presentation_export_url(presentation_id)
    export_result = await EXPORT_TASK_SERVICE.export_from_url(
        url=export_url,
        title=_safe_filename(sanitize_filename(title or str(uuid.uuid4()))),
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
