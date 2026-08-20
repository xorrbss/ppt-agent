"""Canonical Conversation service backed only by WorkspaceService storage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.conversation import (
    AgentRunActionReceipt,
    AgentRunActionRequest,
    AgentRunItem,
    ConversationAsset,
    ConversationMessageCreateRequest,
    ConversationMessageItem,
    ConversationMessageReceipt,
    ConversationResponse,
    ConversationTimelineResponse,
    PresentationCapability,
    PresentationRevision,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PresentationModel,
)
from models.sql.workspace import (
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceOperation,
)
from models.workspace import WorkspaceCommandRequest, WorkspaceOperationActionRequest
from services.workspace_service import (
    WorkspaceServiceError,
    _conversation_for_presentation,
    _get_or_create_conversation,
    _presentation_or_404,
    apply_workspace_action,
    create_workspace_command,
    workspace_timeline,
)
from services.workspace_api_contract import allowed_workspace_actions
from services.presentation_profiles import resolve_presentation_driver
from utils.owner_context import get_current_owner_id
from models.sql.workspace import get_workspace_owner_scope


def _owner_id_predicate(column):
    owner_id = get_current_owner_id()
    return column.is_(None) if owner_id is None else column == owner_id


def derive_presentation_capability(
    presentation: PresentationModel,
) -> PresentationCapability:
    """Derive runtime/tool capability exclusively from persisted presentation data."""

    profile, capabilities, _ = resolve_presentation_driver(presentation)
    if profile.driver_id == "authored":
        presentation_mode = "authored"
    else:
        presentation_mode = "template"
    return PresentationCapability(
        driver_id=profile.driver_id,
        profile=profile,
        capabilities=capabilities,
        generation_mode=profile.generation_mode,
        presentation_mode=presentation_mode,
        tool_profile=profile.driver_id,
        can_edit=presentation.lifecycle_status != PRESENTATION_LIFECYCLE_DELETED,
    )


def revision_projection(
    presentation_id: uuid.UUID,
    *,
    operation_id: uuid.UUID | None,
    source_revision: int | None,
    result_revision: int | None,
    fallback_revision: int = 0,
) -> PresentationRevision:
    source = fallback_revision if source_revision is None else source_revision
    published = result_revision is not None and result_revision > source
    return PresentationRevision(
        presentation_id=presentation_id,
        operation_id=operation_id,
        source_revision=source,
        result_revision=result_revision,
        published=published,
    )


def _conversation_response(
    conversation: WorkspaceConversation,
    presentation: PresentationModel,
) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        presentation_id=presentation.id,
        revision=presentation.revision,
        capability=derive_presentation_capability(presentation),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


async def _conversation_and_presentation(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> tuple[WorkspaceConversation, PresentationModel]:
    row = (
        await session.execute(
            select(WorkspaceConversation, PresentationModel)
            .join(
                PresentationModel,
                PresentationModel.id == WorkspaceConversation.presentation_id,
            )
            .where(
                WorkspaceConversation.id == conversation_id,
                WorkspaceConversation.owner_scope == get_workspace_owner_scope(),
                _owner_id_predicate(PresentationModel.owner_id),
            )
        )
    ).one_or_none()
    if row is None:
        raise WorkspaceServiceError(
            "workspace_conversation_not_found", status_code=404
        )
    conversation, presentation = row
    return conversation, presentation


async def create_conversation(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
) -> ConversationResponse:
    presentation = await _presentation_or_404(session, presentation_id)
    if presentation.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED:
        raise WorkspaceServiceError("presentation_deleted", status_code=409)
    conversation = await _conversation_for_presentation(session, presentation_id)
    if conversation is None:
        try:
            conversation = await _get_or_create_conversation(session, presentation_id)
            await session.commit()
        except IntegrityError:
            # A concurrent creator won the existing unique
            # (owner_scope, presentation_id) identity. Re-read that identity;
            # never create or write a parallel chat record.
            await session.rollback()
            conversation = await _conversation_for_presentation(
                session, presentation_id
            )
            if conversation is None:
                raise
    return _conversation_response(conversation, presentation)


async def get_conversation(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
) -> ConversationResponse:
    conversation, presentation = await _conversation_and_presentation(
        session, conversation_id
    )
    return _conversation_response(conversation, presentation)


def _canonical_agent_context(
    request: ConversationMessageCreateRequest,
    capability: PresentationCapability,
) -> dict[str, Any]:
    """Merge typed operation context with server-vetted editor capability.

    Operation handlers retain their kind-specific inputs (for example a
    version id or review scope), while runtime/editor fields are reserved and
    always rebuilt from validated request models plus the presentation row.
    A client therefore cannot spoof runtime/editor capability flags via the
    generic operation context.
    """

    context: dict[str, Any] = {
        key: value
        for key, value in request.operation_context.items()
        if key not in {"runtime", "viewport", "current_slide"}
    }
    context["runtime"] = {
        "generation_mode": capability.generation_mode,
        "presentation_mode": capability.presentation_mode,
        "tool_profile": capability.tool_profile,
        "can_edit": capability.can_edit,
    }
    if request.context.viewport is not None:
        context["viewport"] = request.context.viewport.model_dump(mode="json")
    if request.context.current_slide is not None:
        context["current_slide"] = request.context.current_slide.model_dump(
            mode="json", exclude_none=True
        )
    return context


async def send_message(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    request: ConversationMessageCreateRequest,
) -> ConversationMessageReceipt:
    conversation, presentation = await _conversation_and_presentation(
        session, conversation_id
    )
    capability = derive_presentation_capability(presentation)
    if not capability.can_edit:
        raise WorkspaceServiceError("presentation_deleted", status_code=409)
    operation, duplicate = await create_workspace_command(
        session,
        presentation_id=presentation.id,
        request=WorkspaceCommandRequest(
            command=request.content,
            command_type=request.command_type,
            idempotency_key=request.idempotency_key,
            expected_revision=request.expected_revision,
            asset_ids=request.asset_ids,
            context=_canonical_agent_context(request, capability),
        ),
    )
    # The store has exactly one conversation per presentation. Guard this even
    # though create_workspace_command resolves by presentation identity.
    if operation.conversation_id != conversation.id:
        raise WorkspaceServiceError("workspace_conversation_not_found", status_code=404)
    message_id = uuid.UUID(str(operation.request_payload["user_message_id"]))
    return ConversationMessageReceipt(
        conversation_id=conversation.id,
        message_id=message_id,
        run_id=operation.id,
        status=operation.state,
        duplicate=duplicate,
        revision=revision_projection(
            presentation.id,
            operation_id=operation.id,
            source_revision=operation.expected_revision,
            result_revision=operation.result_revision,
            fallback_revision=presentation.revision,
        ),
    )


def _safe_asset_projection(
    value: Mapping[str, Any], conversation_id: uuid.UUID
) -> dict[str, Any]:
    asset_id = uuid.UUID(str(value["id"]))
    projected = {
        "id": asset_id,
        "name": str(value.get("name") or "attachment"),
        "status": str(value.get("status") or "deleted"),
        "current_state": value.get("current_state"),
        "media_type": value.get("mime_type"),
        "size_bytes": int(value.get("size_bytes") or 0),
        "sha256": value.get("sha256"),
        "error": value.get("error"),
        "safe_failure": value.get("safe_failure"),
        "unavailable_reason": value.get("unavailable_reason"),
        "download_url": None,
    }
    if value.get("download_action") == "download":
        projected["download_url"] = (
            f"/api/v1/ppt/conversations/{conversation_id}/assets/"
            f"{asset_id}/download"
        )
    return projected


async def conversation_timeline(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    before_position: Optional[int] = None,
    limit: int = 50,
) -> ConversationTimelineResponse:
    conversation, presentation = await _conversation_and_presentation(
        session, conversation_id
    )
    raw = await workspace_timeline(
        session,
        presentation_id=presentation.id,
        before_position=before_position,
        limit=limit,
    )
    items: list[ConversationMessageItem | AgentRunItem] = []
    for item in raw["items"]:
        if item["anchor_type"] == "message":
            items.append(
                ConversationMessageItem(
                    id=item["id"],
                    position=item["position"],
                    role=item["role"],
                    content=item["content"],
                    created_at=item.get("created_at"),
                    assets=[
                        _safe_asset_projection(asset, conversation.id)
                        for asset in item.get("attachments", [])
                    ],
                )
            )
            continue
        items.append(
            AgentRunItem(
                id=item["operation_id"],
                position=item["position"],
                run_type=item["operation_type"],
                status=item["status"],
                progress=item.get("progress"),
                error=item.get("error"),
                safe_error_message=item.get("error_message"),
                retryable=item.get("retryable"),
                result=item.get("result"),
                version=item["version"],
                revision=revision_projection(
                    presentation.id,
                    operation_id=uuid.UUID(str(item["operation_id"])),
                    source_revision=item.get("expected_revision"),
                    result_revision=item.get("result_revision"),
                    fallback_revision=presentation.revision,
                ),
                approval_expires_at=item.get("approval_expires_at"),
                retry_of_run_id=item.get("retry_of_operation_id"),
                actions=[
                    (
                        {**action, "enabled": True, "reason": None}
                        if action.get("id") == "undo"
                        and item.get("status") == "succeeded"
                        and item.get("operation_type")
                        not in {"export.pptx", "export.pdf"}
                        and item.get("result_revision") is not None
                        and item.get("expected_revision") is not None
                        and item["result_revision"] > item["expected_revision"]
                        else action
                    )
                    for action in allowed_workspace_actions(item)
                ],
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
            )
        )
    return ConversationTimelineResponse(
        conversation_id=conversation.id,
        presentation_id=presentation.id,
        revision=raw["revision"],
        capability=derive_presentation_capability(presentation),
        items=items,
        next_before_position=raw.get("next_before_position"),
        event_high_watermark=raw.get("event_high_watermark"),
        has_more=raw.get("has_more", False),
    )


async def act_on_run(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    request: AgentRunActionRequest,
) -> AgentRunActionReceipt:
    conversation, presentation = await _conversation_and_presentation(
        session, conversation_id
    )
    source_run = (
        await session.execute(
            select(WorkspaceOperation).where(
                WorkspaceOperation.id == run_id,
                WorkspaceOperation.conversation_id == conversation.id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
            )
        )
    ).scalar_one_or_none()
    if source_run is None:
        raise WorkspaceServiceError("workspace_operation_not_found", status_code=404)

    if request.action == "undo":
        if (
            source_run.state != "succeeded"
            or source_run.expected_revision is None
            or source_run.result_revision is None
            or source_run.result_revision <= source_run.expected_revision
        ):
            raise WorkspaceServiceError("operation_state_conflict", status_code=409)
        undo_run, duplicate = await create_workspace_command(
            session,
            presentation_id=presentation.id,
            request=WorkspaceCommandRequest(
                command="Undo the previous presentation change",
                command_type="version.restore",
                idempotency_key=request.idempotency_key,
                expected_revision=request.expected_revision,
                context={
                    "strategy": "previous",
                    "source": "undo",
                    "source_run_id": str(source_run.id),
                },
            ),
        )
        return AgentRunActionReceipt(
            conversation_id=conversation.id,
            receipt_id=undo_run.id,
            run_id=undo_run.id,
            status=undo_run.state,
            action="undo",
            duplicate=duplicate,
            revision=revision_projection(
                presentation.id,
                operation_id=undo_run.id,
                source_revision=undo_run.expected_revision,
                result_revision=undo_run.result_revision,
                fallback_revision=presentation.revision,
            ),
        )

    receipt, result_run, duplicate = await apply_workspace_action(
        session,
        operation_id=source_run.id,
        request=WorkspaceOperationActionRequest(
            action=request.action,
            idempotency_key=request.idempotency_key,
            expected_operation_version=request.expected_run_version,
            expected_revision=request.expected_revision,
        ),
    )
    return AgentRunActionReceipt(
        conversation_id=conversation.id,
        receipt_id=receipt.id,
        run_id=result_run.id,
        status=result_run.state,
        action=request.action,
        duplicate=duplicate,
        revision=revision_projection(
            presentation.id,
            operation_id=result_run.id,
            source_revision=result_run.expected_revision,
            result_revision=result_run.result_revision,
            fallback_revision=presentation.revision,
        ),
    )


async def list_conversation_assets(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
) -> list[ConversationAsset]:
    await _conversation_and_presentation(session, conversation_id)
    assets = list(
        (
            await session.scalars(
                select(WorkspaceAsset)
                .where(
                    WorkspaceAsset.conversation_id == conversation_id,
                    _owner_id_predicate(WorkspaceAsset.owner_id),
                )
                .order_by(WorkspaceAsset.created_at.asc())
            )
        ).all()
    )
    return [
        ConversationAsset(
            id=asset.id,
            file_name=asset.original_name,
            media_type=asset.media_type,
            size_bytes=asset.size_bytes,
            sha256=asset.sha256,
            status=asset.state,
            version=asset.version,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
        )
        for asset in assets
    ]
