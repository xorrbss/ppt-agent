"""Document upload for the raster-redesign product-path driver
(generate_raster_deck.py): streams the source file to the asset gateway
without ever holding the whole file in memory.

Split out of the original generate_raster_deck.py (2026-09-25 eval suite) to
keep each module under the repo's file-size limit; behavior is unchanged.
"""
from __future__ import annotations

import mimetypes
import shutil
import tempfile
import urllib.request
import uuid
from pathlib import Path

from raster_deck_http import _urlopen_json

_EXTRA_MIME_TYPES = {
    # mimetypes.guess_type doesn't know Office Open XML types on all platforms
    # (confirmed missing on this Windows Python install); the asset gateway's
    # media-type allowlist requires the exact canonical MIME type, so a wrong
    # guess here fails downstream with asset_media_type_disallowed even though
    # the .pptx extension itself is allowed.
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}


def upload_document(base: str, source_path: Path) -> dict:
    """Upload without ever holding the whole source file (or a copy of it) in
    memory: the multipart envelope is assembled on disk in a temp file by
    streaming the source in chunks, then that temp file is streamed to the
    server as the request body (Python's http.client reads file-like `data`
    in blocks rather than buffering it whole)."""
    content_type = (
        _EXTRA_MIME_TYPES.get(source_path.suffix.lower())
        or mimetypes.guess_type(source_path.name)[0]
        or "application/octet-stream"
    )
    boundary = uuid.uuid4().hex
    fd, tmp_name = tempfile.mkstemp(suffix=".multipart")
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "wb") as tmp:
            tmp.write(f"--{boundary}\r\n".encode())
            tmp.write(
                f'Content-Disposition: form-data; name="file"; filename="{source_path.name}"\r\n'.encode()
            )
            tmp.write(f"Content-Type: {content_type}\r\n\r\n".encode())
            with open(source_path, "rb") as src:
                shutil.copyfileobj(src, tmp, length=1024 * 1024)
            tmp.write(f"\r\n--{boundary}--\r\n".encode())
        size = tmp_path.stat().st_size
        with open(tmp_path, "rb") as body_stream:
            req = urllib.request.Request(
                f"{base}/api/v1/ppt/assets/documents",
                data=body_stream,
                method="POST",
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(size),
                },
            )
            result = _urlopen_json(req, method="POST", path="/api/v1/ppt/assets/documents", timeout=300)
    finally:
        tmp_path.unlink(missing_ok=True)
    print(f"[upload] asset_id={result['asset_id']} sha256={result['sha256'][:12]}...")
    return result
