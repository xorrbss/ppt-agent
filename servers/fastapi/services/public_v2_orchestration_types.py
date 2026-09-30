"""Types shared by the public v2 orchestration boundary and its adapters.

``api.v2.orchestration`` binds these to FastAPI requests; the services that
implement the orchestrator (``public_v2_orchestrator``, ``public_v2_request_bridge``)
raise and accept them without importing the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import uuid

from models.public_v2_contracts import (
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
    scopes: frozenset[str] = frozenset()


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


__all__ = ["PublicRequestActor", "PublicV2OrchestrationError", "PublicV2Orchestrator"]
