"""In-memory CAS narrative checkpoint repository for tests."""

from __future__ import annotations

import asyncio
from uuid import UUID

from models.authored_vnext.narrative_ir_contracts import NarrativeCheckpointV1
from services.authored_vnext.narrative_ir.checkpoint import (
    NarrativeCheckpointError,
    validate_checkpoint,
)


class InMemoryNarrativeCheckpointRepository:
    """CAS reference implementation; product wiring supplies durable SQL/CAS."""

    def __init__(self) -> None:
        self._items: dict[UUID, NarrativeCheckpointV1] = {}
        self._lock = asyncio.Lock()

    async def load(self, operation_id: UUID) -> NarrativeCheckpointV1 | None:
        async with self._lock:
            return self._items.get(operation_id)

    async def commit(
        self,
        checkpoint: NarrativeCheckpointV1,
        *,
        expected_checkpoint_digest: str | None,
    ) -> NarrativeCheckpointV1:
        checkpoint = validate_checkpoint(checkpoint)
        async with self._lock:
            current = self._items.get(checkpoint.operation_id)
            if current is not None:
                if (
                    expected_checkpoint_digest is not None
                    and current.checkpoint_digest != expected_checkpoint_digest
                ):
                    raise NarrativeCheckpointError("narrative_checkpoint_cas_conflict")
                if checkpoint.fencing_token < current.fencing_token:
                    raise NarrativeCheckpointError("narrative_checkpoint_stale_fence")
                if checkpoint.lineage_digest != current.lineage_digest:
                    raise NarrativeCheckpointError("narrative_checkpoint_retry_drift")
            elif expected_checkpoint_digest is not None:
                raise NarrativeCheckpointError("narrative_checkpoint_cas_conflict")
            self._items[checkpoint.operation_id] = checkpoint
            return checkpoint
