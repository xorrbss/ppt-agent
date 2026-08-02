"""Chat-first workspace HTTP contract.

Timeline reads stay available when command rollout is disabled so an operator can
roll back execution without hiding already-durable history.  Unsupported database
dialects fail closed for every workspace route.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from models.workspace import (
    WorkspaceBootstrapRequest,
    WorkspaceBootstrapResponse,
    WorkspaceAssetResponse,
    WorkspaceCapabilitiesResponse,
    WorkspaceCommandRequest,
    WorkspaceOperationActionRequest,
    WorkspaceTimelineResponse,
)
from services.workspace_asset_service import (
    create_workspace_asset,
    get_workspace_asset,
    workspace_asset_response,
)
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
    resolve_workspace_export_artifact,
)
from services.database import async_session_maker, get_async_session, sql_engine
from services.workspace_runtime_profile import (
    ChatFirstWorkspaceRollout,
    CHAT_FIRST_FEATURE_DISABLED,
    resolve_chat_first_workspace_rollout,
    resolve_workspace_runtime_profile,
)
from services.workspace_service import (
    WorkspaceServiceError,
    apply_workspace_action,
    bootstrap_workspace,
    create_workspace_command,
    workspace_commands_enabled,
    workspace_events,
    workspace_timeline,
)


WORKSPACE_ROUTER = APIRouter(prefix="/workspace", tags=["Workspace"])


def _runtime_profile():
    return resolve_workspace_runtime_profile(sql_engine.url)


def _rollout_status() -> ChatFirstWorkspaceRollout:
    return resolve_chat_first_workspace_rollout(
        workspace_commands_enabled(),
        _runtime_profile(),
    )


def _require_supported_runtime() -> None:
    rollout = _rollout_status()
    if not rollout.workspace_reads_supported:
        error_code = rollout.runtime_error or "chat_first_runtime_unsupported"
        raise HTTPException(
            status_code=(
                501
                if error_code == "chat_first_runtime_unsupported"
                else 503
            ),
            detail=error_code,
        )


def _require_workspace_writes() -> None:
    rollout = _rollout_status()
    if rollout.enabled:
        return
    error_code = rollout.reason or CHAT_FIRST_FEATURE_DISABLED
    raise HTTPException(
        status_code=(501 if error_code == "chat_first_runtime_unsupported" else 503),
        detail=error_code,
    )


def _raise_service_error(error: WorkspaceServiceError) -> None:
    # A stable string detail is intentionally used so the shared API envelope
    # promotes it to error.code without leaking private payloads.
    raise HTTPException(status_code=error.status_code, detail=error.code) from error


def _raise_artifact_error(error: WorkspaceArtifactPublicationError) -> None:
    status_code = {
        "workspace_export_not_found": 404,
        "workspace_export_expired": 410,
        "workspace_export_not_ready": 409,
        "workspace_export_missing": 410,
        "workspace_artifact_path_invalid": 404,
    }.get(error.code, 409)
    raise HTTPException(status_code=status_code, detail=error.code) from error


@WORKSPACE_ROUTER.get(
    "/capabilities",
    response_model=WorkspaceCapabilitiesResponse,
)
async def workspace_capabilities() -> WorkspaceCapabilitiesResponse:
    rollout = _rollout_status()
    supported = rollout.workspace_reads_supported
    enabled = rollout.enabled
    return WorkspaceCapabilitiesResponse(
        supported=supported,
        enabled=enabled,
        timeline=supported,
        commands=enabled,
        events=supported,
        runtime_profile=rollout.profile.kind.value,
        reason=rollout.reason,
    )


@WORKSPACE_ROUTER.post("/assets", response_model=WorkspaceAssetResponse)
async def post_workspace_asset(
    file: UploadFile = File(...),
    conversation_id: Optional[uuid.UUID] = Form(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceAssetResponse:
    """Persist an immutable, owner-scoped upload before workspace bootstrap."""

    _require_workspace_writes()
    try:
        asset = await create_workspace_asset(
            session,
            upload=file,
            conversation_id=conversation_id,
            # Admission ends after the immutable source and its ``uploaded``
            # row commit.  Malware scanning and downstream processing run from
            # durable asset state in maintenance, never in this request.
            process_immediately=False,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    return workspace_asset_response(asset)


@WORKSPACE_ROUTER.get(
    "/assets/{asset_id}",
    response_model=WorkspaceAssetResponse,
)
async def get_workspace_asset_metadata(
    asset_id: uuid.UUID,
    conversation_id: Optional[uuid.UUID] = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceAssetResponse:
    _require_supported_runtime()
    try:
        asset = await get_workspace_asset(
            session,
            asset_id=asset_id,
            conversation_id=conversation_id,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    return workspace_asset_response(asset)


@WORKSPACE_ROUTER.get(
    "/artifacts/{artifact_id}/download",
    response_class=FileResponse,
)
async def download_workspace_export_artifact(
    artifact_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> FileResponse:
    """Reauthorize and stream one live export without exposing storage paths."""

    _require_supported_runtime()
    try:
        artifact = await resolve_workspace_export_artifact(
            session,
            artifact_id=artifact_id,
        )
    except WorkspaceArtifactPublicationError as error:
        _raise_artifact_error(error)
    export_format = artifact.kind.removeprefix("export.")
    media_types = {
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pdf": "application/pdf",
    }
    media_type = media_types.get(export_format)
    if media_type is None:
        raise HTTPException(status_code=404, detail="workspace_export_not_found")
    return FileResponse(
        path=artifact.absolute_path,
        filename=(
            f"presentation-{artifact.presentation_id}-r{artifact.source_revision}."
            f"{export_format}"
        ),
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _bootstrap_response(
    *,
    presentation_id: uuid.UUID,
    request: WorkspaceBootstrapRequest,
    session: AsyncSession,
) -> WorkspaceBootstrapResponse:
    _require_workspace_writes()
    try:
        conversation, message, operation, duplicate = await bootstrap_workspace(
            session,
            presentation_id=presentation_id,
            request=request,
        )
        timeline = await workspace_timeline(
            session,
            presentation_id=presentation_id,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    return WorkspaceBootstrapResponse(
        conversation_id=conversation.id,
        first_user_message_id=message.id,
        operation_id=operation.id,
        operation_state=operation.state,
        duplicate=duplicate,
        timeline=WorkspaceTimelineResponse.model_validate(timeline),
    )


@WORKSPACE_ROUTER.post(
    "/presentations/{presentation_id}/bootstrap",
    response_model=WorkspaceBootstrapResponse,
)
async def bootstrap_presentation_workspace(
    presentation_id: uuid.UUID,
    request: WorkspaceBootstrapRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceBootstrapResponse:
    return await _bootstrap_response(
        presentation_id=presentation_id,
        request=request,
        session=session,
    )


@WORKSPACE_ROUTER.post("/bootstrap", response_model=WorkspaceBootstrapResponse)
async def bootstrap_workspace_compat(
    request: WorkspaceBootstrapRequest,
    presentation_id: uuid.UUID = Query(...),
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceBootstrapResponse:
    """Compatibility form matching the original design document route."""

    return await _bootstrap_response(
        presentation_id=presentation_id,
        request=request,
        session=session,
    )


@WORKSPACE_ROUTER.get(
    "/presentations/{presentation_id}/timeline",
    response_model=WorkspaceTimelineResponse,
)
async def get_presentation_workspace_timeline(
    presentation_id: uuid.UUID,
    before_position: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceTimelineResponse:
    _require_supported_runtime()
    try:
        result = await workspace_timeline(
            session,
            presentation_id=presentation_id,
            before_position=before_position,
            limit=limit,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    return WorkspaceTimelineResponse.model_validate(result)


@WORKSPACE_ROUTER.get("/timeline", response_model=WorkspaceTimelineResponse)
async def get_workspace_timeline_compat(
    presentation_id: uuid.UUID = Query(...),
    before_position: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceTimelineResponse:
    return await get_presentation_workspace_timeline(
        presentation_id=presentation_id,
        before_position=before_position,
        limit=limit,
        session=session,
    )


async def _event_stream(
    *,
    request: Request,
    presentation_id: uuid.UUID,
    initial_cursor: Optional[str],
) -> AsyncIterator[str]:
    cursor = initial_cursor
    heartbeat_ticks = 0
    while not await request.is_disconnected():
        async with async_session_maker() as session:
            try:
                page = await workspace_events(
                    session,
                    presentation_id=presentation_id,
                    after=cursor,
                    limit=500,
                )
            except WorkspaceServiceError as error:
                data = json.dumps(
                    {"code": error.code, "retryable": error.retryable},
                    separators=(",", ":"),
                )
                yield f"event: stream_error\ndata: {data}\n\n"
                return
        events = page["events"]
        for event in events:
            event_cursor = event.get("cursor")
            if event_cursor is not None:
                cursor = str(event_cursor)
            data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            yield f"id: {cursor}\nevent: workspace_event\ndata: {data}\n\n"
        heartbeat_ticks += 1
        if not events and heartbeat_ticks >= 15:
            heartbeat_ticks = 0
            yield f"event: heartbeat\ndata: {{\"cursor\":{json.dumps(cursor)} }}\n\n"
        await asyncio.sleep(1)


@WORKSPACE_ROUTER.get("/presentations/{presentation_id}/events")
async def get_presentation_workspace_events(
    request: Request,
    presentation_id: uuid.UUID,
    after: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    stream: bool = Query(default=False),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    session: AsyncSession = Depends(get_async_session),
):
    _require_supported_runtime()
    effective_cursor = after or last_event_id
    if stream:
        # Validate both presentation access and cursor before committing headers.
        try:
            await workspace_events(
                session,
                presentation_id=presentation_id,
                after=effective_cursor,
                limit=1,
            )
        except WorkspaceServiceError as error:
            _raise_service_error(error)
        return StreamingResponse(
            _event_stream(
                request=request,
                presentation_id=presentation_id,
                initial_cursor=effective_cursor,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    try:
        return await workspace_events(
            session,
            presentation_id=presentation_id,
            after=effective_cursor,
            limit=limit,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)


@WORKSPACE_ROUTER.post(
    "/presentations/{presentation_id}/commands",
    response_model=WorkspaceTimelineResponse,
)
async def post_presentation_workspace_command(
    presentation_id: uuid.UUID,
    request: WorkspaceCommandRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceTimelineResponse:
    _require_workspace_writes()
    try:
        await create_workspace_command(
            session,
            presentation_id=presentation_id,
            request=request,
        )
        timeline = await workspace_timeline(
            session,
            presentation_id=presentation_id,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    return WorkspaceTimelineResponse.model_validate(timeline)


@WORKSPACE_ROUTER.post(
    "/operations/{operation_id}/actions",
    response_model=WorkspaceTimelineResponse,
)
async def post_workspace_operation_action(
    operation_id: uuid.UUID,
    request: WorkspaceOperationActionRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceTimelineResponse:
    _require_workspace_writes()
    try:
        _, operation, _ = await apply_workspace_action(
            session,
            operation_id=operation_id,
            request=request,
        )
        timeline = await workspace_timeline(
            session,
            presentation_id=operation.presentation_id,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    return WorkspaceTimelineResponse.model_validate(timeline)
