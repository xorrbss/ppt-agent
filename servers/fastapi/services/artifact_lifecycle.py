"""Feature-gated artifact lifecycle/refcount/GC product service.

The service owns lifecycle ordering and physical-delete safety while an injected
database authority owns durable compare-and-set state.  It remains fail-closed
until the composition root enables it and supplies that authority; local paths
or process memory are never accepted as lifecycle authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from typing import Literal, Protocol
import uuid

from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.public_artifact import (
    ArtifactRef,
    PublicArtifact,
    PublicArtifactPublication,
    PublicExportIdentity,
    PublicSlideCheckpoint,
)
from services.artifact_store import (
    ArtifactObjectStore,
    ArtifactStoreError,
    configured_public_artifact_service,
)
from utils.datetime_utils import get_current_utc_datetime


ArtifactLifecycleState = Literal[
    "provisional",
    "committed",
    "tombstoned",
    "deleted",
]
PUBLIC_ARTIFACT_LIFECYCLE_ENV = "PUBLIC_ARTIFACT_LIFECYCLE_ENABLED"

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

    # Implementations must exclude any artifact referenced by a publication,
    # checkpoint, or active/succeeded export identity in the same DB snapshot.


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
        now = get_current_utc_datetime()
        predicates = [
            PublicArtifact.id == artifact_id,
            PublicArtifact.lifecycle_state == expected_state,
            PublicArtifact.lifecycle_revision == expected_revision,
        ]
        if ref_count_delta < 0:
            predicates.append(PublicArtifact.refcount >= -ref_count_delta)
        if next_state in {"tombstoned", "deleted"}:
            predicates.append(PublicArtifact.refcount == 0)
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
                    ~exists().where(
                        PublicArtifactPublication.artifact_id == PublicArtifact.id
                    ),
                    ~exists().where(
                        PublicSlideCheckpoint.artifact_id == PublicArtifact.id
                    ),
                    ~exists().where(
                        PublicExportIdentity.result_artifact_id == PublicArtifact.id,
                        PublicExportIdentity.state.in_(("running", "succeeded")),
                    ),
                    ~exists().where(
                        PublicExportIdentity.source_artifact_id == PublicArtifact.id,
                        PublicExportIdentity.state.in_(("running", "succeeded")),
                    ),
                )
                .order_by(PublicArtifact.updated_at, PublicArtifact.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
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
                    ~exists().where(
                        PublicArtifactPublication.artifact_id == PublicArtifact.id
                    ),
                    ~exists().where(
                        PublicSlideCheckpoint.artifact_id == PublicArtifact.id
                    ),
                    ~exists().where(
                        PublicExportIdentity.result_artifact_id == PublicArtifact.id,
                        PublicExportIdentity.state.in_(("running", "succeeded")),
                    ),
                    ~exists().where(
                        PublicExportIdentity.source_artifact_id == PublicArtifact.id,
                        PublicExportIdentity.state.in_(("running", "succeeded")),
                    ),
                )
                .order_by(PublicArtifact.expires_at, PublicArtifact.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
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

    async def change_ref_count(
        self,
        record: ArtifactLifecycleRecord,
        *,
        delta: Literal[-1, 1],
    ) -> ArtifactLifecycleRecord:
        self._require_enabled()
        if delta == 1 and record.state != "committed":
            raise ArtifactStoreError("artifact.reference_add_not_committed")
        if delta == -1 and (record.state not in {"committed", "tombstoned"} or record.ref_count == 0):
            raise ArtifactStoreError("artifact.reference_count_underflow")
        updated = await self._authority.compare_and_set(
            artifact_id=record.artifact.artifact_id,
            expected_state=record.state,
            expected_revision=record.state_revision,
            next_state=record.state,
            ref_count_delta=delta,
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
            claimed = await self._authority.compare_and_set(
                artifact_id=record.artifact.artifact_id,
                expected_state="tombstoned",
                expected_revision=record.state_revision,
                next_state="tombstoned",
            )
            if claimed is None:
                continue
            if claimed.ref_count != 0:
                raise ArtifactStoreError("artifact.gc_candidate_invalid")
            await self._object_store.delete(record.artifact)
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
    value = os.getenv(PUBLIC_ARTIFACT_LIFECYCLE_ENV, "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"", "0", "false", "no", "off"}:
        return False
    raise RuntimeError("artifact.lifecycle_feature_flag_invalid")


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
