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
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.workspace import WorkspaceOperation
from models.workspace import (
    WorkspaceBootstrapRequest,
    WorkspaceBootstrapResponse,
    WorkspaceAssetResponse,
    WorkspaceActionResponse,
    WorkspaceCapabilitiesResponse,
    WorkspaceCommandRequest,
    WorkspaceErrorEnvelopeResponse,
    WorkspaceOperationActionRequest,
    WorkspaceSLOResponse,
    WorkspaceTimelineResponse,
)
from services.workspace_api_contract import (
    project_workspace_event_page,
    project_workspace_timeline,
    resolve_workspace_operation_presentation_id,
    workspace_receipt_response,
)
from services.workspace_asset_service import (
    create_workspace_asset,
    get_workspace_asset,
    resolve_workspace_asset_download,
    workspace_asset_response,
)
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
    resolve_workspace_export_artifact,
)
from services.database import async_session_maker, get_async_session, sql_engine
from services.workspace_error_registry import (
    WORKSPACE_ERROR_REGISTRY,
    WorkspaceAPIException,
    resolve_workspace_error,
)
from services.workspace_metrics import WORKSPACE_METRICS
from services.workspace_rollout_policy import (
    WorkspaceWriteRolloutDecision,
    resolve_workspace_write_rollout,
)
from services.workspace_shadow_parity import (
    observe_workspace_timeline_shadow_parity,
)
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


def _workspace_error_responses() -> dict[int, dict[str, object]]:
    """Publish every stable workspace error code in the generated OpenAPI."""

    grouped: dict[int, dict[str, object]] = {}
    for definition in WORKSPACE_ERROR_REGISTRY.values():
        response = grouped.setdefault(
            definition.status_code,
            {
                "model": WorkspaceErrorEnvelopeResponse,
                "description": "Typed workspace error",
                "content": {"application/json": {"examples": {}}},
            },
        )
        content = response["content"]
        assert isinstance(content, dict)
        media_type = content["application/json"]
        assert isinstance(media_type, dict)
        examples = media_type["examples"]
        assert isinstance(examples, dict)
        examples[definition.code] = {
            "summary": definition.safe_message,
            "value": {
                "detail": definition.code,
                "error": {
                    "status_code": definition.status_code,
                    "detail": definition.code,
                    "code": definition.code,
                    "message": definition.safe_message,
                    "request_id": "request-id",
                    "safe_message": definition.safe_message,
                    "retryable": definition.retryable,
                },
                "request_id": "request-id",
            },
        }
    return grouped


WORKSPACE_ROUTER = APIRouter(
    prefix="/workspace",
    tags=["Workspace"],
    responses=_workspace_error_responses(),
)


def _runtime_profile():
    return resolve_workspace_runtime_profile(sql_engine.url)


def _rollout_status() -> ChatFirstWorkspaceRollout:
    return resolve_chat_first_workspace_rollout(
        workspace_commands_enabled(),
        _runtime_profile(),
    )


def _rollout_decision(
    presentation_id: uuid.UUID | None = None,
) -> WorkspaceWriteRolloutDecision:
    decision = resolve_workspace_write_rollout(
        _rollout_status(),
        presentation_id,
    )
    try:
        WORKSPACE_METRICS.record_rollout(
            enabled=decision.enabled,
            reason=decision.reason,
        )
    except Exception:
        pass
    return decision


def _require_supported_runtime() -> None:
    rollout = _rollout_status()
    if not rollout.workspace_reads_supported:
        error_code = rollout.runtime_error or "chat_first_runtime_unsupported"
        raise WorkspaceAPIException(
            error_code,
            status_code=(501 if error_code == "chat_first_runtime_unsupported" else 503),
        )


def _require_workspace_writes(
    presentation_id: uuid.UUID | None = None,
) -> WorkspaceWriteRolloutDecision:
    decision = _rollout_decision(presentation_id)
    if decision.enabled:
        return decision
    error_code = decision.reason or CHAT_FIRST_FEATURE_DISABLED
    raise WorkspaceAPIException(
        error_code,
        status_code=(501 if error_code == "chat_first_runtime_unsupported" else 503),
    )


_REVISION_CONFLICT_CODES = frozenset(
    {"revision_conflict", "stale_presentation_revision"}
)


def _record_operation_admission(
    operation_type: str,
    *,
    duplicate: bool,
) -> None:
    """Count only the first durable admission for an idempotent request."""

    if duplicate:
        return
    try:
        WORKSPACE_METRICS.record_operation_admission(operation_type)
    except Exception:
        # Metrics must never turn an already-durable mutation into a 5xx.
        pass


def _record_operation_action_admission(
    requested_operation_id: uuid.UUID,
    result_operation: WorkspaceOperation,
    *,
    duplicate: bool,
) -> None:
    """Count an action only when it durably created a replacement operation.

    Approve, cancel, and in-place retry mutate an existing operation and must
    not dilute operation-level SLO denominators.  A terminal retry returns the
    newly persisted replacement operation, so it is a genuine admission.
    """

    if result_operation.id == requested_operation_id:
        return
    _record_operation_admission(result_operation.kind, duplicate=duplicate)


def _record_revision_conflict(error_code: str) -> None:
    if error_code not in _REVISION_CONFLICT_CODES:
        return
    try:
        WORKSPACE_METRICS.record_revision_conflict()
    except Exception:
        pass


def _raise_service_error(error: WorkspaceServiceError) -> None:
    _record_revision_conflict(error.code)
    raise WorkspaceAPIException(
        error.code,
        status_code=error.status_code,
        retryable=error.retryable,
    ) from error


def _raise_artifact_error(error: WorkspaceArtifactPublicationError) -> None:
    _record_revision_conflict(error.code)
    status_code = {
        "workspace_export_not_found": 404,
        "workspace_export_expired": 410,
        "workspace_export_not_ready": 409,
        "workspace_export_missing": 410,
        "workspace_artifact_path_invalid": 404,
    }.get(error.code, 409)
    raise WorkspaceAPIException(error.code, status_code=status_code) from error


@WORKSPACE_ROUTER.get(
    "/capabilities",
    response_model=WorkspaceCapabilitiesResponse,
)
async def workspace_capabilities(
    presentation_id: Optional[uuid.UUID] = None,
) -> WorkspaceCapabilitiesResponse:
    rollout = _rollout_status()
    decision = _rollout_decision(presentation_id)
    supported = rollout.workspace_reads_supported
    enabled = decision.enabled
    return WorkspaceCapabilitiesResponse(
        supported=supported,
        enabled=enabled,
        timeline=supported,
        commands=enabled,
        events=supported,
        reads_supported=supported,
        writes_supported=enabled,
        commands_enabled=enabled,
        runtime_profile=rollout.profile.kind.value,
        reason=decision.reason,
        cohort_eligible=decision.cohort_eligible,
        cohort_bucket=decision.cohort_bucket,
        kill_switch_active=decision.kill_switch_active,
        shadow_mode=decision.shadow_mode,
    )


@WORKSPACE_ROUTER.get(
    "/observability/metrics",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def workspace_observability_metrics() -> PlainTextResponse:
    _require_supported_runtime()
    return PlainTextResponse(
        WORKSPACE_METRICS.prometheus_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@WORKSPACE_ROUTER.get(
    "/observability/slo",
    response_model=WorkspaceSLOResponse,
)
async def workspace_observability_slo() -> WorkspaceSLOResponse:
    _require_supported_runtime()
    status = WORKSPACE_METRICS.evaluate_slo()
    return WorkspaceSLOResponse(
        healthy=status.healthy,
        silent_loss_total=status.silent_loss_total,
        shadow_parity_ratio=status.shadow_parity_ratio,
        error_ratio=status.error_ratio,
        dead_letter_total=status.dead_letter_total,
        dead_letter_ratio=status.dead_letter_ratio,
        lease_lost_total=status.lease_lost_total,
        lease_lost_ratio=status.lease_lost_ratio,
        revision_conflict_total=status.revision_conflict_total,
        revision_conflict_ratio=status.revision_conflict_ratio,
        cleanup_dead_letter_total=status.cleanup_dead_letter_total,
        cleanup_dead_letter_ratio=status.cleanup_dead_letter_ratio,
        outbox_dead_letter_total=status.outbox_dead_letter_total,
        outbox_dead_letter_ratio=status.outbox_dead_letter_ratio,
        outbox_lease_lost_total=status.outbox_lease_lost_total,
        outbox_lease_lost_ratio=status.outbox_lease_lost_ratio,
        event_lag_max_ms=status.event_lag_max_ms,
        violations=list(status.violations),
    )


@WORKSPACE_ROUTER.post("/assets", response_model=WorkspaceAssetResponse)
async def post_workspace_asset(
    file: UploadFile = File(...),
    conversation_id: Optional[uuid.UUID] = Form(default=None),
    presentation_id: uuid.UUID = Form(...),
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceAssetResponse:
    """Persist an immutable, owner-scoped upload before workspace bootstrap."""

    _require_workspace_writes(presentation_id)
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
    "/assets/{asset_id}/download",
    response_class=FileResponse,
)
async def download_workspace_asset(
    asset_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> FileResponse:
    """Download one live owner-scoped attachment as a private response."""

    _require_supported_runtime()
    try:
        asset, path = await resolve_workspace_asset_download(
            session,
            asset_id=asset_id,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    return FileResponse(
        path=path,
        filename=asset.original_name,
        # Force attachment handling.  Preview is deliberately unsupported for
        # uploads because several accepted document formats may contain active
        # content and need a separate sanitized rendering pipeline.
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


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
        raise WorkspaceAPIException("workspace_export_not_found", status_code=404)
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
    _require_workspace_writes(presentation_id)
    try:
        conversation, message, operation, duplicate = await bootstrap_workspace(
            session,
            presentation_id=presentation_id,
            request=request,
        )
        _record_operation_admission(
            request.operation_type,
            duplicate=duplicate,
        )
        timeline = await workspace_timeline(
            session,
            presentation_id=presentation_id,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    projected_timeline = project_workspace_timeline(
        timeline,
        presentation_id=presentation_id,
    )
    return WorkspaceBootstrapResponse(
        receipt_id=operation.id,
        conversation_id=conversation.id,
        first_user_message_id=message.id,
        operation_id=operation.id,
        status=operation.state,
        operation_state=operation.state,
        duplicate=duplicate,
        timeline=projected_timeline,
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
    await observe_workspace_timeline_shadow_parity(
        session,
        presentation_id=presentation_id,
        timeline=result,
    )
    return project_workspace_timeline(
        result,
        presentation_id=presentation_id,
    )


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
                definition = resolve_workspace_error(
                    error.code,
                    status_code=error.status_code,
                    retryable=error.retryable,
                )
                data = json.dumps(
                    {
                        "schema_version": 1,
                        "error": {
                            "code": definition.code,
                            "safe_message": definition.safe_message,
                            "retryable": definition.retryable,
                        },
                        # Compatibility fields for the shipped stream client.
                        "code": definition.code,
                        "retryable": definition.retryable,
                    },
                    separators=(",", ":"),
                )
                yield f"event: stream_error\ndata: {data}\n\n"
                return
        projected_page = project_workspace_event_page(page)
        events = projected_page.model_dump(mode="json")["events"]
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
    if (
        after is not None
        and last_event_id is not None
        and after.strip() != last_event_id.strip()
    ):
        _raise_service_error(
            WorkspaceServiceError("invalid_event_cursor", status_code=400)
        )
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
        # The request-scoped session is torn down only after the response ends,
        # and this stream ends only when the client disconnects. On SQLite the
        # read transaction that validation just opened would pin a WAL snapshot
        # for the whole life of the tab, so every checkpoint could copy frames
        # but never restart the log and the WAL grows without bound. The stream
        # body opens its own short session per poll and does not need this one.
        await session.rollback()
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
        page = await workspace_events(
            session,
            presentation_id=presentation_id,
            after=effective_cursor,
            limit=limit,
        )
        return project_workspace_event_page(page)
    except WorkspaceServiceError as error:
        _raise_service_error(error)


@WORKSPACE_ROUTER.post(
    "/presentations/{presentation_id}/commands",
    response_model=WorkspaceActionResponse,
)
async def post_presentation_workspace_command(
    presentation_id: uuid.UUID,
    request: WorkspaceCommandRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceActionResponse:
    _require_workspace_writes(presentation_id)
    try:
        operation, duplicate = await create_workspace_command(
            session,
            presentation_id=presentation_id,
            request=request,
        )
        _record_operation_admission(
            request.command_type,
            duplicate=duplicate,
        )
        timeline = await workspace_timeline(
            session,
            presentation_id=presentation_id,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    projected_timeline = project_workspace_timeline(
        timeline,
        presentation_id=presentation_id,
    )
    return workspace_receipt_response(
        receipt_id=operation.id,
        operation_id=operation.id,
        status=operation.state,
        duplicate=duplicate,
        timeline=projected_timeline,
    )


@WORKSPACE_ROUTER.post(
    "/operations/{operation_id}/actions",
    response_model=WorkspaceActionResponse,
)
async def post_workspace_operation_action(
    operation_id: uuid.UUID,
    request: WorkspaceOperationActionRequest,
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceActionResponse:
    try:
        presentation_id = await resolve_workspace_operation_presentation_id(
            session,
            operation_id,
        )
        if presentation_id is None:
            raise WorkspaceAPIException(
                "workspace_operation_not_found",
                status_code=404,
            )
        _require_workspace_writes(presentation_id)
        receipt, operation, duplicate = await apply_workspace_action(
            session,
            operation_id=operation_id,
            request=request,
        )
        _record_operation_action_admission(
            operation_id,
            operation,
            duplicate=duplicate,
        )
        timeline = await workspace_timeline(
            session,
            presentation_id=presentation_id,
        )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    projected_timeline = project_workspace_timeline(
        timeline,
        presentation_id=presentation_id,
    )
    return workspace_receipt_response(
        receipt_id=receipt.id,
        operation_id=operation.id,
        status=receipt.status,
        duplicate=duplicate,
        timeline=projected_timeline,
    )
