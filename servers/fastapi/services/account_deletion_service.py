from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import PresentationModel
from models.sql.authored_vnext_repository import AuthoredVNextExportJob
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
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
    # This commit is intentionally independent of cleanup.  A crash or
    # external-storage failure must not reactivate sessions or admit new work.
    await session.commit()

    if marked is not None:
        return AccountDeletionMarker(
            user_id=marked.id,
            generation=marked.deletion_generation,
            requested_at=marked.deletion_requested_at,
        )

    user = await session.scalar(
        select(User)
        .where(User.id == user_id)
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise AccountNotFoundError("account does not exist")
    if user.is_superuser:
        raise ProtectedAccountError("administrator accounts cannot be deleted")
    return _marker_from_user(user)


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


async def delete_account_restricted_dependents(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
) -> int:
    """Delete presentation children that intentionally block parent cascades.

    Template V2 canonical and local-state rows use ``ON DELETE RESTRICT`` for
    presentation ownership, so deleting the account cannot rely only on the
    user's FK cascades.  Locking the presentation rows also serializes a
    concurrent template insert on PostgreSQL; SQLite already holds its writer
    reservation after the cleanup-journal snapshot.  A divergent canonical /
    sidecar graph fails closed instead of deleting an ambiguously owned row.

    The caller must snapshot external cleanup targets before invoking this
    helper and owns the surrounding transaction and commit.
    """

    presentation_ids = set(
        (
            await session.scalars(
                select(PresentationModel.id)
                .where(PresentationModel.owner_id == owner_id)
                .with_for_update()
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    if not presentation_ids:
        return 0

    canonical_graph = set(
        (
            await session.execute(
                select(TemplateV2.id, TemplateV2.presentation_id)
                .where(TemplateV2.presentation_id.in_(presentation_ids))
                .with_for_update()
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    sidecar_graph = set(
        (
            await session.execute(
                select(
                    TemplateV2LocalState.template_id,
                    TemplateV2LocalState.presentation_id,
                )
                .where(
                    TemplateV2LocalState.presentation_id.in_(presentation_ids)
                )
                .with_for_update()
                .execution_options(skip_owner_scope=True)
            )
        ).all()
    )
    if canonical_graph != sidecar_graph:
        raise AccountDeletionFenceConflict(
            "account deletion found a divergent Template V2 ownership graph"
        )

    template_ids = {template_id for template_id, _ in canonical_graph}
    if template_ids:
        await session.execute(
            delete(TemplateV2)
            .where(TemplateV2.id.in_(template_ids))
            .execution_options(skip_owner_scope=True)
        )
        await session.flush()
    return len(template_ids)


async def cancel_authored_vnext_exports_for_account(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
) -> int:
    """Fence every active authored-vNext export before the user cascade.

    Incrementing both fences invalidates a worker that already holds a claim.
    Final publication also rechecks the account deletion generation, so a late
    worker cannot recreate an artifact pointer after this transaction commits.
    """

    table_exists = await session.run_sync(
        lambda sync_session: inspect(sync_session.connection()).has_table(
            AuthoredVNextExportJob.__tablename__
        )
    )
    if not table_exists:
        return 0
    result = await session.execute(
        update(AuthoredVNextExportJob)
        .where(
            AuthoredVNextExportJob.owner_id == owner_id,
            AuthoredVNextExportJob.state.in_(
                ("queued", "running", "cancel_requested", "failed")
            ),
        )
        .values(
            state="cancelled",
            cancel_requested=True,
            operation_fence=AuthoredVNextExportJob.operation_fence + 1,
            fence_token=AuthoredVNextExportJob.fence_token + 1,
            worker_id=None,
            lease_expires_at=None,
            failure_code="account_deletion_in_progress",
            failure_detail=None,
            completed_at=get_current_utc_datetime(),
        )
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


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
    "delete_account_restricted_dependents",
    "cancel_authored_vnext_exports_for_account",
    "lock_account_deletion_marker",
    "mark_account_deleting",
    "require_active_account_generation",
]
