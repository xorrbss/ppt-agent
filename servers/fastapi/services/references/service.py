"""Persistence and generation admission for immutable references."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import hmac
import json
from pathlib import Path
from typing import Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.reference_snapshot import ReferenceSnapshot
from services.assets import AssetBinding, AssetGateway, AssetGatewayError, AssetKind
from services.references.contracts import (
    NormalizedReference,
    ReferenceProviderError,
    ReferenceSnapshotView,
)
from services.references.providers import (
    ReferenceSourceProviderRegistry,
    default_reference_source_registry,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


MAX_GENERATION_REFERENCE_SNAPSHOTS = 16
_FORBIDDEN_KEYS = frozenset(
    {
        "path",
        "file_path",
        "filepath",
        "storage_key",
        "storage_path",
        "local_path",
    }
)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ReferenceProviderError("reference_payload_not_json") from error


def _reject_storage_locations(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in _FORBIDDEN_KEYS:
                raise ReferenceProviderError("reference_storage_location_forbidden")
            _reject_storage_locations(child)
    elif isinstance(value, list):
        for child in value:
            _reject_storage_locations(child)


def _snapshot_content(
    normalized: NormalizedReference,
) -> tuple[str, str, list[dict[str, object]]]:
    _reject_storage_locations(normalized.normalized_payload)
    bindings = [binding.as_json() for binding in normalized.asset_bindings]
    payload = {
        "schemaVersion": 1,
        "providerId": normalized.provider_id,
        "sourceRevision": normalized.source_revision,
        "normalizedPayload": normalized.normalized_payload,
        "assetBindings": bindings,
    }
    serialized = _canonical_json(payload)
    return serialized, sha256(serialized.encode("utf-8")).hexdigest(), bindings


def _source_fingerprint(normalized: NormalizedReference) -> str:
    serialized = _canonical_json(
        {
            "providerId": normalized.provider_id,
            "sourceRevision": normalized.source_revision,
            "payload": normalized.normalized_payload,
            "assets": [binding.as_json() for binding in normalized.asset_bindings],
        }
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _owner_predicate():
    owner_id = get_current_owner_id()
    if owner_id is None:
        return ReferenceSnapshot.owner_id.is_(None)
    return ReferenceSnapshot.owner_id == owner_id


def _binding_from_json(value: dict[str, Any]) -> AssetBinding:
    try:
        return AssetBinding(
            asset_id=uuid.UUID(str(value["assetId"])),
            kind=AssetKind(str(value["kind"])),
            media_type=str(value["mediaType"]),
            size_bytes=int(value["sizeBytes"]),
            sha256=str(value["sha256"]),
            version=int(value["version"]),
            conversation_id=(
                uuid.UUID(str(value["conversationId"]))
                if value.get("conversationId")
                else None
            ),
            scan_state=str(value["scanState"]),
            lifecycle_state=str(value["lifecycleState"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ReferenceProviderError(
            "reference_asset_binding_invalid", status_code=409
        ) from error


def _utc_comparable(now: datetime, other: datetime) -> datetime:
    return now.replace(tzinfo=None) if other.tzinfo is None else now


async def create_reference_snapshot(
    session: AsyncSession,
    *,
    provider_id: str,
    request: dict[str, Any],
    conversation_id: uuid.UUID | None = None,
    registry: ReferenceSourceProviderRegistry | None = None,
    asset_gateway: AssetGateway | None = None,
) -> ReferenceSnapshotView:
    """Normalize one source and append an immutable persistence record."""

    provider = (registry or default_reference_source_registry()).require(provider_id)
    gateway = asset_gateway or AssetGateway()
    try:
        normalized = await provider.normalize(
            session,
            request=request,
            asset_gateway=gateway,
            conversation_id=conversation_id,
        )
    except AssetGatewayError as error:
        raise ReferenceProviderError(
            error.code,
            status_code=error.status_code,
            retryable=error.retryable,
        ) from error
    if normalized.provider_id != provider_id:
        raise ReferenceProviderError("reference_provider_identity_mismatch")

    _serialized, content_digest, bindings = _snapshot_content(normalized)
    row = ReferenceSnapshot(
        conversation_id=conversation_id,
        provider_id=provider_id,
        source_revision=normalized.source_revision,
        source_fingerprint=_source_fingerprint(normalized),
        normalized_payload=json.loads(_canonical_json(normalized.normalized_payload)),
        asset_bindings=bindings,
        content_sha256=content_digest,
        expires_at=normalized.expires_at,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return await admit_reference_snapshot(
        session,
        snapshot_id=row.id,
        conversation_id=conversation_id,
        asset_gateway=gateway,
    )


async def admit_reference_snapshot(
    session: AsyncSession,
    *,
    snapshot_id: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
    asset_gateway: AssetGateway | None = None,
    storage_root: Path | None = None,
) -> ReferenceSnapshotView:
    """Load one immutable snapshot and revalidate every live asset binding."""

    predicates = [ReferenceSnapshot.id == snapshot_id, _owner_predicate()]
    if conversation_id is not None:
        predicates.append(ReferenceSnapshot.conversation_id == conversation_id)
    row = (
        await session.execute(select(ReferenceSnapshot).where(*predicates))
    ).scalar_one_or_none()
    if row is None:
        raise ReferenceProviderError("reference_snapshot_not_found", status_code=404)
    now = get_current_utc_datetime()
    if row.expires_at is not None and row.expires_at <= _utc_comparable(now, row.expires_at):
        raise ReferenceProviderError("reference_snapshot_expired", status_code=409)

    bindings = tuple(_binding_from_json(value) for value in row.asset_bindings)
    normalized = NormalizedReference(
        provider_id=row.provider_id,
        source_revision=row.source_revision,
        normalized_payload=row.normalized_payload,
        asset_bindings=bindings,
        expires_at=row.expires_at,
    )
    _serialized, expected_digest, _ = _snapshot_content(normalized)
    if not hmac.compare_digest(expected_digest, row.content_sha256):
        raise ReferenceProviderError("reference_snapshot_integrity_failed", status_code=409)

    gateway = asset_gateway or AssetGateway()
    revalidated: list[AssetBinding] = []
    for binding in bindings:
        try:
            current = await gateway.bind(
                session,
                asset_id=binding.asset_id,
                kind=binding.kind,
                conversation_id=row.conversation_id,
                storage_root=storage_root,
            )
        except AssetGatewayError as error:
            raise ReferenceProviderError(
                error.code,
                status_code=error.status_code,
                retryable=error.retryable,
            ) from error
        if current != binding:
            raise ReferenceProviderError("reference_asset_binding_stale", status_code=409)
        revalidated.append(current)

    payload_json = _canonical_json(row.normalized_payload)
    return ReferenceSnapshotView(
        snapshot_id=row.id,
        provider_id=row.provider_id,
        source_revision=row.source_revision,
        source_fingerprint=row.source_fingerprint,
        normalized_payload_json=payload_json,
        asset_bindings=tuple(revalidated),
        content_sha256=row.content_sha256,
        conversation_id=row.conversation_id,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


async def admit_reference_snapshots(
    session: AsyncSession,
    *,
    snapshot_ids: list[uuid.UUID],
    conversation_id: uuid.UUID | None = None,
    asset_gateway: AssetGateway | None = None,
    storage_root: Path | None = None,
) -> tuple[ReferenceSnapshotView, ...]:
    """Generation entry boundary; accepts snapshot IDs, never raw sources."""

    if len(snapshot_ids) > MAX_GENERATION_REFERENCE_SNAPSHOTS:
        raise ReferenceProviderError("reference_snapshot_limit_exceeded")
    if len(snapshot_ids) != len(set(snapshot_ids)):
        raise ReferenceProviderError("duplicate_reference_snapshot")
    return tuple(
        [
            await admit_reference_snapshot(
                session,
                snapshot_id=snapshot_id,
                conversation_id=conversation_id,
                asset_gateway=asset_gateway,
                storage_root=storage_root,
            )
            for snapshot_id in snapshot_ids
        ]
    )
