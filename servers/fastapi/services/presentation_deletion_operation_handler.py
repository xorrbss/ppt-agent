"""Fenced workspace-operation handler for approved presentation deletion."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PresentationModel,
)
from models.sql.workspace import WorkspaceCleanupJournal, WorkspaceOperation
from services.database import async_session_maker
from services.presentation_deletion_service import (
    PresentationDeleteConflictError,
    PresentationDeletionService,
    is_retryable_sqlite_lock,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationCancelled,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)
from services.workspace_operation_runner import append_operation_event
from utils.datetime_utils import get_current_utc_datetime

PRESENTATION_DELETION_OPERATION_KIND = "presentation.delete"
_CLEANUP_TERMINAL_STATES = frozenset({"succeeded", "dead_letter"})


class PresentationDeletionOperationHandler:
    """Turn an approved delete Command into a durable presentation tombstone."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    ) -> None:
        self._session_factory = session_factory

    async def execute(
        self,
        context: WorkspaceOperationExecutionContext,
    ) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.kind != PRESENTATION_DELETION_OPERATION_KIND:
            raise NonRetryableWorkspaceOperationError(
                "presentation_delete_operation_kind_invalid"
            )
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError(
                "presentation_delete_expected_revision_required"
            )
        current = get_current_utc_datetime()
        async with self._session_factory() as session:
            try:
                operation = (
                    await session.scalars(
                        select(WorkspaceOperation)
                        .where(WorkspaceOperation.id == claim.operation_id)
                        .with_for_update()
                        .execution_options(skip_owner_scope=True)
                    )
                ).one_or_none()
                if operation is None:
                    raise WorkspaceOperationLeaseLost(
                        "presentation_delete_operation_missing"
                    )
                if (
                    operation.state != "running"
                    or operation.kind != PRESENTATION_DELETION_OPERATION_KIND
                    or operation.presentation_id != claim.presentation_id
                    or operation.owner_id != claim.owner_id
                    or operation.owner_scope != claim.owner_scope
                    or operation.attempt_token != claim.attempt_token
                    or operation.attempt_number != claim.attempt_number
                    or operation.fencing_token != claim.fencing_token
                ):
                    raise WorkspaceOperationLeaseLost(
                        "presentation_delete_operation_fenced"
                    )
                if operation.expected_revision != claim.expected_revision:
                    raise NonRetryableWorkspaceOperationError(
                        "presentation_delete_expected_revision_mismatch"
                    )

                presentation = (
                    await session.scalars(
                        select(PresentationModel)
                        .where(PresentationModel.id == claim.presentation_id)
                        .with_for_update()
                        .execution_options(skip_owner_scope=True)
                    )
                ).one_or_none()
                if presentation is None:
                    raise NonRetryableWorkspaceOperationError(
                        "presentation_not_found"
                    )
                if presentation.owner_id != claim.owner_id:
                    raise NonRetryableWorkspaceOperationError(
                        "presentation_delete_owner_mismatch"
                    )
                if presentation.revision != claim.expected_revision:
                    raise NonRetryableWorkspaceOperationError(
                        "revision_conflict",
                        detail={
                            "expected_revision": claim.expected_revision,
                            "actual_revision": presentation.revision,
                        },
                    )
                if (
                    presentation.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED
                    and presentation.deletion_operation_id != claim.operation_id
                ):
                    raise NonRetryableWorkspaceOperationError(
                        "presentation_already_deleted"
                    )

                effect_committed = (
                    presentation.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED
                    and presentation.deletion_operation_id == claim.operation_id
                )
                # Approval expiry is an admission deadline, not an execution
                # deadline.  The durable awaiting_approval -> queued transition
                # proves approval was consumed.  Once this operation's
                # tombstone exists, its irreversible effect also wins over a
                # late cancellation or an old, pre-consumption expiry value.
                if (
                    not effect_committed
                    and operation.cancel_requested_at is not None
                ):
                    raise WorkspaceOperationCancelled(
                        "workspace_operation_cancelled"
                    )

                await PresentationDeletionService(session).tombstone(
                    presentation,
                    operation_id=claim.operation_id,
                )
                cleanup_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(WorkspaceCleanupJournal)
                        .where(
                            WorkspaceCleanupJournal.presentation_id_snapshot
                            == claim.presentation_id,
                            WorkspaceCleanupJournal.status.not_in(
                                _CLEANUP_TERMINAL_STATES
                            ),
                        )
                    )
                    or 0
                )
                result_payload = {
                    "presentation_id": str(claim.presentation_id),
                    "lifecycle_status": PRESENTATION_LIFECYCLE_DELETED,
                    "cleanup_pending": cleanup_count > 0,
                    "deletion_operation_id": str(claim.operation_id),
                    "result_revision": claim.expected_revision,
                }
                publication_conditions = (
                    ()
                    if effect_committed
                    else (WorkspaceOperation.cancel_requested_at.is_(None),)
                )
                terminal_event = await append_operation_event(
                    session,
                    operation_id=claim.operation_id,
                    event_type="operation.result",
                    payload={"state": "succeeded", **result_payload},
                    operation_kind=claim.kind,
                    attempt_token=claim.attempt_token,
                    allowed_states=("running",),
                    projection_values={
                        "state": "succeeded",
                        "result": result_payload,
                        "result_revision": claim.expected_revision,
                        "attempt_token": None,
                        "lease_expires_at": None,
                        "heartbeat_at": current,
                        "approval_expires_at": None,
                        "cancel_requested_at": None,
                        "cancelled_at": None,
                        "error_code": None,
                        "error_detail": None,
                        "completed_at": current,
                    },
                    additional_conditions=publication_conditions,
                )
                if terminal_event is None:
                    raise WorkspaceOperationLeaseLost(
                        "presentation_delete_terminal_publication_fenced"
                    )
                await session.commit()
            except WorkspaceOperationLeaseLost:
                await session.rollback()
                raise
            except WorkspaceOperationCancelled:
                await session.rollback()
                raise
            except NonRetryableWorkspaceOperationError:
                await session.rollback()
                raise
            except PresentationDeleteConflictError as error:
                await session.rollback()
                raise NonRetryableWorkspaceOperationError(
                    "presentation_delete_dependency_conflict"
                ) from error
            except IntegrityError as error:
                await session.rollback()
                raise NonRetryableWorkspaceOperationError(
                    "presentation_delete_dependency_conflict"
                ) from error
            except OperationalError as error:
                await session.rollback()
                if is_retryable_sqlite_lock(error, session):
                    raise RetryableWorkspaceOperationError(
                        "presentation_delete_temporarily_unavailable"
                    ) from error
                raise RetryableWorkspaceOperationError(
                    "presentation_delete_database_unavailable"
                ) from error

        return WorkspaceOperationResult(
            payload=result_payload,
            result_revision=claim.expected_revision,
        )


def register_presentation_deletion_operation_handler(
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    *,
    replace: bool = False,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> PresentationDeletionOperationHandler:
    handler = PresentationDeletionOperationHandler(session_factory=session_factory)
    registry.register(
        PRESENTATION_DELETION_OPERATION_KIND,
        handler,
        replace=replace,
        recovery_policy="automatic",
    )
    return handler


__all__ = [
    "PRESENTATION_DELETION_OPERATION_KIND",
    "PresentationDeletionOperationHandler",
    "register_presentation_deletion_operation_handler",
]
