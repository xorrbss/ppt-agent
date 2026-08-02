"""Transactional adapter for chat-driven presentation mutations.

The legacy chat tools historically commit after each tool call.  Workspace
operations need a stronger boundary: all SQL mutations from a command remain
provisional until the operation fence and presentation revision are published
in one final commit.  ``FlushOnlyAsyncSession`` preserves the existing tool
surface while turning intermediate commits into flushes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.chat import PresentationChatService
from services.chat import memory_layer as chat_memory_layer
from services.database import async_session_maker
from services.presentation_publication_service import (
    PresentationPublicationConflict,
    publish_presentation_revision,
)
from services.presentation_version_service import list_versions, restore_version
from services.workspace_chat_memory_outbox import (
    enqueue_workspace_chat_memory_delivery,
)
from services.workspace_artifact_publication_service import (
    PromotedWorkspaceChatArtifacts,
    WorkspaceArtifactPublicationError,
    compensate_workspace_chat_artifact_staging,
    prepare_workspace_chat_artifact_staging,
    promote_workspace_chat_image_artifacts,
    stage_workspace_chat_artifacts_for_publication,
    workspace_chat_artifacts_were_published,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationCancelled,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)


CHAT_MUTATION_OPERATION_KINDS = (
    "presentation.edit",
    "slide.edit",
    "slide.regenerate",
    "slide.delete",
)

LOGGER = logging.getLogger(__name__)
_CHAT_IMAGE_DIRECTORY_LOCK = asyncio.Lock()


class FlushOnlyAsyncSession:
    """Delegate an ``AsyncSession`` but defer every inner commit.

    A real rollback is deliberately retained.  An inner mutation failure must
    invalidate the whole operation transaction rather than leave a partially
    flushed unit of work available for publication.
    """

    def __init__(self, delegate: AsyncSession) -> None:
        self._delegate = delegate
        self.defer_external_chat_memory = True

    def _retain(self, instance: object) -> None:
        retained = self._delegate.info.setdefault(
            "workspace_chat_provisional_instances", {}
        )
        retained[id(instance)] = instance

    def add(self, instance: object, *, _warn: bool = True) -> None:
        # AsyncSession's identity map uses weak references after a flush. Keep
        # provider-created rows alive until hash promotion can rewrite UUID
        # staging paths in the same transaction.
        self._retain(instance)
        self._delegate.add(instance, _warn=_warn)

    def add_all(self, instances: list[object]) -> None:
        for instance in instances:
            self._retain(instance)
        self._delegate.add_all(instances)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def commit(self) -> None:
        retained = self._delegate.info.setdefault(
            "workspace_chat_provisional_instances", {}
        )
        for instance in self._delegate.new.union(self._delegate.dirty):
            retained[id(instance)] = instance
        await self._delegate.flush()

    async def rollback(self) -> None:
        await self._delegate.rollback()


ChatServiceFactory = Callable[..., PresentationChatService]


def _default_chat_service_factory(**kwargs: Any) -> PresentationChatService:
    return PresentationChatService(**kwargs)


def _error_code(detail: Any, fallback: str) -> str:
    if isinstance(detail, Mapping):
        candidate = detail.get("code") or detail.get("detail")
    else:
        candidate = detail
    normalized = str(candidate or fallback).strip().lower().replace(" ", "_")
    safe = "".join(
        character
        for character in normalized
        if character in "abcdefghijklmnopqrstuvwxyz0123456789_.-"
    )
    return (safe or fallback)[:128]


def _replace_paths(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        updated = value
        for old, new in sorted(
            replacements.items(), key=lambda item: len(item[0]), reverse=True
        ):
            updated = updated.replace(old, new)
        return updated
    if isinstance(value, list):
        return [_replace_paths(item, replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(_replace_paths(item, replacements) for item in value)
    if isinstance(value, dict):
        return {
            key: _replace_paths(item, replacements)
            for key, item in value.items()
        }
    return value


def _rewrite_pending_artifact_paths(
    session: AsyncSession, replacements: Mapping[str, str]
) -> None:
    """Rewrite provider UUID paths before any SQL mutation is published."""

    if not replacements:
        return
    instances: dict[int, object] = {
        id(instance): instance for instance in session.identity_map.values()
    }
    instances.update({id(instance): instance for instance in session.new})
    instances.update({id(instance): instance for instance in session.dirty})
    instances.update(
        session.info.get("workspace_chat_provisional_instances", {})
    )
    for instance in instances.values():
        state = sqlalchemy_inspect(instance)
        for attribute in state.mapper.column_attrs:
            key = attribute.key
            if key not in state.dict:
                continue
            original = state.dict[key]
            updated = _replace_paths(original, replacements)
            if updated != original:
                setattr(instance, key, updated)


class PresentationChatOperationHandler:
    """Execute an existing chat mutation as one fenced publication."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
        service_factory: ChatServiceFactory = _default_chat_service_factory,
    ) -> None:
        self._session_factory = session_factory
        self._service_factory = service_factory

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        command = claim.request_payload.get("command")
        if not isinstance(command, str) or not command.strip():
            raise NonRetryableWorkspaceOperationError(
                "workspace_command_missing"
            )
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError(
                "workspace_expected_revision_required"
            )

        await context.raise_if_cancelled()
        await context.progress(stage="reading_presentation", percent=5)

        async with self._session_factory() as session:
            deferred = cast(AsyncSession, FlushOnlyAsyncSession(session))
            staging = None
            promoted = PromotedWorkspaceChatArtifacts(artifacts=(), replacements={})
            result_payload: dict[str, object] | None = None
            revision: int | None = None
            try:
                # The independently committed journal must exist before the
                # provider receives its operation-owned output directory.
                staging = await prepare_workspace_chat_artifact_staging(
                    session,
                    operation_id=claim.operation_id,
                    presentation_id=claim.presentation_id,
                    source_revision=claim.expected_revision + 1,
                    attempt_token=claim.attempt_token,
                    fencing_token=claim.fencing_token,
                )
                service = self._service_factory(
                    sql_session=deferred,
                    presentation_id=claim.presentation_id,
                    conversation_id=claim.conversation_id,
                )
                await context.progress(stage="applying_command", percent=20)
                # PresentationContextStore imports this helper directly. Keep
                # the narrow module override serialized so another coroutine
                # can never inherit this operation's owner/fence directory.
                async with _CHAT_IMAGE_DIRECTORY_LOCK:
                    original_images_directory = (
                        chat_memory_layer.get_images_directory
                    )
                    chat_memory_layer.get_images_directory = (
                        lambda: str(staging.staging_directory)
                    )
                    try:
                        turn = await service.generate_reply(command.strip())
                    finally:
                        chat_memory_layer.get_images_directory = (
                            original_images_directory
                        )
                await context.raise_if_cancelled()
                promoted = await promote_workspace_chat_image_artifacts(
                    session,
                    staging,
                )
                _rewrite_pending_artifact_paths(session, promoted.replacements)
                await context.progress(stage="publishing_revision", percent=90)

                result_payload = {
                    "conversation_id": str(turn.conversation_id),
                    "summary": _replace_paths(
                        turn.response_text, promoted.replacements
                    ),
                    "tool_calls": _replace_paths(
                        list(turn.tool_calls), promoted.replacements
                    ),
                }
                revision = await publish_presentation_revision(
                    session,
                    presentation_id=claim.presentation_id,
                    expected_revision=claim.expected_revision,
                    operation_id=claim.operation_id,
                    attempt_token=claim.attempt_token,
                    result=result_payload,
                )
                await stage_workspace_chat_artifacts_for_publication(
                    session,
                    staging=staging,
                    artifacts=promoted.artifacts,
                )
                await enqueue_workspace_chat_memory_delivery(
                    session,
                    operation_id=claim.operation_id,
                    owner_id=claim.owner_id,
                    owner_scope=claim.owner_scope,
                    presentation_id=claim.presentation_id,
                    conversation_id=turn.conversation_id,
                    result_revision=revision,
                )
                await session.commit()
            except BaseException as error:
                await session.rollback()
                published = False
                if staging is not None:
                    try:
                        published = await workspace_chat_artifacts_were_published(
                            session,
                            staging=staging,
                            artifacts=promoted.artifacts,
                        )
                    except Exception:
                        await session.rollback()
                        LOGGER.warning(
                            "Unable to reconcile chat artifact publication %s",
                            staging.artifact_id,
                            exc_info=True,
                        )
                if published and result_payload is not None and revision is not None:
                    return WorkspaceOperationResult(
                        payload=result_payload,
                        result_revision=revision,
                    )

                if staging is not None:
                    try:
                        await compensate_workspace_chat_artifact_staging(
                            session, staging
                        )
                    except BaseException:
                        # Preserve cancellation/control flow and the primary
                        # provider/CAS failure. The durable cleanup_pending or
                        # staging journal remains recoverable by the sweeper.
                        LOGGER.warning(
                            "Chat artifact compensation deferred for %s",
                            staging.artifact_id,
                            exc_info=True,
                        )

                if isinstance(error, PresentationPublicationConflict):
                    raise NonRetryableWorkspaceOperationError(
                        "stale_presentation_revision"
                    ) from error
                if isinstance(error, HTTPException):
                    code = _error_code(
                        error.detail, "presentation_edit_failed"
                    )
                    error_type = (
                        RetryableWorkspaceOperationError
                        if error.status_code == 429 or error.status_code >= 500
                        else NonRetryableWorkspaceOperationError
                    )
                    raise error_type(code) from error
                if isinstance(
                    error,
                    (
                        WorkspaceOperationCancelled,
                        NonRetryableWorkspaceOperationError,
                        RetryableWorkspaceOperationError,
                        asyncio.CancelledError,
                        KeyboardInterrupt,
                        SystemExit,
                    ),
                ):
                    raise
                if isinstance(error, WorkspaceArtifactPublicationError):
                    raise RetryableWorkspaceOperationError(error.code) from error
                # SQL changes have not crossed the publication boundary. The
                # runner may safely retry without exposing a partial revision.
                raise RetryableWorkspaceOperationError(
                    "presentation_edit_failed"
                ) from error

        assert result_payload is not None and revision is not None
        return WorkspaceOperationResult(
            payload=result_payload,
            result_revision=revision,
        )


class VersionRestoreOperationHandler:
    """Restore a durable slide snapshot behind the workspace operation fence.

    ``restore_version`` deliberately does not commit.  Keeping its automatic
    checkpoint, slide replacement, operation fence, and revision CAS in this
    single transaction means a stale approval or lost lease exposes no partial
    restore and the publication token advances exactly once.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    ) -> None:
        self._session_factory = session_factory

    @staticmethod
    async def _target_version_id(
        session: AsyncSession,
        *,
        presentation_id: uuid.UUID,
        request_payload: Mapping[str, Any],
    ) -> uuid.UUID:
        raw_context = request_payload.get("context")
        context = raw_context if isinstance(raw_context, Mapping) else {}
        raw_version_id = context.get("version_id", context.get("versionId"))
        if raw_version_id is not None:
            try:
                return uuid.UUID(str(raw_version_id))
            except (TypeError, ValueError, AttributeError) as error:
                raise NonRetryableWorkspaceOperationError(
                    "version_restore_target_invalid"
                ) from error

        if context.get("strategy") == "previous":
            versions = await list_versions(session, presentation_id)
            if versions:
                return versions[0].id
            raise NonRetryableWorkspaceOperationError("version_not_found")

        raise NonRetryableWorkspaceOperationError(
            "version_restore_target_invalid"
        )

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError(
                "workspace_expected_revision_required"
            )

        await context.raise_if_cancelled()
        await context.progress(stage="reading_version", percent=10)

        async with self._session_factory() as session:
            try:
                target_version_id = await self._target_version_id(
                    session,
                    presentation_id=claim.presentation_id,
                    request_payload=claim.request_payload,
                )
                # Check cancellation immediately before the first provisional
                # mutation.  Once the DELETE/INSERT begins, publish remains a
                # short, indivisible database transaction.
                await context.raise_if_cancelled()
                await context.progress(stage="restoring_version", percent=45)
                # No out-of-transaction progress write may run after the first
                # slide mutation (SQLite would otherwise contend with its own
                # open writer transaction).  Announce the final atomic phase
                # immediately before entering it.
                await context.progress(stage="publishing_revision", percent=90)
                restored = await restore_version(
                    session,
                    claim.presentation_id,
                    target_version_id,
                )
                if restored is None:
                    raise NonRetryableWorkspaceOperationError(
                        "version_not_found"
                    )

                result_payload: dict[str, object] = {
                    "kind": "version.restore",
                    "summary": "선택한 버전으로 복원했습니다.",
                    "restored_version_id": str(target_version_id),
                    "slide_count": len(restored),
                    "source_revision": claim.expected_revision,
                    "revision": claim.expected_revision + 1,
                }
                revision = await publish_presentation_revision(
                    session,
                    presentation_id=claim.presentation_id,
                    expected_revision=claim.expected_revision,
                    operation_id=claim.operation_id,
                    attempt_token=claim.attempt_token,
                    result=result_payload,
                )
                await session.commit()
            except BaseException as error:
                await session.rollback()
                if isinstance(error, PresentationPublicationConflict):
                    raise NonRetryableWorkspaceOperationError(
                        "stale_presentation_revision"
                    ) from error
                if isinstance(
                    error,
                    (
                        WorkspaceOperationCancelled,
                        NonRetryableWorkspaceOperationError,
                        RetryableWorkspaceOperationError,
                        asyncio.CancelledError,
                        KeyboardInterrupt,
                        SystemExit,
                    ),
                ):
                    raise
                if isinstance(error, (TypeError, ValueError)):
                    raise NonRetryableWorkspaceOperationError(
                        "version_snapshot_invalid"
                    ) from error
                raise RetryableWorkspaceOperationError(
                    "version_restore_failed"
                ) from error

        return WorkspaceOperationResult(
            payload=result_payload,
            result_revision=revision,
        )


def register_presentation_chat_operation_handlers(
    *,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    replace: bool = False,
    handler: PresentationChatOperationHandler | None = None,
    version_restore_handler: VersionRestoreOperationHandler | None = None,
) -> PresentationChatOperationHandler:
    resolved = handler or PresentationChatOperationHandler()
    for kind in CHAT_MUTATION_OPERATION_KINDS:
        registry.register(kind, resolved, replace=replace)
    registry.register(
        "version.restore",
        version_restore_handler or VersionRestoreOperationHandler(),
        replace=replace,
        recovery_policy="automatic",
    )
    return resolved
