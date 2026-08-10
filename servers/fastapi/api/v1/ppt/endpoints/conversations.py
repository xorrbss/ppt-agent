"""Canonical Unified Conversation Runtime HTTP and SSE transport."""

# FastAPI dependency declarations intentionally call Depends/File in defaults.
# ruff: noqa: B008

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional
import uuid

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from models.conversation import (
    AgentRunActionReceipt,
    AgentRunActionRequest,
    ConversationAsset,
    ConversationCreateRequest,
    ConversationMessageCreateRequest,
    ConversationMessageReceipt,
    ConversationResponse,
    ConversationTimelineResponse,
)
from services.conversations.events import (
    LiveConversationEventSource,
    conversation_events,
)
from services.agent_runtime.events import TRANSIENT_RUN_EVENTS
from services.conversations.service import (
    _conversation_and_presentation,
    act_on_run,
    conversation_timeline,
    create_conversation,
    get_conversation,
    list_conversation_assets,
    send_message,
)
from services.database import async_session_maker, get_async_session, sql_engine
from services.workspace_asset_service import (
    create_workspace_asset,
    get_workspace_asset,
    resolve_workspace_asset_download,
)
from services.workspace_error_registry import (
    WorkspaceAPIException,
    resolve_workspace_error,
)
from services.workspace_operation_runner import wake_workspace_operation_runner
from services.workspace_runtime_profile import (
    WORKSPACE_RUNTIME_UNSUPPORTED,
    resolve_workspace_runtime_profile,
)
from services.workspace_service import WorkspaceServiceError


CONVERSATION_ROUTER = APIRouter(prefix="/conversations", tags=["Conversations"])

# Optional process-local delta source. Durable SQL checkpoints and their
# Workspace cursor remain authoritative; a runtime adapter may set this during
# composition without changing the canonical service or persistence layer.
LIVE_CONVERSATION_EVENT_SOURCE: LiveConversationEventSource | None = (
    TRANSIENT_RUN_EVENTS
)


def _require_supported_runtime() -> None:
    if not resolve_workspace_runtime_profile(sql_engine.url).supported:
        raise WorkspaceAPIException(WORKSPACE_RUNTIME_UNSUPPORTED, status_code=501)


def _raise_service_error(error: WorkspaceServiceError) -> None:
    raise WorkspaceAPIException(
        error.code,
        status_code=error.status_code,
        retryable=error.retryable,
    ) from error


def _asset_projection(asset) -> ConversationAsset:
    return ConversationAsset(
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


@CONVERSATION_ROUTER.post("", response_model=ConversationResponse)
async def post_conversation(
    request: ConversationCreateRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ConversationResponse:
    _require_supported_runtime()
    try:
        return await create_conversation(
            session, presentation_id=request.presentation_id
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)


@CONVERSATION_ROUTER.get(
    "/{conversation_id}", response_model=ConversationResponse
)
async def get_conversation_by_id(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> ConversationResponse:
    _require_supported_runtime()
    try:
        return await get_conversation(session, conversation_id=conversation_id)
    except WorkspaceServiceError as error:
        _raise_service_error(error)


@CONVERSATION_ROUTER.get(
    "/{conversation_id}/timeline", response_model=ConversationTimelineResponse
)
async def get_conversation_timeline(
    conversation_id: uuid.UUID,
    before_position: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_async_session),
) -> ConversationTimelineResponse:
    _require_supported_runtime()
    try:
        return await conversation_timeline(
            session,
            conversation_id=conversation_id,
            before_position=before_position,
            limit=limit,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)


@CONVERSATION_ROUTER.post(
    "/{conversation_id}/messages", response_model=ConversationMessageReceipt
)
async def post_conversation_message(
    conversation_id: uuid.UUID,
    request: ConversationMessageCreateRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ConversationMessageReceipt:
    _require_supported_runtime()
    try:
        receipt = await send_message(
            session,
            conversation_id=conversation_id,
            request=request,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    wake_workspace_operation_runner()
    return receipt


@CONVERSATION_ROUTER.post(
    "/{conversation_id}/runs/{run_id}/actions",
    response_model=AgentRunActionReceipt,
)
async def post_agent_run_action(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    request: AgentRunActionRequest,
    session: AsyncSession = Depends(get_async_session),
) -> AgentRunActionReceipt:
    _require_supported_runtime()
    try:
        receipt = await act_on_run(
            session,
            conversation_id=conversation_id,
            run_id=run_id,
            request=request,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    if request.action in {"retry", "approve", "undo"}:
        wake_workspace_operation_runner()
    return receipt


async def _conversation_event_stream(
    *,
    request: Request,
    conversation_id: uuid.UUID,
    initial_cursor: str | None,
) -> AsyncIterator[str]:
    cursor = initial_cursor
    heartbeat_ticks = 0
    while not await request.is_disconnected():
        async with async_session_maker() as session:
            try:
                page = await conversation_events(
                    session,
                    conversation_id=conversation_id,
                    after=cursor,
                    limit=500,
                )
            except WorkspaceServiceError as error:
                definition = resolve_workspace_error(
                    error.code,
                    status_code=error.status_code,
                    retryable=error.retryable,
                )
                payload = json.dumps(
                    {
                        "schema_version": 1,
                        "error": {
                            "code": definition.code,
                            "safe_message": definition.safe_message,
                            "retryable": definition.retryable,
                        },
                    },
                    separators=(",", ":"),
                )
                yield f"event: stream_error\ndata: {payload}\n\n"
                return
        for event in page.events:
            assert event.cursor is not None
            cursor = event.cursor
            payload = json.dumps(
                event.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"id: {cursor}\nevent: conversation_event\ndata: {payload}\n\n"

        # A live adapter can merge transient assistant.delta envelopes here.
        # They carry no cursor and never advance durable reconnect state. Drain
        # a bounded burst so provider token chunks are not reduced to one event
        # per durable polling interval.
        live_source = LIVE_CONVERSATION_EVENT_SOURCE
        live_delivered = False
        if live_source is not None:
            for burst_index in range(128):
                try:
                    live = await asyncio.wait_for(
                        live_source.next_event(
                            conversation_id=conversation_id,
                            after_checkpoint=cursor,
                        ),
                        timeout=0.05 if burst_index == 0 else 0.001,
                    )
                except TimeoutError:
                    break
                if live is None:
                    break
                live_delivered = True
                payload = json.dumps(
                    live.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield f"event: conversation_event\ndata: {payload}\n\n"

        if page.events or live_delivered:
            heartbeat_ticks = 0
        else:
            heartbeat_ticks += 1
            if heartbeat_ticks >= 30:
                heartbeat_ticks = 0
                payload = json.dumps(
                    {"cursor": cursor}, separators=(",", ":")
                )
                yield f"event: heartbeat\ndata: {payload}\n\n"
        await asyncio.sleep(0.05 if live_delivered else 0.5)


@CONVERSATION_ROUTER.get("/{conversation_id}/events")
async def get_conversation_events(
    request: Request,
    conversation_id: uuid.UUID,
    after: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    stream: bool = Query(default=False),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    session: AsyncSession = Depends(get_async_session),
):
    _require_supported_runtime()
    if (
        after is not None
        and last_event_id is not None
        and after.strip() != last_event_id.strip()
    ):
        _raise_service_error(
            WorkspaceServiceError("invalid_event_cursor", status_code=400)
        )
    cursor = after or last_event_id
    if stream:
        try:
            await conversation_events(
                session,
                conversation_id=conversation_id,
                after=cursor,
                limit=1,
            )
        except WorkspaceServiceError as error:
            _raise_service_error(error)
        # Avoid pinning a SQLite WAL snapshot for the lifetime of the stream.
        await session.rollback()
        return StreamingResponse(
            _conversation_event_stream(
                request=request,
                conversation_id=conversation_id,
                initial_cursor=cursor,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    try:
        return await conversation_events(
            session,
            conversation_id=conversation_id,
            after=cursor,
            limit=limit,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)


@CONVERSATION_ROUTER.post(
    "/{conversation_id}/assets", response_model=ConversationAsset
)
async def post_conversation_asset(
    conversation_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_async_session),
) -> ConversationAsset:
    _require_supported_runtime()
    try:
        await _conversation_and_presentation(session, conversation_id)
        asset = await create_workspace_asset(
            session,
            upload=file,
            conversation_id=conversation_id,
            # The canonical composer may attach only server-vetted assets to a
            # run. Complete scanning/processing before returning so the UI
            # never mistakes an ``uploaded`` row for agent-ready context.
            process_immediately=True,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    return _asset_projection(asset)


@CONVERSATION_ROUTER.get(
    "/{conversation_id}/assets", response_model=list[ConversationAsset]
)
async def get_conversation_assets(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> list[ConversationAsset]:
    _require_supported_runtime()
    try:
        return await list_conversation_assets(
            session, conversation_id=conversation_id
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)


@CONVERSATION_ROUTER.get(
    "/{conversation_id}/assets/{asset_id}", response_model=ConversationAsset
)
async def get_conversation_asset(
    conversation_id: uuid.UUID,
    asset_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> ConversationAsset:
    _require_supported_runtime()
    try:
        await _conversation_and_presentation(session, conversation_id)
        asset = await get_workspace_asset(
            session,
            asset_id=asset_id,
            conversation_id=conversation_id,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    return _asset_projection(asset)


@CONVERSATION_ROUTER.get("/{conversation_id}/assets/{asset_id}/download")
async def download_conversation_asset(
    conversation_id: uuid.UUID,
    asset_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> FileResponse:
    _require_supported_runtime()
    try:
        await get_workspace_asset(
            session,
            asset_id=asset_id,
            conversation_id=conversation_id,
        )
        asset, path = await resolve_workspace_asset_download(
            session, asset_id=asset_id
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    return FileResponse(
        path,
        filename=asset.original_name,
        media_type=asset.media_type or "application/octet-stream",
        headers={"Cache-Control": "private, no-store"},
    )
