from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import logging
import sys
from time import perf_counter
import uuid

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services.database import async_session_maker
from services.export_task_service import EXPORT_TASK_SERVICE
from services.template_v2_pptx_retention_service import (
    maybe_cleanup_expired_private_sources,
    terminal_source_retention,
)
from services.template_v2_pptx_observability import (
    log_pptx_analysis_observation,
)
from services.template_v2_pptx_queue_observability import (
    log_pptx_queue_observation,
)
from services import template_v2_pptx_dispatcher as _dispatcher_runtime
from services.template_v2_pptx_analysis_service import (
    analysis_observation as _analysis_observation,
    analysis_source_inventory as _analysis_source_inventory,
    analyze_import_source as _analyze_import_source,
    analyze_import_source_via_runtime as _analyze_import_source_via_runtime,
    apply_deprecated_template_v2_constructor_bridge as _apply_deprecated_template_v2_constructor_bridge,
    assemble_confirmed_candidate as _assemble_confirmed_candidate,
    build_runtime_analysis as _build_runtime_analysis,
    is_runtime_analysis as _is_runtime_analysis,
    persist_analysis as _persist_analysis,
    runtime_assets_reclaimed as _runtime_assets_reclaimed,
    runtime_confirmed_draft as _runtime_confirmed_draft,
    with_private_asset_references as _with_private_asset_references,
)
from services.template_v2_pptx_storage import (
    get_private_source_retention_ttl,
    relocate_runtime_assets,
    verify_private_source,
)
from templates.v2.constants import TEMPLATE_V2_VERSION
from templates.v2.models.layouts import RawSlideLayouts, SlideLayouts
from templates.v2.generation import build_generated_slide
from templates.v2.pptx.assembler import (
    AssembledTemplateV2Draft,
    assemble_template_v2_draft,
)
from templates.v2.pptx.analyzer import analyze_ooxml_candidates
from templates.v2.pptx.models import PresentationCandidates
from templates.v2.pptx.ooxml_parser import parse_presentation_candidates
from templates.v2.pptx.runtime_layouts import (
    build_runtime_slide_layouts,
    classify_runtime_fillable_layouts,
    restore_runtime_default_contents,
    runtime_default_contents,
)
from templates.v2.pptx.package_reader import PptxPackageReader, UnsafePptxPackage
from templates.v2.pptx.repeat_suggestions import (
    build_repeat_block_suggestions,
)
from templates.v2.pptx.repeat_application import (
    resolve_repeat_suggestion_decisions,
)
from templates.v2.pptx.source_inventory import (
    HashedInventoryItem,
    SecretFreeSourceMetadata,
    SourceInventory,
    candidate_inventory_item,
)
from templates.v2.policy import get_structured_template_policy
from templates.v2.strategies import (
    TEMPLATE_V2_STRATEGIES,
    resolve_presentation_strategies,
)
from utils.datetime_utils import get_current_utc_datetime


logger = logging.getLogger(__name__)
IMPORT_TASK_KIND = "template-v2-pptx-import"
IMPORT_LEASE_DURATION = timedelta(minutes=5)
IMPORT_HEARTBEAT_INTERVAL_SECONDS = 30
IMPORT_DISPATCH_INTERVAL_SECONDS = 5
IMPORT_DISPATCH_BATCH_SIZE = 20


class AttemptOwnershipLost(RuntimeError):
    pass


def _now() -> datetime:
    return get_current_utc_datetime()


def _task_timestamp(value: datetime) -> datetime:
    """Match the legacy task table's timezone-naive UTC timestamp columns."""

    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _task_data(
    import_id: uuid.UUID,
    *,
    state: str,
    attempt_number: int,
    draft_template_id: str | None = None,
) -> dict:
    data = {
        "kind": IMPORT_TASK_KIND,
        "import_id": str(import_id),
        "state": state,
        "attempt_number": attempt_number,
    }
    if draft_template_id is not None:
        data["draft_template_id"] = draft_template_id
    return data


async def claim_template_v2_pptx_import(
    session: AsyncSession,
    import_id: uuid.UUID,
    task_id: str,
    *,
    token: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """Atomically claim one queued row; exactly one concurrent caller wins."""

    claimed_at = now or _now()
    attempt_token = token or uuid.uuid4().hex
    result = await session.execute(
        update(TemplateV2PptxImport)
        .where(
            TemplateV2PptxImport.id == import_id,
            TemplateV2PptxImport.task_id == task_id,
            TemplateV2PptxImport.state == "queued",
            TemplateV2PptxImport.attempt_token.is_(None),
        )
        .values(
            state="processing",
            attempt_token=attempt_token,
            attempt_number=TemplateV2PptxImport.attempt_number + 1,
            lease_expires_at=claimed_at + IMPORT_LEASE_DURATION,
            heartbeat_at=claimed_at,
            last_started_at=claimed_at,
            updated_at=claimed_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        return None
    import_job = await session.get(TemplateV2PptxImport, import_id)
    if import_job is None:
        await session.rollback()
        return None
    task_result = await session.execute(
        update(AsyncPresentationGenerationTaskModel)
        .where(AsyncPresentationGenerationTaskModel.id == task_id)
        .values(
            status="running",
            message="Validating private PPTX package",
            error=None,
            data=_task_data(
                import_id,
                state="processing",
                attempt_number=import_job.attempt_number,
            ),
            updated_at=_task_timestamp(claimed_at),
        )
        .execution_options(synchronize_session=False)
    )
    if task_result.rowcount != 1:
        await session.rollback()
        return None
    await session.commit()
    return attempt_token


async def heartbeat_template_v2_pptx_import(
    import_id: uuid.UUID,
    task_id: str,
    attempt_token: str,
    *,
    now: datetime | None = None,
) -> bool:
    heartbeat_at = now or _now()
    async with async_session_maker() as session:
        result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.state.in_(("processing", "finalizing")),
                TemplateV2PptxImport.attempt_token == attempt_token,
                TemplateV2PptxImport.lease_expires_at > heartbeat_at,
            )
            .values(
                heartbeat_at=heartbeat_at,
                lease_expires_at=heartbeat_at + IMPORT_LEASE_DURATION,
                updated_at=heartbeat_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        await session.commit()
        return True


async def _heartbeat_loop(
    import_id: uuid.UUID,
    task_id: str,
    attempt_token: str,
    stop: asyncio.Event,
    ownership_lost: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=IMPORT_HEARTBEAT_INTERVAL_SECONDS,
            )
            return
        except TimeoutError:
            try:
                owned = await heartbeat_template_v2_pptx_import(
                    import_id,
                    task_id,
                    attempt_token,
                )
            except Exception:
                logger.exception(
                    "Template V2 PPTX import heartbeat failed for %s",
                    import_id,
                )
                continue
            if not owned:
                ownership_lost.set()
                return


RUNTIME_ANALYZER_PROVIDER = "runtime-pptx-to-json"
RUNTIME_ANALYSIS_MARKER = "runtime-pptx-to-json-v1"
DETERMINISTIC_ANALYZER_PROVIDER = "deterministic-ooxml-static"
DETERMINISTIC_ANALYSIS_MARKER = "deterministic-ooxml-static-analysis-v1"


async def confirm_template_v2_pptx_import(
    session: AsyncSession,
    import_id: uuid.UUID,
    task_id: str,
    *,
    owner_scope: str,
    expected_revision: int,
    accepted_repeat_suggestion_ids: tuple[str, ...] = (),
) -> str:
    """Create Template V2 once, only after an owner-scoped explicit confirm."""
    import_job = (
        await session.execute(
            select(TemplateV2PptxImport).where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.owner_scope == owner_scope,
            )
        )
    ).scalar_one_or_none()
    if import_job is None:
        return "not_found"
    if import_job.state == "confirmed" and import_job.draft_template_id:
        return "already_confirmed"
    if import_job.state != "review_required":
        return "state_conflict"
    if import_job.revision != expected_revision:
        return "revision_conflict"
    if _runtime_assets_reclaimed(import_job):
        # Retention reclaims the source and the relocated media together, and
        # `review_required` is an eligible state, so an import can sit past its TTL
        # and still look confirmable. Confirming it would persist a template whose
        # every image reference points at a deleted file -- unrecoverable, since the
        # source deck is gone too, and silent, since the endpoint 404s per asset and
        # export still succeeds.
        return "assets_reclaimed"
    if await session.get(TemplateV2, import_job.requested_template_id) is not None:
        return "template_conflict"

    try:
        accepted_suggestions, repeat_decisions = (
            resolve_repeat_suggestion_decisions(
                import_job.repeat_suggestions or [],
                accepted_repeat_suggestion_ids,
            )
        )
        assembled = _assemble_confirmed_candidate(
            import_job,
            accepted_suggestions,
        )
    except ValueError as error:
        if str(error) in {
            "duplicate_repeat_suggestion_id",
            "unknown_repeat_suggestion_id",
            "invalid_repeat_suggestion",
            "repeat_suggestion_source_missing",
            "overlapping_repeat_suggestions",
        }:
            return "suggestion_conflict"
        raise
    confirmed_at = _now()
    gate_predicates = [
        TemplateV2PptxImport.id == import_id,
        TemplateV2PptxImport.task_id == task_id,
        TemplateV2PptxImport.owner_scope == owner_scope,
        TemplateV2PptxImport.state == "review_required",
        TemplateV2PptxImport.revision == expected_revision,
        TemplateV2PptxImport.draft_template_id.is_(None),
    ]
    if _is_runtime_analysis(import_job):
        # The guard above reads the row loaded when this request started; retention
        # claims and empties the private directory without touching `state` or
        # `revision`, so nothing else here would notice a cleanup that began after
        # that read. Only the runtime path is narrowed: a deterministic import is
        # still allowed to confirm once its source is gone.
        gate_predicates.append(TemplateV2PptxImport.source_deleted_at.is_(None))
        gate_predicates.append(TemplateV2PptxImport.source_cleanup_token.is_(None))
    gate = await session.execute(
        update(TemplateV2PptxImport)
        .where(*gate_predicates)
        .values(
            state="confirming",
            revision=TemplateV2PptxImport.revision + 1,
            updated_at=confirmed_at,
        )
        .execution_options(synchronize_session=False)
    )
    if gate.rowcount != 1:
        await session.rollback()
        current = (
            await session.execute(
                select(TemplateV2PptxImport).where(
                    TemplateV2PptxImport.id == import_id,
                    TemplateV2PptxImport.owner_scope == owner_scope,
                )
            )
        ).scalar_one_or_none()
        if current and current.state == "confirmed" and current.draft_template_id:
            return "already_confirmed"
        if current is not None and _runtime_assets_reclaimed(current):
            return "assets_reclaimed"
        return "revision_conflict"

    presentation = PresentationModel(
        content=f"Private PPTX import {import_job.source_filename}",
        n_slides=len(assembled.layouts.layouts),
        language="en",
        title=import_job.source_filename.rsplit(".", 1)[0],
        layout=None,
        structure=None,
        theme={"mode": "template"},
        mode="template",
        version=TEMPLATE_V2_VERSION,
    )
    slides: list[SlideModel] = []
    for index, layout in enumerate(assembled.layouts.layouts):
        generated = build_generated_slide(layout, assembled.contents[index])
        slides.append(
            SlideModel(
                presentation=presentation.id,
                layout_group="native",
                layout=generated.layout_id,
                index=index,
                content=generated.content,
                ui=generated.ui,
                html_content=None,
                properties=None,
            )
        )
    if resolve_presentation_strategies(presentation, slides) != TEMPLATE_V2_STRATEGIES:
        await session.rollback()
        raise RuntimeError("template_v2_strategy_boundary_violation")
    all_components = [
        component.model_dump(mode="json")
        for layout in assembled.layouts.layouts
        for component in layout.components
    ]
    template = TemplateV2(
        **_apply_deprecated_template_v2_constructor_bridge(
            {
                "id": import_job.requested_template_id,
                "name": presentation.title or "Imported PPTX",
                "description": (
                    "Confirmed deterministic OOXML import; visual review retained."
                ),
                "raw_layouts": assembled.raw_layouts.model_dump(mode="json"),
                "components": {"components": all_components},
                "merged_components": None,
                "layouts": assembled.layouts.model_dump(mode="json"),
                "assets": None,
                "is_default": False,
            },
            presentation_id=presentation.id,
        )
    )
    local_state = TemplateV2LocalState(
        template_id=template.id,
        presentation_id=presentation.id,
        revision=1,
    )
    manifest = {
        **deepcopy(import_job.manifest or {}),
        "confirmation": {
            "confirmed_at": confirmed_at.isoformat(),
            "repeat_suggestions_applied": bool(accepted_suggestions),
            "accepted_repeat_suggestion_ids": [
                suggestion["id"] for suggestion in accepted_suggestions
            ],
            "unapplied_repeat_suggestion_ids": [
                suggestion["id"]
                for suggestion in repeat_decisions
                if suggestion["status"] == "unapplied"
            ],
        },
        "review": {
            "required": False,
            "reason": "owner_confirmed_candidate",
        },
    }
    session.add(presentation)
    session.add_all(slides)
    session.add(template)
    session.add(local_state)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return "template_conflict"
    await session.execute(
        update(TemplateV2PptxImport)
        .where(
            TemplateV2PptxImport.id == import_id,
            TemplateV2PptxImport.owner_scope == owner_scope,
            TemplateV2PptxImport.state == "confirming",
            TemplateV2PptxImport.revision == expected_revision + 1,
        )
        .values(
            state="confirmed",
            draft_template_id=template.id,
            confirmed_at=confirmed_at,
            manifest=manifest,
            repeat_suggestions=repeat_decisions,
            updated_at=confirmed_at,
        )
        .execution_options(synchronize_session=False)
    )
    task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
    if task is None:
        await session.rollback()
        return "state_conflict"
    task.status = "completed"
    task.message = "Template V2 created after explicit confirmation"
    task.error = None
    task.data = _task_data(
        import_id,
        state="confirmed",
        attempt_number=import_job.attempt_number,
        draft_template_id=template.id,
    )
    task.updated_at = _task_timestamp(confirmed_at)
    session.add(task)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        current = (
            await session.execute(
                select(TemplateV2PptxImport).where(
                    TemplateV2PptxImport.id == import_id,
                    TemplateV2PptxImport.owner_scope == owner_scope,
                )
            )
        ).scalar_one_or_none()
        if current and current.state == "confirmed" and current.draft_template_id:
            return "already_confirmed"
        return "template_conflict"
    return "confirmed"


def _failure_code(error: Exception) -> str:
    if isinstance(error, IntegrityError):
        return "template_v2_import_analysis_persistence_conflict"
    if isinstance(error, UnsafePptxPackage):
        return error.code
    return getattr(error, "code", "template_v2_pptx_import_failed")


async def fail_template_v2_pptx_import(
    import_id: uuid.UUID,
    task_id: str,
    attempt_token: str,
    error: Exception,
) -> bool:
    """Record failure only while the caller still owns the active attempt."""

    failed_at = _now()
    code = _failure_code(error)
    async with async_session_maker() as session:
        import_job = await session.get(TemplateV2PptxImport, import_id)
        if import_job is None:
            return False
        retention_expires_at, manifest = terminal_source_retention(
            {
                **deepcopy(import_job.manifest or {}),
                "attempt_number": import_job.attempt_number,
                "failure": {"code": code},
            },
            terminal_at=failed_at,
        )
        result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.state.in_(("processing", "finalizing")),
                TemplateV2PptxImport.attempt_token == attempt_token,
                TemplateV2PptxImport.lease_expires_at > failed_at,
            )
            .values(
                state="failed",
                revision=TemplateV2PptxImport.revision + 1,
                attempt_token=None,
                lease_expires_at=None,
                source_retention_expires_at=retention_expires_at,
                source_cleanup_token=None,
                source_cleanup_lease_expires_at=None,
                source_cleanup_attempted_at=None,
                source_deleted_at=None,
                manifest=manifest,
                updated_at=failed_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if task is None:
            await session.rollback()
            return False
        task.status = "error"
        task.message = "Template V2 PPTX import failed"
        task.error = {"code": code}
        task.data = _task_data(
            import_id,
            state="failed",
            attempt_number=import_job.attempt_number,
        )
        task.updated_at = _task_timestamp(failed_at)
        session.add(task)
        await session.commit()
        return True


async def requeue_failed_template_v2_pptx_import(
    session: AsyncSession,
    import_id: uuid.UUID,
    task_id: str,
    manifest: dict,
    *,
    expected_revision: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Atomically move exactly one failed import back to the durable queue."""

    queued_at = now or _now()
    legacy_cutoff = queued_at - get_private_source_retention_ttl()
    retry_manifest = {
        **deepcopy(manifest),
        "private_source_retention": {
            **deepcopy(manifest.get("private_source_retention") or {}),
            "superseded_by_retry_at": queued_at.isoformat(),
        },
    }
    predicates = [
        TemplateV2PptxImport.id == import_id,
        TemplateV2PptxImport.task_id == task_id,
        TemplateV2PptxImport.state == "failed",
        TemplateV2PptxImport.attempt_token.is_(None),
        TemplateV2PptxImport.source_deleted_at.is_(None),
        TemplateV2PptxImport.source_cleanup_token.is_(None),
        or_(
            TemplateV2PptxImport.source_retention_expires_at > queued_at,
            and_(
                TemplateV2PptxImport.source_retention_expires_at.is_(None),
                TemplateV2PptxImport.updated_at > legacy_cutoff,
            ),
        ),
    ]
    if expected_revision is not None:
        predicates.append(TemplateV2PptxImport.revision == expected_revision)
    result = await session.execute(
        update(TemplateV2PptxImport)
        .where(*predicates)
        .values(
            state="queued",
            revision=TemplateV2PptxImport.revision + 1,
            attempt_token=None,
            lease_expires_at=None,
            source_retention_expires_at=None,
            source_cleanup_token=None,
            source_cleanup_lease_expires_at=None,
            source_cleanup_attempted_at=None,
            source_deleted_at=None,
            analysis_result=None,
            repeat_suggestions=[],
            manifest=retry_manifest,
            updated_at=queued_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        return False
    attempt_number = (
        await session.execute(
            select(TemplateV2PptxImport.attempt_number).where(
                TemplateV2PptxImport.id == import_id
            )
        )
    ).scalar_one()
    task_result = await session.execute(
        update(AsyncPresentationGenerationTaskModel)
        .where(
            AsyncPresentationGenerationTaskModel.id == task_id,
            AsyncPresentationGenerationTaskModel.status == "error",
        )
        .values(
            status="pending",
            message="Queued for private PPTX validation retry",
            error=None,
            data=_task_data(
                import_id,
                state="queued",
                attempt_number=attempt_number,
            ),
            updated_at=_task_timestamp(queued_at),
        )
        .execution_options(synchronize_session=False)
    )
    if task_result.rowcount != 1:
        await session.rollback()
        return False
    await session.commit()
    notify_template_v2_pptx_dispatcher()
    return True


async def cancel_template_v2_pptx_import(
    session: AsyncSession,
    import_id: uuid.UUID,
    task_id: str,
    *,
    owner_scope: str,
    expected_revision: int,
    now: datetime | None = None,
) -> bool:
    """Cancel an owner-scoped import with optimistic concurrency."""
    cancelled_at = now or _now()
    import_job = (
        await session.execute(
            select(TemplateV2PptxImport).where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.owner_scope == owner_scope,
            )
        )
    ).scalar_one_or_none()
    if import_job is None:
        return False
    if import_job.state == "cancelled":
        return True
    retention_expires_at, manifest = terminal_source_retention(
        {
            **deepcopy(import_job.manifest or {}),
            "cancelled_at": cancelled_at.isoformat(),
            "review": {
                "required": False,
                "reason": "owner_cancelled",
            },
        },
        terminal_at=cancelled_at,
    )
    result = await session.execute(
        update(TemplateV2PptxImport)
        .where(
            TemplateV2PptxImport.id == import_id,
            TemplateV2PptxImport.task_id == task_id,
            TemplateV2PptxImport.owner_scope == owner_scope,
            TemplateV2PptxImport.revision == expected_revision,
            TemplateV2PptxImport.state.in_(
                ("queued", "processing", "finalizing", "failed", "review_required")
            ),
        )
        .values(
            state="cancelled",
            revision=TemplateV2PptxImport.revision + 1,
            attempt_token=None,
            lease_expires_at=None,
            source_retention_expires_at=retention_expires_at,
            source_cleanup_token=None,
            source_cleanup_lease_expires_at=None,
            source_cleanup_attempted_at=None,
            source_deleted_at=None,
            cancelled_at=cancelled_at,
            manifest=manifest,
            updated_at=cancelled_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        return False
    task_result = await session.execute(
        update(AsyncPresentationGenerationTaskModel)
        .where(AsyncPresentationGenerationTaskModel.id == task_id)
        .values(
            status="cancelled",
            message="Template V2 PPTX import cancelled",
            error=None,
            data=_task_data(
                import_id,
                state="cancelled",
                attempt_number=import_job.attempt_number,
            ),
            updated_at=_task_timestamp(cancelled_at),
        )
        .execution_options(synchronize_session=False)
    )
    if task_result.rowcount != 1:
        await session.rollback()
        return False
    await session.commit()
    return True


async def release_template_v2_pptx_import(
    import_id: uuid.UUID,
    task_id: str,
    attempt_token: str,
) -> bool:
    """Return an owned attempt to the durable queue during graceful shutdown."""

    released_at = _now()
    async with async_session_maker() as session:
        result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.state.in_(("processing", "finalizing")),
                TemplateV2PptxImport.attempt_token == attempt_token,
            )
            .values(
                state="queued",
                attempt_token=None,
                lease_expires_at=None,
                updated_at=released_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        import_job = await session.get(TemplateV2PptxImport, import_id)
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if import_job is None or task is None:
            await session.rollback()
            return False
        task.status = "pending"
        task.message = "Queued after worker shutdown"
        task.error = None
        task.data = _task_data(
            import_id,
            state="queued",
            attempt_number=import_job.attempt_number,
        )
        task.updated_at = _task_timestamp(released_at)
        session.add(task)
        await session.commit()
        notify_template_v2_pptx_dispatcher()
        return True


async def recover_stalled_template_v2_pptx_imports(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    return await _dispatcher_runtime.recover_stalled_template_v2_pptx_imports(
        session,
        _runtime_dependencies(),
        now=now,
    )


async def run_template_v2_pptx_import(import_id: uuid.UUID, task_id: str) -> None:
    await _dispatcher_runtime.run_template_v2_pptx_import(
        import_id,
        task_id,
        _runtime_dependencies(),
    )


_dispatcher_task: asyncio.Task | None = None
_dispatcher_stop: asyncio.Event | None = None
_dispatcher_wake: asyncio.Event | None = None
_inflight_tasks: set[asyncio.Task] = set()


def _runtime_dependencies():
    return sys.modules[__name__]


def notify_template_v2_pptx_dispatcher() -> None:
    _dispatcher_runtime.notify_template_v2_pptx_dispatcher(
        _runtime_dependencies()
    )


def _track_import_task(task: asyncio.Task) -> None:
    _dispatcher_runtime.track_import_task(task, _runtime_dependencies())


async def dispatch_template_v2_pptx_imports_once() -> int:
    return await _dispatcher_runtime.dispatch_template_v2_pptx_imports_once(
        _runtime_dependencies()
    )


async def _dispatcher_loop() -> None:
    await _dispatcher_runtime.dispatcher_loop(_runtime_dependencies())


async def start_template_v2_pptx_dispatcher() -> None:
    await _dispatcher_runtime.start_template_v2_pptx_dispatcher(
        _runtime_dependencies()
    )


async def stop_template_v2_pptx_dispatcher() -> None:
    await _dispatcher_runtime.stop_template_v2_pptx_dispatcher(
        _runtime_dependencies()
    )
