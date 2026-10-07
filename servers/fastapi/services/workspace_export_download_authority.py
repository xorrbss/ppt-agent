"""Completed workspace exports use their own fenced publication authority.

Legacy workspace exports already have immutable public artifacts and admission
rows, but do not claim the public-v2 operation lifecycle contract. Downloading
those artifacts must validate that contract explicitly instead of treating the
absence of a public-v2 contract as a transient authority outage.
"""
from __future__ import annotations

from sqlalchemy import select

from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation
from services.workspace_operation_principal import payload_tenants


async def completed_workspace_export_operation(session, *, artifact):
    """Return only a terminal legacy export with exact owner/tenant admission.

    The caller must additionally compare the immutable publication's operation,
    revision and fencing token. This never accepts a malformed public-v2 row.
    """
    if artifact.kind not in {"presentation-pptx", "presentation-pdf"} or artifact.retention_class != "published":
        return None
    row = (await session.execute(select(WorkspaceOperation, PublicOperationAdmission).join(
        PublicOperationAdmission,
        PublicOperationAdmission.operation_id == WorkspaceOperation.id,
    ).where(
        WorkspaceOperation.id == artifact.created_by_operation_id,
        WorkspaceOperation.owner_id == artifact.owner_id,
        WorkspaceOperation.owner_scope == str(artifact.owner_id),
        WorkspaceOperation.kind == "export." + artifact.kind.removeprefix("presentation-"),
        WorkspaceOperation.state == "succeeded",
        WorkspaceOperation.contract_version.is_(None),
        WorkspaceOperation.execution_profile_version.is_(None),
        WorkspaceOperation.phase.is_(None),
        WorkspaceOperation.deadline_at.is_(None),
        PublicOperationAdmission.owner_id == artifact.owner_id,
        PublicOperationAdmission.tenant_id == artifact.tenant_id,
        PublicOperationAdmission.operation_kind == "export",
        PublicOperationAdmission.state == "succeeded",
        PublicOperationAdmission.fence == 0,
    ))).one_or_none()
    if row is None:
        return None
    operation, _admission = row
    if payload_tenants(operation.request_payload) - {artifact.tenant_id}:
        return None
    return operation
