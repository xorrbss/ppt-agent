"""Narrow orchestration boundary for public v2 request adapters.

The control plane owns idempotency, state, admission, and artifact authority.
This module intentionally supplies no fallback in-memory or process-local
implementation: an unbound deployment fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import uuid

from fastapi import HTTPException, Request, status

from api.v1.auth.principal import principal_from_request
from api.v2.contracts import (
    AuthorizedDownloadGrant,
    CancelOperationReceiptV2,
    OperationEventPageV2,
    OperationReceiptV2,
    OperationStatusV2,
    PresentationExportRequestV2,
    PresentationGenerationRequestV2,
)


@dataclass(frozen=True, slots=True)
class PublicRequestActor:
    owner_id: uuid.UUID
    tenant_id: str
    correlation_id: str


class PublicV2OrchestrationError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        reason_code: str,
        message: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.status_code = status_code
        self.reason_code = reason_code
        self.message = message
        self.retry_after_seconds = retry_after_seconds


class PublicV2Orchestrator(Protocol):
    async def submit_generation(
        self,
        *,
        actor: PublicRequestActor,
        idempotency_key: str,
        request: PresentationGenerationRequestV2,
    ) -> OperationReceiptV2: ...

    async def submit_export(
        self,
        *,
        actor: PublicRequestActor,
        presentation_id: str,
        idempotency_key: str,
        request: PresentationExportRequestV2,
    ) -> OperationReceiptV2: ...

    async def get_operation(
        self,
        *,
        actor: PublicRequestActor,
        operation_id: str,
    ) -> OperationStatusV2: ...

    async def list_operation_events(
        self,
        *,
        actor: PublicRequestActor,
        operation_id: str,
        after: str | None,
        limit: int,
    ) -> OperationEventPageV2: ...

    async def cancel_operation(
        self,
        *,
        actor: PublicRequestActor,
        operation_id: str,
    ) -> CancelOperationReceiptV2: ...

    async def authorize_download(
        self,
        *,
        actor: PublicRequestActor,
        artifact_id: str,
    ) -> AuthorizedDownloadGrant: ...


def get_public_request_actor(request: Request) -> PublicRequestActor:
    principal = principal_from_request(request)
    return PublicRequestActor(
        owner_id=principal.user_id,
        tenant_id=principal.tenant_id,
        correlation_id=principal.trace_id,
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
