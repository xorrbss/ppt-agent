"""Public error mapping for the async generation admission request.

Admission runs artifact-store work (raster approval re-verifies every approved
crop under the CAS key guard). Its failures must reach the client as typed,
allow-listed codes through the central error envelope, and reach the log with
the request id and traceback, instead of one opaque 500.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from services.artifact_store import ArtifactKeyBusy, ArtifactStoreError
from services.workspace_error_registry import WorkspaceAPIException

LOGGER = logging.getLogger(__name__)

GENERATION_FAILED_DETAIL = "Presentation generation failed"


def generation_admission_http_error(error: Exception, *, request_id: str | None) -> HTTPException:
    """Return the HTTP error for ``error``; call only from its ``except`` block."""

    if isinstance(error, HTTPException):
        return error
    if isinstance(error, ArtifactKeyBusy):
        # Contention on a shared content key: the transaction unwinds and the
        # client may retry the same request.
        LOGGER.warning(
            "generation admission artifact key busy request_id=%s code=%s",
            request_id, error.code, exc_info=True,
        )
        return WorkspaceAPIException("artifact_key_busy")
    if isinstance(error, ArtifactStoreError):
        # Store messages are stable internal codes, never payload or paths.
        LOGGER.exception(
            "generation admission artifact store failure request_id=%s code=%s", request_id, error,
        )
        return WorkspaceAPIException("artifact_store_failed")
    LOGGER.exception("generation admission failed unexpectedly request_id=%s", request_id)
    return HTTPException(status_code=500, detail=GENERATION_FAILED_DETAIL)
