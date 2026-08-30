"""PostgreSQL-authoritative append port for generation QualityReceipt V3.

The receipt and its digest-bound evidence are written into the owning
operation's append-only event stream under the same attempt token and fencing
token as generation.  Public event projection exposes only bounded metadata;
the full payload remains an internal durability record.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.workspace import WorkspaceOperation, WorkspaceOperationEvent
from services.authored_quality_receipt_v2 import QualityReceiptV3


_EVENT_TYPE = "quality.receipt.v3"
_SCHEMA_VERSION = "quality-receipt-event/v3"
_MAX_EVENT_BYTES = 2 * 1024 * 1024


class PostgresOperationQualityReceiptV3Port:
    """Append one immutable V3 record only for the current fenced attempt."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        operation_id: uuid.UUID,
        attempt_token: str,
        fencing_token: int,
    ) -> None:
        if not attempt_token or fencing_token < 1:
            raise ValueError("quality_receipt_v3_fence_invalid")
        self._session = session
        self._operation_id = operation_id
        self._attempt_token = attempt_token
        self._fencing_token = fencing_token

    async def append_quality_receipt_v3(
        self,
        *,
        receipt: QualityReceiptV3,
        evidence: Mapping[str, object],
    ) -> None:
        receipt_payload = receipt.to_dict()
        payload: dict[str, Any] = {
            "schemaVersion": _SCHEMA_VERSION,
            "receipt": receipt_payload,
            "evidence": dict(evidence),
        }
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("quality_receipt_v3_event_not_json_safe") from error
        if len(encoded) > _MAX_EVENT_BYTES:
            raise ValueError("quality_receipt_v3_event_too_large")

        operation = await self._session.scalar(
            select(WorkspaceOperation)
            .where(WorkspaceOperation.id == self._operation_id)
            .with_for_update()
        )
        if (
            operation is None
            or operation.state != "running"
            or operation.attempt_token != self._attempt_token
            or operation.fencing_token != self._fencing_token
        ):
            raise RuntimeError("quality_receipt_v3_operation_lease_lost")

        existing_rows = list(
            (
                await self._session.scalars(
                    select(WorkspaceOperationEvent).where(
                        WorkspaceOperationEvent.operation_id == self._operation_id,
                        WorkspaceOperationEvent.event_type == _EVENT_TYPE,
                    )
                )
            ).all()
        )
        for existing in existing_rows:
            existing_payload = (
                existing.payload if isinstance(existing.payload, Mapping) else {}
            )
            existing_receipt = existing_payload.get("receipt")
            if not isinstance(existing_receipt, Mapping):
                continue
            same_identity = (
                existing_receipt.get("attemptId") == receipt.attempt_id
                and existing_receipt.get("revision") == receipt.revision
                and existing_receipt.get("sequence") == receipt.sequence
            )
            if not same_identity:
                continue
            if (
                existing_receipt.get("receiptDigest") == receipt.receipt_digest
                and existing_receipt.get("evidenceDigest") == receipt.evidence_digest
            ):
                return
            raise RuntimeError("quality_receipt_v3_append_conflict")

        if receipt.sequence == 0:
            if receipt.previous_receipt_digest is not None:
                raise RuntimeError("quality_receipt_v3_chain_invalid")
        else:
            previous_found = any(
                isinstance(item.payload, Mapping)
                and isinstance(item.payload.get("receipt"), Mapping)
                and item.payload["receipt"].get("attemptId") == receipt.attempt_id
                and item.payload["receipt"].get("revision") == receipt.revision
                and item.payload["receipt"].get("sequence") == receipt.sequence - 1
                and item.payload["receipt"].get("receiptDigest")
                == receipt.previous_receipt_digest
                for item in existing_rows
            )
            if not previous_found:
                raise RuntimeError("quality_receipt_v3_chain_invalid")

        sequence = operation.next_event_sequence
        operation.next_event_sequence = sequence + 1
        self._session.add(
            WorkspaceOperationEvent(
                owner_id=operation.owner_id,
                operation_id=operation.id,
                sequence=sequence,
                operation_version=operation.version,
                event_type=_EVENT_TYPE,
                payload=payload,
            )
        )
        await self._session.flush()


__all__ = ["PostgresOperationQualityReceiptV3Port"]
