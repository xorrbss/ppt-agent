"""Slide-order persistence for the editor's drag reorder.

The editor's autosave was calling ``PATCH /presentation/update``
(servers/nextjs .../presentation-generation.ts), which has no FastAPI
handler, so reorder never persisted for any deck. Every persistence write in
this codebase goes through the workspace command/operation runner (see
services/workspace_legacy_write_gate.py); this endpoint submits the same
``presentation.slides.reorder`` command
(services/workspace_slide_order_operation_handler.py) and waits briefly for
the local, DB-only mutation to publish before responding, so the caller sees
the new revision synchronously instead of having to poll.
"""

# FastAPI dependency declarations intentionally call Depends in defaults.
# ruff: noqa: B008

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.workspace import WorkspaceOperation
from models.workspace import WorkspaceCommandRequest
from services.database import get_async_session
from services.workspace_error_registry import WorkspaceAPIException
from services.workspace_operation_runner import wake_workspace_operation_runner
from services.workspace_service import WorkspaceServiceError, create_workspace_command
from services.workspace_slide_order_operation_handler import (
    OPERATION_KIND as SLIDE_ORDER_OPERATION_KIND,
)
from utils.owner_context import get_current_owner_id, get_current_tenant_id

PRESENTATION_SLIDE_ORDER_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation Slide Order"])

_TERMINAL_STATES = {"succeeded", "failed", "cancelled", "deadline_exceeded", "dead_letter"}
# A retry of the *same* submit (same order, same revision) must still land on
# this set for the endpoint's own duplicate-terminal-failure handling below to
# ever fire.
_TERMINAL_FAILURE_STATES = _TERMINAL_STATES - {"succeeded"}
_POLL_INTERVAL_SECONDS = 0.15
_POLL_BUDGET_SECONDS = 6.0
# Bounds the in-request key-escalation loop below; each iteration only runs
# when create_workspace_command just told us the previous key's operation is
# a fresh terminal failure, so more than one or two per request would only
# happen under a pathological retry storm within a single poll budget.
_MAX_RETRY_KEY_ESCALATIONS = 3


class SlideOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slide_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=0)


class SlideOrderResponse(BaseModel):
    presentation_id: uuid.UUID
    revision: int
    order: list[uuid.UUID]
    operation_id: uuid.UUID


def _raise_service_error(error: WorkspaceServiceError) -> None:
    raise WorkspaceAPIException(
        error.code, status_code=error.status_code, retryable=error.retryable,
    ) from error


@PRESENTATION_SLIDE_ORDER_ROUTER.patch(
    "/{presentation_id}/slides/order", response_model=SlideOrderResponse,
)
async def reorder_presentation_slides(
    presentation_id: uuid.UUID,
    request: SlideOrderRequest,
    session: AsyncSession = Depends(get_async_session),
) -> SlideOrderResponse:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(401, detail={"code": "slide_order_authenticated_scope_required"})
    # Deterministic per (presentation, revision, requested order): a retried
    # submit of the same order at the same revision collapses onto the same
    # operation instead of racing a second one. Hashed so the key stays within
    # the 255-character limit and two different orders never collide.
    order_digest = hashlib.sha256(
        "-".join(
            (str(presentation_id), str(request.expected_revision), *(str(x) for x in request.slide_ids)),
        ).encode("utf-8"),
    ).hexdigest()
    idempotency_key = f"slide-order:{order_digest}"
    # The durable runner executes this command outside any HTTP request, so
    # SessionAuthMiddleware's per-request tenant contextvar (api/middlewares.py)
    # is never set there -- renumbering a raster-redesign page's footer after
    # a reorder needs a trusted tenant (services/svg_raster_renumber.py's
    # _fenced_operation), so it has to be captured here and threaded through
    # the command, the same way raster analysis threads tenant_id through its
    # own request_payload (services/raster_analysis_operation_handler.py).
    tenant_id = get_current_tenant_id() or (os.getenv("PPT_SERVICE_TENANT_ID") or "default").strip()
    command = WorkspaceCommandRequest(
        command="Reorder slides",
        command_type=SLIDE_ORDER_OPERATION_KIND,
        idempotency_key=idempotency_key,
        expected_revision=request.expected_revision,
        context={
            "slide_ids": [str(slide_id) for slide_id in request.slide_ids],
            "tenant_id": tenant_id,
        },
    )
    try:
        # create_workspace_command commits (or rolls back) on its own session.
        operation, duplicate = await create_workspace_command(
            session, presentation_id=presentation_id, request=command,
        )
        # An idempotency key stays permanently bound to whichever operation
        # first claimed it -- create_workspace_command never lets a fresh
        # submit reclaim a key from an existing operation as long as the
        # payload still hashes the same, and that rule is generic to the
        # workspace command layer (presentation.generate's bootstrap admission
        # has the identical "existing + matching request_hash -> return the
        # existing row" behavior in services/workspace_service.py). The
        # layer's own answer to "retry a terminal failure" is the separate,
        # explicit retry action (apply_workspace_action, POST .../operations/
        # {id}/actions) keyed off the operation id rather than resubmission --
        # not available here since a first-ever submit has no operation id to
        # retry against, and the editor only ever calls this narrow PATCH.
        # So a real retry of a terminal failure escalates the key itself,
        # deterministically, from the failed operation's own id: two truly
        # concurrent submits of the same order still race onto the same base
        # key (existing dedupe, unchanged), while a submit that lands after
        # the previous attempt is already a terminal failure gets a new
        # operation and an actual re-execution instead of replaying the old
        # error forever.
        escalations = 0
        while (
            duplicate
            and operation.state in _TERMINAL_FAILURE_STATES
            and escalations < _MAX_RETRY_KEY_ESCALATIONS
        ):
            escalations += 1
            command = command.model_copy(
                update={"idempotency_key": f"{idempotency_key}:retry:{operation.id}"},
            )
            operation, duplicate = await create_workspace_command(
                session, presentation_id=presentation_id, request=command,
            )
    except WorkspaceServiceError as error:
        _raise_service_error(error)
    wake_workspace_operation_runner()

    deadline = asyncio.get_running_loop().time() + _POLL_BUDGET_SECONDS
    state, result, result_revision, error_code = (
        operation.state, operation.result, operation.result_revision, operation.error_code,
    )
    while state not in _TERMINAL_STATES and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        row = await session.get(
            WorkspaceOperation, operation.id, populate_existing=True,
        )
        if row is None:
            raise WorkspaceAPIException("presentation_not_found", status_code=404)
        state, result, result_revision, error_code = (
            row.state, row.result, row.result_revision, row.error_code,
        )

    if state == "succeeded":
        order = (result or {}).get("order") or [str(slide_id) for slide_id in request.slide_ids]
        return SlideOrderResponse(
            presentation_id=presentation_id,
            revision=result_revision if result_revision is not None else request.expected_revision,
            order=[uuid.UUID(item) for item in order],
            operation_id=operation.id,
        )
    if state in _TERMINAL_STATES:
        raise WorkspaceAPIException(
            error_code or "slide_order_publish_failed",
            status_code=409 if (error_code or "").startswith("stale_") else 422,
        )
    # Still running past the poll budget: the caller can follow the
    # operation id through the existing workspace timeline; this is not a
    # failure, just slower than the synchronous-feeling common case. A 2xx
    # status would make simple ok-based frontend clients treat this as the
    # success response shape, so this stays a (retryable) error status.
    raise WorkspaceAPIException("slide_order_still_running", status_code=503)
