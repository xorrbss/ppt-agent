"""Contracts shared by reference providers and generation admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Protocol
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.assets import AssetBinding, AssetGateway


class ReferenceProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status_code: int = 422,
        retryable: bool = False,
        filename: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.filename = filename


@dataclass(frozen=True, slots=True)
class NormalizedReference:
    provider_id: str
    source_revision: str
    normalized_payload: dict[str, Any]
    asset_bindings: tuple[AssetBinding, ...] = ()
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReferenceSnapshotView:
    """Deeply immutable view: payload is retained as canonical JSON text."""

    snapshot_id: uuid.UUID
    provider_id: str
    source_revision: str
    source_fingerprint: str
    normalized_payload_json: str
    asset_bindings: tuple[AssetBinding, ...]
    content_sha256: str
    conversation_id: uuid.UUID | None
    created_at: datetime
    expires_at: datetime | None

    def payload(self) -> dict[str, Any]:
        """Return a fresh copy so callers cannot mutate the admitted snapshot."""

        value = json.loads(self.normalized_payload_json)
        if not isinstance(value, dict):
            raise ReferenceProviderError(
                "reference_snapshot_payload_invalid", status_code=409
            )
        return value


class ReferenceSourceProvider(Protocol):
    provider_id: str

    async def normalize(
        self,
        session: AsyncSession,
        *,
        request: dict[str, Any],
        asset_gateway: AssetGateway,
        conversation_id: uuid.UUID | None,
    ) -> NormalizedReference: ...
