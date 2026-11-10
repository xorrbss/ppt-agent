from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.account_deletion_request import AccountDeletionRequest
from models.sql.public_artifact import (
    PublicArtifact,
    PublicArtifactPublication,
    PublicExportIdentity,
    PublicSlideCheckpoint,
)
from models.sql.user import User
from utils.datetime_utils import get_current_utc_datetime

ACCOUNT_DELETION_ACTIVE = "active"
ACCOUNT_DELETION_DELETING = "deleting"


class AccountDeletionError(RuntimeError):
    """Base class for durable account-deletion admission failures."""


class AccountNotFoundError(AccountDeletionError):
    pass


class ProtectedAccountError(AccountDeletionError):
    pass


class AccountDeletionFenceConflict(AccountDeletionError):
    pass


@dataclass(frozen=True)
class AccountDeletionMarker:
    user_id: uuid.UUID
    generation: int
    requested_at: datetime.datetime


async def _ensure_account_deletion_request(
    session: AsyncSession,
    *,
    marker: AccountDeletionMarker,
) -> None:
    values = {
        "owner_id": marker.user_id,
        "generation": marker.generation,
        "state": "pending",
        "available_at": marker.requested_at,
        "requested_at": marker.requested_at,
        "updated_at": marker.requested_at,
    }
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert

        statement = dialect_insert(AccountDeletionRequest).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["owner_id"])
        await session.execute(statement)
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        statement = dialect_insert(AccountDeletionRequest).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["owner_id"])
        await session.execute(statement)
    else:
        existing = await session.get(AccountDeletionRequest, marker.user_id)
        if existing is None:
            session.add(AccountDeletionRequest(**values))
            await session.flush()

    request = await session.get(
        AccountDeletionRequest,
        marker.user_id,
        populate_existing=True,
    )
    if request is None or request.generation != marker.generation:
        raise AccountDeletionFenceConflict(
            "account deletion request generation is inconsistent"
        )


def _marker_from_user(user: User) -> AccountDeletionMarker:
    requested_at = user.deletion_requested_at
    if (
        user.deletion_state != ACCOUNT_DELETION_DELETING
        or user.is_active
        or user.deletion_generation < 1
        or requested_at is None
    ):
        raise AccountDeletionFenceConflict(
            "account deletion marker is internally inconsistent"
        )
    return AccountDeletionMarker(
        user_id=user.id,
        generation=user.deletion_generation,
        requested_at=requested_at,
    )


async def mark_account_deleting(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> AccountDeletionMarker:
    """Durably revoke an account before cleanup or its FK cascade begins.

    The conditional update is the serialization point for duplicate DELETE
    requests.  Only its winner bumps the authentication and deletion
    generations; retries resume from the already committed marker.
    """

    requested_at = get_current_utc_datetime()
    marked = (
        await session.execute(
            update(User)
            .where(
                User.id == user_id,
                User.is_superuser.is_(False),
                User.deletion_state == ACCOUNT_DELETION_ACTIVE,
            )
            .values(
                is_active=False,
                auth_version=User.auth_version + 1,
                deletion_state=ACCOUNT_DELETION_DELETING,
                deletion_generation=User.deletion_generation + 1,
                deletion_requested_at=requested_at,
            )
            .returning(
                User.id,
                User.deletion_generation,
                User.deletion_requested_at,
            )
            .execution_options(synchronize_session=False)
        )
    ).one_or_none()
    if marked is not None:
        marker = AccountDeletionMarker(
            user_id=marked.id,
            generation=marked.deletion_generation,
            requested_at=marked.deletion_requested_at,
        )
    else:
        user = await session.scalar(
            select(User)
            .where(User.id == user_id)
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise AccountNotFoundError("account does not exist")
        if user.is_superuser:
            raise ProtectedAccountError("administrator accounts cannot be deleted")
        marker = _marker_from_user(user)

    # Account revocation and its controller outbox are one durable commit.  A
    # crash or external-storage failure after this point cannot reactivate the
    # user and cannot lose the deletion request.
    await _ensure_account_deletion_request(session, marker=marker)
    await session.commit()
    return marker


async def lock_account_deletion_marker(
    session: AsyncSession,
    *,
    marker: AccountDeletionMarker,
) -> User | None:
    """Lock and revalidate a marker immediately before snapshot/cascade.

    ``None`` means another idempotent finalizer already completed the cascade.
    """

    user = await session.scalar(
        select(User)
        .where(User.id == marker.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        return None
    current = _marker_from_user(user)
    if current.generation != marker.generation:
        raise AccountDeletionFenceConflict(
            "account deletion generation changed during finalization"
        )
    return user


async def retire_public_artifacts_for_account(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
) -> int:
    """Fence public export reuse and tombstone every account-owned object.

    Public artifact rows intentionally have no user foreign key because their
    immutable object-store identity can outlive a request transaction.  Account
    deletion must therefore remove publication/checkpoint references and mark
    the remaining objects for the controller-owned retention worker before the
    user cascade commits.  Physical object deletion stays asynchronous and
    feature-gated; a disabled collector leaves an auditable tombstone rather
    than silently losing deletion intent.
    """

    # The target owner normally differs from the authenticated administrator.
    # FORCE RLS therefore makes this a cross-tenant/controller scan in public
    # PostgreSQL profiles.  Never interpret an API-role zero-row result as a
    # completed deletion.
    from services.database import require_public_controller_database_authority

    bind = session.bind
    if bind is None:
        raise AccountDeletionFenceConflict(
            "account deletion database authority is unavailable"
        )
    await require_public_controller_database_authority(bind)

    tables_exist = await session.run_sync(
        lambda sync_session: {
            table: inspect(sync_session.connection()).has_table(table)
            for table in (
                PublicArtifact.__tablename__,
                PublicArtifactPublication.__tablename__,
                PublicSlideCheckpoint.__tablename__,
                PublicExportIdentity.__tablename__,
            )
        }
    )
    if not any(tables_exist.values()):
        return 0
    if not all(tables_exist.values()):
        raise AccountDeletionFenceConflict(
            "public artifact lifecycle schema is only partially applied"
        )

    now = get_current_utc_datetime()
    owned_artifacts = select(PublicArtifact.id).where(
        PublicArtifact.owner_id == owner_id
    )
    await session.execute(
        delete(PublicArtifactPublication).where(
            PublicArtifactPublication.artifact_id.in_(owned_artifacts)
        )
    )
    await session.execute(
        delete(PublicSlideCheckpoint).where(
            PublicSlideCheckpoint.artifact_id.in_(owned_artifacts)
        )
    )
    await session.execute(
        update(PublicExportIdentity)
        .where(
            PublicExportIdentity.owner_id == owner_id,
            PublicExportIdentity.state.in_(("running", "succeeded")),
        )
        .values(
            state="failed",
            result_artifact_id=None,
            error_code="account_deletion_in_progress",
            fencing_token=PublicExportIdentity.fencing_token + 1,
            lease_expires_at=now,
            completed_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    retired = await session.execute(
        update(PublicArtifact)
        .where(
            PublicArtifact.owner_id == owner_id,
            PublicArtifact.lifecycle_state.in_(("provisional", "committed")),
        )
        .values(
            lifecycle_state="tombstoned",
            refcount=0,
            lifecycle_revision=PublicArtifact.lifecycle_revision + 1,
            expires_at=now,
            tombstoned_at=func.coalesce(PublicArtifact.tombstoned_at, now),
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    return int(retired.rowcount or 0)


async def require_active_account_generation(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    expected_generation: int | None = None,
) -> int:
    """Return the active owner generation or reject stale/new publication.

    Mutation admission can omit ``expected_generation``.  Long-running work
    should capture the returned value and provide it before publishing an
    external resource or a final database result.
    """

    predicates = [
        User.id == user_id,
        User.deletion_state == ACCOUNT_DELETION_ACTIVE,
        User.is_active.is_(True),
    ]
    if expected_generation is not None:
        predicates.append(User.deletion_generation == expected_generation)

    # This intentionally writes the current value back to itself.  PostgreSQL
    # obtains a row lock and SQLite obtains its database writer reservation, so
    # account deletion cannot interleave until the caller commits or rolls back
    # the protected admission/publication transaction.
    generation = await session.scalar(
        update(User)
        .where(*predicates)
        .values(deletion_generation=User.deletion_generation)
        .returning(User.deletion_generation)
        .execution_options(synchronize_session=False)
    )
    if generation is None:
        raise AccountDeletionFenceConflict(
            "account deletion is in progress, the account is missing/inactive, "
            "or its generation is stale"
        )
    return int(generation)


__all__ = [
    "ACCOUNT_DELETION_ACTIVE",
    "ACCOUNT_DELETION_DELETING",
    "AccountDeletionFenceConflict",
    "AccountDeletionMarker",
    "AccountNotFoundError",
    "ProtectedAccountError",
    "lock_account_deletion_marker",
    "mark_account_deleting",
    "retire_public_artifacts_for_account",
    "require_active_account_generation",
]
