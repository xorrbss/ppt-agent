"""Digest-bound Narrative IR checkpoints with retry-stable lineage semantics."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import hashlib
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from .contracts import (
    FrameSearchReceiptV1,
    FrameSelectionReceiptV1,
    NarrativeCheckpointV1,
    NarrativeIRV1,
    ProviderCallBudgetV1,
    RouteResolutionReceiptV1,
    model_payload,
)
from .validation import canonical_digest, validate_narrative_ir


class NarrativeCheckpointError(ValueError):
    code = "narrative_checkpoint_invalid"

    def __init__(self, code: str | None = None) -> None:
        resolved = code or self.code
        super().__init__(resolved)
        self.code = resolved


class NarrativeCheckpointRepository(Protocol):
    async def load(self, operation_id: UUID) -> NarrativeCheckpointV1 | None: ...

    async def commit(
        self,
        checkpoint: NarrativeCheckpointV1,
        *,
        expected_checkpoint_digest: str | None,
    ) -> NarrativeCheckpointV1: ...


def attempt_id(attempt_token: str) -> str:
    if not attempt_token:
        raise NarrativeCheckpointError("narrative_checkpoint_attempt_missing")
    return "sha256:" + hashlib.sha256(attempt_token.encode("utf-8")).hexdigest()


def _lineage_payload(
    *,
    operation_id: UUID,
    job_id: UUID,
    strategy_receipt_digest: str,
    narrative: NarrativeIRV1,
    provider_call_budget: ProviderCallBudgetV1,
    frame_search_receipt: FrameSearchReceiptV1 | None,
    frame_selection_receipt: FrameSelectionReceiptV1 | None,
    route_resolution_receipt: RouteResolutionReceiptV1,
) -> dict[str, object]:
    return {
        "operationId": str(operation_id),
        "jobId": str(job_id),
        "strategyReceiptDigest": strategy_receipt_digest,
        "narrativeDigest": narrative.narrative_digest,
        "providerCallBudget": model_payload(provider_call_budget),
        "frameSearchReceiptDigest": (
            frame_search_receipt.receipt_digest if frame_search_receipt else None
        ),
        "frameSelectionReceiptDigest": (
            frame_selection_receipt.receipt_digest if frame_selection_receipt else None
        ),
        "routeResolutionReceiptDigest": route_resolution_receipt.receipt_digest,
    }


def seal_checkpoint(
    *,
    operation_id: UUID,
    job_id: UUID,
    attempt_token: str,
    fencing_token: int,
    strategy_receipt_digest: str,
    narrative: NarrativeIRV1,
    provider_call_budget: ProviderCallBudgetV1,
    frame_search_receipt: FrameSearchReceiptV1 | None,
    frame_selection_receipt: FrameSelectionReceiptV1 | None,
    route_resolution_receipt: RouteResolutionReceiptV1,
) -> NarrativeCheckpointV1:
    narrative = validate_narrative_ir(narrative)
    if fencing_token < 1:
        raise NarrativeCheckpointError("narrative_checkpoint_fence_invalid")
    if route_resolution_receipt.operation_id != operation_id:
        raise NarrativeCheckpointError("narrative_checkpoint_operation_mismatch")
    if route_resolution_receipt.narrative_digest != narrative.narrative_digest:
        raise NarrativeCheckpointError("narrative_checkpoint_narrative_mismatch")
    if model_payload(route_resolution_receipt.provider_call_budget) != model_payload(
        provider_call_budget
    ):
        raise NarrativeCheckpointError("narrative_checkpoint_provider_budget_mismatch")
    if frame_search_receipt is not None:
        if (
            frame_search_receipt.operation_id != operation_id
            or frame_search_receipt.narrative_digest != narrative.narrative_digest
        ):
            raise NarrativeCheckpointError("narrative_checkpoint_search_mismatch")
    if frame_selection_receipt is not None:
        if (
            frame_search_receipt is None
            or frame_selection_receipt.search_receipt_digest
            != frame_search_receipt.receipt_digest
        ):
            raise NarrativeCheckpointError("narrative_checkpoint_selection_mismatch")
    lineage_payload = _lineage_payload(
        operation_id=operation_id,
        job_id=job_id,
        strategy_receipt_digest=strategy_receipt_digest,
        narrative=narrative,
        provider_call_budget=provider_call_budget,
        frame_search_receipt=frame_search_receipt,
        frame_selection_receipt=frame_selection_receipt,
        route_resolution_receipt=route_resolution_receipt,
    )
    lineage_digest = canonical_digest(lineage_payload)
    unsigned = {
        "schemaVersion": "narrative-ir-checkpoint/v1",
        "operationId": str(operation_id),
        "jobId": str(job_id),
        "attemptId": attempt_id(attempt_token),
        "fencingToken": fencing_token,
        "strategyReceiptDigest": strategy_receipt_digest,
        "narrative": model_payload(narrative),
        "providerCallBudget": model_payload(provider_call_budget),
        "frameSearchReceipt": (
            model_payload(frame_search_receipt) if frame_search_receipt else None
        ),
        "frameSelectionReceipt": (
            model_payload(frame_selection_receipt) if frame_selection_receipt else None
        ),
        "routeResolutionReceipt": model_payload(route_resolution_receipt),
        "lineageDigest": lineage_digest,
    }
    try:
        return NarrativeCheckpointV1.model_validate(
            {**unsigned, "checkpointDigest": canonical_digest(unsigned)}
        )
    except ValidationError as error:
        raise NarrativeCheckpointError("narrative_checkpoint_seal_invalid") from error


def validate_checkpoint(
    value: NarrativeCheckpointV1 | Mapping[str, object],
    *,
    operation_id: UUID | None = None,
    job_id: UUID | None = None,
    strategy_receipt_digest: str | None = None,
) -> NarrativeCheckpointV1:
    try:
        checkpoint = (
            value
            if isinstance(value, NarrativeCheckpointV1)
            else NarrativeCheckpointV1.model_validate(value)
        )
    except ValidationError as error:
        raise NarrativeCheckpointError("narrative_checkpoint_schema_invalid") from error
    payload = model_payload(checkpoint)
    supplied_checkpoint = payload.pop("checkpointDigest")
    if supplied_checkpoint != canonical_digest(payload):
        raise NarrativeCheckpointError("narrative_checkpoint_digest_mismatch")
    lineage = _lineage_payload(
        operation_id=checkpoint.operation_id,
        job_id=checkpoint.job_id,
        strategy_receipt_digest=checkpoint.strategy_receipt_digest,
        narrative=checkpoint.narrative,
        provider_call_budget=checkpoint.provider_call_budget,
        frame_search_receipt=checkpoint.frame_search_receipt,
        frame_selection_receipt=checkpoint.frame_selection_receipt,
        route_resolution_receipt=checkpoint.route_resolution_receipt,
    )
    if checkpoint.lineage_digest != canonical_digest(lineage):
        raise NarrativeCheckpointError("narrative_checkpoint_lineage_mismatch")
    if operation_id is not None and checkpoint.operation_id != operation_id:
        raise NarrativeCheckpointError("narrative_checkpoint_operation_mismatch")
    if job_id is not None and checkpoint.job_id != job_id:
        raise NarrativeCheckpointError("narrative_checkpoint_job_mismatch")
    if (
        strategy_receipt_digest is not None
        and checkpoint.strategy_receipt_digest != strategy_receipt_digest
    ):
        raise NarrativeCheckpointError("narrative_checkpoint_strategy_mismatch")
    return checkpoint


def reseal_checkpoint_for_retry(
    checkpoint: NarrativeCheckpointV1,
    *,
    attempt_token: str,
    fencing_token: int,
) -> NarrativeCheckpointV1:
    checkpoint = validate_checkpoint(checkpoint)
    if fencing_token < checkpoint.fencing_token:
        raise NarrativeCheckpointError("narrative_checkpoint_stale_fence")
    retried = seal_checkpoint(
        operation_id=checkpoint.operation_id,
        job_id=checkpoint.job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
        strategy_receipt_digest=checkpoint.strategy_receipt_digest,
        narrative=checkpoint.narrative,
        provider_call_budget=checkpoint.provider_call_budget,
        frame_search_receipt=checkpoint.frame_search_receipt,
        frame_selection_receipt=checkpoint.frame_selection_receipt,
        route_resolution_receipt=checkpoint.route_resolution_receipt,
    )
    if retried.lineage_digest != checkpoint.lineage_digest:
        raise NarrativeCheckpointError("narrative_checkpoint_retry_drift")
    return retried


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


__all__ = [
    "InMemoryNarrativeCheckpointRepository",
    "NarrativeCheckpointError",
    "NarrativeCheckpointRepository",
    "attempt_id",
    "reseal_checkpoint_for_retry",
    "seal_checkpoint",
    "validate_checkpoint",
]
