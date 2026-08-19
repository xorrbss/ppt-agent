"""Durable, fenced runner for chat-first workspace operations.

Database rows and operation events are the source of truth.  The in-process
wake event only reduces polling latency; losing it never loses an operation.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from services.workspace_batch_payload import BATCH_KIND, BATCH_RESULT_KEYS, sanitize_batch_value
from services.workspace_quality_review_payload import (
    _normalize_quality_review_payload,
    _quality_review_count,
    _quality_review_issue_message,
    _quality_review_number,
    _sanitize_quality_review_issues,
    _sanitize_quality_review_reason_codes,
    _sanitize_quality_review_slide_results,
    _sanitize_quality_review_stage_timings,
)
import inspect
import json
import logging
import os
import secrets
from typing import Any
import uuid

from sqlalchemy import and_, case, exists, func, or_, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PresentationModel,
)
from models.sql.workspace import (
    WorkspaceCleanupJournal,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services.database import async_session_maker
from services.account_deletion_service import (
    AccountDeletionFenceConflict,
    require_active_account_generation,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationBlocked,
    WorkspaceOperationCancelled,
    WorkspaceOperationClaim,
    WorkspaceOperationDeferred,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationExecutionError,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)
from services.workspace_operation_lease import (  # noqa: F401
    TERMINAL_OPERATION_STATES,
    WORKSPACE_OPERATION_HEARTBEAT_SECONDS,
    WORKSPACE_OPERATION_LEASE_SECONDS,
    extend_workspace_operation_lease,
    heartbeat_workspace_operation,
    operation_cancel_requested as _operation_cancel_requested,
    run_attempt_heartbeat_loop,
)
from services.workspace_operation_retry_policy import (
    decide_workspace_operation_retry,
)
from services.workspace_operation_observability import (
    WorkspaceOperationObservationName,
    log_workspace_operation_observation,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import (
    get_current_owner_id,
    reset_current_owner_id,
    set_current_owner_id,
)
from utils.runtime_settings import create_runtime_settings_task
from models.sql.workspace import get_workspace_owner_scope


LOGGER = logging.getLogger(__name__)

WORKSPACE_OPERATION_POLL_SECONDS = 1.0
WORKSPACE_OPERATION_MAX_CONCURRENCY = 2
# Serialize the short owner-aware dispatch decision across PostgreSQL workers.
# SQLite already uses BEGIN IMMEDIATE below.  The lock is transaction-scoped,
# so a crashed worker cannot strand the queue.
_WORKSPACE_CLAIM_LOCK_ID = 0x50505457534F5043
_MAX_OBSERVATION_DURATION_MS = 30 * 24 * 60 * 60 * 1000

_TERMINAL_STATES = TERMINAL_OPERATION_STATES
_RUNNING_STATES = frozenset({"running", "cancelling"})
# Only one mutating operation may execute for a presentation at a time.  Wait
# states do not hold this execution lock: they must not indefinitely block a
# later independent command while waiting for a user or an asset processor.
_PRESENTATION_EXECUTING_STATES = frozenset({"running", "cancelling"})
_DIRECT_CANCEL_STATES = frozenset(
    {
        "queued",
        "waiting_for_assets",
        "blocked",
        "approval_required",
        "awaiting_approval",
    }
)

# Event/outbox payloads are intentionally metadata-only.  Full prompts,
# filenames, paths, provider responses and exception messages stay out of the
# event stream and telemetry by construction.
_SAFE_PAYLOAD_KEYS = frozenset(
    {
        "operation_id",
        "presentation_id",
        "conversation_id",
        "event_cursor",
        "event_type",
        "state",
        "status",
        "stage",
        "current",
        "completed",
        "total",
        "percent",
        "attempt_number",
        "retry_count",
        "max_attempts",
        "backoff_seconds",
        "capacity_waiting",
        "next_attempt_at",
        "error_code",
        "retryable",
        "result_revision",
        "revision",
        "artifact_id",
        "action_id",
        "action_type",
        "reason_code",
        "task_id",
        "job_id",
        "fencing_token",
        "queue_position",
        "wait_reason",
    }
)

_PUBLIC_RUNTIME_EVENT_TYPES = frozenset(
    {
        "assistant.checkpoint",
        "tool.started",
        "tool.completed",
        "run.status",
    }
)
_PUBLIC_RUNTIME_PAYLOAD_KEYS = frozenset(
    {
        "message_id",
        "assistant_text",
        "checkpoint_sequence",
        "tool_name",
        "tool_status",
        "tool_summary",
    }
)
_MAX_ASSISTANT_CHECKPOINT_LENGTH = 100_000
_MAX_TOOL_SUMMARY_LENGTH = 500

# Result payload additions are scoped to a specific operation kind.  Keeping
# these out of the general event allowlist prevents an unrelated handler from
# publishing fields that only make sense for another operation's result card.
_SAFE_RESULT_PAYLOAD_KEYS_BY_KIND: dict[str, frozenset[str]] = {
    "quality.review": frozenset(
        {
            "kind",
            "message",
            "mode",
            "scope",
            "slide_indices",
            "inspected_revision",
            "reviewed_count",
            "issue_slide_count",
            "fixed_count",
            "fixed_slide_indices",
            "remaining_issue_count",
            "remaining_slide_indices",
            "remaining_issue_indices",
            "remaining_slide_count",
            "detailed_issue_count",
            "confirmed_issue_count",
            "resolved_issue_count",
            "pattern_cluster_count",
            "pattern_diagnostic_family_count",
            "pattern_representative_count",
            "pattern_recipe_count",
            "pattern_reuse_attempts",
            "pattern_reuse_hits",
            "pattern_reuse_applied",
            "composite_plan_count",
            "composite_operations_merged",
            "render_candidates_rejected_early",
            "full_render_count",
            "llm_pattern_call_count",
            "llm_eligible_count",
            "llm_response_count",
            "llm_schema_pass_count",
            "llm_valid_plan_count",
            "llm_provider_failure_count",
            "llm_schema_reject_count",
            "llm_preflight_pass_count",
            "llm_rendered_count",
            "proposal_preflight_unprojectable_count",
            "negative_cache_hit_count",
            "llm_circuit_open_count",
            "no_candidate_reasons",
            "individual_fallback_count",
            "stage_timings_ms",
            "wall_time_ms",
            "aggregate_stage_time_ms",
            "total_ms",
            "remaining_issues",
            "issues",
            "slide_results",
            "version_saved",
            "canvas_refresh_required",
            "canvas_refresh_slide_index",
            "canvas_refresh_status",
            "result",
        }
    ),
    BATCH_KIND: BATCH_RESULT_KEYS,
    "presentation.delete": frozenset(
        {
            "lifecycle_status",
            "cleanup_pending",
            "deletion_operation_id",
        }
    ),
}

_QUALITY_REVIEW_INDEX_LIST_KEYS = frozenset(
    {
        "slide_indices",
        "fixed_slide_indices",
        "remaining_slide_indices",
        "remaining_issue_indices",
    }
)
_QUALITY_REVIEW_ISSUE_LIST_KEYS = frozenset({"issues", "remaining_issues"})
_QUALITY_REVIEW_REASON_LIST_KEYS = frozenset({"no_candidate_reasons"})
_MAX_QUALITY_REVIEW_ISSUES = 500
_MAX_QUALITY_REVIEW_MESSAGE_LENGTH = 500
_MAX_QUALITY_REVIEW_REASON_CODE_LENGTH = 128
_MAX_QUALITY_REVIEW_REASON_CODES = 32
_MAX_QUALITY_REVIEW_METRIC_VALUE = 1_000_000
_MAX_QUALITY_REVIEW_TIMING_MS = 86_400_000
_MAX_QUALITY_REVIEW_STAGE_TIMINGS = 32
_QUALITY_REVIEW_COUNT_KEYS = frozenset(
    {
        "reviewed_count",
        "issue_slide_count",
        "detailed_issue_count",
        "confirmed_issue_count",
        "fixed_count",
        "resolved_issue_count",
        "remaining_slide_count",
        "remaining_issue_count",
        "pattern_cluster_count",
        "pattern_diagnostic_family_count",
        "pattern_representative_count",
        "pattern_recipe_count",
        "pattern_reuse_attempts",
        "pattern_reuse_hits",
        "pattern_reuse_applied",
        "composite_plan_count",
        "composite_operations_merged",
        "render_candidates_rejected_early",
        "full_render_count",
        "llm_pattern_call_count",
        "llm_eligible_count",
        "llm_response_count",
        "llm_schema_pass_count",
        "llm_valid_plan_count",
        "llm_provider_failure_count",
        "llm_schema_reject_count",
        "llm_preflight_pass_count",
        "llm_rendered_count",
        "proposal_preflight_unprojectable_count",
        "negative_cache_hit_count",
        "llm_circuit_open_count",
        "individual_fallback_count",
    }
)


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _positive_float_env(name: str, default: float) -> float:
    try:
        return max(0.05, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _utc_now() -> datetime:
    return get_current_utc_datetime()


def _coerce_aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _elapsed_ms(started_at: datetime | None, ended_at: datetime) -> int | None:
    started_at = _coerce_aware(started_at)
    ended_at = _coerce_aware(ended_at) or ended_at
    if started_at is None:
        return None
    return min(
        _MAX_OBSERVATION_DURATION_MS,
        max(0, round((ended_at - started_at).total_seconds() * 1000)),
    )


def _observe_claim(
    claim: WorkspaceOperationClaim,
    *,
    name: WorkspaceOperationObservationName,
    result_status: str,
    duration_ms: int | None = None,
    error_code: str | None = None,
    retry_count: int | None = None,
    recovery_count: int | None = None,
    result_revision: int | None = None,
    revision_conflict: bool | None = None,
    fence_outcome: str | None = None,
) -> None:
    log_workspace_operation_observation(
        name=name,
        operation_id=claim.operation_id,
        presentation_id=claim.presentation_id,
        conversation_id=claim.conversation_id,
        command_type=claim.kind,
        result_status=result_status,
        error_code=error_code,
        duration_ms=duration_ms,
        attempt_number=claim.attempt_number,
        retry_count=retry_count,
        recovery_count=recovery_count,
        expected_revision=claim.expected_revision,
        result_revision=result_revision,
        fencing_token=claim.fencing_token,
        revision_conflict=revision_conflict,
        fence_outcome=fence_outcome,
    )


def sanitize_workspace_event_payload(
    payload: Mapping[str, Any] | None,
    *,
    operation_kind: str | None = None,
) -> dict[str, Any]:
    """Return a bounded, metadata-only event payload.

    Quality review results additionally retain a deliberately small,
    presentation-safe summary used by the workspace result card.  Raw model
    responses, DOM inspection details, OCR output and slide HTML remain
    excluded.
    """

    if not payload:
        return {}
    if operation_kind == "quality.review":
        payload = _normalize_quality_review_payload(payload)
    allowed_keys = _SAFE_PAYLOAD_KEYS | _SAFE_RESULT_PAYLOAD_KEYS_BY_KIND.get(operation_kind or "", frozenset())
    if operation_kind in {"presentation.edit", "slide.edit", "slide.delete"}:
        allowed_keys |= _PUBLIC_RUNTIME_PAYLOAD_KEYS
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        normalized = str(key)
        if normalized not in allowed_keys:
            continue
        if isinstance(value, uuid.UUID):
            safe[normalized] = str(value)
        elif isinstance(value, datetime):
            safe[normalized] = value.isoformat()
        elif (
            operation_kind == "quality.review"
            and normalized in _QUALITY_REVIEW_INDEX_LIST_KEYS
            and isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
        ):
            safe[normalized] = [
                item for item in value if isinstance(item, int) and not isinstance(item, bool) and item >= 0
            ][:500]
        elif operation_kind == "quality.review" and normalized in _QUALITY_REVIEW_ISSUE_LIST_KEYS:
            safe[normalized] = _sanitize_quality_review_issues(value)
        elif operation_kind == "quality.review" and normalized in _QUALITY_REVIEW_REASON_LIST_KEYS:
            safe[normalized] = _sanitize_quality_review_reason_codes(value)
        elif operation_kind == "quality.review" and normalized == "slide_results":
            safe[normalized] = _sanitize_quality_review_slide_results(value)
        elif operation_kind == "quality.review" and normalized == "stage_timings_ms":
            # This was normalized from approved aggregate telemetry above.
            safe[normalized] = _sanitize_quality_review_stage_timings(value)
        elif operation_kind == "quality.review" and normalized == "result" and isinstance(value, Mapping):
            # A nested result is emitted for live timeline projection.  Strip
            # another nested result to keep recursion bounded for hostile data.
            safe[normalized] = sanitize_workspace_event_payload(
                {key: item for key, item in value.items() if key != "result"},
                operation_kind=operation_kind,
            )
        elif operation_kind == BATCH_KIND and isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            safe[normalized] = sanitize_batch_value(normalized, value)
        elif value is None or isinstance(value, (str, int, float, bool)):
            if isinstance(value, str):
                if normalized == "assistant_text":
                    value = value[:_MAX_ASSISTANT_CHECKPOINT_LENGTH]
                elif normalized == "tool_summary":
                    value = value[:_MAX_TOOL_SUMMARY_LENGTH]
                elif normalized in {"message_id", "tool_name", "tool_status"}:
                    value = value[:128]
                elif operation_kind == "quality.review" and normalized == "message":
                    value = value[:_MAX_QUALITY_REVIEW_MESSAGE_LENGTH]
            safe[normalized] = value
    return safe


def _linked_generation_job_id(operation: WorkspaceOperation) -> uuid.UUID | None:
    """Return the durable compatibility job id only for admitted generation."""

    if operation.kind != "presentation.generate" or not isinstance(operation.request_payload, Mapping):
        return None
    raw_job_id = operation.request_payload.get("job_id")
    if raw_job_id is None:
        return None
    try:
        return uuid.UUID(str(raw_job_id))
    except (TypeError, ValueError, AttributeError):
        return None


async def _project_linked_generation_terminal(
    session: AsyncSession,
    operation: WorkspaceOperation,
    *,
    state: str,
    error_code: str | None = None,
) -> bool:
    """Keep the rollout compatibility task in the operation transaction."""

    job_id = _linked_generation_job_id(operation)
    if job_id is None:
        return True
    # Runtime import avoids making the compatibility adapter part of the
    # generic runner's module initialization graph.
    from services.presentation_generation_job_service import (
        project_workspace_linked_generation_cancelled,
        project_workspace_linked_generation_failed,
    )

    if state == "cancelled":
        return await project_workspace_linked_generation_cancelled(
            session,
            operation.id,
            expected_job_id=job_id,
        )
    return await project_workspace_linked_generation_failed(
        session,
        operation.id,
        error_code=error_code or "presentation_generation_failed",
        expected_job_id=job_id,
    )


async def _project_linked_generation_recovered(
    session: AsyncSession,
    operation: WorkspaceOperation,
    *,
    expected_attempt_token: str | None,
) -> bool:
    """Keep a retryable recovery and its legacy projection atomic."""

    job_id = _linked_generation_job_id(operation)
    if job_id is None:
        return True
    from services.presentation_generation_job_service import (
        project_workspace_linked_generation_recovered,
    )

    return await project_workspace_linked_generation_recovered(
        session,
        operation.id,
        expected_job_id=job_id,
        expected_attempt_token=expected_attempt_token,
    )


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _EventAppendResult:
    event: WorkspaceOperationEvent
    state: str
    version: int


async def append_operation_event(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    operation_kind: str | None = None,
    attempt_token: str | None = None,
    allowed_states: Collection[str] | None = None,
    projection_values: Mapping[str, Any] | None = None,
    increment_version: bool = False,
    expected_version: int | None = None,
    additional_conditions: Sequence[Any] = (),
    enqueue_notification: bool = True,
) -> WorkspaceOperationEvent | None:
    """CAS a projection and reserve/insert its event in one transaction.

    The counter UPDATE is the sequence allocator on SQLite and PostgreSQL.
    When ``attempt_token`` is supplied, a stale worker cannot mutate the
    projection, allocate a sequence, or append an event.
    """

    now = _utc_now()
    values: dict[str, Any] = {
        "next_event_sequence": WorkspaceOperation.next_event_sequence + 1,
        "updated_at": now,
    }
    values.update(dict(projection_values or {}))
    if increment_version or projection_values:
        values["version"] = WorkspaceOperation.version + 1
    statement = update(WorkspaceOperation).where(WorkspaceOperation.id == operation_id)
    if attempt_token is not None:
        statement = statement.where(WorkspaceOperation.attempt_token == attempt_token)
    if allowed_states:
        statement = statement.where(WorkspaceOperation.state.in_(tuple(allowed_states)))
    if expected_version is not None:
        statement = statement.where(WorkspaceOperation.version == expected_version)
    if additional_conditions:
        statement = statement.where(*additional_conditions)
    statement = statement.values(**values).returning(
        WorkspaceOperation.next_event_sequence,
        WorkspaceOperation.owner_id,
        WorkspaceOperation.owner_scope,
        WorkspaceOperation.presentation_id,
        WorkspaceOperation.conversation_id,
        WorkspaceOperation.state,
        WorkspaceOperation.version,
    )
    # The CAS result is consumed from RETURNING below; synchronizing an ORM
    # instance is unnecessary.  More importantly, SQLAlchemy's evaluator can
    # otherwise compare SQLite's offset-naive hydrated datetimes with our
    # UTC-aware retry cutoff and raise before the UPDATE reaches the database.
    statement = statement.execution_options(synchronize_session=False)
    row = (await session.execute(statement)).mappings().one_or_none()
    if row is None:
        return None

    sequence = int(row["next_event_sequence"]) - 1
    safe_payload = sanitize_workspace_event_payload(
        payload,
        operation_kind=operation_kind,
    )
    safe_payload.setdefault("operation_id", str(operation_id))
    safe_payload.setdefault("presentation_id", str(row["presentation_id"]))
    safe_payload.setdefault("conversation_id", str(row["conversation_id"]))
    safe_payload.setdefault("state", str(row["state"]))
    event = WorkspaceOperationEvent(
        owner_id=row["owner_id"],
        operation_id=operation_id,
        sequence=sequence,
        operation_version=int(row["version"]),
        event_type=event_type,
        payload=safe_payload,
    )
    session.add(event)
    await session.flush()

    if enqueue_notification:
        notification = {
            "operation_id": str(operation_id),
            "presentation_id": str(row["presentation_id"]),
            "conversation_id": str(row["conversation_id"]),
            "event_cursor": int(event.cursor) if event.cursor is not None else None,
            "event_type": event_type,
            "operation_version": int(row["version"]),
            "status": str(row["state"]),
        }
        dedupe_key = f"workspace-event:{operation_id}:{sequence}"
        session.add(
            WorkspaceOutbox(
                owner_id=row["owner_id"],
                owner_scope=str(row["owner_scope"]),
                operation_id=operation_id,
                event_cursor=event.cursor,
                topic="workspace.operation.event",
                idempotency_key_hash=_canonical_hash({"key": dedupe_key}),
                payload_hash=_canonical_hash(notification),
                payload=notification,
            )
        )
        await session.flush()
    return event


async def claim_next_workspace_operation(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    lease_seconds: int = WORKSPACE_OPERATION_LEASE_SECONDS,
) -> WorkspaceOperationClaim | None:
    """Claim a ready operation using a durable owner-aware fair order.

    Each owner's oldest ready command is effectively its queue head.  Owners
    with fewer active commands are served first, then the owner least recently
    served (claim/heartbeat time).  All inputs live in
    ``workspace_operations``, so the order survives process restarts without a
    scheduler-only cursor or migration.
    """

    now = _utc_now()
    token = secrets.token_urlsafe(32)
    async with session_factory() as session:
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            await session.execute(text("BEGIN IMMEDIATE"))
        elif dialect == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _WORKSPACE_CLAIM_LOCK_ID},
            )
        executing = aliased(WorkspaceOperation)
        owner_activity = aliased(WorkspaceOperation)
        owner_history = aliased(WorkspaceOperation)
        owner_active_count = (
            select(func.count(owner_activity.id))
            .where(owner_activity.owner_scope == WorkspaceOperation.owner_scope)
            .where(owner_activity.state.in_(tuple(_PRESENTATION_EXECUTING_STATES)))
            .correlate(WorkspaceOperation)
            .scalar_subquery()
        )
        owner_last_served_at = (
            select(
                func.max(
                    func.coalesce(
                        owner_history.heartbeat_at,
                        owner_history.started_at,
                    )
                )
            )
            .where(owner_history.owner_scope == WorkspaceOperation.owner_scope)
            .correlate(WorkspaceOperation)
            .scalar_subquery()
        )
        statement = (
            select(WorkspaceOperation)
            .where(WorkspaceOperation.state == "queued")
            .where(WorkspaceOperation.cancel_requested_at.is_(None))
            .where(
                or_(
                    WorkspaceOperation.next_attempt_at.is_(None),
                    WorkspaceOperation.next_attempt_at <= now,
                )
            )
            .where(
                ~exists(
                    select(executing.id).where(
                        executing.presentation_id == WorkspaceOperation.presentation_id,
                        executing.state.in_(tuple(_PRESENTATION_EXECUTING_STATES)),
                    )
                )
            )
            .order_by(
                owner_active_count,
                case((owner_last_served_at.is_(None), 0), else_=1),
                owner_last_served_at,
                WorkspaceOperation.created_at,
                WorkspaceOperation.id,
            )
            .limit(1)
        )
        if dialect == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        candidate = (await session.execute(statement)).scalar_one_or_none()
        if candidate is None:
            await session.rollback()
            return None

        if dialect == "postgresql":
            # The candidate SKIP LOCKED clause only serializes claims for that
            # operation row.  Locking the presentation makes claims for two
            # distinct queued rows of the same deck mutually exclusive too.
            await session.execute(
                select(PresentationModel.id)
                .where(PresentationModel.id == candidate.presentation_id)
                .with_for_update()
                .execution_options(skip_owner_scope=True)
            )

        # Recheck under SQLite's writer lock / PostgreSQL's presentation lock.
        # Without this second check, two workers can both observe no running
        # sibling before either publishes its claim.
        sibling = await session.scalar(
            select(WorkspaceOperation.id)
            .where(WorkspaceOperation.presentation_id == candidate.presentation_id)
            .where(WorkspaceOperation.id != candidate.id)
            .where(WorkspaceOperation.state.in_(tuple(_PRESENTATION_EXECUTING_STATES)))
            .limit(1)
            .execution_options(skip_owner_scope=True)
        )
        if sibling is not None:
            await session.rollback()
            return None

        next_attempt_number = candidate.attempt_number + 1
        next_fencing_token = candidate.fencing_token + 1
        event = await append_operation_event(
            session,
            operation_id=candidate.id,
            event_type="operation.progress",
            payload={
                "state": "running",
                "stage": "started",
                "attempt_number": next_attempt_number,
                "fencing_token": next_fencing_token,
            },
            allowed_states=("queued",),
            expected_version=candidate.version,
            additional_conditions=(
                WorkspaceOperation.cancel_requested_at.is_(None),
                or_(
                    WorkspaceOperation.next_attempt_at.is_(None),
                    WorkspaceOperation.next_attempt_at <= now,
                ),
            ),
            projection_values={
                "state": "running",
                "attempt_number": next_attempt_number,
                "attempt_token": token,
                "fencing_token": next_fencing_token,
                "lease_expires_at": now + timedelta(seconds=max(1, lease_seconds)),
                "heartbeat_at": now,
                "next_attempt_at": None,
                "started_at": candidate.started_at or now,
                "error_code": None,
                "error_detail": None,
            },
        )
        if event is None:
            await session.rollback()
            return None
        await session.commit()
        claim = WorkspaceOperationClaim(
            operation_id=candidate.id,
            owner_id=candidate.owner_id,
            owner_scope=candidate.owner_scope,
            conversation_id=candidate.conversation_id,
            presentation_id=candidate.presentation_id,
            kind=candidate.kind,
            request_payload=dict(candidate.request_payload),
            expected_revision=candidate.expected_revision,
            attempt_number=next_attempt_number,
            attempt_token=token,
            fencing_token=next_fencing_token,
        )
        claim_duration_ms = _elapsed_ms(candidate.created_at, now)
        if next_attempt_number == 1 and candidate.retry_count == 0:
            _observe_claim(
                claim,
                name="accepted",
                result_status="running",
                duration_ms=claim_duration_ms,
                retry_count=0,
                fence_outcome="claim_acquired",
            )
        _observe_claim(
            claim,
            name="claimed",
            result_status="running",
            duration_ms=claim_duration_ms,
            retry_count=candidate.retry_count,
            fence_outcome="claim_acquired",
        )
        _observe_claim(
            claim,
            name="running",
            result_status="running",
            duration_ms=claim_duration_ms,
            retry_count=candidate.retry_count,
            fence_outcome="claim_acquired",
        )
        return claim


async def _transition_claim_failure(
    claim: WorkspaceOperationClaim,
    *,
    code: str,
    retryable: bool,
    detail: Mapping[str, Any] | None = None,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    now = _utc_now()
    async with session_factory() as session:
        operation = await session.get(WorkspaceOperation, claim.operation_id)
        if (
            operation is None
            or operation.attempt_token != claim.attempt_token
            or operation.state not in _RUNNING_STATES
        ):
            await session.rollback()
            return False
        decision = decide_workspace_operation_retry(
            operation_id=operation.id,
            retry_count=operation.retry_count,
            max_attempts=operation.max_attempts,
            now=now,
            retryable=retryable,
        )
        error_detail = sanitize_workspace_event_payload(detail)
        error_detail.update(
            {
                "error_code": code,
                "retryable": retryable,
                "retry_count": decision.retry_count,
                "max_attempts": operation.max_attempts,
            }
        )
        event = await append_operation_event(
            session,
            operation_id=operation.id,
            event_type="operation.error",
            payload={
                **error_detail,
                "state": decision.state,
                "backoff_seconds": decision.backoff_seconds,
                "next_attempt_at": decision.next_attempt_at,
            },
            attempt_token=claim.attempt_token,
            allowed_states=_RUNNING_STATES,
            projection_values={
                "state": decision.state,
                "retry_count": decision.retry_count,
                "backoff_seconds": decision.backoff_seconds,
                "next_attempt_at": decision.next_attempt_at,
                "attempt_token": None,
                "lease_expires_at": None,
                "heartbeat_at": now,
                "error_code": code,
                "error_detail": error_detail,
                "completed_at": now if decision.state in {"failed", "dead_letter"} else None,
            },
        )
        if event is None:
            await session.rollback()
            return False
        if decision.state in {"failed", "dead_letter"} and not await _project_linked_generation_terminal(
            session,
            operation,
            state=decision.state,
            error_code=code,
        ):
            await session.rollback()
            return False
        await session.commit()
        if decision.state == "queued":
            observation_name: WorkspaceOperationObservationName = "retry_scheduled"
        elif decision.state == "dead_letter":
            observation_name = "dead_letter"
        else:
            observation_name = "failed"
        _observe_claim(
            claim,
            name=observation_name,
            result_status=decision.state,
            duration_ms=_elapsed_ms(operation.created_at, now),
            error_code=code,
            retry_count=decision.retry_count,
            revision_conflict=code in {"revision_conflict", "stale_presentation_revision"},
            fence_outcome="transition_committed",
        )
        return True


async def _transition_claim_blocked(
    claim: WorkspaceOperationClaim,
    error: WorkspaceOperationBlocked,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    now = _utc_now()
    async with session_factory() as session:
        operation = await session.get(WorkspaceOperation, claim.operation_id)
        event = await append_operation_event(
            session,
            operation_id=claim.operation_id,
            event_type="operation.error",
            payload={"state": "blocked", "error_code": error.code},
            attempt_token=claim.attempt_token,
            allowed_states=("running",),
            projection_values={
                "state": "blocked",
                "attempt_token": None,
                "lease_expires_at": None,
                "heartbeat_at": now,
                "error_code": error.code,
                "error_detail": sanitize_workspace_event_payload(error.detail),
            },
        )
        if event is None:
            await session.rollback()
            return False
        await session.commit()
        _observe_claim(
            claim,
            name="blocked",
            result_status="blocked",
            duration_ms=_elapsed_ms(
                operation.created_at if operation is not None else None,
                now,
            ),
            error_code=error.code,
            retry_count=operation.retry_count if operation is not None else None,
            fence_outcome="transition_committed",
        )
        return True


async def finalize_workspace_operation_success(
    claim: WorkspaceOperationClaim,
    result: WorkspaceOperationResult,
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> bool:
    now = _utc_now()
    safe_result = sanitize_workspace_event_payload(
        result.payload,
        operation_kind=claim.kind,
    )
    if result.result_revision is not None:
        safe_result["result_revision"] = result.result_revision
    event_payload: dict[str, Any] = {"state": "succeeded", **safe_result}
    projection_values: dict[str, Any] = {
        "state": "succeeded",
        "result": safe_result,
        "result_revision": result.result_revision,
        "attempt_token": None,
        "lease_expires_at": None,
        "heartbeat_at": now,
        "error_code": None,
        "error_detail": None,
        "completed_at": now,
    }
    if claim.kind == "quality.review":
        completion_progress = {
            "stage": "quality_review_completed",
            "percent": 100,
        }
        # The nested result lets an already-open timeline render the result
        # card from the terminal event without waiting for a snapshot reload.
        event_payload.update(completion_progress, result=safe_result)
        projection_values["progress"] = completion_progress
    elif claim.kind in {"export.pptx", "export.pdf"}:
        completion_progress = {
            "stage": "export_completed",
            "percent": 100,
        }
        event_payload.update(completion_progress)
        projection_values["progress"] = completion_progress
    async with session_factory() as session:
        operation_before = await session.get(WorkspaceOperation, claim.operation_id)
        if (
            operation_before is not None
            and operation_before.state == "running"
            and operation_before.attempt_token == claim.attempt_token
        ):
            # Fence the final event/projection commit in the same transaction.
            # A successful handler may have raced either account deletion or a
            # deck tombstone while doing external work; neither may publish a
            # late result after the durable deletion serialization point.
            await _fence_workspace_claim(
                session,
                claim,
                check_revision=False,
            )
        event = await append_operation_event(
            session,
            operation_id=claim.operation_id,
            event_type="operation.result",
            payload=event_payload,
            operation_kind=claim.kind,
            attempt_token=claim.attempt_token,
            allowed_states=("running",),
            projection_values=projection_values,
        )
        if event is not None:
            await session.commit()
            _observe_claim(
                claim,
                name="succeeded",
                result_status="succeeded",
                duration_ms=_elapsed_ms(
                    operation_before.created_at if operation_before is not None else None,
                    now,
                ),
                retry_count=(operation_before.retry_count if operation_before is not None else None),
                result_revision=result.result_revision,
                revision_conflict=False,
                fence_outcome="publish_committed",
            )
            return True

        # Publication may atomically consume the same fencing token and
        # terminalize the operation.  Current publishers also append the
        # result event in that transaction; this fallback supports older or
        # custom publishers while still preventing duplicate terminal events.
        operation = await session.get(WorkspaceOperation, claim.operation_id)
        if operation is None or operation.state != "succeeded":
            await session.rollback()
            return False
        existing = (
            await session.execute(
                select(WorkspaceOperationEvent.cursor)
                .where(WorkspaceOperationEvent.operation_id == claim.operation_id)
                .where(WorkspaceOperationEvent.event_type == "operation.result")
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            await session.rollback()
            return True
        publication_payload = dict(safe_result)
        if operation.result_revision is not None:
            publication_payload["result_revision"] = operation.result_revision
        event = await append_operation_event(
            session,
            operation_id=claim.operation_id,
            event_type="operation.result",
            payload={"state": "succeeded", **publication_payload},
            allowed_states=("succeeded",),
            expected_version=operation.version,
            increment_version=True,
        )
        if event is None:
            await session.rollback()
            return False
        await session.commit()
        _observe_claim(
            claim,
            name="succeeded",
            result_status="succeeded",
            duration_ms=_elapsed_ms(operation.created_at, now),
            retry_count=operation.retry_count,
            result_revision=operation.result_revision,
            revision_conflict=False,
            fence_outcome="publish_committed",
        )
        return True


def _claim_account_generation(claim: WorkspaceOperationClaim) -> int | None:
    value = claim.request_payload.get("account_deletion_generation")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        # Operations written before the additive fence field remain runnable,
        # but still undergo the active-account check at publication time.
        return None
    return value


async def _fence_workspace_claim(
    session: AsyncSession,
    claim: WorkspaceOperationClaim,
    *,
    check_revision: bool,
) -> int:
    """Serialize runner execution/publication against durable deletion."""

    if claim.owner_id is not None:
        try:
            await require_active_account_generation(
                session,
                user_id=claim.owner_id,
                expected_generation=_claim_account_generation(claim),
            )
        except AccountDeletionFenceConflict as error:
            raise NonRetryableWorkspaceOperationError("account_deletion_in_progress") from error

    owner_predicate = (
        PresentationModel.owner_id.is_(None) if claim.owner_id is None else PresentationModel.owner_id == claim.owner_id
    )
    lifecycle_predicate = (
        or_(
            PresentationModel.lifecycle_status != PRESENTATION_LIFECYCLE_DELETED,
            and_(
                PresentationModel.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED,
                PresentationModel.deletion_operation_id == claim.operation_id,
            ),
        )
        if claim.kind == "presentation.delete"
        else PresentationModel.lifecycle_status != PRESENTATION_LIFECYCLE_DELETED
    )
    row = (
        await session.execute(
            update(PresentationModel)
            .where(
                PresentationModel.id == claim.presentation_id,
                owner_predicate,
                lifecycle_predicate,
            )
            .values(updated_at=PresentationModel.updated_at)
            .returning(
                PresentationModel.revision,
                PresentationModel.lifecycle_status,
            )
            .execution_options(synchronize_session=False)
        )
    ).one_or_none()
    if row is None:
        lifecycle = (
            await session.execute(
                select(PresentationModel.lifecycle_status).where(
                    PresentationModel.id == claim.presentation_id,
                    owner_predicate,
                )
            )
        ).scalar_one_or_none()
        if lifecycle is None:
            raise NonRetryableWorkspaceOperationError("presentation_not_found")
        if lifecycle == PRESENTATION_LIFECYCLE_DELETED:
            raise NonRetryableWorkspaceOperationError("presentation_deleted")
        raise NonRetryableWorkspaceOperationError("workspace_mutation_admission_conflict")

    revision = int(row.revision)
    if check_revision and claim.expected_revision is not None and revision != claim.expected_revision:
        raise NonRetryableWorkspaceOperationError(
            "revision_conflict",
            detail={
                "error_code": "revision_conflict",
                "revision": revision,
            },
        )
    return revision


async def _published_workspace_operation_result(
    claim: WorkspaceOperationClaim,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> WorkspaceOperationResult | None:
    """Load the canonical result after publication consumes the runner lease.

    Revision publication is permitted to atomically mark an operation as
    succeeded and clear its attempt token.  A heartbeat can observe that
    commit before the handler returns, so losing the lease is not necessarily
    a failed execution.  In that case the durable operation row, not the
    handler's in-flight return value, is the source of truth.
    """

    async with session_factory() as session:
        operation = (
            await session.execute(
                select(WorkspaceOperation)
                .where(WorkspaceOperation.id == claim.operation_id)
                .where(WorkspaceOperation.owner_id == claim.owner_id)
            )
        ).scalar_one_or_none()
    if operation is None or operation.state != "succeeded":
        return None
    payload = operation.result if isinstance(operation.result, Mapping) else {}
    return WorkspaceOperationResult(
        payload=dict(payload),
        result_revision=operation.result_revision,
    )


async def reconcile_succeeded_workspace_operation_events(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    batch_size: int = 100,
) -> int:
    """Backfill terminal result events stranded by an older publisher.

    Current publishers commit the operation projection, presentation revision,
    terminal event, and event outbox atomically.  This startup repair is kept
    for rows written before that invariant existed.  The operation version CAS
    plus the missing-event predicate makes concurrent process startup safe and
    exactly-once.
    """

    repaired = 0
    bounded_batch_size = max(1, int(batch_size))
    while True:
        terminal_event_exists = exists(
            select(WorkspaceOperationEvent.cursor).where(
                WorkspaceOperationEvent.operation_id == WorkspaceOperation.id,
                WorkspaceOperationEvent.event_type == "operation.result",
            )
        )
        async with session_factory() as session:
            candidates = (
                (
                    await session.execute(
                        select(
                            WorkspaceOperation.id,
                            WorkspaceOperation.presentation_id,
                            WorkspaceOperation.conversation_id,
                            WorkspaceOperation.kind,
                            WorkspaceOperation.result,
                            WorkspaceOperation.result_revision,
                            WorkspaceOperation.version,
                            WorkspaceOperation.attempt_number,
                            WorkspaceOperation.retry_count,
                            WorkspaceOperation.fencing_token,
                        )
                        .where(WorkspaceOperation.state == "succeeded")
                        .where(~terminal_event_exists)
                        .order_by(WorkspaceOperation.updated_at, WorkspaceOperation.id)
                        .limit(bounded_batch_size)
                    )
                )
                .mappings()
                .all()
            )

        if not candidates:
            break

        repaired_this_batch = 0
        for candidate in candidates:
            missing_terminal_event = ~exists(
                select(WorkspaceOperationEvent.cursor).where(
                    WorkspaceOperationEvent.operation_id == WorkspaceOperation.id,
                    WorkspaceOperationEvent.event_type == "operation.result",
                )
            )
            result = candidate["result"]
            payload = dict(result) if isinstance(result, Mapping) else {}
            payload["state"] = "succeeded"
            if candidate["result_revision"] is not None:
                payload["result_revision"] = candidate["result_revision"]

            async with session_factory() as session:
                event = await append_operation_event(
                    session,
                    operation_id=candidate["id"],
                    event_type="operation.result",
                    payload=payload,
                    allowed_states=("succeeded",),
                    expected_version=candidate["version"],
                    increment_version=True,
                    additional_conditions=(missing_terminal_event,),
                )
                if event is None:
                    await session.rollback()
                    continue
                await session.commit()

            log_workspace_operation_observation(
                name="recovered",
                operation_id=candidate["id"],
                presentation_id=candidate["presentation_id"],
                conversation_id=candidate["conversation_id"],
                command_type=candidate["kind"],
                result_status="succeeded",
                attempt_number=candidate["attempt_number"],
                retry_count=candidate["retry_count"],
                recovery_count=1,
                result_revision=candidate["result_revision"],
                fencing_token=candidate["fencing_token"],
                fence_outcome="recovery_committed",
            )
            repaired += 1
            repaired_this_batch += 1

        if len(candidates) < bounded_batch_size or repaired_this_batch == 0:
            break

    return repaired


async def finalize_workspace_operation_cancelled(
    claim: WorkspaceOperationClaim,
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> bool:
    now = _utc_now()
    async with session_factory() as session:
        operation = await session.get(WorkspaceOperation, claim.operation_id)
        event = await append_operation_event(
            session,
            operation_id=claim.operation_id,
            event_type="operation.cancelled",
            payload={"state": "cancelled", "reason_code": "user_cancelled"},
            attempt_token=claim.attempt_token,
            allowed_states=_RUNNING_STATES,
            projection_values={
                "state": "cancelled",
                "cancelled_at": now,
                "completed_at": now,
                "attempt_token": None,
                "lease_expires_at": None,
                "heartbeat_at": now,
            },
        )
        if event is None:
            await session.rollback()
            return False
        if operation is not None and not await _project_linked_generation_terminal(
            session,
            operation,
            state="cancelled",
        ):
            await session.rollback()
            return False
        await session.commit()
        _observe_claim(
            claim,
            name="cancelled",
            result_status="cancelled",
            duration_ms=_elapsed_ms(
                operation.created_at if operation is not None else None,
                now,
            ),
            retry_count=operation.retry_count if operation is not None else None,
            fence_outcome="transition_committed",
        )
        return True


async def release_workspace_operation_claim(
    claim: WorkspaceOperationClaim,
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> bool:
    """Release during orderly shutdown without charging a failed retry."""

    now = _utc_now()
    async with session_factory() as session:
        operation = await session.get(WorkspaceOperation, claim.operation_id)
        if (
            operation is None
            or operation.attempt_token != claim.attempt_token
            or operation.state not in _RUNNING_STATES
        ):
            await session.rollback()
            return False
        if operation.cancel_requested_at is not None or operation.state == "cancelling":
            await session.rollback()
            return await finalize_workspace_operation_cancelled(claim, session_factory=session_factory)
        event = await append_operation_event(
            session,
            operation_id=claim.operation_id,
            event_type="operation.progress",
            payload={"state": "queued", "stage": "released"},
            attempt_token=claim.attempt_token,
            allowed_states=("running",),
            projection_values={
                "state": "queued",
                "attempt_token": None,
                "lease_expires_at": None,
                "heartbeat_at": now,
                "next_attempt_at": now,
            },
        )
        if event is None:
            await session.rollback()
            return False
        await session.commit()
        _observe_claim(
            claim,
            name="released",
            result_status="queued",
            duration_ms=_elapsed_ms(operation.created_at, now),
            retry_count=operation.retry_count,
            fence_outcome="released",
        )
        return True


async def defer_workspace_operation_claim(
    claim: WorkspaceOperationClaim,
    deferred: WorkspaceOperationDeferred,
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> bool:
    """Return a capacity-bound claim to the durable queue for free.

    ``attempt_number`` is restored because the handler never acquired scarce
    generation capacity.  The fencing token remains monotonic, while the
    attempt token and lease are cleared atomically so a later poll or another
    worker can safely reclaim the operation.
    """

    now = _utc_now()
    next_attempt_at = now + timedelta(seconds=deferred.retry_after_seconds)
    async with session_factory() as session:
        operation = await session.get(WorkspaceOperation, claim.operation_id)
        if (
            operation is None
            or operation.attempt_token != claim.attempt_token
            or operation.state not in _RUNNING_STATES
        ):
            await session.rollback()
            return False
        if (
            operation.cancel_requested_at is not None
            or operation.state == "cancelling"
        ):
            await session.rollback()
            return await finalize_workspace_operation_cancelled(
                claim,
                session_factory=session_factory,
            )

        raw_detail = dict(deferred.detail)
        queue_position = getattr(deferred, "queue_position", None)
        if isinstance(queue_position, int) and queue_position > 0:
            raw_detail.setdefault("queue_position", queue_position)
        detail = sanitize_workspace_event_payload(raw_detail)
        progress = {
            **detail,
            "state": "queued",
            "status": "queued",
            "stage": "waiting_for_capacity",
            "wait_reason": deferred.code,
            "capacity_waiting": True,
        }
        event = await append_operation_event(
            session,
            operation_id=claim.operation_id,
            event_type="operation.progress",
            payload=progress,
            attempt_token=claim.attempt_token,
            allowed_states=("running",),
            projection_values={
                "state": "queued",
                "attempt_number": max(0, operation.attempt_number - 1),
                "attempt_token": None,
                "lease_expires_at": None,
                "heartbeat_at": now,
                "next_attempt_at": next_attempt_at,
                "backoff_seconds": max(1, math.ceil(deferred.retry_after_seconds)),
                "progress": progress,
                "error_code": None,
                "error_detail": None,
                "started_at": (
                    None if claim.attempt_number == 1 else operation.started_at
                ),
            },
        )
        if event is None:
            await session.rollback()
            if await _operation_cancel_requested(
                claim.operation_id,
                claim.attempt_token,
                session_factory=session_factory,
            ):
                return await finalize_workspace_operation_cancelled(
                    claim,
                    session_factory=session_factory,
                )
            return False
        await session.commit()
        _observe_claim(
            claim,
            name="released",
            result_status="queued",
            duration_ms=_elapsed_ms(operation.created_at, now),
            retry_count=operation.retry_count,
            fence_outcome="capacity_deferred",
        )
        return True


async def request_workspace_operation_cancellation(
    operation_id: uuid.UUID,
    *,
    expected_version: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> bool:
    """Durably request cancellation under the current owner scope."""

    now = _utc_now()
    owner_scope = get_workspace_owner_scope()
    async with session_factory() as session:
        operation = (
            await session.execute(
                select(WorkspaceOperation)
                .where(WorkspaceOperation.id == operation_id)
                .where(WorkspaceOperation.owner_scope == owner_scope)
            )
        ).scalar_one_or_none()
        if operation is None or operation.state in _TERMINAL_STATES:
            await session.rollback()
            return False
        if expected_version is not None and operation.version != expected_version:
            await session.rollback()
            return False
        if operation.state in _DIRECT_CANCEL_STATES:
            event_type = "operation.cancelled"
            next_state = "cancelled"
            projection = {
                "state": next_state,
                "cancel_requested_at": now,
                "cancelled_at": now,
                "completed_at": now,
                "attempt_token": None,
                "lease_expires_at": None,
            }
        elif operation.state == "running":
            event_type = "operation.cancelling"
            next_state = "cancelling"
            projection = {
                "state": next_state,
                "cancel_requested_at": now,
            }
        elif operation.state == "cancelling":
            await session.rollback()
            return True
        else:
            await session.rollback()
            return False
        event = await append_operation_event(
            session,
            operation_id=operation.id,
            event_type=event_type,
            payload={"state": next_state, "reason_code": "user_cancelled"},
            allowed_states=(operation.state,),
            expected_version=operation.version,
            projection_values=projection,
        )
        if event is None:
            await session.rollback()
            return False
        if next_state == "cancelled" and not await _project_linked_generation_terminal(
            session,
            operation,
            state="cancelled",
        ):
            await session.rollback()
            return False
        await session.commit()
        log_workspace_operation_observation(
            name="cancelled" if next_state == "cancelled" else "cancel_requested",
            operation_id=operation.id,
            presentation_id=operation.presentation_id,
            conversation_id=operation.conversation_id,
            command_type=operation.kind,
            result_status=next_state,
            duration_ms=_elapsed_ms(operation.created_at, now),
            attempt_number=operation.attempt_number,
            retry_count=operation.retry_count,
            expected_revision=operation.expected_revision,
            result_revision=operation.result_revision,
            fencing_token=operation.fencing_token,
            fence_outcome="transition_committed",
        )
    notify_workspace_operation_state_change(
        operation_id,
        cancel_requested=True,
    )
    return True


async def recover_expired_workspace_operations(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
) -> int:
    """Recover expired leases with a token/lease CAS against heartbeats."""

    now = _utc_now()
    recovered = 0
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(
                        WorkspaceOperation.id,
                        WorkspaceOperation.presentation_id,
                        WorkspaceOperation.conversation_id,
                        WorkspaceOperation.kind,
                        WorkspaceOperation.request_payload,
                        WorkspaceOperation.state,
                        WorkspaceOperation.attempt_token,
                        WorkspaceOperation.lease_expires_at,
                        WorkspaceOperation.cancel_requested_at,
                        WorkspaceOperation.retry_count,
                        WorkspaceOperation.max_attempts,
                        WorkspaceOperation.attempt_number,
                        WorkspaceOperation.fencing_token,
                        WorkspaceOperation.expected_revision,
                        WorkspaceOperation.result_revision,
                        WorkspaceOperation.version,
                        WorkspaceOperation.created_at,
                        WorkspaceOperation.updated_at,
                    )
                    .where(WorkspaceOperation.state.in_(tuple(_RUNNING_STATES)))
                    .where(
                        or_(
                            WorkspaceOperation.lease_expires_at.is_(None),
                            WorkspaceOperation.lease_expires_at <= now,
                        )
                    )
                    .order_by(WorkspaceOperation.updated_at)
                )
            )
            .mappings()
            .all()
        )

    for stale in rows:
        async with session_factory() as session:
            lease_condition = (
                WorkspaceOperation.lease_expires_at.is_(None)
                if stale["lease_expires_at"] is None
                else and_(
                    WorkspaceOperation.lease_expires_at == stale["lease_expires_at"],
                    WorkspaceOperation.lease_expires_at <= now,
                )
            )
            token_condition = (
                WorkspaceOperation.attempt_token.is_(None)
                if stale["attempt_token"] is None
                else WorkspaceOperation.attempt_token == stale["attempt_token"]
            )
            committed_delete_result: dict[str, Any] | None = None
            if stale["kind"] == "presentation.delete":
                deletion_operation_id = await session.scalar(
                    select(PresentationModel.deletion_operation_id)
                    .where(
                        PresentationModel.id == stale["presentation_id"],
                        PresentationModel.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED,
                    )
                    .execution_options(skip_owner_scope=True)
                )
                if deletion_operation_id == stale["id"]:
                    cleanup_count = int(
                        await session.scalar(
                            select(func.count())
                            .select_from(WorkspaceCleanupJournal)
                            .where(
                                WorkspaceCleanupJournal.presentation_id_snapshot == stale["presentation_id"],
                                WorkspaceCleanupJournal.status.not_in(("succeeded", "dead_letter")),
                            )
                        )
                        or 0
                    )
                    committed_delete_result = {
                        "presentation_id": str(stale["presentation_id"]),
                        "lifecycle_status": PRESENTATION_LIFECYCLE_DELETED,
                        "cleanup_pending": cleanup_count > 0,
                        "deletion_operation_id": str(stale["id"]),
                        "result_revision": stale["expected_revision"],
                    }

            if committed_delete_result is not None:
                # A same-operation tombstone is the durable serialization
                # point for deletion.  It wins over a late cancel marker and
                # legacy approval TTL left behind by the former split commit.
                next_state = "succeeded"
                event_type = "operation.result"
                payload = {"state": next_state, **committed_delete_result}
                projection = {
                    "state": next_state,
                    "result": committed_delete_result,
                    "result_revision": stale["expected_revision"],
                    "attempt_token": None,
                    "lease_expires_at": None,
                    "heartbeat_at": now,
                    "next_attempt_at": None,
                    "approval_expires_at": None,
                    "cancel_requested_at": None,
                    "cancelled_at": None,
                    "error_code": None,
                    "error_detail": None,
                    "completed_at": now,
                }
                observed_retry_count = stale["retry_count"]
            elif stale["state"] == "cancelling" or stale["cancel_requested_at"] is not None:
                next_state = "cancelled"
                event_type = "operation.cancelled"
                payload = {
                    "state": next_state,
                    "reason_code": "cancel_recovered",
                }
                projection = {
                    "state": next_state,
                    "attempt_token": None,
                    "lease_expires_at": None,
                    "heartbeat_at": now,
                    "cancelled_at": now,
                    "completed_at": now,
                }
                observed_retry_count = stale["retry_count"]
            elif registry.recovery_policy(stale["kind"]) == "automatic":
                decision = decide_workspace_operation_retry(
                    operation_id=stale["id"],
                    retry_count=stale["retry_count"],
                    max_attempts=stale["max_attempts"],
                    now=now,
                    retryable=True,
                )
                next_state = decision.state
                event_type = "operation.error"
                payload = {
                    "state": next_state,
                    "error_code": "operation_recovery_required",
                    "retryable": True,
                    "retry_count": decision.retry_count,
                    "max_attempts": stale["max_attempts"],
                    "backoff_seconds": decision.backoff_seconds,
                    "next_attempt_at": decision.next_attempt_at,
                }
                projection = {
                    "state": next_state,
                    "retry_count": decision.retry_count,
                    "backoff_seconds": decision.backoff_seconds,
                    "next_attempt_at": decision.next_attempt_at,
                    "attempt_token": None,
                    "lease_expires_at": None,
                    "heartbeat_at": now,
                    "error_code": "operation_recovery_required",
                    "error_detail": {
                        "error_code": "operation_recovery_required",
                        "retryable": True,
                    },
                    "completed_at": now if next_state in {"failed", "dead_letter"} else None,
                }
                observed_retry_count = decision.retry_count
            else:
                # An expired lease proves that the prior worker's completion
                # is unknown.  Handlers that have not explicitly opted into
                # replay-safe recovery must never be executed a second time.
                next_state = "failed"
                event_type = "operation.error"
                payload = {
                    "state": next_state,
                    "error_code": "operation_recovery_required",
                    "retryable": False,
                    "retry_count": stale["retry_count"],
                    "max_attempts": stale["max_attempts"],
                    "reason_code": "manual_recovery_required",
                }
                projection = {
                    "state": next_state,
                    "attempt_token": None,
                    "lease_expires_at": None,
                    "heartbeat_at": now,
                    "next_attempt_at": None,
                    # The column is NOT NULL on both supported runtimes.  A
                    # terminal manual-recovery decision has no pending delay,
                    # so persist the neutral value instead of SQL NULL.
                    "backoff_seconds": 0,
                    "error_code": "operation_recovery_required",
                    "error_detail": {
                        "error_code": "operation_recovery_required",
                        "retryable": False,
                        "reason_code": "manual_recovery_required",
                    },
                    "completed_at": now,
                }
                observed_retry_count = stale["retry_count"]
            event = await append_operation_event(
                session,
                operation_id=stale["id"],
                event_type=event_type,
                payload=payload,
                operation_kind=stale["kind"],
                allowed_states=(stale["state"],),
                expected_version=stale["version"],
                projection_values=projection,
                additional_conditions=(lease_condition, token_condition),
            )
            if event is None:
                await session.rollback()
                continue
            if next_state in {"cancelled", "failed", "dead_letter"}:
                operation = await session.get(WorkspaceOperation, stale["id"])
                if operation is None or not await _project_linked_generation_terminal(
                    session,
                    operation,
                    state=next_state,
                    error_code=(None if next_state == "cancelled" else "operation_recovery_required"),
                ):
                    await session.rollback()
                    continue
            elif next_state == "queued":
                operation = await session.get(WorkspaceOperation, stale["id"])
                if operation is None or not await _project_linked_generation_recovered(
                    session,
                    operation,
                    expected_attempt_token=stale["attempt_token"],
                ):
                    await session.rollback()
                    continue
            await session.commit()
            log_workspace_operation_observation(
                name="recovered",
                operation_id=stale["id"],
                presentation_id=stale["presentation_id"],
                conversation_id=stale["conversation_id"],
                command_type=stale["kind"],
                result_status=next_state,
                error_code=(None if next_state in {"cancelled", "succeeded"} else "operation_recovery_required"),
                duration_ms=_elapsed_ms(stale["created_at"], now),
                attempt_number=stale["attempt_number"],
                retry_count=observed_retry_count,
                recovery_count=1,
                expected_revision=stale["expected_revision"],
                result_revision=(stale["expected_revision"] if next_state == "succeeded" else stale["result_revision"]),
                fencing_token=stale["fencing_token"],
                fence_outcome="recovery_committed",
            )
            recovered += 1
    if recovered:
        wake_workspace_operation_runner()
    return recovered


class _ExecutionContext(WorkspaceOperationExecutionContext):
    def __init__(
        self,
        claim: WorkspaceOperationClaim,
        session_factory: async_sessionmaker[AsyncSession],
        lease_seconds: int = WORKSPACE_OPERATION_LEASE_SECONDS,
    ) -> None:
        self.claim = claim
        self._session_factory = session_factory
        self._lease_seconds = lease_seconds
        # Widened by `extend_lease`, read by the heartbeat loop, so the window a
        # handler asked for is not narrowed back on the next renewal.
        self.lease_seconds_override: int | None = None

    async def extend_lease(self, *, seconds: int) -> bool:
        """Widen this attempt's lease before taking a long write lock."""
        granted = await extend_workspace_operation_lease(
            self.claim.operation_id,
            self.claim.attempt_token,
            session_factory=self._session_factory,
            lease_seconds=seconds,
            minimum_seconds=self._lease_seconds,
        )
        if granted is None:
            return False
        self.lease_seconds_override = granted
        return True

    async def progress(
        self,
        *,
        stage: str,
        current: int | None = None,
        total: int | None = None,
        percent: int | None = None,
        message: str | None = None,
    ) -> bool:
        progress = {
            "stage": stage,
            "current": current,
            "total": total,
            "percent": percent,
        }
        event_payload = {"state": "running", **progress}
        if message:
            event_payload["message"] = message
        try:
            async with self._session_factory() as session:
                event = await append_operation_event(
                    session,
                    operation_id=self.claim.operation_id,
                    event_type="operation.progress",
                    payload=event_payload,
                    attempt_token=self.claim.attempt_token,
                    allowed_states=("running",),
                    projection_values={"progress": sanitize_workspace_event_payload(progress)},
                )
                if event is None:
                    await session.rollback()
                    return False
                await session.commit()
                return True
        except OperationalError:
            # Progress is advisory telemetry written from its own session, so a
            # long-running handler that holds the write lock can time it out.
            # Losing a progress row must not destroy an otherwise healthy
            # attempt: only `event is None` means the lease was actually taken,
            # and publication still enforces fencing on the attempt token.
            LOGGER.warning(
                "Progress not recorded for operation %s at stage %s",
                self.claim.operation_id,
                stage,
                exc_info=True,
            )
            return True

    async def emit_durable_event(
        self,
        *,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> bool:
        """Persist one public runtime checkpoint under the active attempt fence."""

        if event_type not in _PUBLIC_RUNTIME_EVENT_TYPES:
            raise ValueError("unsupported public runtime event type")
        try:
            async with self._session_factory() as session:
                event = await append_operation_event(
                    session,
                    operation_id=self.claim.operation_id,
                    event_type=event_type,
                    payload={"state": "running", **dict(payload)},
                    operation_kind=self.claim.kind,
                    attempt_token=self.claim.attempt_token,
                    allowed_states=("running",),
                )
                if event is None:
                    await session.rollback()
                    return False
                await session.commit()
                return True
        except OperationalError:
            # Runtime checkpoints are recoverability hints. Publication and
            # completion still use the operation/presentation CAS fence, so a
            # transient writer-lock timeout must not invalidate the run.
            LOGGER.warning(
                "Runtime event %s not recorded for operation %s",
                event_type,
                self.claim.operation_id,
                exc_info=True,
            )
            return True

    async def cancellation_requested(self) -> bool:
        return await _operation_cancel_requested(
            self.claim.operation_id,
            self.claim.attempt_token,
            session_factory=self._session_factory,
        )

    async def raise_if_cancelled(self) -> None:
        if await self.cancellation_requested():
            raise WorkspaceOperationCancelled("workspace_operation_cancelled")


class WorkspaceOperationRunner:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
        registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
        max_concurrency: int | None = None,
        lease_seconds: int | None = None,
        heartbeat_seconds: int | None = None,
        poll_seconds: float | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self.max_concurrency = max_concurrency or _positive_int_env(
            "WORKSPACE_OPERATION_MAX_CONCURRENCY",
            WORKSPACE_OPERATION_MAX_CONCURRENCY,
        )
        self.lease_seconds = lease_seconds or _positive_int_env(
            "WORKSPACE_OPERATION_LEASE_SECONDS", WORKSPACE_OPERATION_LEASE_SECONDS
        )
        self.heartbeat_seconds = heartbeat_seconds or _positive_int_env(
            "WORKSPACE_OPERATION_HEARTBEAT_SECONDS",
            WORKSPACE_OPERATION_HEARTBEAT_SECONDS,
        )
        self.poll_seconds = poll_seconds or _positive_float_env(
            "WORKSPACE_OPERATION_POLL_SECONDS", WORKSPACE_OPERATION_POLL_SECONDS
        )
        self._loop_task: asyncio.Task[None] | None = None
        self._active: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._wake = asyncio.Event()
        self._stopping = False

    @property
    def active_operation_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(self._active)

    async def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._stopping = False
        await reconcile_succeeded_workspace_operation_events(
            session_factory=self.session_factory,
        )
        await recover_expired_workspace_operations(
            session_factory=self.session_factory,
            registry=self.registry,
        )
        self._loop_task = asyncio.create_task(self._run_loop(), name="workspace-operation-runner")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._loop_task is not None:
            # Let the dispatcher observe ``_stopping`` after waking instead of
            # cancelling ``asyncio.wait_for(Event.wait())``.  Cancelling that
            # nested wait can strand Python 3.11's cancellation handshake and
            # make application shutdown hang indefinitely.
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None
        tasks = tuple(self._active.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active.clear()

    def wake(self) -> None:
        self._wake.set()

    def cancel_local_attempt(self, operation_id: uuid.UUID) -> None:
        task = self._active.get(operation_id)
        if task is not None:
            task.cancel()

    async def dispatch_once(self) -> int:
        dispatched = 0
        while len(self._active) < self.max_concurrency:
            claim = await claim_next_workspace_operation(
                session_factory=self.session_factory,
                lease_seconds=self.lease_seconds,
            )
            if claim is None:
                break
            task = asyncio.create_task(
                self._execute(claim),
                name=f"workspace-operation-{claim.operation_id}",
            )
            self._active[claim.operation_id] = task

            def _discard(done: asyncio.Task[None], operation_id: uuid.UUID = claim.operation_id) -> None:
                if self._active.get(operation_id) is done:
                    self._active.pop(operation_id, None)
                self._wake.set()

            task.add_done_callback(_discard)
            dispatched += 1
        return dispatched

    async def _wait_for_wake(self) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
        except TimeoutError:
            pass
        self._wake.clear()

    async def _run_loop(self) -> None:
        while not self._stopping:
            try:
                await recover_expired_workspace_operations(
                    session_factory=self.session_factory,
                    registry=self.registry,
                )
                await self.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A malformed row or transient database failure must not kill
                # the durable dispatcher.  Polling is the recovery path.
                log_workspace_operation_observation(
                    name="runner_fault",
                    result_status="running",
                    error_code="workspace_operation_internal_error",
                    fence_outcome="not_applicable",
                )
            if self._stopping:
                break
            await self._wait_for_wake()

    async def _heartbeat_loop(
        self, claim: WorkspaceOperationClaim, context: _ExecutionContext
    ) -> None:
        await run_attempt_heartbeat_loop(
            claim,
            session_factory=self.session_factory,
            heartbeat_seconds=self.heartbeat_seconds,
            poll_seconds=self.poll_seconds,
            lease_seconds=lambda: context.lease_seconds_override or self.lease_seconds,
        )

    async def _call_handler(
        self,
        handler: Any,
        context: _ExecutionContext,
    ) -> WorkspaceOperationResult:
        target = handler.execute if hasattr(handler, "execute") else handler
        value = target(context)
        if inspect.isawaitable(value):
            value = await value
        if value is None:
            return WorkspaceOperationResult()
        if isinstance(value, WorkspaceOperationResult):
            return value
        if isinstance(value, Mapping):
            return WorkspaceOperationResult(payload=dict(value))
        raise NonRetryableWorkspaceOperationError("invalid_operation_handler_result")

    async def _validate_claim_revision(self, claim: WorkspaceOperationClaim) -> None:
        """Recheck deck/account deletion fences immediately before execution."""
        async with self.session_factory() as session:
            await _fence_workspace_claim(
                session,
                claim,
                check_revision=True,
            )

    async def _execute(self, claim: WorkspaceOperationClaim) -> None:
        # asyncio tasks copy contextvars at creation time.  Establish the
        # durable operation owner before creating either task so ORM owner
        # criteria and all downstream generation services execute in the same
        # tenant scope as the originating request.
        owner_token = set_current_owner_id(claim.owner_id)
        heartbeat: asyncio.Task[None] | None = None
        handler_task: asyncio.Task[WorkspaceOperationResult] | None = None
        try:
            context = _ExecutionContext(
                claim, self.session_factory, lease_seconds=self.lease_seconds
            )
            handler = self.registry.resolve(claim.kind)
            if handler is None:
                await _transition_claim_failure(
                    claim,
                    code="unsupported_operation_type",
                    retryable=False,
                    session_factory=self.session_factory,
                )
                return
            try:
                await self._validate_claim_revision(claim)
            except WorkspaceOperationExecutionError as error:
                await _transition_claim_failure(
                    claim,
                    code=error.code,
                    retryable=error.retryable,
                    detail=error.detail,
                    session_factory=self.session_factory,
                )
                return

            heartbeat = asyncio.create_task(self._heartbeat_loop(claim, context))
            handler_task = create_runtime_settings_task(self._call_handler(handler, context))
            done, _ = await asyncio.wait((handler_task, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                try:
                    heartbeat.result()
                except WorkspaceOperationCancelled:
                    handler_task.cancel()
                    # Fence the durable attempt before waiting for handler
                    # cleanup.  A handler that delays or swallows task
                    # cancellation must not keep the operation in
                    # `cancelling` or later publish a false success.
                    await finalize_workspace_operation_cancelled(claim, session_factory=self.session_factory)
                    await asyncio.gather(handler_task, return_exceptions=True)
                    return
                except WorkspaceOperationLeaseLost:
                    published_result = await _published_workspace_operation_result(
                        claim, session_factory=self.session_factory
                    )
                    if published_result is not None:
                        await finalize_workspace_operation_success(
                            claim,
                            published_result,
                            session_factory=self.session_factory,
                        )
                        return
                    raise
                raise WorkspaceOperationLeaseLost("workspace_operation_heartbeat_stopped")
            result = handler_task.result()
            finalized = await finalize_workspace_operation_success(claim, result, session_factory=self.session_factory)
            if not finalized and await context.cancellation_requested():
                await finalize_workspace_operation_cancelled(claim, session_factory=self.session_factory)
        except WorkspaceOperationDeferred as deferred:
            await asyncio.shield(
                defer_workspace_operation_claim(
                    claim,
                    deferred,
                    session_factory=self.session_factory,
                )
            )
        except WorkspaceOperationBlocked as error:
            await _transition_claim_blocked(claim, error, session_factory=self.session_factory)
        except WorkspaceOperationCancelled:
            await finalize_workspace_operation_cancelled(claim, session_factory=self.session_factory)
        except WorkspaceOperationLeaseLost:
            if handler_task is not None:
                handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)
            _observe_claim(
                claim,
                name="lease_lost",
                result_status="running",
                error_code="workspace_operation_lease_lost",
                revision_conflict=False,
                fence_outcome="lease_lost",
            )
        except WorkspaceOperationExecutionError as error:
            await _transition_claim_failure(
                claim,
                code=error.code,
                retryable=error.retryable,
                detail=error.detail,
                session_factory=self.session_factory,
            )
        except asyncio.CancelledError:
            cancellation_requested = await _operation_cancel_requested(
                claim.operation_id,
                claim.attempt_token,
                session_factory=self.session_factory,
            )
            if handler_task is not None:
                handler_task.cancel()
            if cancellation_requested:
                # `cancel_local_attempt` cancels this coordinator task.  Fence
                # the durable attempt before waiting for handler cleanup for
                # the same reason as the cross-process heartbeat path.
                await asyncio.shield(
                    finalize_workspace_operation_cancelled(claim, session_factory=self.session_factory)
                )
                if handler_task is not None:
                    await asyncio.gather(handler_task, return_exceptions=True)
            else:
                # During runner shutdown preserve the existing ordering: stop
                # handler work before making the claim available for replay.
                if handler_task is not None:
                    await asyncio.gather(handler_task, return_exceptions=True)
                await asyncio.shield(release_workspace_operation_claim(claim, session_factory=self.session_factory))
            raise
        except Exception:
            await _transition_claim_failure(
                claim,
                code="workspace_operation_internal_error",
                retryable=True,
                session_factory=self.session_factory,
            )
        finally:
            pending: list[asyncio.Task[Any]] = []
            if heartbeat is not None:
                heartbeat.cancel()
                pending.append(heartbeat)
            if handler_task is not None:
                handler_task.cancel()
                pending.append(handler_task)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            reset_current_owner_id(owner_token)


_workspace_operation_runner: WorkspaceOperationRunner | None = None


async def start_workspace_operation_runner() -> WorkspaceOperationRunner:
    global _workspace_operation_runner
    if _workspace_operation_runner is None:
        _workspace_operation_runner = WorkspaceOperationRunner()
    await _workspace_operation_runner.start()
    return _workspace_operation_runner


async def stop_workspace_operation_runner() -> None:
    global _workspace_operation_runner
    if _workspace_operation_runner is None:
        return
    await _workspace_operation_runner.stop()
    _workspace_operation_runner = None


def wake_workspace_operation_runner() -> None:
    if _workspace_operation_runner is not None:
        _workspace_operation_runner.wake()


def notify_workspace_operation_state_change(
    operation_id: uuid.UUID,
    *,
    cancel_requested: bool = False,
) -> None:
    """Best-effort local signal layered over the durable DB/outbox state."""

    wake_workspace_operation_runner()
    if cancel_requested and _workspace_operation_runner is not None:
        _workspace_operation_runner.cancel_local_attempt(operation_id)
