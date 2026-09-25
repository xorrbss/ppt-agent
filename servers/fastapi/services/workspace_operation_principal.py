"""The principal a workspace operation handler runs as.

A request runs with the owner and tenant that ``api.middlewares`` puts into
``utils.owner_context``.  An operation handler runs later, in the runner (the
embedded worker or an external worker process), where no request exists: the
runner restored the owner but never the tenant, so every tenant-bound read in
a handler -- e.g. ``svg_document_mutations.read_svg_slide_assets`` while a
generated SVG deck with image or table assets is published -- failed with
``svg_asset_principal_required``.

The tenant is resolved from durable operation state, never invented:

1. the operation's public admission row (``tenant_id``), the authority for
   admitted generation and export operations;
2. the submitting principal's tenant captured at command submission
   (``request_payload["principal_tenant_id"]``,
   ``services/workspace_service.py``);
3. the older per-kind carriers (``request_payload["context"]["tenant_id"]``
   for slide order, ``request_payload["tenant_id"]`` for raster analysis).

Sources that disagree fail the operation with
``workspace_operation_principal_conflict``.  No source at all (rows submitted
before the capture, or ``DISABLE_AUTH`` where there is no principal) runs the
handler with what exists -- the owner only -- and tenant-bound code keeps
failing with its own explicit code.

The principal is set only inside the handler task, so the runner's own claim,
lease, heartbeat and outbox bookkeeping keeps its previous scoping.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.public_admission import PublicOperationAdmission
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
)
from utils.owner_context import (
    get_current_tenant_id,
    reset_current_owner_id,
    reset_current_tenant_id,
    set_current_owner_id,
    set_current_tenant_id,
)

PRINCIPAL_TENANT_PAYLOAD_KEY = "principal_tenant_id"
PRINCIPAL_CONFLICT_CODE = "workspace_operation_principal_conflict"


def _tenant(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def payload_tenants(request_payload: Mapping[str, Any] | None) -> set[str]:
    payload = request_payload if isinstance(request_payload, Mapping) else {}
    context = payload.get("context")
    candidates = (
        payload.get(PRINCIPAL_TENANT_PAYLOAD_KEY),
        context.get("tenant_id") if isinstance(context, Mapping) else None,
        payload.get("tenant_id"),
    )
    return {tenant for tenant in map(_tenant, candidates) if tenant is not None}


async def resolve_operation_tenant(
    claim: WorkspaceOperationClaim,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> str | None:
    """Return the operation's tenant, ``None`` when it has none, or raise."""

    if claim.owner_id is None:
        return None
    async with session_factory() as session:
        admitted = await session.scalar(
            select(PublicOperationAdmission.tenant_id).where(
                PublicOperationAdmission.operation_id == claim.operation_id,
                PublicOperationAdmission.owner_id == claim.owner_id,
            )
        )
    tenants = payload_tenants(claim.request_payload)
    if _tenant(admitted) is not None:
        tenants.add(_tenant(admitted))
    if len(tenants) > 1:
        raise NonRetryableWorkspaceOperationError(PRINCIPAL_CONFLICT_CODE)
    return next(iter(tenants), None)


def submitting_principal_tenant() -> dict[str, str]:
    """The submitting request's tenant as a ``request_payload`` entry.

    Stored outside the command's request hash, so it never changes idempotent
    resubmission; empty without a principal (``DISABLE_AUTH``).
    """

    tenant = _tenant(get_current_tenant_id())
    return {PRINCIPAL_TENANT_PAYLOAD_KEY: tenant} if tenant else {}


@contextmanager
def operation_principal(owner_id: uuid.UUID | None, tenant_id: str | None) -> Iterator[None]:
    owner_token = set_current_owner_id(owner_id)
    tenant_token = set_current_tenant_id(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant_id(tenant_token)
        reset_current_owner_id(owner_token)


__all__ = [
    "PRINCIPAL_CONFLICT_CODE",
    "PRINCIPAL_TENANT_PAYLOAD_KEY",
    "operation_principal",
    "payload_tenants",
    "resolve_operation_tenant",
    "submitting_principal_tenant",
]
