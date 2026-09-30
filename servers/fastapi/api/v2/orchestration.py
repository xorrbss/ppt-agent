"""Narrow orchestration boundary for public v2 request adapters.

The control plane owns idempotency, state, admission, and artifact authority.
This module intentionally supplies no fallback in-memory or process-local
implementation: an unbound deployment fails closed.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from api.v1.auth.principal import principal_from_request
from services.public_v2_orchestration_types import (
    PublicRequestActor,
    PublicV2OrchestrationError,
    PublicV2Orchestrator,
)

__all__ = [
    "PublicRequestActor",
    "PublicV2OrchestrationError",
    "PublicV2Orchestrator",
    "get_public_request_actor",
    "get_public_v2_orchestrator",
    "require_public_actor_scope",
]


def get_public_request_actor(request: Request) -> PublicRequestActor:
    principal = principal_from_request(request)
    return PublicRequestActor(
        owner_id=principal.user_id,
        tenant_id=principal.tenant_id,
        correlation_id=principal.trace_id,
        scopes=frozenset(principal.scopes),
    )


def require_public_actor_scope(actor: PublicRequestActor, required_scope: str) -> None:
    if required_scope not in actor.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"reasonCode": "principal_scope_required"},
        )


def get_public_v2_orchestrator(request: Request) -> PublicV2Orchestrator:
    """Return the app-bound authority adapter or fail closed when unbound."""

    orchestrator = getattr(request.app.state, "public_v2_orchestrator", None)
    if orchestrator is not None:
        return orchestrator

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"reasonCode": "operation_authority_unavailable"},
    )
