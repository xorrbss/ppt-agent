"""Owner-scoped source-content analysis API."""

# FastAPI dependency declarations intentionally call Depends/Form in defaults.
# ruff: noqa: B008

from __future__ import annotations

from typing import Annotated, Literal
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from services.database import get_async_session
from services.source_content.contracts import SourceContentAnalysisReceipt
from services.source_content.errors import SourceContentError
from services.source_content.manifest_store import (
    analyze_reference_snapshot,
    get_source_content_manifest_status,
)
from utils.owner_context import get_current_owner_id


SOURCE_CONTENT_ROUTER = APIRouter(
    prefix="/source-content/v1",
    tags=["Source Content"],
)


def _http_error(error: SourceContentError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, **error.detail},
    )


@SOURCE_CONTENT_ROUTER.post(
    "/analyze",
    response_model=SourceContentAnalysisReceipt,
    response_model_by_alias=True,
)
async def analyze_source_content(
    contract_version: Annotated[
        Literal["source-content-analysis-request/v1"],
        Form(alias="contractVersion"),
    ],
    reference_snapshot_id: Annotated[
        uuid.UUID,
        Form(alias="referenceSnapshotId"),
    ],
    session: AsyncSession = Depends(get_async_session),
) -> SourceContentAnalysisReceipt:
    del contract_version
    try:
        return await analyze_reference_snapshot(
            session,
            reference_snapshot_id=reference_snapshot_id,
        )
    except SourceContentError as error:
        raise _http_error(error) from error


@SOURCE_CONTENT_ROUTER.get(
    "/manifests/{manifest_id}/status",
    response_model=SourceContentAnalysisReceipt,
    response_model_by_alias=True,
)
async def source_content_manifest_status(
    manifest_id: str,
    digest: str = Query(...),
    session: AsyncSession = Depends(get_async_session),
) -> SourceContentAnalysisReceipt:
    try:
        return await get_source_content_manifest_status(
            session,
            owner_id=get_current_owner_id(),
            manifest_id=manifest_id,
            digest=digest,
        )
    except SourceContentError as error:
        raise _http_error(error) from error
