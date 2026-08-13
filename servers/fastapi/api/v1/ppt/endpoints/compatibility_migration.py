"""HTTP contract for authored-only frozen compatibility and upgrade forks."""

from __future__ import annotations

import os
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from models.compatibility_migration import (
    CompatibilityErrorDetail,
    FrozenOperationDecision,
    FrozenRuntimeDescriptor,
    UpgradeForkRequest,
    UpgradeForkResponse,
)
from services.compatibility_migration.archive import ArchiveManifestError
from services.compatibility_migration.feature_manifest import (
    build_authored_only_capability_manifest,
)
from services.compatibility_migration.runtime import (
    FrozenCompatibilityError,
    authorize_frozen_operation,
    get_frozen_runtime_descriptor,
)
from services.compatibility_migration.upgrade import (
    CompatibilityUpgradeError,
    upgrade_presentation_fork,
)
from services.database import get_async_session

COMPATIBILITY_MIGRATION_ROUTER = APIRouter(
    prefix="/compatibility",
    tags=["Compatibility Migration"],
)


def _error_detail(code: str) -> dict[str, str]:
    return CompatibilityErrorDetail(code=code, message=code).model_dump()


def _archive_signing_config() -> tuple[bytes, str]:
    return (
        os.getenv("COMPATIBILITY_ARCHIVE_SIGNING_KEY", "").encode("utf-8"),
        os.getenv("COMPATIBILITY_ARCHIVE_SIGNING_KEY_ID", "compatibility-v1"),
    )


@COMPATIBILITY_MIGRATION_ROUTER.get("/capabilities")
async def get_compatibility_capabilities() -> dict[str, object]:
    return build_authored_only_capability_manifest()


@COMPATIBILITY_MIGRATION_ROUTER.get(
    "/presentations/{presentation_id}/runtime",
    response_model=FrozenRuntimeDescriptor,
)
async def get_compatibility_runtime(
    presentation_id: uuid.UUID,
    sql_session: Annotated[AsyncSession, Depends(get_async_session)],
) -> FrozenRuntimeDescriptor:
    try:
        return await get_frozen_runtime_descriptor(sql_session, presentation_id)
    except FrozenCompatibilityError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=_error_detail(error.code),
        ) from error


@COMPATIBILITY_MIGRATION_ROUTER.post(
    "/presentations/{presentation_id}/runtime/{operation}",
    response_model=FrozenOperationDecision,
)
async def authorize_compatibility_runtime_operation(
    presentation_id: uuid.UUID,
    operation: str,
    sql_session: Annotated[AsyncSession, Depends(get_async_session)],
) -> FrozenOperationDecision:
    try:
        return await authorize_frozen_operation(
            sql_session,
            presentation_id,
            operation,
        )
    except FrozenCompatibilityError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=_error_detail(error.code),
        ) from error


@COMPATIBILITY_MIGRATION_ROUTER.post(
    "/presentations/{presentation_id}/upgrade-fork",
    response_model=UpgradeForkResponse,
    status_code=201,
)
async def create_authored_upgrade_fork(
    presentation_id: uuid.UUID,
    request: UpgradeForkRequest,
    sql_session: Annotated[AsyncSession, Depends(get_async_session)],
) -> UpgradeForkResponse:
    signing_key, signing_key_id = _archive_signing_config()
    try:
        return await upgrade_presentation_fork(
            sql_session,
            source_presentation_id=presentation_id,
            request=request,
            archive_signing_key=signing_key,
            archive_signing_key_id=signing_key_id,
        )
    except CompatibilityUpgradeError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=_error_detail(error.code),
        ) from error
    except ArchiveManifestError as error:
        raise HTTPException(
            status_code=503,
            detail=_error_detail(error.code),
        ) from error


__all__ = ["COMPATIBILITY_MIGRATION_ROUTER"]
