"""Versioned read and export-binding API for Authored semantic emission."""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import (
    ActiveAuthoredEmissionResponseV1,
    ExportAttemptDigestBindingV1,
    ExportAttemptDigestBindingV2,
    WorkbenchCapabilityProjectionV1,
)
from models.authored_vnext.semantic.lifecycle import (
    AuthoredEmissionLifecycleReceiptV1,
    AuthoredLegacyCutoverRequestV1,
    AuthoredManifestRollbackRequestV1,
    AuthoredOrderedRemakeRequestV1,
)
from models.authored_vnext.semantic.readiness import (
    CapabilityReceiptIssueRequestV2,
    CapabilityReceiptRevokeRequestV2,
    CapabilityReceiptV2,
    WorkbenchCapabilityProjectionV2,
)
from services.authored_vnext.semantic.canonical import SemanticContractError
from services.authored_vnext.semantic.export_binding import bind_existing_export_attempt
from services.authored_vnext.semantic.query import (
    get_active_authored_emission,
    get_workbench_capability_projection,
)
from services.authored_vnext.semantic.lifecycle import (
    cutover_authored_emission_to_legacy,
    remake_ordered_authored_emission,
    rollback_authored_emission_manifest,
)
from services.authored_vnext.semantic.readiness import (
    get_workbench_capability_projection_v2,
    issue_capability_receipt,
    revoke_capability_receipt,
)
from services.database import get_async_session
from utils.owner_context import get_current_owner_id, get_current_owner_is_admin


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
        "authored_semantic_lifecycle_stale_revision",
        "authored_semantic_lifecycle_stale_generation",
        "authored_semantic_lifecycle_stale_manifest",
        "authored_semantic_lifecycle_attempt_reused",
        "authored_semantic_lifecycle_cas_conflict",
        "authored_semantic_rollback_slide_cas_conflict",
    }:
        return HTTPException(status_code=409, detail={"code": error.code})
    if error.code == "authored_capability_receipt_admin_required":
        return HTTPException(status_code=403, detail={"code": error.code})
    return HTTPException(status_code=422, detail={"code": error.code})


def _admin_owner() -> uuid.UUID:
    owner_id = _owner()
    if not get_current_owner_is_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "authored_capability_receipt_admin_required"},
        )
    return owner_id


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


@AUTHORED_SEMANTIC_ROUTER.get(
    "/presentation/{presentation_id}/authored-emission/capability-projection-v2",
    response_model=WorkbenchCapabilityProjectionV2,
)
async def read_workbench_capability_projection_v2(
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> WorkbenchCapabilityProjectionV2:
    try:
        return await get_workbench_capability_projection_v2(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
    except SemanticContractError as error:
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/capability-receipts-v2",
    response_model=CapabilityReceiptV2,
    status_code=status.HTTP_201_CREATED,
)
async def create_capability_receipt_v2(
    request: CapabilityReceiptIssueRequestV2,
    session: AsyncSession = Depends(get_async_session),
) -> CapabilityReceiptV2:
    issuer_id = _admin_owner()
    try:
        receipt = await issue_capability_receipt(
            session,
            request=request,
            issuer_principal_id=issuer_id,
            issuer_is_admin=True,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/capability-receipts-v2/{receipt_digest}/revoke",
    response_model=CapabilityReceiptV2,
    status_code=status.HTTP_201_CREATED,
)
async def revoke_capability_receipt_v2(
    request: CapabilityReceiptRevokeRequestV2,
    receipt_digest: str = Path(pattern=r"^sha256:[0-9a-f]{64}$"),
    session: AsyncSession = Depends(get_async_session),
) -> CapabilityReceiptV2:
    issuer_id = _admin_owner()
    try:
        receipt = await revoke_capability_receipt(
            session,
            receipt_digest=receipt_digest,
            request=request,
            issuer_principal_id=issuer_id,
            issuer_is_admin=True,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
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


@AUTHORED_SEMANTIC_ROUTER.post(
    "/presentation/{presentation_id}/authored-emission/export-attempt-bindings-v2",
    response_model=ExportAttemptDigestBindingV2,
    status_code=status.HTTP_201_CREATED,
)
async def create_export_attempt_binding_v2(
    binding: ExportAttemptDigestBindingV2,
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> ExportAttemptDigestBindingV2:
    """Bind evidence receipts and independently validated release-authority pins."""

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
        if not isinstance(sealed, ExportAttemptDigestBindingV2):
            raise SemanticContractError("authored_semantic_export_binding_version_mismatch")
        await session.commit()
        return sealed
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


def _writes_enabled() -> None:
    if os.getenv("AUTHORED_SEMANTIC_BUNDLE_WRITES_ENABLED", "false").lower() != "true":
        raise HTTPException(
            status_code=404,
            detail={"code": "authored_semantic_bundle_writes_disabled"},
        )


@AUTHORED_SEMANTIC_ROUTER.post(
    "/presentation/{presentation_id}/authored-emission/remake-ordered-set",
    response_model=AuthoredEmissionLifecycleReceiptV1,
)
async def remake_ordered_set(
    request: AuthoredOrderedRemakeRequestV1,
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> AuthoredEmissionLifecycleReceiptV1:
    _writes_enabled()
    try:
        receipt = await remake_ordered_authored_emission(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            request=request,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/presentation/{presentation_id}/authored-emission/rollback-manifest",
    response_model=AuthoredEmissionLifecycleReceiptV1,
)
async def rollback_manifest(
    request: AuthoredManifestRollbackRequestV1,
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> AuthoredEmissionLifecycleReceiptV1:
    _writes_enabled()
    try:
        receipt = await rollback_authored_emission_manifest(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            request=request,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/presentation/{presentation_id}/authored-emission/cutover-legacy-html",
    response_model=AuthoredEmissionLifecycleReceiptV1,
)
async def cutover_legacy_html(
    request: AuthoredLegacyCutoverRequestV1,
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> AuthoredEmissionLifecycleReceiptV1:
    _writes_enabled()
    try:
        receipt = await cutover_authored_emission_to_legacy(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            request=request,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


__all__ = ["AUTHORED_SEMANTIC_ROUTER"]
