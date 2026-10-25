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
from contextlib import asynccontextmanager, contextmanager
from typing import Any
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.public_admission import PublicOperationAdmission
from models.sql.presentation import PresentationModel
from models.sql.workspace import WorkspaceOperation
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
)
from utils.owner_context import (
    get_current_tenant_id,
    get_current_owner_id,
    reset_current_owner_id,
    reset_current_tenant_id,
    set_current_owner_id,
    set_current_tenant_id,
)

PRINCIPAL_TENANT_PAYLOAD_KEY = "principal_tenant_id"
PRINCIPAL_CONFLICT_CODE = "workspace_operation_principal_conflict"


async def _sync_transaction_tenant(session: AsyncSession) -> None:
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SELECT set_config('presenton.tenant_id', :tenant, true)"),
                              {"tenant": get_current_tenant_id() or ""})


def local_development_export_bridge(request) -> bool:
    """Only the middleware's selected local admin bypass may bridge old records."""
    from models.principal import PrincipalEnvelope, is_loopback_development_request

    principal = getattr(getattr(request, "state", None), "auth_principal", None)
    return (isinstance(principal, PrincipalEnvelope)
            and principal.issuer == "presenton-loopback-development"
            and principal.method == "jwt" and principal.is_admin
            and principal.user_id == get_current_owner_id()
            and is_loopback_development_request(request))


@asynccontextmanager
async def _export_admission_principal(session, *, sources, operation_kind,
                                      allow_local_development, legacy_ok,
                                      admission_states=("succeeded",)):
    from models.principal import local_development_tenant_id
    from utils.env_settings.public_service import get_public_process_identity_settings

    owner = get_current_owner_id()
    current = _tenant(get_current_tenant_id())
    local = local_development_tenant_id()
    bridge = (allow_local_development
              and current == get_public_process_identity_settings().service_tenant_id)
    captured = {_tenant((source.request_payload or {}).get(PRINCIPAL_TENANT_PAYLOAD_KEY))
                for source in sources}
    captured.discard(None)
    if len(captured) > 1:
        raise NonRetryableWorkspaceOperationError(PRINCIPAL_CONFLICT_CODE)
    target = next(iter(captured), current)
    if target != current and not (bridge and target == local):
        raise NonRetryableWorkspaceOperationError(PRINCIPAL_CONFLICT_CODE)
    candidates = [target]
    if not captured and bridge and local != current:
        candidates.append(local)
    for tenant in candidates:
        token = set_current_tenant_id(tenant)
        switched = tenant != current
        try:
            if switched:
                await _sync_transaction_tenant(session)
            admitted = await session.scalar(select(PublicOperationAdmission.operation_id).where(
                PublicOperationAdmission.operation_id.in_([source.id for source in sources]),
                PublicOperationAdmission.owner_id == owner,
                PublicOperationAdmission.tenant_id == tenant,
                PublicOperationAdmission.operation_kind == operation_kind,
                PublicOperationAdmission.state.in_(admission_states),
            ).limit(1))
            if admitted is not None:
                yield
                return
        finally:
            reset_current_tenant_id(token)
            if switched and session.in_transaction():
                await _sync_transaction_tenant(session)
    if not legacy_ok or target != current:
        raise NonRetryableWorkspaceOperationError(PRINCIPAL_CONFLICT_CODE)
    yield


@asynccontextmanager
async def generation_action_principal(session: AsyncSession, *, source: WorkspaceOperation,
                                      action: str, allow_local_development: bool = False):
    """Use the exact owned generation's declared tenant for cancel/retry only.

    The HTTP boundary must verify the existing development loopback bridge.
    Regular principals cannot switch tenants, and neither database authority
    nor the action's ordinary state/CAS rules change inside this scope.
    """
    if (action not in {"cancel", "retry"} or get_current_owner_id() is None
            or source.owner_id != get_current_owner_id()
            or source.kind != "presentation.generate"
            or source.execution_profile_version != "public-generation-export/v2"
            or len(payload_tenants(source.request_payload)) != 1
            or not _tenant(source.request_payload.get(PRINCIPAL_TENANT_PAYLOAD_KEY))):
        raise NonRetryableWorkspaceOperationError(PRINCIPAL_CONFLICT_CODE)
    # Terminal admission visibility also permits the existing exact action
    # receipt replay. The ordinary cancel state/CAS rules still reject a new
    # cancel action against a terminal operation.
    states = ("queued", "active", "succeeded", "failed", "cancelled",
              "deadline_exceeded", "budget_exhausted")
    async with _export_admission_principal(session, sources=[source], operation_kind="generation",
        allow_local_development=allow_local_development, legacy_ok=False, admission_states=states):
        yield


@asynccontextmanager
async def presentation_export_principal(session: AsyncSession, *, presentation_id: uuid.UUID,
                                        allow_local_development: bool = False):
    """Bind export work to its owned generation admission, with a local-only bridge.

    The HTTP boundary alone may enable the bridge after proving a development
    loopback request. No request payload tenant is trusted, and owner filters
    and PostgreSQL RLS stay enabled throughout the scoped handoff.
    """
    owner = get_current_owner_id()
    if owner is None:
        yield
        return
    presentation = await session.scalar(select(PresentationModel).where(
        PresentationModel.id == presentation_id, PresentationModel.owner_id == owner))
    if presentation is None:
        raise NonRetryableWorkspaceOperationError(PRINCIPAL_CONFLICT_CODE)
    sources = list((await session.scalars(select(WorkspaceOperation).where(
        WorkspaceOperation.presentation_id == presentation_id,
        WorkspaceOperation.owner_id == owner,
        WorkspaceOperation.kind == "presentation.generate",
        WorkspaceOperation.state == "succeeded",
    ).order_by(WorkspaceOperation.updated_at.desc()))).all())
    if not sources:
        yield
        return
    async with _export_admission_principal(session, sources=sources, operation_kind="generation",
                                           allow_local_development=allow_local_development, legacy_ok=True):
        yield


@asynccontextmanager
async def bound_export_operation_principal(session: AsyncSession, *, operation_id: uuid.UUID,
                                           allow_local_development: bool = False):
    owner = get_current_owner_id()
    if owner is None:
        raise NonRetryableWorkspaceOperationError(PRINCIPAL_CONFLICT_CODE)
    source = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == operation_id, WorkspaceOperation.owner_id == owner,
        WorkspaceOperation.kind.in_(["export.pptx", "export.pdf"]),
        WorkspaceOperation.state == "succeeded"))
    if source is None:
        raise NonRetryableWorkspaceOperationError(PRINCIPAL_CONFLICT_CODE)
    async with _export_admission_principal(session, sources=[source], operation_kind="export",
                                           allow_local_development=allow_local_development, legacy_ok=False):
        yield


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
    "generation_action_principal",
    "presentation_export_principal",
    "bound_export_operation_principal",
    "local_development_export_bridge",
    "payload_tenants",
    "resolve_operation_tenant",
    "submitting_principal_tenant",
]
