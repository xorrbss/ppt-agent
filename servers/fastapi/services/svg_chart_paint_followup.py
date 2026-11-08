"""Background continuation of the v5 native chart paint verification.

One ``export.pptx`` verifies only the charts its admitted deadline leaves room
for (about three real charts, ``svg_chart_paint_verification``). The rest stay
drawn in that artifact. This module continues the remaining verification as a
separate, durable ``svg.chart.verify`` workspace operation, so the export the
user waits for keeps its length:

* a successful v5 ``export.pptx`` whose bundle still kept charts drawn as
  unverified enqueues round 1 (``enqueue_after_export``);
* each round runs the unchanged proof (``verify_slide_chart_paint``) page by
  page, cheapest capture first (``order_by_verification_cost``), while its
  own budget allows, stores the same
  digest-bound evidence in ``slide.properties`` and re-enqueues the next round
  only when it was cut short with pages left, up to ``MAX_ROUNDS``;
* when the chain ends and at least one additional chart passed, it submits one
  refreshed ``export.pptx`` with the source export's options, so the newest
  artifact carries the newly native charts.

Bounds: the idempotency key is bound to (presentation, revision, round), so a
revision has at most ``MAX_ROUNDS`` follow-ups, and no follow-up is enqueued
while another one of the presentation is in flight. A later manual export at
the same revision continues with the next unused round; a chain's own
refreshed export (keyed by its final round) starts nothing. A deck revision change
stops the chain without applying anything (evidence is digest-bound anyway).
A capture error or timeout stores nothing (as in the export) and only counts
as unverified; a round that attempted every remaining page does not
re-enqueue, so such pages cannot loop. A round yields to queued user work on
the same presentation (the runner executes one operation per presentation at
a time) and resumes in the next round. Every non-v5 deck enqueues nothing.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Mapping

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

LOGGER = logging.getLogger(__name__)

OPERATION_KIND = "svg.chart.verify"
MAX_ROUNDS = 5
# One round's verification budget. A capture needs up to the renderer timeout
# plus the client grace (145 s); real charts take 66-117 s, so a round covers
# about four to six charts while the presentation's other work waits at most
# one capture (``_user_work_queued``).
ROUND_SECONDS = 600
_LEASE_MARGIN_SECONDS = 120
_IN_FLIGHT_STATES = ("queued", "waiting_for_assets", "blocked", "running", "cancelling",
                     "approval_required", "awaiting_approval")
_EXPORT_OPTION_KEYS = ("font_embedding", "quality_policy")
REFRESH_EXPORT_COMMAND_TEXT = "Export presentation as PowerPoint"


def followup_idempotency_key(*, presentation_id: uuid.UUID, revision: int, round_number: int) -> str:
    from utils.hashing import sha256_hex
    digest = sha256_hex(f"{OPERATION_KIND}:{presentation_id}:{revision}:{round_number}".encode("utf-8"))
    return f"svg-chart-verify:{digest}"


def refresh_export_idempotency_key(*, presentation_id: uuid.UUID, revision: int, round_number: int) -> str:
    """One refreshed export per (presentation, revision, final round of a chain)."""
    from utils.hashing import sha256_hex
    digest = sha256_hex(f"{OPERATION_KIND}.refresh:{presentation_id}:{revision}:{round_number}".encode("utf-8"))
    return f"svg-chart-verify-export:{digest}"


def refresh_export_context(request_payload: Mapping[str, Any]) -> dict:
    """The source export's options, as the context of a refreshed command."""
    context = request_payload.get("context")
    result = dict(context) if isinstance(context, Mapping) else {}
    for key in _EXPORT_OPTION_KEYS:
        if key in request_payload and key not in result:
            result[key] = request_payload[key]
    return result


def unverified_chart_pages(chart_paint_fallbacks) -> int:
    """How many pages an export kept drawn only because evidence was missing."""
    from services.generation_core.drivers.svg.chart_paint_verification import UNVERIFIED_REASON
    return sum(1 for page in chart_paint_fallbacks or ()
               if any(isinstance(entry, Mapping) and entry.get("reason") == UNVERIFIED_REASON
                      for entry in page or ()))


async def enqueue_chart_paint_followup(session_factory, *, owner_id, owner_scope: str, conversation_id: uuid.UUID,
                                       presentation_id: uuid.UUID, revision: int, round_number: int,
                                       passed_so_far: int, export_context: Mapping[str, Any],
                                       source_export_operation_id: uuid.UUID,
                                       account_deletion_generation=None, tenant_payload: Mapping[str, str] | None = None,
                                       exclude_operation_id: uuid.UUID | None = None) -> uuid.UUID | None:
    """Insert one queued follow-up round, or return ``None`` when it must not exist.

    Nothing is inserted when the round exceeds ``MAX_ROUNDS``, the presentation
    left ``revision``, another follow-up of the presentation is in flight, or
    this (presentation, revision, round) already has one.
    """
    if round_number > MAX_ROUNDS:
        return None
    from models.sql.presentation import PresentationModel
    from models.sql.workspace import WorkspaceOperation
    from services.workspace_db_adapter import reserve_conversation_positions
    from services.workspace_service import (
        _enqueue_event_notification, append_workspace_operation_event, canonical_payload_hash,
        workspace_idempotency_hash,
    )
    key_hash = workspace_idempotency_hash(followup_idempotency_key(
        presentation_id=presentation_id, revision=revision, round_number=round_number))
    payload = {
        "presentation_id": str(presentation_id),
        "command_type": OPERATION_KIND,
        "source_revision": revision,
        "round": round_number,
        "passed_so_far": passed_so_far,
        "export_context": dict(export_context),
        "source_export_operation_id": str(source_export_operation_id),
    }
    async with session_factory() as session:
        try:
            if session.get_bind().dialect.name == "sqlite":
                # Read-then-insert: take the writer lock first, as the claim
                # does, so a concurrent writer cannot turn the lock upgrade
                # into an immediate SQLITE_BUSY.
                await session.execute(text("BEGIN IMMEDIATE"))
            current = await session.scalar(select(PresentationModel.revision).where(
                PresentationModel.id == presentation_id))
            if current != revision:
                return None
            in_flight = select(WorkspaceOperation.id).where(
                WorkspaceOperation.presentation_id == presentation_id,
                WorkspaceOperation.kind == OPERATION_KIND,
                WorkspaceOperation.state.in_(_IN_FLIGHT_STATES))
            if exclude_operation_id is not None:
                in_flight = in_flight.where(WorkspaceOperation.id != exclude_operation_id)
            if await session.scalar(in_flight.limit(1)) is not None:
                return None
            if await session.scalar(select(WorkspaceOperation.id).where(
                    WorkspaceOperation.owner_scope == owner_scope,
                    WorkspaceOperation.presentation_id == presentation_id,
                    WorkspaceOperation.kind == OPERATION_KIND,
                    WorkspaceOperation.idempotency_key_hash == key_hash).limit(1)) is not None:
                return None
            (position,) = (await reserve_conversation_positions(session, conversation_id, 1)).positions
            operation = WorkspaceOperation(
                owner_id=owner_id, owner_scope=owner_scope, conversation_id=conversation_id,
                presentation_id=presentation_id, anchor_position=position, kind=OPERATION_KIND,
                state="queued", idempotency_key_hash=key_hash, request_hash=canonical_payload_hash(payload),
                request_payload={
                    **payload,
                    "account_deletion_generation": account_deletion_generation,
                    "initial_operation_state": "queued",
                    **dict(tenant_payload or {}),
                },
                # The handler checks the revision itself and ends a stale round
                # as a no-op instead of a failed timeline item.
                expected_revision=None,
                # A crashed or lease-lost round is not retried: the bound on
                # rounds is the only re-entry.
                max_attempts=1,
            )
            session.add(operation)
            await session.flush()
            event = await append_workspace_operation_event(
                session, operation=operation, event_type="queued", payload={"status": "queued", "message": "Queued."})
            await _enqueue_event_notification(session, operation=operation, event=event)
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return None
    from services.workspace_operation_runner import wake_workspace_operation_runner
    wake_workspace_operation_runner()
    return operation.id


async def _round_after_export(session_factory, *, claim) -> int | None:
    """The next free round of ``claim``'s revision, or ``None`` when none may start.

    A chain's own refreshed export never starts another chain. Any other export
    (the first one, or a later manual one at the same revision) continues with
    the round after the last one used, so a revision still has at most
    ``MAX_ROUNDS`` rounds in total.
    """
    from models.sql.workspace import WorkspaceOperation
    from services.workspace_service import workspace_idempotency_hash
    presentation_id, revision = claim.presentation_id, claim.expected_revision
    rounds = range(1, MAX_ROUNDS + 1)
    refresh_hashes = {workspace_idempotency_hash(refresh_export_idempotency_key(
        presentation_id=presentation_id, revision=revision, round_number=number)) for number in rounds}
    round_hashes = {workspace_idempotency_hash(followup_idempotency_key(
        presentation_id=presentation_id, revision=revision, round_number=number)): number for number in rounds}
    async with session_factory() as session:
        source = await session.scalar(select(WorkspaceOperation.idempotency_key_hash).where(
            WorkspaceOperation.id == claim.operation_id))
        if source in refresh_hashes:
            return None
        used = (await session.execute(select(WorkspaceOperation.idempotency_key_hash).where(
            WorkspaceOperation.owner_scope == claim.owner_scope,
            WorkspaceOperation.presentation_id == presentation_id,
            WorkspaceOperation.kind == OPERATION_KIND,
            WorkspaceOperation.idempotency_key_hash.in_(tuple(round_hashes))))).scalars().all()
    following = max((round_hashes[value] for value in used), default=0) + 1
    return following if following <= MAX_ROUNDS else None


async def enqueue_after_export(session_factory, *, claim, chart_paint_fallbacks) -> uuid.UUID | None:
    """The next round after a successful v5 ``export.pptx`` that kept charts unverified.

    Never raises: a missing follow-up only leaves the charts drawn, as before.
    """
    if chart_paint_fallbacks is None or not unverified_chart_pages(chart_paint_fallbacks):
        return None
    try:
        from services.workspace_operation_principal import submitting_principal_tenant
        round_number = await _round_after_export(session_factory, claim=claim)
        if round_number is None:
            return None
        return await enqueue_chart_paint_followup(
            session_factory, owner_id=claim.owner_id, owner_scope=claim.owner_scope,
            conversation_id=claim.conversation_id, presentation_id=claim.presentation_id,
            revision=claim.expected_revision, round_number=round_number, passed_so_far=0,
            export_context=refresh_export_context(claim.request_payload),
            source_export_operation_id=claim.operation_id,
            account_deletion_generation=claim.request_payload.get("account_deletion_generation"),
            tenant_payload=submitting_principal_tenant())
    except Exception:
        LOGGER.warning("SVG chart paint follow-up for presentation %s was not enqueued", claim.presentation_id,
                       exc_info=True)
        return None


async def submit_refresh_export(session_factory, *, presentation_id: uuid.UUID, revision: int, round_number: int,
                                export_context: Mapping[str, Any]) -> uuid.UUID | None:
    """Submit one refreshed ``export.pptx`` at ``revision``; ``None`` when refused."""
    from models.workspace import WorkspaceCommandRequest
    from services.workspace_service import create_workspace_command
    try:
        async with session_factory() as session:
            operation, _duplicate = await create_workspace_command(session, presentation_id=presentation_id,
                request=WorkspaceCommandRequest(
                    command=REFRESH_EXPORT_COMMAND_TEXT, command_type="export.pptx",
                    idempotency_key=refresh_export_idempotency_key(presentation_id=presentation_id,
                                                                   revision=revision, round_number=round_number),
                    expected_revision=revision, context=dict(export_context), asset_ids=[]))
            await session.commit()
    except Exception:
        LOGGER.warning("Refreshed export after chart verification of presentation %s was not submitted",
                       presentation_id, exc_info=True)
        return None
    from services.workspace_operation_runner import wake_workspace_operation_runner
    wake_workspace_operation_runner()
    return operation.id


class SvgChartPaintVerificationOperationHandler:
    """Run one bounded round of the deferred v5 chart paint verification."""

    def __init__(self, *, session_factory=None, render=None,
                 round_seconds: int = ROUND_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._session_factory = session_factory
        self._render = render
        self._round_seconds = round_seconds
        self._clock = clock

    def _sessions(self):
        if self._session_factory is not None:
            return self._session_factory
        from services.database import async_session_maker
        return async_session_maker

    async def execute(self, context):
        from services.workspace_operation_handler_registry import WorkspaceOperationResult
        return WorkspaceOperationResult(payload=await self._run(context))

    async def _revision(self, sessions, presentation_id):
        from models.sql.presentation import PresentationModel
        async with sessions() as session:
            return await session.scalar(select(PresentationModel.revision).where(
                PresentationModel.id == presentation_id))

    async def _user_work_queued(self, sessions, claim) -> bool:
        from models.sql.workspace import WorkspaceOperation
        async with sessions() as session:
            return await session.scalar(select(WorkspaceOperation.id).where(
                WorkspaceOperation.presentation_id == claim.presentation_id,
                WorkspaceOperation.id != claim.operation_id,
                WorkspaceOperation.kind != OPERATION_KIND,
                WorkspaceOperation.state == "queued").limit(1)) is not None

    async def _run(self, context) -> dict:
        from models.sql.presentation import PresentationModel
        from models.sql.slide import SlideModel
        from services.generation_core.drivers.svg.chart_paint_verification import VERIFICATION_TIMEOUT_SECONDS
        from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
        from services.svg_chart_paint_verification import (
            CLIENT_GRACE_SECONDS, deck_requirements_revision, order_by_verification_cost, slide_chart_paint_pending,
            store_chart_paint_evidence, verify_slide_chart_paint,
        )
        from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError
        claim, sessions = context.claim, self._sessions()
        payload = claim.request_payload
        revision, round_number = payload.get("source_revision"), payload.get("round")
        passed_so_far = payload.get("passed_so_far")
        if (type(revision) is not int or type(round_number) is not int or not 1 <= round_number <= MAX_ROUNDS
                or type(passed_so_far) is not int or passed_so_far < 0
                or not isinstance(payload.get("export_context"), Mapping)):
            raise NonRetryableWorkspaceOperationError("svg_chart_verify_payload_invalid")
        result: dict[str, Any] = {"round": round_number, "charts_passed": 0, "charts_failed": 0,
                                  "pages_unverified": 0, "pages_remaining": 0}
        await context.raise_if_cancelled()
        started = self._clock()
        async with sessions() as session:
            presentation = await session.get(PresentationModel, claim.presentation_id)
            if presentation is None or presentation.owner_id != claim.owner_id:
                raise NonRetryableWorkspaceOperationError("presentation_not_found")
            if presentation.revision != revision:
                return {**result, "outcome": "stale"}
            slides = (await session.execute(select(SlideModel).where(
                SlideModel.presentation == claim.presentation_id).order_by(SlideModel.index, SlideModel.id))
            ).scalars().all()
        requirements = deck_requirements_revision(presentation)
        if (requirements != REQUIREMENTS_VERSION_V5 or not slides
                or any(slide.source_format != "svg" or not slide.svg_content or slide.owner_id != claim.owner_id
                       for slide in slides)):
            return {**result, "outcome": "not_applicable"}
        pending = order_by_verification_cost(
            [slide for slide in slides if slide_chart_paint_pending(slide, revision=requirements)])
        await context.extend_lease(seconds=self._round_seconds + _LEASE_MARGIN_SECONDS)
        per_page = VERIFICATION_TIMEOUT_SECONDS + CLIENT_GRACE_SECONDS
        attempted, cut = 0, None
        for ordinal, slide in enumerate(pending):
            await context.raise_if_cancelled()
            if await self._revision(sessions, claim.presentation_id) != revision:
                return {**result, "outcome": "stale"}
            if self._round_seconds - (self._clock() - started) < per_page:
                cut = "budget"
                break
            if await self._user_work_queued(sessions, claim):
                cut = "yielded"
                break
            await context.progress(stage="chart_verification", current=ordinal, total=len(pending),
                                   percent=int(100 * ordinal / max(1, len(pending))))
            async with sessions() as session:
                record = await verify_slide_chart_paint(
                    session, presentation, slide, capture_timeout=VERIFICATION_TIMEOUT_SECONDS,
                    **({"render": self._render} if self._render is not None else {}))
            attempted += 1
            if record is None or not await store_chart_paint_evidence(sessions, slide, record):
                result["pages_unverified"] += 1
            elif (record.get("paint") or {}).get("passed") is True:
                result["charts_passed"] += len(record.get("chartIds") or ())
            else:
                result["charts_failed"] += len(record.get("chartIds") or ())
        result["pages_remaining"] = len(pending) - attempted
        passed_total = passed_so_far + result["charts_passed"]
        await context.raise_if_cancelled()
        if await self._revision(sessions, claim.presentation_id) != revision:
            return {**result, "outcome": "stale"}
        if cut is not None and result["pages_remaining"] and round_number < MAX_ROUNDS:
            next_id = await enqueue_chart_paint_followup(
                sessions, owner_id=claim.owner_id, owner_scope=claim.owner_scope,
                conversation_id=claim.conversation_id, presentation_id=claim.presentation_id,
                revision=revision, round_number=round_number + 1, passed_so_far=passed_total,
                export_context=payload["export_context"],
                source_export_operation_id=uuid.UUID(str(payload.get("source_export_operation_id"))),
                account_deletion_generation=payload.get("account_deletion_generation"),
                tenant_payload=_tenant_payload(payload), exclude_operation_id=claim.operation_id)
            if next_id is not None:
                return {**result, "outcome": "continued", "next_operation_id": str(next_id)}
        if passed_total <= 0:
            return {**result, "outcome": "completed"}
        export_id = await submit_refresh_export(sessions, presentation_id=claim.presentation_id,
                                                revision=revision, round_number=round_number,
                                                export_context=payload["export_context"])
        return {**result, "outcome": "completed", "charts_passed_total": passed_total,
                **({"refresh_export_operation_id": str(export_id)} if export_id is not None else {})}


def _tenant_payload(payload: Mapping[str, Any]) -> dict:
    from services.workspace_operation_principal import PRINCIPAL_TENANT_PAYLOAD_KEY
    tenant = payload.get(PRINCIPAL_TENANT_PAYLOAD_KEY)
    return {PRINCIPAL_TENANT_PAYLOAD_KEY: tenant} if isinstance(tenant, str) and tenant else {}


__all__ = ["MAX_ROUNDS", "OPERATION_KIND", "ROUND_SECONDS", "SvgChartPaintVerificationOperationHandler",
           "enqueue_after_export", "enqueue_chart_paint_followup", "submit_refresh_export"]
