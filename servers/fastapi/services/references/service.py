"""Persistence and generation admission for immutable references."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import hmac
import json
from pathlib import Path
import asyncio
from typing import Any
import uuid

from sqlalchemy import delete
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
from services.workspace_asset_service import import_workspace_document_asset
from services.workspace_service import WorkspaceServiceError
from utils.datetime_utils import get_current_utc_datetime
from utils.document_path_security import validate_document_paths
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


def _binding_matches_snapshot(
    snapshot: AssetBinding,
    current: AssetBinding,
) -> bool:
    if current == snapshot:
        return True
    # Workspace bootstrap claims a previously unattached upload by assigning a
    # conversation and incrementing its lifecycle version exactly once. That
    # transition does not mutate bytes, media type, scan state, or readiness;
    # accept it as a rolling ownership facade while still rejecting every
    # content/lifecycle change and every later version drift.
    return bool(
        snapshot.conversation_id is None
        and current.conversation_id is not None
        and current.version == snapshot.version + 1
        and current.asset_id == snapshot.asset_id
        and current.kind == snapshot.kind
        and current.media_type == snapshot.media_type
        and current.size_bytes == snapshot.size_bytes
        and hmac.compare_digest(current.sha256, snapshot.sha256)
        and current.scan_state == snapshot.scan_state
        and current.lifecycle_state == snapshot.lifecycle_state
    )


async def canonicalize_legacy_document_paths(
    session: AsyncSession,
    *,
    file_paths: list[str],
    conversation_id: uuid.UUID | None = None,
    storage_root: Path | None = None,
    admin_legacy_roots: list[str] | None = None,
) -> ReferenceSnapshotView:
    """Immediately turn legacy server paths into one immutable snapshot.

    Raw paths are accepted only at this compatibility boundary. Each path is
    containment-validated, copied, scanned, hashed and then forgotten. The
    returned snapshot id is the only value generation jobs may persist.
    """

    validated = validate_document_paths(
        file_paths,
        admin_legacy_roots=admin_legacy_roots,
    )
    asset_ids: list[str] = []
    imported_copies: list[Path] = []
    try:
        for path in validated:
            asset, _private_copy = await import_workspace_document_asset(
                session,
                source_path=path,
                original_name=Path(path).name,
                storage_root=storage_root,
            )
            imported_copies.append(_private_copy)
            asset_ids.append(str(asset.id))
        return await create_reference_snapshot(
            session,
            provider_id="uploaded_document",
            request={"asset_ids": asset_ids},
            conversation_id=conversation_id,
            asset_gateway=AssetGateway(storage_root=storage_root),
            commit=False,
        )
    except BaseException as error:
        # The import helper flushes rows and returns a promoted private copy, but
        # deliberately leaves transaction ownership here. Compensate every
        # successfully imported prefix if a later import, provider validation,
        # or snapshot commit fails so rolled-back rows never strand files.
        await session.rollback()
        for private_copy in reversed(imported_copies):
            await asyncio.to_thread(_remove_imported_private_copy, private_copy)
        if isinstance(error, WorkspaceServiceError):
            raise ReferenceProviderError(
                error.code,
                status_code=error.status_code,
                retryable=error.retryable,
            ) from error
        raise


def _remove_imported_private_copy(private_copy: Path) -> None:
    private_copy.unlink(missing_ok=True)
    try:
        private_copy.parent.rmdir()
    except OSError:
        pass


async def create_reference_snapshot(
    session: AsyncSession,
    *,
    provider_id: str,
    request: dict[str, Any],
    conversation_id: uuid.UUID | None = None,
    registry: ReferenceSourceProviderRegistry | None = None,
    asset_gateway: AssetGateway | None = None,
    commit: bool = True,
) -> ReferenceSnapshotView:
    """Normalize, admit, and atomically append one immutable record.

    Admission deliberately happens after ``flush`` but before an optional
    commit. A failed live-asset revalidation therefore cannot leave a durable
    snapshot pointing at bytes that the importing compatibility boundary
    compensates. ``commit=False`` composes the append into a caller-owned
    transaction such as one-call generation bootstrap.
    """

    provider = (registry or default_reference_source_registry()).require(provider_id)
    gateway = asset_gateway or AssetGateway()
    row: ReferenceSnapshot | None = None
    admitted: ReferenceSnapshotView | None = None
    committing = False
    try:
        normalized = await provider.normalize(
            session,
            request=request,
            asset_gateway=gateway,
            conversation_id=conversation_id,
        )
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
        await session.flush()
        admitted = await admit_reference_snapshot(
            session,
            snapshot_id=row.id,
            conversation_id=conversation_id,
            asset_gateway=gateway,
        )
        if commit:
            committing = True
            await session.commit()
        return admitted
    except BaseException as error:
        await session.rollback()
        if committing and row is not None and admitted is not None:
            try:
                committed = (
                    await session.execute(
                        select(ReferenceSnapshot).where(
                            ReferenceSnapshot.id == row.id,
                            _owner_predicate(),
                        )
                    )
                ).scalar_one_or_none()
            except BaseException:
                await session.rollback()
                committed = None
            if (
                committed is not None
                and hmac.compare_digest(
                    committed.content_sha256,
                    row.content_sha256,
                )
            ):
                return admitted
        if isinstance(error, AssetGatewayError):
            raise ReferenceProviderError(
                error.code,
                status_code=error.status_code,
                retryable=error.retryable,
            ) from error
        raise


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
        if not _binding_matches_snapshot(binding, current):
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


async def claim_reference_snapshots(
    session: AsyncSession,
    *,
    snapshot_ids: list[uuid.UUID],
    conversation_id: uuid.UUID,
) -> None:
    """Attach admitted upload snapshots to one durable conversation once.

    Upload happens before workspace bootstrap, so both assets and snapshots are
    initially owner-scoped orphans with a TTL. Bootstrap claims the assets; this
    matching one-way claim gives snapshot rows the same deletion boundary while
    preserving their immutable normalized payload and binding digest.
    """

    if not snapshot_ids:
        return
    rows = list(
        (
            await session.scalars(
                select(ReferenceSnapshot).where(
                    ReferenceSnapshot.id.in_(snapshot_ids),
                    _owner_predicate(),
                )
            )
        ).all()
    )
    if len(rows) != len(set(snapshot_ids)):
        raise ReferenceProviderError("reference_snapshot_not_found", status_code=404)
    for row in rows:
        if row.conversation_id not in (None, conversation_id):
            raise ReferenceProviderError(
                "reference_snapshot_conversation_mismatch", status_code=409
            )
        if row.conversation_id is None:
            row.conversation_id = conversation_id
        # A durable conversation owns the snapshot from this point onward.
        # Match the workspace asset claim semantics and remove the orphan TTL;
        # the conversation FK cascade is now the cleanup boundary.
        row.expires_at = None
        session.add(row)
    await session.flush()


async def cleanup_expired_reference_snapshots(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 100,
    commit: bool = True,
) -> int:
    """Purge unattached reference payloads after their admission TTL.

    Uploaded-document snapshots contain normalized source text.  Assets have a
    separate physical-file sweeper, so deleting only the asset would retain
    that extracted text indefinitely.  This bounded pass removes orphaned
    snapshot rows; claimed rows have ``expires_at = NULL`` and are deleted by
    their conversation cascade instead.
    """

    current = now or get_current_utc_datetime()
    snapshot_ids = list(
        (
            await session.scalars(
                select(ReferenceSnapshot.id)
                .where(
                    ReferenceSnapshot.conversation_id.is_(None),
                    ReferenceSnapshot.expires_at.is_not(None),
                    ReferenceSnapshot.expires_at <= current,
                )
                .order_by(ReferenceSnapshot.expires_at, ReferenceSnapshot.id)
                .limit(max(1, min(limit, 1000)))
            )
        ).all()
    )
    if snapshot_ids:
        await session.execute(
            delete(ReferenceSnapshot).where(ReferenceSnapshot.id.in_(snapshot_ids))
        )
    await session.flush()
    if commit:
        await session.commit()
    return len(snapshot_ids)
