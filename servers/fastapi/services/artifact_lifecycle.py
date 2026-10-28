"""Feature-gated artifact lifecycle/refcount/GC product service.

The service owns lifecycle ordering and physical-delete safety while an injected
database authority owns durable compare-and-set state.  It remains fail-closed
until the composition root enables it and supplies that authority; local paths
or process memory are never accepted as lifecycle authority.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
import uuid

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.public_artifact import (
    ArtifactRef,
    PublicArtifact,
    PublicArtifactPublication,
    PublicExportIdentity,
    PublicSlideCheckpoint,
)
from models.sql.workspace import WorkspaceArtifact
from models.sql.svg_template import SvgTemplateAssetBinding
from services.svg_templates.retention import retention_schema_available
from services.artifact_store import (
    ArtifactObjectStore,
    ArtifactStoreError,
    _await_physical_delete_completion,
    configured_public_artifact_service,
    try_artifact_key_guard,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.env_settings.public_service import (
    PUBLIC_ARTIFACT_LIFECYCLE_ENV as _PUBLIC_ARTIFACT_LIFECYCLE_ENV,
    is_public_artifact_lifecycle_enabled,
)


ArtifactLifecycleState = Literal[
    "provisional",
    "committed",
    "tombstoned",
    "deleted",
]
PUBLIC_ARTIFACT_LIFECYCLE_ENV = _PUBLIC_ARTIFACT_LIFECYCLE_ENV

_NEXT_STATE: dict[ArtifactLifecycleState, ArtifactLifecycleState] = {
    "provisional": "committed",
    "committed": "tombstoned",
    "tombstoned": "deleted",
}


@dataclass(frozen=True, slots=True)
class ArtifactLifecycleRecord:
    artifact: ArtifactRef
    state: ArtifactLifecycleState
    ref_count: int
    state_revision: int
    updated_at: datetime
    expires_at: datetime | None = None
    delete_after: datetime | None = None
    last_gc_error: str | None = None

    def __post_init__(self) -> None:
        if self.ref_count < 0 or self.state_revision < 1:
            raise ValueError("artifact lifecycle counters are invalid")
        if self.updated_at.tzinfo is None:
            raise ValueError("artifact lifecycle timestamps must be timezone-aware")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("artifact expiry must be timezone-aware")
        if self.delete_after is not None and self.delete_after.tzinfo is None:
            raise ValueError("artifact delete-after must be timezone-aware")
        if self.state == "deleted" and self.ref_count != 0:
            raise ValueError("deleted artifacts cannot retain references")


class ArtifactLifecycleAuthority(Protocol):
    """DB adapter contract; every mutation is one compare-and-set statement."""

    async def compare_and_set(
        self,
        *,
        artifact_id: uuid.UUID,
        expected_state: ArtifactLifecycleState,
        expected_revision: int,
        next_state: ArtifactLifecycleState,
        ref_count_delta: int = 0,
    ) -> ArtifactLifecycleRecord | None: ...

    async def list_gc_candidates(
        self,
        *,
        older_than: datetime,
        limit: int,
    ) -> list[ArtifactLifecycleRecord]: ...

    async def list_expired_candidates(
        self,
        *,
        expires_at_or_before: datetime,
        limit: int,
    ) -> list[ArtifactLifecycleRecord]: ...

    async def claim_gc_candidate(
        self,
        record: ArtifactLifecycleRecord,
        *,
        older_than: datetime,
    ) -> ArtifactLifecycleRecord | None: ...

    # Claims must guard the shared storage key through deletion and commit,
    # freshly exclude every live sibling/pointer, and preserve sibling grace.


def _live_artifact_pointer(*, template_retention=True):
    return or_(
        exists().where(SvgTemplateAssetBinding.artifact_id == PublicArtifact.id) if template_retention else False,
        exists().where(PublicArtifactPublication.artifact_id == PublicArtifact.id),
        exists().where(PublicSlideCheckpoint.artifact_id == PublicArtifact.id),
        exists().where(
            PublicExportIdentity.result_artifact_id == PublicArtifact.id,
            PublicExportIdentity.state.in_(("running", "succeeded")),
        ),
        exists().where(
            PublicExportIdentity.source_artifact_id == PublicArtifact.id,
            PublicExportIdentity.state.in_(("running", "succeeded")),
        ),
        exists().where(
            WorkspaceArtifact.public_artifact_id == PublicArtifact.id,
            WorkspaceArtifact.status.in_(("staging", "ready")),
        ),
    )


class SqlArtifactLifecycleAuthority:
    """ABA-safe lifecycle repository over the canonical public-artifact row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _record(row: PublicArtifact) -> ArtifactLifecycleRecord:
        return ArtifactLifecycleRecord(
            artifact=row.to_ref(),
            state=row.lifecycle_state,  # type: ignore[arg-type]
            ref_count=row.refcount,
            state_revision=row.lifecycle_revision,
            updated_at=row.updated_at,
            expires_at=row.expires_at,
            last_gc_error=row.last_gc_error,
        )

    async def compare_and_set(
        self,
        *,
        artifact_id: uuid.UUID,
        expected_state: ArtifactLifecycleState,
        expected_revision: int,
        next_state: ArtifactLifecycleState,
        ref_count_delta: int = 0,
    ) -> ArtifactLifecycleRecord | None:
        if ref_count_delta not in {-1, 0, 1}:
            raise ValueError("artifact lifecycle refcount delta is invalid")
        # Candidate discovery never holds row locks before the per-key guard.
        # This also applies to expiry: the runtime expires and collects in one
        # outer transaction, so an expiry row lock otherwise reverses ordering.
        hint = await self._session.scalar(
            select(PublicArtifact)
            .where(PublicArtifact.id == artifact_id)
            .execution_options(autoflush=False)
        )
        if hint is None:
            return None
        reference = hint.to_ref()
        if not await try_artifact_key_guard(self._session, reference):
            return None
        fresh = await self._session.scalar(
            select(PublicArtifact)
            .where(
                PublicArtifact.id == artifact_id,
                PublicArtifact.owner_id == reference.owner_id,
                PublicArtifact.tenant_id == reference.tenant_id,
                PublicArtifact.storage_key == reference.storage_key,
                PublicArtifact.sha256 == reference.sha256,
                PublicArtifact.lifecycle_state == expected_state,
                PublicArtifact.lifecycle_revision == expected_revision,
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True, autoflush=False)
        )
        if fresh is None or fresh.to_ref() != reference:
            return None
        now = get_current_utc_datetime()
        predicates = [
            PublicArtifact.id == artifact_id,
            PublicArtifact.owner_id == reference.owner_id,
            PublicArtifact.tenant_id == reference.tenant_id,
            PublicArtifact.storage_key == reference.storage_key,
            PublicArtifact.sha256 == reference.sha256,
            PublicArtifact.lifecycle_state == expected_state,
            PublicArtifact.lifecycle_revision == expected_revision,
        ]
        if ref_count_delta < 0:
            predicates.append(PublicArtifact.refcount >= -ref_count_delta)
        if next_state in {"tombstoned", "deleted"}:
            predicates.append(PublicArtifact.refcount == 0)
            predicates.append(~_live_artifact_pointer(template_retention=await retention_schema_available(self._session)))
        values: dict[str, object] = {
            "lifecycle_state": next_state,
            "refcount": PublicArtifact.refcount + ref_count_delta,
            "lifecycle_revision": PublicArtifact.lifecycle_revision + 1,
            "updated_at": now,
        }
        if next_state == "tombstoned" and expected_state != "tombstoned":
            values["tombstoned_at"] = now
        if next_state == "deleted":
            values["deleted_at"] = now
        result = await self._session.execute(
            update(PublicArtifact)
            .where(*predicates)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return None
        row = await self._session.scalar(
            select(PublicArtifact)
            .where(PublicArtifact.id == artifact_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise ArtifactStoreError("artifact.lifecycle_row_disappeared")
        return self._record(row)

    async def claim_gc_candidate(
        self,
        record: ArtifactLifecycleRecord,
        *,
        older_than: datetime,
    ) -> ArtifactLifecycleRecord | None:
        reference = record.artifact
        if not await try_artifact_key_guard(
            self._session, reference, for_physical_delete=True,
        ):
            return None
        fresh = await self._session.scalar(
            select(PublicArtifact)
            .where(
                PublicArtifact.id == reference.artifact_id,
                PublicArtifact.owner_id == reference.owner_id,
                PublicArtifact.tenant_id == reference.tenant_id,
                PublicArtifact.storage_key == reference.storage_key,
                PublicArtifact.sha256 == reference.sha256,
                PublicArtifact.lifecycle_state == "tombstoned",
                PublicArtifact.lifecycle_revision == record.state_revision,
                PublicArtifact.refcount == 0,
                PublicArtifact.updated_at <= older_than,
                or_(PublicArtifact.expires_at.is_(None), PublicArtifact.expires_at <= older_than),
                ~_live_artifact_pointer(template_retention=await retention_schema_available(self._session)),
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True, autoflush=False)
        )
        if fresh is None or fresh.to_ref() != reference:
            return None
        # The byte key is shared across artifact IDs, kinds and operations.
        # Even an unreferenced tombstone keeps its own expiry/grace window.
        protected_sibling = await self._session.scalar(
            select(PublicArtifact.id)
            .where(
                PublicArtifact.storage_key == reference.storage_key,
                or_(
                    _live_artifact_pointer(template_retention=await retention_schema_available(self._session)),
                    PublicArtifact.refcount > 0,
                    PublicArtifact.lifecycle_state.not_in(("tombstoned", "deleted")),
                    and_(
                        PublicArtifact.lifecycle_state != "deleted",
                        or_(
                            PublicArtifact.updated_at > older_than,
                            PublicArtifact.expires_at > older_than,
                        ),
                    ),
                ),
            )
            .limit(1)
            .execution_options(autoflush=False)
        )
        if protected_sibling is not None:
            return None
        return await self.compare_and_set(
            artifact_id=reference.artifact_id,
            expected_state="tombstoned",
            expected_revision=record.state_revision,
            next_state="tombstoned",
        )

    async def list_gc_candidates(
        self,
        *,
        older_than: datetime,
        limit: int,
    ) -> list[ArtifactLifecycleRecord]:
        if older_than.tzinfo is None or limit <= 0:
            raise ValueError("artifact GC bounds are invalid")
        rows = (
            await self._session.scalars(
                select(PublicArtifact)
                .where(
                    PublicArtifact.lifecycle_state == "tombstoned",
                    PublicArtifact.refcount == 0,
                    PublicArtifact.updated_at <= older_than,
                    or_(
                        PublicArtifact.expires_at.is_(None),
                        PublicArtifact.expires_at <= older_than,
                    ),
                    ~_live_artifact_pointer(template_retention=await retention_schema_available(self._session)),
                )
                .order_by(PublicArtifact.updated_at, PublicArtifact.id)
                .limit(limit)
                .execution_options(autoflush=False)
            )
        ).all()
        return [self._record(row) for row in rows]

    async def list_expired_candidates(
        self,
        *,
        expires_at_or_before: datetime,
        limit: int,
    ) -> list[ArtifactLifecycleRecord]:
        if expires_at_or_before.tzinfo is None or limit <= 0:
            raise ValueError("artifact expiry bounds are invalid")
        rows = (
            await self._session.scalars(
                select(PublicArtifact)
                .where(
                    PublicArtifact.lifecycle_state == "committed",
                    PublicArtifact.refcount == 0,
                    PublicArtifact.expires_at.is_not(None),
                    PublicArtifact.expires_at <= expires_at_or_before,
                    ~_live_artifact_pointer(template_retention=await retention_schema_available(self._session)),
                )
                .order_by(PublicArtifact.expires_at, PublicArtifact.id)
                .limit(limit)
                .execution_options(autoflush=False)
            )
        ).all()
        return [self._record(row) for row in rows]


def validate_lifecycle_transition(
    record: ArtifactLifecycleRecord,
    next_state: ArtifactLifecycleState,
) -> None:
    if _NEXT_STATE.get(record.state) != next_state:
        raise ArtifactStoreError("artifact.lifecycle_transition_invalid")
    if next_state in {"tombstoned", "deleted"} and record.ref_count != 0:
        raise ArtifactStoreError("artifact.lifecycle_references_remain")


async def _delete_before_guard_unwinds(
    object_store: ArtifactObjectStore, reference: ArtifactRef,
) -> None:
    """Drain an owned physical delete before cancellation releases its DB guard.

    Local/S3 adapters can have blocking work running in a thread. Shielding once
    is insufficient: the caller must retain its transaction until that work has
    settled, even when cancellation is requested repeatedly. This protects task
    cancellation, not process loss or an independently pending remote request.
    """

    deletion = asyncio.create_task(object_store.delete(reference))
    await _await_physical_delete_completion(deletion)


class ArtifactLifecycleService:
    """Feature-gated lifecycle coordinator backed by canonical DB CAS state."""

    def __init__(
        self,
        *,
        authority: ArtifactLifecycleAuthority,
        object_store: ArtifactObjectStore,
        enabled: bool = False,
    ) -> None:
        self._authority = authority
        self._object_store = object_store
        self._enabled = enabled

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise ArtifactStoreError("artifact.lifecycle_feature_disabled")

    async def transition(
        self,
        record: ArtifactLifecycleRecord,
        next_state: ArtifactLifecycleState,
    ) -> ArtifactLifecycleRecord:
        self._require_enabled()
        validate_lifecycle_transition(record, next_state)
        if next_state == "deleted":
            raise ArtifactStoreError("artifact.lifecycle_delete_requires_gc")
        if next_state == "committed":
            metadata = await self._object_store.head(record.artifact)
            if metadata is None:
                raise ArtifactStoreError("artifact.committed_object_missing")
        updated = await self._authority.compare_and_set(
            artifact_id=record.artifact.artifact_id,
            expected_state=record.state,
            expected_revision=record.state_revision,
            next_state=next_state,
        )
        if updated is None:
            raise ArtifactStoreError("artifact.lifecycle_fence_stale")
        return updated


    async def collect_once(self, *, older_than: datetime, limit: int) -> int:
        self._require_enabled()
        if older_than.tzinfo is None or limit <= 0:
            raise ValueError("artifact GC bounds are invalid")
        candidates = await self._authority.list_gc_candidates(
            older_than=older_than,
            limit=limit,
        )
        deleted = 0
        for record in candidates[:limit]:
            if record.state != "tombstoned" or record.ref_count != 0:
                raise ArtifactStoreError("artifact.gc_candidate_invalid")
            if record.expires_at is not None and record.expires_at > older_than:
                continue
            if record.delete_after is not None and record.delete_after > older_than:
                continue
            claim = getattr(self._authority, "claim_gc_candidate", None)
            if claim is None:
                raise ArtifactStoreError("artifact.gc_shared_key_authority_required")
            claimed = await claim(record, older_than=older_than)
            if claimed is None:
                continue
            if claimed.ref_count != 0:
                raise ArtifactStoreError("artifact.gc_candidate_invalid")
            await _delete_before_guard_unwinds(self._object_store, record.artifact)
            updated = await self._authority.compare_and_set(
                artifact_id=record.artifact.artifact_id,
                expected_state="tombstoned",
                expected_revision=claimed.state_revision,
                next_state="deleted",
            )
            if updated is None:
                raise ArtifactStoreError("artifact.gc_finalize_stale")
            deleted += 1
        return deleted

    async def expire_once(self, *, now: datetime, limit: int) -> int:
        """Tombstone expired unreferenced objects under DB compare-and-set."""

        self._require_enabled()
        if now.tzinfo is None or limit <= 0:
            raise ValueError("artifact expiry bounds are invalid")
        candidates = await self._authority.list_expired_candidates(
            expires_at_or_before=now,
            limit=limit,
        )
        tombstoned = 0
        for record in candidates[:limit]:
            if record.state != "committed" or record.ref_count != 0:
                raise ArtifactStoreError("artifact.expiry_candidate_invalid")
            updated = await self._authority.compare_and_set(
                artifact_id=record.artifact.artifact_id,
                expected_state="committed",
                expected_revision=record.state_revision,
                next_state="tombstoned",
            )
            if updated is not None:
                tombstoned += 1
        return tombstoned

    async def sweep_provisional_orphans(
        self,
        *,
        older_than: datetime,
        limit: int,
    ) -> int:
        self._require_enabled()
        return await self._object_store.sweep_provisional_orphans(
            older_than=older_than,
            limit=limit,
        )


ShadowArtifactLifecycleService = ArtifactLifecycleService


def public_artifact_lifecycle_enabled() -> bool:
    return is_public_artifact_lifecycle_enabled()


def configured_artifact_lifecycle_service(
    session: AsyncSession,
) -> ArtifactLifecycleService:
    """Bind canonical DB CAS and configured object storage in one service."""

    return ArtifactLifecycleService(
        authority=SqlArtifactLifecycleAuthority(session),
        object_store=configured_public_artifact_service().object_store,
        enabled=public_artifact_lifecycle_enabled(),
    )


__all__ = [
    "ArtifactLifecycleService",
    "ArtifactLifecycleAuthority",
    "ArtifactLifecycleRecord",
    "ArtifactLifecycleState",
    "PUBLIC_ARTIFACT_LIFECYCLE_ENV",
    "SqlArtifactLifecycleAuthority",
    "ShadowArtifactLifecycleService",
    "validate_lifecycle_transition",
    "configured_artifact_lifecycle_service",
    "public_artifact_lifecycle_enabled",
]
