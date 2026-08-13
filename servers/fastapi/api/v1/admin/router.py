import asyncio
import logging
import os
from pathlib import Path
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, SecretStr
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
from models.sql.provider_credential_reference import ProviderCredentialReference
from services.account_deletion_service import (
    AccountNotFoundError,
    ProtectedAccountError,
    delete_account_restricted_dependents,
    cancel_authored_vnext_exports_for_account,
    lock_account_deletion_marker,
    mark_account_deleting,
)
from services.authored_vnext.generation.account_lifecycle import (
    cancel_authored_vnext_generation_operations_for_account,
)
from services.authored_vnext.master.assets import quarantine_owner_master_assets
from services.authored_vnext.ooxml.account_lifecycle import (
    fence_authored_vnext_ooxml_packages_for_account,
)
from services.database import get_async_session
from services.provider_settings import (
    ProviderSettingsConcurrencyError,
    ProviderSettingsValidationError,
    REDACTED_SECRET_PLACEHOLDER,
    get_provider_settings,
    get_provider_settings_update_lock,
    redact_provider_settings,
    save_provider_settings_record,
)
from services.provider_egress_policy import (
    EgressCategory,
    EgressPolicyError,
    validate_configured_provider_endpoint,
)
from services.provider_credential_reference_service import (
    revoke_provider_credential,
    revoke_account_provider_credentials,
    rotate_provider_credential,
)
from services.provider_credential_vault import (
    CredentialScope,
    CredentialVaultError,
    provider_credential_vault_from_environment,
)
from services.provider_secure_runtime import (
    resolve_provider_secret,
    setting_credential_id,
)
from utils.available_models import list_available_openai_compatible_models
from services.workspace_cleanup_journal_service import (
    account_cleanup_tombstone_exists,
    redrive_workspace_cleanup_dead_letter,
    snapshot_account_workspace_cleanup_targets,
)
from utils.get_env import (
    get_app_data_directory_env,
    get_can_change_keys_env,
    is_disable_auth_enabled,
)
from utils.user_config import refresh_process_runtime_settings

API_V1_ADMIN_ROUTER = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
logger = logging.getLogger(__name__)


class ProviderCredentialWrite(BaseModel):
    secret: SecretStr


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
            committed = await save_provider_settings_record(session, config)
        except ProviderSettingsValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except ProviderSettingsConcurrencyError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Provider settings changed; retry the request",
            ) from None
        refresh_process_runtime_settings(
            persisted_config=committed.config,
            revision=committed.revision,
        )
        return redact_provider_settings(committed.config)


@API_V1_ADMIN_ROUTER.get("/provider-credentials")
async def list_provider_credentials(
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, object]:
    rows = list(
        (
            await session.scalars(
                select(ProviderCredentialReference).where(
                    ProviderCredentialReference.user_id == admin.id
                )
            )
        ).all()
    )
    return {
        "providers": [
            {"provider": row.provider, "backend": row.backend, "configured": True}
            for row in sorted(rows, key=lambda item: item.provider)
        ]
    }


@API_V1_ADMIN_ROUTER.put("/provider-credentials/{provider}")
async def put_provider_credential(
    provider: str,
    body: ProviderCredentialWrite,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, object]:
    scope = CredentialScope(
        os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"), admin.id
    )
    try:
        vault = provider_credential_vault_from_environment()
        reference = await rotate_provider_credential(
            session,
            vault=vault,
            scope=scope,
            provider=provider,
            secret=body.secret.get_secret_value(),
        )
    except CredentialVaultError as error:
        await session.rollback()
        raise HTTPException(status_code=503, detail=error.code) from error
    return {
        "provider": provider,
        "backend": reference.backend,
        "configured": True,
    }


@API_V1_ADMIN_ROUTER.delete("/provider-credentials/{provider}", status_code=204)
async def delete_provider_credential(
    provider: str,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    scope = CredentialScope(
        os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"), admin.id
    )
    try:
        vault = provider_credential_vault_from_environment()
        removed = await revoke_provider_credential(
            session, vault=vault, scope=scope, provider=provider
        )
    except CredentialVaultError as error:
        await session.rollback()
        raise HTTPException(status_code=503, detail=error.code) from error
    if not removed:
        raise HTTPException(status_code=404, detail="provider_credential_not_found")


@API_V1_ADMIN_ROUTER.post("/provider-settings/custom-model-available")
async def check_stored_custom_model_available(
    _: None = Depends(require_settings_admin),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, bool]:
    """Validate the selected custom model without exposing its stored secret."""
    config = await get_provider_settings(session)
    url = config.get("CUSTOM_LLM_URL")
    configured_api_key = config.get("CUSTOM_LLM_API_KEY", "")
    model = config.get("CUSTOM_MODEL")

    if (
        not isinstance(url, str)
        or not url
        or not isinstance(configured_api_key, str)
        or configured_api_key == REDACTED_SECRET_PLACEHOLDER
        or not isinstance(model, str)
        or not model
    ):
        raise HTTPException(
            status_code=422,
            detail="Stored custom LLM configuration is incomplete",
        )

    try:
        api_key = resolve_provider_secret(
            setting_credential_id("CUSTOM_LLM_API_KEY"),
            configured_api_key,
        )
        if not api_key:
            raise CredentialVaultError("credential_reference_required")
        endpoint = validate_configured_provider_endpoint(
            url,
            category=EgressCategory.PROVIDER,
            service_id="custom-llm",
            allowlist_env="PROVIDER_CUSTOM_EGRESS_ALLOWLIST",
        )
        models = await list_available_openai_compatible_models(
            endpoint.url, api_key
        )
    except (CredentialVaultError, EgressPolicyError) as error:
        raise HTTPException(status_code=400, detail=error.code) from error
    except Exception:
        # Upstream errors can contain request details. Keep credentials and the
        # private endpoint out of both the API response and application logs.
        raise HTTPException(
            status_code=502,
            detail="Unable to validate stored custom LLM configuration",
        ) from None

    return {"available": model in models}


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
        await cancel_authored_vnext_generation_operations_for_account(
            session, owner_id=user.id
        )
        await cancel_authored_vnext_exports_for_account(session, owner_id=user.id)
        app_data_directory = (get_app_data_directory_env() or "").strip()
        if app_data_directory:
            await asyncio.to_thread(
                quarantine_owner_master_assets,
                Path(app_data_directory),
                owner_id=str(user.id),
                expected_generation=marker.generation,
            )
            await asyncio.to_thread(
                fence_authored_vnext_ooxml_packages_for_account,
                Path(app_data_directory),
                owner_id=str(user.id),
                expected_generation=marker.generation,
            )
        elif any(
            os.getenv(flag, "false").lower() in {"1", "true", "yes", "on"}
            for flag in (
                "AUTHORED_VNEXT_OOXML_PACKAGE_ENABLED",
                "AUTHORED_VNEXT_MASTER_ENABLED",
            )
        ):
            raise RuntimeError("authored_vnext_ooxml_storage_unavailable")
        await revoke_account_provider_credentials(session, owner_id=user.id)
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
