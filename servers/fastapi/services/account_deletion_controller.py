"""Durable controller finalization for account deletion requests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
from pathlib import Path
import uuid

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from utils.env_settings.workspace_maintenance import (
    get_account_deletion_interval_seconds,
    is_authored_vnext_ooxml_storage_required,
)
from models.sql.account_deletion_request import AccountDeletionRequest
from models.sql.key_value import KeyValueSqlModel
from services.account_deletion_service import (
    AccountDeletionMarker,
    AccountDeletionFenceConflict,
    cancel_authored_vnext_exports_for_account,
    lock_account_deletion_marker,
    retire_public_artifacts_for_account,
)
from services.authored_vnext.generation.account_lifecycle import (
    cancel_authored_vnext_generation_operations_for_account,
)
from services.authored_vnext.master.assets import quarantine_owner_master_assets
from services.database import (
    async_session_maker,
    require_public_controller_database_authority,
)
from services.provider_credential_reference_service import (
    revoke_account_provider_credentials,
)
from services.workspace_cleanup_journal_service import (
    snapshot_account_workspace_cleanup_targets,
)
from utils.datetime_utils import as_utc, get_current_utc_datetime
from utils.get_env import get_app_data_directory_env


LOGGER = logging.getLogger(__name__)
_EXTERNAL_IO_TIMEOUT_SECONDS = 300.0
_SHUTDOWN_TIMEOUT_SECONDS = 30.0
_DEFAULT_INTERVAL_SECONDS = 5


def _account_deletion_controller_interval_seconds() -> int:
    return get_account_deletion_interval_seconds(_DEFAULT_INTERVAL_SECONDS)


@dataclass(frozen=True, slots=True)
class AccountDeletionClaim:
    owner_id: uuid.UUID
    generation: int
    claim_fence: int
    worker_id: str


async def _apply_external_account_deletion_io(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    generation: int,
) -> None:
    """Run generation-fenced filesystem/vault work within one bounded window."""

    app_data_directory = (get_app_data_directory_env() or "").strip()
    if app_data_directory:
        await asyncio.to_thread(
            quarantine_owner_master_assets,
            Path(app_data_directory),
            owner_id=str(owner_id),
            expected_generation=generation,
        )
        # The OOXML package service, whose queued operations this used to
        # cancel, was removed with authored generation (P3 B4c); its state
        # files hold no deck content and nothing reads them any more.
    elif is_authored_vnext_ooxml_storage_required():
        raise RuntimeError("authored_vnext_ooxml_storage_unavailable")

    await revoke_account_provider_credentials(session, owner_id=owner_id)


async def _run_external_account_deletion_io(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    generation: int,
) -> None:
    async with asyncio.timeout(_EXTERNAL_IO_TIMEOUT_SECONDS):
        await _apply_external_account_deletion_io(
            session,
            owner_id=owner_id,
            generation=generation,
        )


def _consume_controller_task_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        LOGGER.exception("Account deletion controller stopped with an error")


async def claim_account_deletion_request(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = 600,
) -> AccountDeletionClaim | None:
    if not worker_id.strip() or lease_seconds < 30:
        raise ValueError("account deletion claim configuration is invalid")
    bind = session.bind
    if bind is None:
        raise RuntimeError("account_deletion_controller_database_unavailable")
    await require_public_controller_database_authority(bind)
    now = get_current_utc_datetime()
    request = await session.scalar(
        select(AccountDeletionRequest)
        .where(
            AccountDeletionRequest.available_at <= now,
            or_(
                AccountDeletionRequest.state.in_(("pending", "retry")),
                (
                    (AccountDeletionRequest.state == "processing")
                    & (AccountDeletionRequest.claim_expires_at <= now)
                ),
            ),
        )
        .order_by(
            AccountDeletionRequest.available_at,
            AccountDeletionRequest.requested_at,
            AccountDeletionRequest.owner_id,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if request is None:
        await session.rollback()
        return None
    request.state = "processing"
    request.claim_fence += 1
    request.attempt_count += 1
    request.claimed_by = worker_id.strip()
    request.claim_expires_at = now + timedelta(seconds=lease_seconds)
    request.updated_at = now
    request.last_error_code = None
    claim = AccountDeletionClaim(
        owner_id=request.owner_id,
        generation=request.generation,
        claim_fence=request.claim_fence,
        worker_id=request.claimed_by,
    )
    await session.commit()
    return claim


async def finalize_account_deletion_claim(
    session: AsyncSession,
    *,
    claim: AccountDeletionClaim,
) -> bool:
    bind = session.bind
    if bind is None:
        raise RuntimeError("account_deletion_controller_database_unavailable")
    await require_public_controller_database_authority(bind)
    now = get_current_utc_datetime()
    request = await session.scalar(
        select(AccountDeletionRequest)
        .where(
            AccountDeletionRequest.owner_id == claim.owner_id,
            AccountDeletionRequest.generation == claim.generation,
            AccountDeletionRequest.state == "processing",
            AccountDeletionRequest.claim_fence == claim.claim_fence,
            AccountDeletionRequest.claimed_by == claim.worker_id,
            AccountDeletionRequest.claim_expires_at > now,
        )
        .with_for_update()
    )
    if request is None:
        await session.rollback()
        return False
    if request.claim_expires_at is None:
        raise AccountDeletionFenceConflict("account deletion claim lease is missing")

    marker = AccountDeletionMarker(
        user_id=claim.owner_id,
        generation=claim.generation,
        requested_at=request.requested_at,
    )
    user = await lock_account_deletion_marker(session, marker=marker)
    if user is None:
        # A completed user cascade should also remove the request.  Treat a
        # residual row as repairable controller state, never as API success.
        await session.delete(request)
        await session.commit()
        return True

    await snapshot_account_workspace_cleanup_targets(session, owner_id=user.id)
    await cancel_authored_vnext_generation_operations_for_account(
        session,
        owner_id=user.id,
    )
    await cancel_authored_vnext_exports_for_account(session, owner_id=user.id)
    await retire_public_artifacts_for_account(session, owner_id=user.id)

    await _run_external_account_deletion_io(
        session,
        owner_id=user.id,
        generation=claim.generation,
    )
    await session.execute(
        delete(KeyValueSqlModel).where(
            KeyValueSqlModel.key == f"presentation_custom_themes:{user.id}"
        )
    )
    # External quarantine/revocation can consume most of the lease.  A worker
    # may not turn an expired claim into the authoritative user cascade merely
    # because it held the row lock while doing that work; roll the DB unit back
    # and let the same generation be reclaimed with a fresh fence instead.
    if (
        request.claim_expires_at is None
        or as_utc(request.claim_expires_at) <= get_current_utc_datetime()
    ):
        raise AccountDeletionFenceConflict(
            "account deletion claim expired before final cascade"
        )
    await session.delete(user)
    await session.commit()
    return True


async def _release_failed_claim(
    session: AsyncSession,
    *,
    claim: AccountDeletionClaim,
    error: BaseException,
) -> None:
    now = get_current_utc_datetime()
    delay_seconds = min(300, 2 ** min(claim.claim_fence, 8))
    await session.execute(
        update(AccountDeletionRequest)
        .where(
            AccountDeletionRequest.owner_id == claim.owner_id,
            AccountDeletionRequest.generation == claim.generation,
            AccountDeletionRequest.state == "processing",
            AccountDeletionRequest.claim_fence == claim.claim_fence,
            AccountDeletionRequest.claimed_by == claim.worker_id,
        )
        .values(
            state="retry",
            claimed_by=None,
            claim_expires_at=None,
            available_at=now + timedelta(seconds=delay_seconds),
            updated_at=now,
            last_error_code=type(error).__name__[:128],
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def process_one_account_deletion(
    *,
    worker_id: str,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> str:
    async with session_factory() as session:
        claim = await claim_account_deletion_request(session, worker_id=worker_id)
    if claim is None:
        return "idle"
    try:
        async with session_factory() as session:
            completed = await finalize_account_deletion_claim(session, claim=claim)
        return "completed" if completed else "stale"
    except Exception as error:
        async with session_factory() as session:
            await _release_failed_claim(session, claim=claim, error=error)
        LOGGER.error(
            "Account deletion finalization deferred owner_id=%s generation=%s error_type=%s",
            claim.owner_id,
            claim.generation,
            type(error).__name__,
        )
        return "retry"


class AccountDeletionControllerRuntime:
    def __init__(self, *, worker_id: str) -> None:
        if not worker_id.strip():
            raise ValueError("account deletion controller worker id is required")
        self._worker_id = worker_id.strip()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="account-deletion-controller",
        )

    async def close(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None:
            try:
                async with asyncio.timeout(_SHUTDOWN_TIMEOUT_SECONDS):
                    await asyncio.shield(task)
            except TimeoutError:
                LOGGER.error(
                    "Account deletion controller shutdown timed out; cancelling in-flight work"
                )
                task.cancel()
                task.add_done_callback(_consume_controller_task_result)
        self._task = None

    async def _run(self) -> None:
        interval = _account_deletion_controller_interval_seconds()
        while not self._stop.is_set():
            try:
                outcome = await process_one_account_deletion(
                    worker_id=self._worker_id
                )
            except Exception as error:
                LOGGER.error(
                    "Account deletion controller iteration failed error_type=%s",
                    type(error).__name__,
                )
                outcome = "idle"
            if outcome != "idle":
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass


_runtime: AccountDeletionControllerRuntime | None = None


async def start_account_deletion_controller(
    *,
    worker_id: str,
) -> AccountDeletionControllerRuntime:
    global _runtime
    if _runtime is None:
        _runtime = AccountDeletionControllerRuntime(worker_id=worker_id)
    await _runtime.start()
    return _runtime


async def stop_account_deletion_controller() -> None:
    global _runtime
    if _runtime is None:
        return
    await _runtime.close()
    _runtime = None


__all__ = [
    "AccountDeletionClaim",
    "AccountDeletionControllerRuntime",
    "claim_account_deletion_request",
    "finalize_account_deletion_claim",
    "process_one_account_deletion",
    "start_account_deletion_controller",
    "stop_account_deletion_controller",
]
