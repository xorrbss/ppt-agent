import logging
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.auth.schemas import (
    AdminCreateUserRequest,
    AdminResetPasswordRequest,
    PublicUser,
)
from api.v1.auth.users import (
    PASSWORD_HELPER,
    get_current_admin,
    read_user_from_cookie,
    serialize_user,
)
from models.sql.key_value import KeyValueSqlModel
from models.sql.user import User
from services.account_deletion_service import (
    AccountNotFoundError,
    ProtectedAccountError,
    delete_account_restricted_dependents,
    lock_account_deletion_marker,
    mark_account_deleting,
)
from services.database import get_async_session
from services.provider_settings import (
    ProviderSettingsValidationError,
    get_provider_settings,
    get_provider_settings_update_lock,
    redact_provider_settings,
    save_provider_settings,
)
from services.workspace_cleanup_journal_service import (
    account_cleanup_tombstone_exists,
    redrive_workspace_cleanup_dead_letter,
    snapshot_account_workspace_cleanup_targets,
)
from utils.get_env import (
    get_can_change_keys_env,
    is_disable_auth_enabled,
)
from utils.user_config import refresh_process_runtime_settings

API_V1_ADMIN_ROUTER = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
logger = logging.getLogger(__name__)


async def require_settings_admin(
    request: Request,
    user: User | None = Depends(read_user_from_cookie),
) -> None:
    del request
    if is_disable_auth_enabled():
        return
    if user is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")


def _ensure_settings_are_mutable() -> None:
    if get_can_change_keys_env() == "false":
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to access this resource",
        )


@API_V1_ADMIN_ROUTER.get("/provider-settings")
async def read_provider_settings(
    _: None = Depends(require_settings_admin),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    _ensure_settings_are_mutable()
    return redact_provider_settings(await get_provider_settings(session))


@API_V1_ADMIN_ROUTER.put("/provider-settings")
async def update_provider_settings(
    config: dict[str, Any] = Body(...),
    _: None = Depends(require_settings_admin),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    _ensure_settings_are_mutable()
    async with get_provider_settings_update_lock():
        try:
            saved = await save_provider_settings(session, config)
        except ProviderSettingsValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        refresh_process_runtime_settings(persisted_config=saved)
        return redact_provider_settings(saved)


@API_V1_ADMIN_ROUTER.get("/users", response_model=list[PublicUser])
async def list_users(
    _: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
):
    users = (
        await session.scalars(
            select(User).order_by(User.created_at.desc(), User.username.asc())
        )
    ).all()
    return [serialize_user(user) for user in users]


@API_V1_ADMIN_ROUTER.post(
    "/users", response_model=PublicUser, status_code=status.HTTP_201_CREATED
)
async def create_user(
    body: AdminCreateUserRequest,
    _: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
):
    username = body.username.strip()
    if len(username) < 3:
        raise HTTPException(
            status_code=422,
            detail="Username must be at least 3 characters",
        )
    exists = await session.scalar(
        select(User.id).where(func.lower(User.username) == username.casefold())
    )
    if exists:
        raise HTTPException(status_code=409, detail="Username already exists")
    user = User(
        username=username,
        hashed_password=PASSWORD_HELPER.hash(body.password),
        is_active=True,
        is_verified=True,
        is_superuser=False,
        auth_version=1,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return serialize_user(user)


@API_V1_ADMIN_ROUTER.put("/users/{user_id}/password", response_model=PublicUser)
async def reset_user_password(
    user_id: uuid.UUID,
    body: AdminResetPasswordRequest,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id or user.is_superuser:
        raise HTTPException(
            status_code=403,
            detail="The primary administrator password is managed through deployment settings",
        )
    user.hashed_password = PASSWORD_HELPER.hash(body.password)
    user.auth_version += 1
    await session.commit()
    return serialize_user(user)


@API_V1_ADMIN_ROUTER.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
):
    if user_id == admin.id:
        raise HTTPException(
            status_code=403,
            detail="The primary administrator account cannot be deleted",
        )

    # Phase one is committed independently.  This immediately revokes every
    # outstanding token through auth_version, and leaves a durable, resumable
    # deletion marker if snapshotting or the final cascade later fails.
    try:
        marker = await mark_account_deleting(session, user_id=user_id)
    except AccountNotFoundError as exc:
        if await account_cleanup_tombstone_exists(session, owner_id=user_id):
            # The user FK cascade and its durable cleanup snapshot committed,
            # but the caller may have lost the 204 response.  Replay success
            # only for UUIDs proven by an account-owned cleanup tombstone.
            return None
        raise HTTPException(status_code=404, detail="User not found") from exc
    except ProtectedAccountError as exc:
        raise HTTPException(
            status_code=403,
            detail="The primary administrator account cannot be deleted",
        ) from exc

    try:
        user = await lock_account_deletion_marker(session, marker=marker)
        if user is None:
            # A concurrent idempotent finalizer already completed the cascade.
            return None

        # Snapshot once, flush its durable intents, then rescan before the FK
        # cascade.  Admission/publication fences use deletion_generation, while
        # the rescan closes work that committed immediately before phase one.
        await snapshot_account_workspace_cleanup_targets(session, owner_id=user.id)
        await session.flush()
        await snapshot_account_workspace_cleanup_targets(session, owner_id=user.id)
        await delete_account_restricted_dependents(session, owner_id=user.id)
        await session.execute(
            delete(KeyValueSqlModel).where(
                KeyValueSqlModel.key == f"presentation_custom_themes:{user.id}"
            )
        )
        await session.delete(user)
        await session.commit()
    except Exception:
        await session.rollback()
        raise


@API_V1_ADMIN_ROUTER.post(
    "/workspace-cleanup/dead-letters/{journal_id}/redrive",
    status_code=status.HTTP_202_ACCEPTED,
)
async def redrive_workspace_cleanup(
    journal_id: uuid.UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    result = await redrive_workspace_cleanup_dead_letter(
        session,
        journal_id=journal_id,
        requested_by=admin.id,
    )
    if result.outcome == "not_found":
        await session.rollback()
        raise HTTPException(status_code=404, detail="Cleanup target not found")
    if result.outcome == "non_retryable":
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Cleanup target is not safely retryable",
        )
    if result.outcome == "conflict":
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Cleanup target changed during redrive",
        )

    await session.commit()
    logger.info(
        "workspace cleanup redrive admin_id=%s journal_id=%s outcome=%s status=%s",
        admin.id,
        journal_id,
        result.outcome,
        result.status,
    )
    return {
        "journal_id": str(result.journal_id),
        "status": result.status,
        "redriven": result.outcome == "redriven",
        "max_attempts": result.max_attempts,
        "redrive_count": result.redrive_count,
    }
