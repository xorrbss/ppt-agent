"""PostgreSQL-authoritative append port for generation QualityReceipt V3.

The receipt and its digest-bound evidence are written into the owning
operation's append-only event stream under the same attempt token and fencing
token as generation.  Public event projection exposes only bounded metadata;
the full payload remains an internal durability record.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.workspace import WorkspaceOperation, WorkspaceOperationEvent
from services.authored_quality_receipt_v2 import (
    QualityReceiptV3,
    validate_quality_receipt_v3_payload,
)


_EVENT_TYPE = "quality.receipt.v3"
_SCHEMA_VERSION = "quality-receipt-event/v3"
_MAX_EVENT_BYTES = 2 * 1024 * 1024
_MAX_REVIEW_CHAIN_HOPS = 32


def quality_evidence_digest(payload: Mapping[str, object]) -> str:
    """Use the receipt ledger's exact JSON hashing, not NFC normalization."""
    encoded = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


async def load_public_quality_evidence(
    session: AsyncSession, *, owner_id: uuid.UUID, owner_scope: str,
    presentation_id: uuid.UUID, receipt_digest: str,
    operation_id: uuid.UUID | None = None,
    _visited_operations: tuple[uuid.UUID, ...] = (),
) -> dict[str, Any] | None:
    """Read exact private evidence: <=32 review hops and <=2 rows per query."""
    if len(_visited_operations) >= _MAX_REVIEW_CHAIN_HOPS:
        raise ValueError("quality_review_evidence_chain_too_deep")
    if operation_id is not None and operation_id in _visited_operations:
        raise ValueError("quality_review_evidence_chain_cycle")
    query = (
        select(WorkspaceOperationEvent, WorkspaceOperation)
        .join(WorkspaceOperation, WorkspaceOperation.id == WorkspaceOperationEvent.operation_id)
        .where(
            WorkspaceOperation.owner_id == owner_id,
            WorkspaceOperation.owner_scope == owner_scope,
            WorkspaceOperation.presentation_id == presentation_id,
            WorkspaceOperation.state == "succeeded",
            WorkspaceOperationEvent.owner_id == owner_id,
            WorkspaceOperationEvent.event_type == _EVENT_TYPE,
            WorkspaceOperationEvent.payload["receipt"]["receiptDigest"].as_string()
            == receipt_digest,
        )
    )
    if operation_id is not None:
        query = query.where(WorkspaceOperation.id == operation_id)
    # A duplicate is an integrity failure, not an invitation to load every
    # historical 2 MiB snapshot for this presentation.
    query = query.limit(2)
    matches: list[dict[str, Any]] = []
    for event, operation in (await session.execute(query)).all():
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        receipt = payload.get("receipt")
        if not isinstance(receipt, Mapping) or receipt.get("receiptDigest") != receipt_digest:
            continue
        evidence = payload.get("evidence")
        if not isinstance(evidence, Mapping):
            raise ValueError("quality_receipt_v3_evidence_missing")
        schema = payload.get("schemaVersion")
        if schema == _SCHEMA_VERSION:
            parsed = validate_quality_receipt_v3_payload(receipt)
            if operation.kind != "presentation.generate" or quality_evidence_digest(evidence) != parsed.evidence_digest:
                raise ValueError("quality_receipt_v3_evidence_digest_mismatch")
        elif schema == "export-review-evidence/v1":
            if operation.kind != "quality.review" or payload.get("snapshotDigest") != quality_evidence_digest(evidence):
                raise ValueError("quality_review_evidence_digest_mismatch")
            if not isinstance(operation.result, Mapping) or operation.result.get("release_review_receipt") != receipt:
                raise ValueError("quality_review_evidence_receipt_mismatch")
        else:
            raise ValueError("quality_receipt_v3_evidence_schema_invalid")
        matches.append({
            **dict(payload),
            "operationId": str(operation.id),
            "ownerId": str(operation.owner_id),
            "ownerScope": operation.owner_scope,
            "presentationId": str(operation.presentation_id),
            "sourceRevision": operation.expected_revision,
            "resultRevision": operation.result_revision,
        })
    if len(matches) > 1:
        raise ValueError("quality_receipt_v3_evidence_ambiguous")
    if not matches:
        return None
    record = matches[0]
    receipt = record["receipt"]
    if (record["schemaVersion"] == "export-review-evidence/v1"
        and receipt.get("baseEvidenceKind") == "release-review-transition"):
        try:
            parent_operation_id = uuid.UUID(str(receipt.get("baseEvidenceOperationId")))
        except (TypeError, ValueError) as error:
            raise ValueError("quality_review_evidence_chain_operation_invalid") from error
        parent = await load_public_quality_evidence(
            session, owner_id=owner_id, owner_scope=owner_scope,
            presentation_id=presentation_id,
            receipt_digest=str(receipt.get("baseQualityReceiptDigest", "")),
            operation_id=parent_operation_id,
            _visited_operations=(*_visited_operations, uuid.UUID(record["operationId"])),
        )
        if parent is not None:
            parent_receipt = parent["receipt"]
            if (parent["schemaVersion"] != "export-review-evidence/v1"
                or parent["resultRevision"] != record["sourceRevision"]
                or parent_receipt.get("resultDeckHtmlDigest") != receipt.get("baseDeckHtmlDigest")
                or parent_receipt.get("resultRenderIdentityDigest") != receipt.get("sourceRenderIdentityDigest")
                or parent_receipt.get("renderRuntimeBindingDigest") != receipt.get("renderRuntimeBindingDigest")
                or parent_receipt.get("checkerPolicyDigest") != receipt.get("checkerPolicyDigest")):
                raise ValueError("quality_review_evidence_chain_binding_mismatch")
        # Hydrated ancestry is not part of the stored child snapshot. Each
        # ancestor retains its own independent receipt/evidence digest checks.
        record["baseEvidenceRecord"] = parent
    return record


async def append_public_review_evidence(
    session: AsyncSession, *, operation_id: uuid.UUID, attempt_token: str,
    fencing_token: int, receipt: Mapping[str, Any], evidence: Mapping[str, Any],
) -> None:
    """Persist private checker inputs before their public timeline is sanitized."""
    payload = {
        "schemaVersion": "export-review-evidence/v1", "receipt": dict(receipt),
        "evidence": dict(evidence), "snapshotDigest": quality_evidence_digest(evidence),
    }
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > _MAX_EVENT_BYTES:
        raise ValueError("quality_receipt_v3_event_too_large")
    operation = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == operation_id).with_for_update().execution_options(populate_existing=True))
    if (operation is None or operation.kind != "quality.review" or operation.state != "running"
        or operation.attempt_token != attempt_token or operation.fencing_token != fencing_token):
        raise RuntimeError("quality_receipt_v3_operation_lease_lost")
    existing = (await session.scalars(select(WorkspaceOperationEvent).where(
        WorkspaceOperationEvent.operation_id == operation_id,
        WorkspaceOperationEvent.event_type == _EVENT_TYPE))).all()
    for event in existing:
        previous = event.payload if isinstance(event.payload, Mapping) else {}
        if previous.get("schemaVersion") == payload["schemaVersion"]:
            if previous == payload:
                return
            raise RuntimeError("quality_receipt_v3_append_conflict")
    sequence = operation.next_event_sequence
    operation.next_event_sequence += 1
    session.add(WorkspaceOperationEvent(
        owner_id=operation.owner_id, operation_id=operation.id, sequence=sequence,
        operation_version=operation.version, event_type=_EVENT_TYPE, payload=payload,
    ))
    await session.flush()


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
        validate_quality_receipt_v3_payload(receipt_payload)
        if quality_evidence_digest(evidence) != receipt.evidence_digest:
            raise ValueError("quality_receipt_v3_evidence_digest_mismatch")
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
            .execution_options(populate_existing=True)
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


__all__ = [
    "PostgresOperationQualityReceiptV3Port",
    "append_public_review_evidence",
    "load_public_quality_evidence",
    "quality_evidence_digest",
]
