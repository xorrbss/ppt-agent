"""Versioned read and export-binding API for Authored semantic emission."""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import (
    ActiveAuthoredEmissionResponseV1,
    ExportAttemptDigestBindingV1,
    WorkbenchCapabilityProjectionV1,
)
from services.authored_vnext.semantic.canonical import SemanticContractError
from services.authored_vnext.semantic.export_binding import bind_existing_export_attempt
from services.authored_vnext.semantic.query import (
    get_active_authored_emission,
    get_workbench_capability_projection,
)
from services.database import get_async_session
from utils.owner_context import get_current_owner_id


AUTHORED_SEMANTIC_ROUTER = APIRouter(tags=["Authored Semantic Emission"])


def _owner() -> uuid.UUID:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authored_semantic_owner_required"},
        )
    return owner_id


def _error(error: SemanticContractError) -> HTTPException:
    if error.code in {
        "authored_semantic_presentation_not_found",
        "authored_semantic_export_attempt_not_found",
    }:
        return HTTPException(status_code=404, detail={"code": error.code})
    if error.code in {
        "authored_semantic_cas_conflict",
        "authored_semantic_export_stale_manifest",
        "authored_semantic_export_attempt_already_bound",
    }:
        return HTTPException(status_code=409, detail={"code": error.code})
    return HTTPException(status_code=422, detail={"code": error.code})


@AUTHORED_SEMANTIC_ROUTER.get(
    "/presentation/{presentation_id}/authored-emission/active",
    response_model=ActiveAuthoredEmissionResponseV1,
)
async def read_active_authored_emission(
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> ActiveAuthoredEmissionResponseV1:
    try:
        return await get_active_authored_emission(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
    except SemanticContractError as error:
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.get(
    "/presentation/{presentation_id}/authored-emission/capability-projection",
    response_model=WorkbenchCapabilityProjectionV1,
)
async def read_workbench_capability_projection(
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> WorkbenchCapabilityProjectionV1:
    try:
        return await get_workbench_capability_projection(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
    except SemanticContractError as error:
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/presentation/{presentation_id}/authored-emission/export-attempt-bindings",
    response_model=ExportAttemptDigestBindingV1,
    status_code=status.HTTP_201_CREATED,
)
async def create_export_attempt_binding(
    binding: ExportAttemptDigestBindingV1,
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> ExportAttemptDigestBindingV1:
    if os.getenv("AUTHORED_SEMANTIC_BUNDLE_WRITES_ENABLED", "false").lower() != "true":
        raise HTTPException(
            status_code=404,
            detail={"code": "authored_semantic_bundle_writes_disabled"},
        )
    if binding.presentation_id != presentation_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "authored_semantic_path_mismatch"},
        )
    try:
        sealed = await bind_existing_export_attempt(
            session,
            owner_id=_owner(),
            binding=binding,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return sealed
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


__all__ = ["AUTHORED_SEMANTIC_ROUTER"]
