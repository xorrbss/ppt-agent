"""Transactional adapter for chat-driven presentation mutations.

The legacy chat tools historically commit after each tool call.  Workspace
operations need a stronger boundary: all SQL mutations from a command remain
provisional until the operation fence and presentation revision are published
in one final commit.  ``BufferedAsyncSession`` preserves the existing tool
surface while turning intermediate commits into pure buffering, so the writer
lock is taken once - after the provider responds - rather than held across it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import inspect as sqlalchemy_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.chat_history_message import ChatHistoryMessageModel
from models.sql.presentation import PresentationModel
from models.sql.workspace import WorkspaceAsset, WorkspaceMessage
from services.agent_runtime import (
    AgentRunResult,
    BoundedCheckpointEventSink,
    OperationArtifactScope,
    PresentationAgentRuntime,
    RunAsset,
    RunContext,
    RunMessage,
    RunViewport,
)
from services.chat import PresentationChatService
from services.chat.service import ChatTurnResult
from services.compatibility_migration.runtime import (
    FrozenCompatibilityError,
    require_authored_mutation_evidence,
)
from services.database import async_session_maker
from services.presentation_publication_service import (
    PresentationPublicationConflict,
    publish_presentation_revision,
)
from services.presentation_version_service import list_versions, restore_version
from services.workspace_chat_memory_outbox import (
    enqueue_workspace_chat_memory_delivery,
)
from services.workspace_db_adapter import reserve_conversation_positions
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
from services.workspace_buffered_session import (
    BufferedAsyncSession as BufferedAsyncSession,
)


# `slide.regenerate` is deliberately absent: it belongs to the authored page
# remake handler, which registers after this one and would otherwise win only
# by registration order.  Chat cannot serve that kind anyway - on an authored
# deck its tools can delete a page but never rebuild one - so claiming the kind
# here left the destructive path one line reorder away from being reachable.
CHAT_MUTATION_OPERATION_KINDS = (
    "presentation.edit",
    "slide.edit",
    "slide.delete",
)

LOGGER = logging.getLogger(__name__)


def _turn_changed_the_deck(session: AsyncSession) -> bool:
    """Whether the buffered turn holds any change beyond its own transcript.

    Because the turn buffers instead of flushing, its complete unit of work is
    still in the session here - so this is the durable answer to "did the model
    actually edit anything", not a guess from the tool names it called.

    Every turn appends chat history, including one that only answered a
    question, so those rows are not evidence of an edit.
    """

    return any(
        not isinstance(instance, (ChatHistoryMessageModel, WorkspaceMessage))
        for instance in (*session.new, *session.dirty, *session.deleted)
    )


ChatServiceFactory = Callable[..., PresentationChatService]


def _default_chat_service_factory(**kwargs: Any) -> PresentationChatService:
    return PresentationChatService(**kwargs)


def _error_code(detail: Any, fallback: str) -> str:
    if isinstance(detail, Mapping):
        candidate = detail.get("code") or detail.get("detail")
    else:
        candidate = detail
    normalized = str(candidate or fallback).strip().lower().replace(" ", "_")
    safe = "".join(character for character in normalized if character in "abcdefghijklmnopqrstuvwxyz0123456789_.-")
    return (safe or fallback)[:128]


def chat_turn_result_payload(turn: ChatTurnResult | AgentRunResult, replacements: Mapping[str, str]) -> dict[str, Any]:
    """Describe a finished chat turn for the workspace client."""

    response_text = turn.assistant_output if isinstance(turn, AgentRunResult) else turn.response_text
    payload: dict[str, Any] = {
        "conversation_id": str(turn.conversation_id),
        "summary": _replace_paths(response_text, replacements),
        "tool_calls": _replace_paths(list(turn.tool_calls), replacements),
    }
    if isinstance(turn, AgentRunResult):
        payload["mutations"] = [mutation.kind for mutation in turn.mutations]
        payload["tool_summaries"] = [
            {
                "tool": summary.name,
                "status": summary.status,
                "summary": summary.summary,
            }
            for summary in turn.tool_summaries
        ]
    # The turn answered but could not act: the page is model-authored HTML the
    # chat tools cannot rebuild.  Naming the page turns a reply that merely
    # describes the remake flow into one the client can offer.  The key is
    # absent when nothing was refused, so existing readers see no change.
    if turn.page_remake_slide_index is not None:
        payload["page_remake_slide_index"] = turn.page_remake_slide_index
    return payload


def _replace_paths(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        updated = value
        for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            updated = updated.replace(old, new)
        return updated
    if isinstance(value, list):
        return [_replace_paths(item, replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(_replace_paths(item, replacements) for item in value)
    if isinstance(value, dict):
        return {key: _replace_paths(item, replacements) for key, item in value.items()}
    return value


def _rewrite_pending_artifact_paths(session: AsyncSession, replacements: Mapping[str, str]) -> None:
    """Rewrite provider UUID paths before any SQL mutation is published."""

    if not replacements:
        return
    instances: dict[int, object] = {id(instance): instance for instance in session.identity_map.values()}
    instances.update({id(instance): instance for instance in session.new})
    instances.update({id(instance): instance for instance in session.dirty})
    instances.update(session.info.get("workspace_chat_provisional_instances", {}))
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


def _optional_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _bounded_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:limit] if normalized else None


def _run_viewport(raw_context: Mapping[str, Any]) -> RunViewport | None:
    raw_viewport = raw_context.get("viewport")
    viewport = raw_viewport if isinstance(raw_viewport, Mapping) else {}
    width = viewport.get("width")
    height = viewport.get("height")
    zoom = viewport.get("zoom", 1.0)
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or width < 1
        or width > 100_000
        or not isinstance(height, int)
        or isinstance(height, bool)
        or height < 1
        or height > 100_000
        or not isinstance(zoom, (int, float))
        or isinstance(zoom, bool)
        or zoom <= 0
        or zoom > 20
    ):
        return None

    raw_slide = raw_context.get("current_slide", raw_context.get("currentSlide"))
    slide = raw_slide if isinstance(raw_slide, Mapping) else {}
    raw_index = (
        slide.get("index")
        if slide
        else raw_slide
        if isinstance(raw_slide, int) and not isinstance(raw_slide, bool)
        else None
    )
    slide_index = (
        raw_index if isinstance(raw_index, int) and not isinstance(raw_index, bool) and raw_index >= 0 else None
    )
    return RunViewport(
        width=width,
        height=height,
        zoom=float(zoom),
        slide_id=_optional_uuid(slide.get("slide_id", slide.get("slideId")) if slide else None),
        slide_index=slide_index,
    )


async def _load_canonical_history(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    current_user_message_id: uuid.UUID | None,
) -> tuple[RunMessage, ...]:
    rows = list(
        (
            await session.scalars(
                select(WorkspaceMessage)
                .where(WorkspaceMessage.conversation_id == conversation_id)
                .order_by(WorkspaceMessage.position.asc())
            )
        ).all()
    )
    return tuple(
        RunMessage(role=cast(Any, row.role), content=row.content)
        for row in rows
        if row.id != current_user_message_id and row.role in {"user", "assistant", "system", "tool"} and row.content
    )


async def _load_ready_run_assets(
    session: AsyncSession,
    *,
    raw_asset_ids: Any,
    conversation_id: uuid.UUID,
    owner_id: uuid.UUID | None,
) -> tuple[RunAsset, ...]:
    if not raw_asset_ids:
        return ()
    if not isinstance(raw_asset_ids, list) or len(raw_asset_ids) > 100:
        raise NonRetryableWorkspaceOperationError("workspace_asset_not_ready")
    asset_ids: list[uuid.UUID] = []
    for value in raw_asset_ids:
        asset_id = _optional_uuid(value)
        if asset_id is None or asset_id in asset_ids:
            raise NonRetryableWorkspaceOperationError("workspace_asset_not_ready")
        asset_ids.append(asset_id)

    statement = select(WorkspaceAsset).where(
        WorkspaceAsset.id.in_(asset_ids),
        WorkspaceAsset.conversation_id == conversation_id,
        WorkspaceAsset.state == "ready",
        WorkspaceAsset.deleted_at.is_(None),
        WorkspaceAsset.sha256.is_not(None),
    )
    statement = statement.where(
        WorkspaceAsset.owner_id == owner_id if owner_id is not None else WorkspaceAsset.owner_id.is_(None)
    )
    rows = list((await session.scalars(statement)).all())
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(asset_ids):
        # The worker revalidates after admission so a concurrent quarantine,
        # expiry or ownership change can never enter provider context.
        raise NonRetryableWorkspaceOperationError("workspace_asset_not_ready")
    return tuple(
        RunAsset(
            id=asset_id,
            name=by_id[asset_id].original_name,
            media_type=by_id[asset_id].media_type,
            size_bytes=by_id[asset_id].size_bytes,
            sha256=cast(str, by_id[asset_id].sha256),
        )
        for asset_id in asset_ids
    )


async def _build_run_context(
    session: AsyncSession,
    *,
    claim: Any,
    command: str,
    artifact_scope: OperationArtifactScope,
    cancellation_check: Callable[[], Any],
) -> RunContext:
    presentation = await session.get(PresentationModel, claim.presentation_id)
    if presentation is None:
        raise NonRetryableWorkspaceOperationError("presentation_not_found")
    try:
        profile = await require_authored_mutation_evidence(session, presentation)
    except FrozenCompatibilityError as error:
        raise NonRetryableWorkspaceOperationError(error.code) from error
    generation_mode = "standard"
    raw_context = claim.request_payload.get("context")
    structured = raw_context if isinstance(raw_context, Mapping) else {}
    current_user_message_id = _optional_uuid(claim.request_payload.get("user_message_id"))
    return RunContext(
        run_id=claim.operation_id,
        presentation_id=claim.presentation_id,
        conversation_id=claim.conversation_id,
        user_message=command,
        expected_revision=claim.expected_revision,
        generation_mode=cast(Any, generation_mode),
        history=await _load_canonical_history(
            session,
            conversation_id=claim.conversation_id,
            current_user_message_id=current_user_message_id,
        ),
        viewport=_run_viewport(structured),
        assets=await _load_ready_run_assets(
            session,
            raw_asset_ids=claim.request_payload.get("asset_ids", []),
            conversation_id=claim.conversation_id,
            owner_id=claim.owner_id,
        ),
        artifact_scope=artifact_scope,
        cancellation_check=cancellation_check,
    )


async def _append_canonical_assistant_message(
    session: AsyncSession,
    *,
    context: RunContext,
    result: AgentRunResult,
) -> WorkspaceMessage:
    existing = await session.get(WorkspaceMessage, context.assistant_message_id)
    if existing is not None:
        if (
            existing.conversation_id != context.conversation_id
            or existing.role != "assistant"
            or existing.content != result.assistant_output
        ):
            raise RuntimeError("assistant_message_publication_conflict")
        return existing

    reservation = await reserve_conversation_positions(session, context.conversation_id, 1)
    message = WorkspaceMessage(
        id=context.assistant_message_id,
        conversation_id=context.conversation_id,
        position=reservation.start,
        role="assistant",
        content=result.assistant_output,
        message_metadata={
            "run_id": str(context.run_id),
            "tool_calls": list(result.tool_calls),
            "mutations": [mutation.kind for mutation in result.mutations],
        },
    )
    session.add(message)
    return message


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

    async def execute(self, context: WorkspaceOperationExecutionContext) -> WorkspaceOperationResult:
        claim = context.claim
        command = claim.request_payload.get("command")
        if not isinstance(command, str) or not command.strip():
            raise NonRetryableWorkspaceOperationError("workspace_command_missing")
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError("workspace_expected_revision_required")

        await context.raise_if_cancelled()
        await context.progress(stage="reading_presentation", percent=5)

        async with self._session_factory() as session:
            deferred = cast(AsyncSession, BufferedAsyncSession(session))
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
                run_context = await _build_run_context(
                    deferred,
                    claim=claim,
                    command=command.strip(),
                    artifact_scope=OperationArtifactScope(staging.staging_directory),
                    cancellation_check=context.raise_if_cancelled,
                )

                async def durable_writer(event_type: str, payload: dict[str, object]) -> bool:
                    emit = getattr(context, "emit_durable_event", None)
                    if not callable(emit):
                        # Compatibility for deterministic unit contexts. The
                        # real runner always supplies the fenced writer.
                        return True
                    return bool(await emit(event_type=event_type, payload=payload))

                event_sink = BoundedCheckpointEventSink(
                    conversation_id=claim.conversation_id,
                    run_id=claim.operation_id,
                    message_id=run_context.assistant_message_id,
                    durable_writer=durable_writer,
                )
                runtime = PresentationAgentRuntime(
                    sql_session=deferred,
                    service_factory=self._service_factory,
                )
                await context.progress(stage="applying_command", percent=20)
                # Autoflush would defeat the buffer: any read the tools issue
                # after their first mutation would flush it to the connection
                # and take the writer lock for the rest of the provider call.
                with session.sync_session.no_autoflush:
                    turn = await runtime.run(run_context, event_sink)
                # Sample before promotion adds artifact rows of its own.
                deck_changed = _turn_changed_the_deck(session)
                await context.raise_if_cancelled()
                # Announce the stage while the turn is still buffered.  Artifact
                # promotion flushes, which is where this transaction finally
                # takes the writer lock, and this progress write comes from an
                # independent session.  Everything from here to the commit is
                # local work, so that lock is held for milliseconds rather than
                # across the provider call.
                await context.progress(stage="publishing_revision", percent=90)
                promoted = await promote_workspace_chat_image_artifacts(
                    session,
                    staging,
                )
                _rewrite_pending_artifact_paths(session, promoted.replacements)

                # `advance_revision` below splits "record this attempt's result"
                # from "advance the publication token" inside the one fence, so
                # a turn that only answered reports it without invalidating
                # every other client's expected_revision.
                result_payload = chat_turn_result_payload(turn, promoted.replacements)
                assistant_message = await _append_canonical_assistant_message(
                    session,
                    context=run_context,
                    result=turn,
                )
                result_payload["assistant_message_id"] = str(assistant_message.id)
                revision = await publish_presentation_revision(
                    session,
                    presentation_id=claim.presentation_id,
                    expected_revision=claim.expected_revision,
                    operation_id=claim.operation_id,
                    attempt_token=claim.attempt_token,
                    result=result_payload,
                    advance_revision=deck_changed,
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
                        await compensate_workspace_chat_artifact_staging(session, staging)
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
                    raise NonRetryableWorkspaceOperationError("stale_presentation_revision") from error
                if isinstance(error, HTTPException):
                    code = _error_code(error.detail, "presentation_edit_failed")
                    error_type = (
                        RetryableWorkspaceOperationError
                        if error.status_code == 429 or error.status_code >= 500
                        else NonRetryableWorkspaceOperationError
                    )
                    # An unmapped detail collapses to the generic code, which on
                    # its own says nothing about what actually went wrong.
                    LOGGER.warning(
                        "Chat turn failed for operation %s (HTTP %s -> %s)",
                        claim.operation_id,
                        error.status_code,
                        code,
                        exc_info=True,
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
                #
                # The public code is deliberately generic, so log the cause here
                # or the turn dead-letters after its retries with nothing to
                # diagnose it by. The command text is not logged.
                LOGGER.warning(
                    "Chat turn failed for operation %s (%s: %s)",
                    claim.operation_id,
                    type(error).__name__,
                    error,
                    exc_info=True,
                )
                raise RetryableWorkspaceOperationError("presentation_edit_failed") from error

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
                raise NonRetryableWorkspaceOperationError("version_restore_target_invalid") from error

        if context.get("strategy") == "previous":
            versions = await list_versions(session, presentation_id)
            if versions:
                return versions[0].id
            raise NonRetryableWorkspaceOperationError("version_not_found")

        raise NonRetryableWorkspaceOperationError("version_restore_target_invalid")

    async def execute(self, context: WorkspaceOperationExecutionContext) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError("workspace_expected_revision_required")

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
                    raise NonRetryableWorkspaceOperationError("version_not_found")

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
                    raise NonRetryableWorkspaceOperationError("stale_presentation_revision") from error
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
                    raise NonRetryableWorkspaceOperationError("version_snapshot_invalid") from error
                raise RetryableWorkspaceOperationError("version_restore_failed") from error

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
