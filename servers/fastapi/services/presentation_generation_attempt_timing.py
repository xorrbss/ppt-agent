"""Attempt-timing receipts for the presentation generation operation handler.

Split out of ``services/presentation_generation_operation_handler.py`` to keep
that adapter under the file-size ratchet.  This module owns the whole path from
a raw failure payload to a durable timing receipt: classifying a failure into a
stage and a retryability verdict, turning the recorder plus provider-usage
ledger into a v2 attempt timing receipt, and CAS-projecting that receipt onto
the workspace operation row before the failure unwind loses the fence.

The handler re-exports these names, so tests and monkeypatches that reach for
them on the handler module keep working unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
import logging
import time
import uuid

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.workspace import WorkspaceOperation
from services.authored_provider_rate_profile import resolve_authored_rate_profile
from services.authored_generation_timing_receipt import (
    GENERATION_ATTEMPT_TIMING_STAGES,
    GenerationTimingRecorder,
    create_generation_attempt_timing_receipt_v2,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationExecutionContext,
)
from services.workspace_generation_timing_payload import (
    generation_timing_result_projection,
)


logger = logging.getLogger(__name__)


def _safe_failure(payload: Mapping[str, object]) -> tuple[str, bool]:
    detail = payload.get("detail")
    nested = detail if isinstance(detail, Mapping) else {}
    code = payload.get("code") or nested.get("code")
    if not code and isinstance(detail, str):
        code = detail
    code = str(code or "presentation_generation_failed")[:128]

    explicit_retryable = payload.get("retryable", nested.get("retryable"))
    if isinstance(explicit_retryable, bool):
        return code, explicit_retryable
    status_code = payload.get("status_code")
    try:
        numeric_status = int(status_code)
    except (TypeError, ValueError):
        numeric_status = 500
    return code, numeric_status == 429 or numeric_status >= 500


def _attempt_failure_stage(
    error: BaseException,
    payload: Mapping[str, object],
    current_stage: object = None,
) -> str:
    detail = payload.get("detail")
    nested = detail if isinstance(detail, Mapping) else {}
    stage = str(
        getattr(error, "stage", "")
        or payload.get("stage")
        or nested.get("stage")
        or current_stage
        or ""
    ).strip().lower()
    if stage in GENERATION_ATTEMPT_TIMING_STAGES:
        return stage
    if stage == "persist":
        return "checkpoint"
    return "author"


def _queue_wait_ms(operation: WorkspaceOperation | None) -> int:
    if operation is None or not isinstance(operation.created_at, datetime):
        return 0
    started_at = operation.started_at
    if not isinstance(started_at, datetime):
        return 0
    created_at = operation.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return max(0, round((started_at - created_at).total_seconds() * 1000))


def _public_executed_stages(
    recorder: GenerationTimingRecorder,
    *,
    logical_calls: int,
    require_terminal_success_stages: bool,
) -> tuple[str, ...]:
    mapping = {
        "outline": "outline",
        "provider": "provider",
        "author": "authoring",
        "repair": "repair",
        "render": "chromiumReview",
        "quality": "chromiumReview",
        "publication": "publication",
    }
    executed = {
        mapping[name]
        for name in recorder.stage_observations()
        if name in mapping
    }
    if logical_calls > 0:
        executed.add("provider")
    if require_terminal_success_stages:
        executed.update(
            {"outline", "authoring", "chromiumReview", "publication"}
        )
    return tuple(sorted(executed))


def _provider_call_timing_snapshot(provider_usage: object) -> dict[str, object] | None:
    ledger = getattr(provider_usage, "call_timing", None)
    snapshot = getattr(ledger, "snapshot", None)
    return snapshot() if callable(snapshot) else None


async def _persist_terminal_attempt_timing(
    *,
    job_id: uuid.UUID,
    context: WorkspaceOperationExecutionContext,
    recorder: GenerationTimingRecorder,
    provider_usage: object,
    queue_wait_ms: object,
    outcome: str,
    retry_reason: str | None,
    terminal_error: str | None,
    request: object = None,
) -> dict[str, object] | None:
    usage_snapshot = provider_usage.snapshot()
    totals = usage_snapshot.get("totals", {})
    logical_calls = (
        int(totals.get("logicalCalls", 0)) if isinstance(totals, Mapping) else 0
    )
    if logical_calls > 0:
        latency_ms = totals.get("latencyMs") if isinstance(totals, Mapping) else None
        if isinstance(latency_ms, (int, float)) and not isinstance(latency_ms, bool):
            recorder.record_stage_ms("provider", latency_ms)
    try:
        receipt = await asyncio.to_thread(
            create_generation_attempt_timing_receipt_v2,
            job_id=job_id,
            attempt_id=context.claim.attempt_token,
            attempt_number=context.claim.attempt_number,
            revision=max(0, int(context.claim.expected_revision or 0)),
            outcome=outcome,
            stage_observations=recorder.stage_observations(),
            executed_stages=_public_executed_stages(
                recorder,
                logical_calls=logical_calls,
                require_terminal_success_stages=outcome == "succeeded",
            ),
            queue_wait_ms=queue_wait_ms,
            wall_ms=recorder.elapsed_ms,
            provider_usage=usage_snapshot,
            retry_reason=retry_reason,
            worker="workspace-generation-worker",
            terminal_error=terminal_error,
            provider_rate_profile=resolve_authored_rate_profile().name,
            # D7: the terminal-slide fallback stashes what it flagged on the
            # request, the same channel the authored timing stage uses.
            flagged_slides=getattr(request, "_authored_flagged_slides", ()) or (),
            # M1: the composition gate stashes the failing slides' fit magnitude
            # on the same request channel.
            fit_summaries=getattr(request, "_authored_gate_fit_summaries", None) or {},
            # SPEED-12: the per-call admission/executor/provider split the
            # usage ledger collected alongside its counters.
            provider_call_timing=_provider_call_timing_snapshot(provider_usage),
            # F3: seconds the authoring wave scheduler spent idle in a provider
            # cooldown, stashed on the request by the generation path.
            authoring_cooldown_wait_seconds=getattr(
                request, "_authored_cooldown_wait_seconds", None
            ),
        )
        return receipt.to_dict()
    except (OSError, TypeError, ValueError):
        logger.warning(
            "Failed to persist generation attempt timing for job %s",
            job_id,
            exc_info=True,
        )
        return None


async def _persist_failed_attempt_timing(
    *,
    request: object,
    job_id: uuid.UUID,
    status_error: Mapping[str, object],
    context: WorkspaceOperationExecutionContext,
    error: BaseException,
    recorder: GenerationTimingRecorder,
    provider_usage: object,
    queue_wait_ms: object = 0,
) -> dict[str, object] | None:
    if str(getattr(request, "template", "")).lower() != "authored":
        return None
    failure_stage = _attempt_failure_stage(
        error,
        status_error,
        getattr(request, "_authored_timing_stage", None),
    )
    active_stage = str(
        getattr(request, "_authored_timing_stage", "") or ""
    ).strip().lower()
    measurement_stage = (
        active_stage
        if active_stage in GENERATION_ATTEMPT_TIMING_STAGES
        else failure_stage
    )
    stage_started_at = getattr(request, "_authored_timing_stage_started_at", None)
    if isinstance(stage_started_at, (int, float)):
        recorder.record_stage_ms(
            measurement_stage,
            max(
                recorder.snapshot_stage_timings_ms().get(measurement_stage, 0),
                round((time.monotonic() - stage_started_at) * 1000),
            ),
        )
    failure_code, retryable = _safe_failure(status_error)
    explicit_code = getattr(error, "code", None)
    if isinstance(explicit_code, str) and explicit_code:
        failure_code = explicit_code
    elif failure_code == "presentation_generation_failed":
        error_code = str(error).strip()
        if error_code:
            failure_code = error_code[:128]
    explicit_retryable = getattr(error, "retryable", None)
    if isinstance(explicit_retryable, bool):
        retryable = explicit_retryable
    normalized_stage_reason = "".join(
        character
        if character in "abcdefghijklmnopqrstuvwxyz0123456789._-"
        else "_"
        for character in failure_code.strip().lower()
    )[:64].strip("_") or "presentation_generation_failed"
    return await _persist_terminal_attempt_timing(
        job_id=job_id,
        context=context,
        recorder=recorder,
        provider_usage=provider_usage,
        queue_wait_ms=queue_wait_ms,
        outcome=(
            "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
        ),
        retry_reason=("retryable_failure" if retryable else "terminal_failure"),
        terminal_error=normalized_stage_reason,
        request=request,
    )


async def _project_failed_attempt_timing(
    *,
    session: AsyncSession,
    context: WorkspaceOperationExecutionContext,
    receipt_payload: Mapping[str, object] | None,
) -> bool:
    """CAS-project one verified receipt into PostgreSQL before failure unwind.

    The local immutable ledger remains crash-recovery evidence, but the
    operation row is the public authority once a handled exception reaches this
    boundary.  Losing the fence leaves the generation outcome unchanged and
    prevents stale timing from amending a newer attempt.
    """

    if receipt_payload is None:
        return False
    projection = generation_timing_result_projection(receipt_payload)
    if not projection:
        return False
    operation = await session.get(
        WorkspaceOperation, context.claim.operation_id
    )
    if (
        operation is None
        or operation.state != "running"
        or operation.attempt_token != context.claim.attempt_token
        or operation.fencing_token != context.claim.fencing_token
    ):
        return False
    current_result = (
        dict(operation.result) if isinstance(operation.result, Mapping) else {}
    )
    projected = await session.execute(
        update(WorkspaceOperation)
        .where(
            WorkspaceOperation.id == context.claim.operation_id,
            WorkspaceOperation.state == "running",
            WorkspaceOperation.attempt_token == context.claim.attempt_token,
            WorkspaceOperation.fencing_token == context.claim.fencing_token,
            WorkspaceOperation.version == operation.version,
        )
        .values(
            result={**current_result, **projection},
            version=WorkspaceOperation.version + 1,
            updated_at=func.now(),
        )
    )
    if projected.rowcount != 1:
        await session.rollback()
        return False
    await session.commit()
    return True
