from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import logging
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
from services.template_v2_pptx_retention_service import (
    maybe_cleanup_expired_private_sources,
    terminal_source_retention,
)
from services.template_v2_pptx_storage import (
    get_private_source_retention_ttl,
    verify_private_source,
)
from templates.v2.constants import TEMPLATE_V2_VERSION
from templates.v2.generation import build_generated_slide
from templates.v2.pptx.assembler import (
    AssembledTemplateV2Draft,
    assemble_template_v2_draft,
)
from templates.v2.pptx.ooxml_parser import parse_presentation_candidates
from templates.v2.pptx.package_reader import PptxPackageReader, UnsafePptxPackage
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
            updated_at=claimed_at,
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


def _analyze_import_source(
    storage_key: str,
    source_sha256: str,
) -> AssembledTemplateV2Draft:
    source = verify_private_source(storage_key, source_sha256)
    candidates = parse_presentation_candidates(
        PptxPackageReader(source),
        source_sha256=source_sha256,
    )
    return assemble_template_v2_draft(candidates)


def _apply_deprecated_template_v2_constructor_bridge(
    canonical_values: dict,
    *,
    presentation_id: uuid.UUID,
) -> dict:
    """Supply legacy non-null columns during the two-stage sidecar rollout.

    New import lifecycle code must read and write ``TemplateV2LocalState``.
    This bridge exists only until a later migration removes the transitional
    columns from ``template_v2``.
    """

    values = dict(canonical_values)
    if "presentation_id" in TemplateV2.model_fields:
        values["presentation_id"] = presentation_id
    return values


async def _persist_success(
    import_id: uuid.UUID,
    task_id: str,
    attempt_token: str,
    assembled: AssembledTemplateV2Draft,
) -> bool:
    finalized_at = _now()
    async with async_session_maker() as session:
        gate = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                TemplateV2PptxImport.id == import_id,
                TemplateV2PptxImport.task_id == task_id,
                TemplateV2PptxImport.state == "processing",
                TemplateV2PptxImport.attempt_token == attempt_token,
                TemplateV2PptxImport.lease_expires_at > finalized_at,
            )
            .values(
                state="finalizing",
                lease_expires_at=finalized_at + IMPORT_LEASE_DURATION,
                updated_at=finalized_at,
            )
            .execution_options(synchronize_session=False)
        )
        if gate.rowcount != 1:
            await session.rollback()
            return False
        import_job = await session.get(TemplateV2PptxImport, import_id)
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if import_job is None or task is None:
            await session.rollback()
            return False
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
        if (
            resolve_presentation_strategies(presentation, slides)
            != TEMPLATE_V2_STRATEGIES
        ):
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
                        "Deterministic OOXML draft; visual review required."
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
        import_job.draft_template_id = template.id
        import_job.state = "review_required"
        import_job.attempt_token = None
        import_job.lease_expires_at = None
        retention_expires_at, manifest = terminal_source_retention(
            {
                **deepcopy(assembled.manifest),
                "attempt_number": import_job.attempt_number,
            },
            terminal_at=finalized_at,
        )
        import_job.manifest = manifest
        import_job.source_retention_expires_at = retention_expires_at
        import_job.source_cleanup_token = None
        import_job.source_cleanup_lease_expires_at = None
        import_job.source_cleanup_attempted_at = None
        import_job.source_deleted_at = None
        import_job.updated_at = finalized_at
        task.status = "completed"
        task.message = "Template V2 draft created; visual review required"
        task.error = None
        task.data = _task_data(
            import_id,
            state=import_job.state,
            attempt_number=import_job.attempt_number,
            draft_template_id=template.id,
        )
        task.updated_at = finalized_at
        session.add(presentation)
        session.add_all(slides)
        session.add(template)
        session.add(local_state)
        session.add(import_job)
        session.add(task)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise
        return True


def _failure_code(error: Exception) -> str:
    if isinstance(error, IntegrityError):
        return "template_v2_draft_persistence_conflict"
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
        task.updated_at = failed_at
        session.add(task)
        await session.commit()
        return True


async def requeue_failed_template_v2_pptx_import(
    session: AsyncSession,
    import_id: uuid.UUID,
    task_id: str,
    manifest: dict,
    *,
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
    result = await session.execute(
        update(TemplateV2PptxImport)
        .where(
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
        )
        .values(
            state="queued",
            attempt_token=None,
            lease_expires_at=None,
            source_retention_expires_at=None,
            source_cleanup_token=None,
            source_cleanup_lease_expires_at=None,
            source_cleanup_attempted_at=None,
            source_deleted_at=None,
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
            updated_at=queued_at,
        )
        .execution_options(synchronize_session=False)
    )
    if task_result.rowcount != 1:
        await session.rollback()
        return False
    await session.commit()
    notify_template_v2_pptx_dispatcher()
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
        task.updated_at = released_at
        session.add(task)
        await session.commit()
        notify_template_v2_pptx_dispatcher()
        return True


async def recover_stalled_template_v2_pptx_imports(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """CAS stale processing rows back to queued without trusting local memory."""

    recovered_at = now or _now()
    rows = (
        await session.execute(
            select(
                TemplateV2PptxImport.id,
                TemplateV2PptxImport.task_id,
                TemplateV2PptxImport.attempt_token,
                TemplateV2PptxImport.attempt_number,
            ).where(
                TemplateV2PptxImport.state.in_(("processing", "finalizing")),
                or_(
                    TemplateV2PptxImport.lease_expires_at.is_(None),
                    TemplateV2PptxImport.lease_expires_at <= recovered_at,
                ),
            )
        )
    ).all()
    recovered = 0
    for import_id, task_id, attempt_token, attempt_number in rows:
        predicates = [
            TemplateV2PptxImport.id == import_id,
            TemplateV2PptxImport.task_id == task_id,
            TemplateV2PptxImport.state.in_(("processing", "finalizing")),
        ]
        if attempt_token is None:
            predicates.append(TemplateV2PptxImport.attempt_token.is_(None))
        else:
            predicates.append(TemplateV2PptxImport.attempt_token == attempt_token)
        result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                *predicates,
                or_(
                    TemplateV2PptxImport.lease_expires_at.is_(None),
                    TemplateV2PptxImport.lease_expires_at <= recovered_at,
                ),
            )
            .values(
                state="queued",
                attempt_token=None,
                lease_expires_at=None,
                updated_at=recovered_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        await session.execute(
            update(AsyncPresentationGenerationTaskModel)
            .where(AsyncPresentationGenerationTaskModel.id == task_id)
            .values(
                status="pending",
                message="Recovered stalled Template V2 PPTX import",
                error=None,
                data=_task_data(
                    import_id,
                    state="queued",
                    attempt_number=attempt_number,
                ),
                updated_at=recovered_at,
            )
            .execution_options(synchronize_session=False)
        )
        recovered += 1
    await session.commit()
    return recovered


async def run_template_v2_pptx_import(import_id: uuid.UUID, task_id: str) -> None:
    """Claim, heartbeat, and complete a durable import attempt."""

    async with async_session_maker() as session:
        attempt_token = await claim_template_v2_pptx_import(
            session,
            import_id,
            task_id,
        )
    if attempt_token is None:
        return
    stop_heartbeat = asyncio.Event()
    ownership_lost = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            import_id,
            task_id,
            attempt_token,
            stop_heartbeat,
            ownership_lost,
        )
    )
    try:
        async with async_session_maker() as session:
            import_job = await session.get(TemplateV2PptxImport, import_id)
            if (
                import_job is None
                or import_job.task_id != task_id
                or import_job.attempt_token != attempt_token
            ):
                raise AttemptOwnershipLost()
            storage_key = import_job.source_storage_key
            source_sha256 = import_job.source_sha256
        assembled = await asyncio.to_thread(
            _analyze_import_source,
            storage_key,
            source_sha256,
        )
        if ownership_lost.is_set():
            raise AttemptOwnershipLost()
        if not await _persist_success(
            import_id,
            task_id,
            attempt_token,
            assembled,
        ):
            raise AttemptOwnershipLost()
    except asyncio.CancelledError:
        await release_template_v2_pptx_import(
            import_id,
            task_id,
            attempt_token,
        )
        raise
    except AttemptOwnershipLost:
        logger.warning(
            "Template V2 PPTX import attempt lost ownership for %s",
            import_id,
        )
    except Exception as error:
        recorded = await fail_template_v2_pptx_import(
            import_id,
            task_id,
            attempt_token,
            error,
        )
        if recorded:
            logger.exception(
                "Template V2 PPTX import failed: %s",
                _failure_code(error),
            )
    finally:
        stop_heartbeat.set()
        await heartbeat_task


_dispatcher_task: asyncio.Task | None = None
_dispatcher_stop: asyncio.Event | None = None
_dispatcher_wake: asyncio.Event | None = None
_inflight_tasks: set[asyncio.Task] = set()


def notify_template_v2_pptx_dispatcher() -> None:
    if _dispatcher_wake is not None:
        _dispatcher_wake.set()


def _track_import_task(task: asyncio.Task) -> None:
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)


async def dispatch_template_v2_pptx_imports_once() -> int:
    async with async_session_maker() as session:
        recovered = await recover_stalled_template_v2_pptx_imports(session)
        queued = (
            await session.execute(
                select(
                    TemplateV2PptxImport.id,
                    TemplateV2PptxImport.task_id,
                )
                .where(TemplateV2PptxImport.state == "queued")
                .order_by(TemplateV2PptxImport.created_at)
                .limit(IMPORT_DISPATCH_BATCH_SIZE)
            )
        ).all()
    for import_id, task_id in queued:
        _track_import_task(
            asyncio.create_task(run_template_v2_pptx_import(import_id, task_id))
        )
    if recovered:
        logger.warning("Recovered %s stalled Template V2 PPTX imports", recovered)
    return len(queued)


async def _dispatcher_loop() -> None:
    assert _dispatcher_stop is not None
    assert _dispatcher_wake is not None
    while not _dispatcher_stop.is_set():
        _dispatcher_wake.clear()
        try:
            await maybe_cleanup_expired_private_sources()
            await dispatch_template_v2_pptx_imports_once()
        except Exception:
            logger.exception("Template V2 PPTX durable dispatcher iteration failed")
        try:
            await asyncio.wait_for(
                _dispatcher_wake.wait(),
                timeout=IMPORT_DISPATCH_INTERVAL_SECONDS,
            )
        except TimeoutError:
            pass


async def start_template_v2_pptx_dispatcher() -> None:
    global _dispatcher_stop, _dispatcher_task, _dispatcher_wake
    policy = get_structured_template_policy()
    if not policy.creation_enabled or not policy.allowed_template_ids:
        logger.info(
            "Template V2 PPTX dispatcher remains disabled by rollout policy"
        )
        return
    if _dispatcher_task is not None and not _dispatcher_task.done():
        return
    _dispatcher_stop = asyncio.Event()
    _dispatcher_wake = asyncio.Event()
    await maybe_cleanup_expired_private_sources()
    await dispatch_template_v2_pptx_imports_once()
    _dispatcher_task = asyncio.create_task(_dispatcher_loop())


async def stop_template_v2_pptx_dispatcher() -> None:
    global _dispatcher_stop, _dispatcher_task, _dispatcher_wake
    if _dispatcher_stop is not None:
        _dispatcher_stop.set()
    if _dispatcher_wake is not None:
        _dispatcher_wake.set()
    if _dispatcher_task is not None:
        await _dispatcher_task
    active = list(_inflight_tasks)
    for task in active:
        task.cancel()
    if active:
        await asyncio.gather(*active, return_exceptions=True)
    _inflight_tasks.clear()
    _dispatcher_task = None
    _dispatcher_stop = None
    _dispatcher_wake = None
