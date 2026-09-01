from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from typing import Any, Mapping
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation
from services.authored_checkpoint_artifacts import (
    atomic_write,
    checkpoint_root,
    render_artifact_path,
    read_valid_evidence_artifact,
    recover_unreferenced_evidence,
    evidence_attempt_id,
    write_valid_evidence,
)
from services.artifact_store import configured_public_artifact_service
from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_slide_checkpoint_bundle import (
    SlideCheckpointBundle,
    SlideCheckpointConflict,
)
from services.authored_generation_observability import log_authored_slide_failure_observation
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime


STAGES = ("author", "illustrate", "render", "persist")
_STATUS_FIELD = {stage: f"{stage}_status" for stage in STAGES}


async def _lock_public_admission_if_available(
    session: AsyncSession,
    operation_id: uuid.UUID,
) -> PublicOperationAdmission | None:
    try:
        async with session.begin_nested():
            return await session.scalar(
                select(PublicOperationAdmission)
                .where(PublicOperationAdmission.operation_id == operation_id)
                .with_for_update()
            )
    except OperationalError:
        return None


@asynccontextmanager
async def _checkpoint_mutation_session(session: AsyncSession | None):
    if session is not None:
        yield session, False
        return
    async with async_session_maker() as owned_session:
        yield owned_session, True


async def initialize_checkpoints(
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    roles: list[str],
    fencing_token: int | None = None,
    session: AsyncSession | None = None,
) -> None:
    now = get_current_utc_datetime()
    async with _checkpoint_mutation_session(session) as (mutation_session, owns_session):
        job = await _owned_job(
            mutation_session, job_id, attempt_token, fencing_token=fencing_token
        )
        existing = set(
            await mutation_session.scalars(
                select(AuthoredSlideCheckpoint.slide_index).where(
                    AuthoredSlideCheckpoint.job_id == job_id
                )
            )
        )
        for index, role in enumerate(roles):
            if index not in existing:
                mutation_session.add(
                    AuthoredSlideCheckpoint(
                        job_id=job_id,
                        slide_index=index,
                        role=role,
                    )
                )
        job.total_slides = len(roles)
        job.completed_slides = await _completed_count(mutation_session, job_id)
        job.current_stage = "author"
        job.updated_at = now
        mutation_session.add(job)
        if owns_session:
            await mutation_session.commit()


async def get_checkpoint(
    job_id: uuid.UUID,
    slide_index: int,
    *,
    attempt_token: str | None = None,
    fencing_token: int | None = None,
) -> AuthoredSlideCheckpoint:
    async with async_session_maker() as session:
        job = await session.get(PresentationGenerationJob, job_id)
        checkpoint = await _checkpoint(session, job_id, slide_index)
        recovered = recover_unreferenced_evidence(checkpoint)
        if recovered is not None and job is not None and job.workspace_operation_id is not None:
            # A crash-orphaned render belongs to the attempt that wrote its
            # checkpoint.  Reads remain available without authority, but only
            # the current fenced attempt may publish the recovered pointer.
            if not attempt_token or checkpoint.attempt_token != attempt_token:
                return checkpoint
            await _owned_job(
                session,
                job_id,
                attempt_token,
                fencing_token=fencing_token,
            )
            await session.refresh(checkpoint)
            recovered = recover_unreferenced_evidence(checkpoint)
        if recovered is not None:
            path, sha256, bundle = recovered
            now = get_current_utc_datetime()
            checkpoint.render_path = path
            checkpoint.render_sha256 = sha256
            checkpoint.illustrated_html = bundle.html
            checkpoint.render_status = "completed"
            checkpoint.render_completed_at = checkpoint.render_completed_at or now
            if checkpoint.failed_stage == "render":
                checkpoint.failed_stage = None
                checkpoint.error = None
            checkpoint.updated_at = now
            session.add(checkpoint)
            await session.commit()
        return checkpoint


async def start_stage(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    stage: str,
    attempt_token: str,
    fencing_token: int | None = None,
    session: AsyncSession | None = None,
) -> AuthoredSlideCheckpoint:
    _require_stage(stage)
    async with _checkpoint_mutation_session(session) as (mutation_session, owns_session):
        job = await _owned_job(
            mutation_session, job_id, attempt_token, fencing_token=fencing_token
        )
        checkpoint = await _checkpoint(mutation_session, job_id, slide_index)
        stage_index = STAGES.index(stage)
        for downstream in STAGES[stage_index + 1 :]:
            setattr(checkpoint, _STATUS_FIELD[downstream], "pending")
        setattr(checkpoint, _STATUS_FIELD[stage], "running")
        setattr(checkpoint, f"{stage}_started_at", get_current_utc_datetime())
        checkpoint.failed_stage = None
        checkpoint.attempt_token = attempt_token
        checkpoint.updated_at = get_current_utc_datetime()
        job.current_stage = stage
        job.completed_slides = await _completed_count(mutation_session, job_id)
        job.updated_at = checkpoint.updated_at
        mutation_session.add_all((checkpoint, job))
        if owns_session:
            await mutation_session.commit()
        return checkpoint


async def reserve_provider_attempt(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    attempt_token: str,
    ordinal: int,
    fencing_token: int | None = None,
    session: AsyncSession | None = None,
) -> AuthoredSlideCheckpoint:
    """Durably consume one slide-local provider ordinal before call admission.

    The job-row fence prevents a stale worker from advancing the counter after
    lease takeover. Replaying the same ordinal is idempotent; skipping or
    decreasing an ordinal fails closed. The counter lives in the existing JSON
    checkpoint seam so this crash fence needs no schema migration.
    """

    if isinstance(ordinal, bool) or ordinal not in {1, 2}:
        raise ValueError("authored_provider_attempt_ordinal_invalid")
    async with _checkpoint_mutation_session(session) as (mutation_session, owns_session):
        job = await _owned_job(
            mutation_session, job_id, attempt_token, fencing_token=fencing_token
        )
        checkpoint = await _checkpoint(mutation_session, job_id, slide_index)
        metadata = dict(checkpoint.error) if isinstance(checkpoint.error, Mapping) else {}
        raw_current = metadata.get("provider_attempt_count", 0)
        current = (
            raw_current
            if isinstance(raw_current, int) and not isinstance(raw_current, bool)
            else 0
        )
        if current not in {0, 1, 2}:
            await mutation_session.rollback()
            raise AuthoredStageError(
                slide_index=slide_index,
                stage="author",
                code="authored_provider_attempt_checkpoint_invalid",
                retryable=False,
            )
        if ordinal < current or ordinal > current + 1:
            await mutation_session.rollback()
            raise AuthoredStageError(
                slide_index=slide_index,
                stage="author",
                code="authored_provider_attempt_checkpoint_conflict",
                retryable=False,
                provider_attempt_count=current,
            )
        if ordinal == current:
            return checkpoint
        metadata["provider_attempt_count"] = ordinal
        metadata["provider_attempt_state"] = "reserved"
        checkpoint.error = metadata
        checkpoint.attempt_token = attempt_token
        checkpoint.updated_at = get_current_utc_datetime()
        job.current_stage = "author"
        job.updated_at = checkpoint.updated_at
        mutation_session.add_all((checkpoint, job))
        if owns_session:
            await mutation_session.commit()
        return checkpoint


async def complete_stage(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    stage: str,
    attempt_token: str,
    html: str | None = None,
    png: bytes | None = None,
    browser_metrics: Mapping[str, object] | None = None,
    checkpoint_revision: int | None = None,
    skipped: bool = False,
    fencing_token: int | None = None,
    session: AsyncSession | None = None,
) -> AuthoredSlideCheckpoint:
    _require_stage(stage)
    render_path: str | None = None
    render_sha256: str | None = None
    expected_render_path: str | None = None
    expected_artifact_refs: dict | None = None
    artifact_refs: dict | None = None
    has_evidence = stage == "render" and html is not None and browser_metrics is not None
    if has_evidence and png is None:
        raise ValueError("checkpoint_evidence_requires_png")
    if png is not None:
        prior = None
        if has_evidence:
            previous = await get_checkpoint(
                job_id,
                slide_index,
                attempt_token=attempt_token,
                fencing_token=fencing_token,
            )
            expected_render_path = previous.render_path
            expected_artifact_refs = previous.artifact_refs
            prior = await read_valid_evidence_artifact(previous)
            if checkpoint_revision is None:
                checkpoint_revision = (prior.revision + 1) if prior else 0
            elif prior is not None and checkpoint_revision <= prior.revision:
                raise SlideCheckpointConflict("checkpoint_revision_regressed")
        render_sha256 = hashlib.sha256(png).hexdigest()
        public_binding = await _public_checkpoint_binding(
            job_id, attempt_token, fencing_token=fencing_token
        )
        if public_binding is not None:
            owner_id, tenant_id, operation_id = public_binding
            artifact_service = configured_public_artifact_service()
            async with async_session_maker() as artifact_session:
                render_ref = await artifact_service.persist_bytes(
                    artifact_session,
                    owner_id=owner_id,
                    tenant_id=tenant_id,
                    kind="slide-png",
                    payload=png,
                    media_type="image/png",
                    created_by_operation_id=operation_id,
                    retention_class="checkpoint",
                )
                artifact_refs = {
                    "render": render_ref.model_dump(mode="json", by_alias=True)
                }
                if has_evidence:
                    bundle = SlideCheckpointBundle(
                        evidence_attempt_id(attempt_token),
                        slide_index,
                        checkpoint_revision,
                        html,
                        bytes(png),
                        dict(browser_metrics),
                        SlideCheckpointBundle._digest_for_payload(
                            html, png, browser_metrics
                        ),
                    )
                    evidence_payload = json.dumps(
                        bundle.to_payload(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                    evidence_ref = await artifact_service.persist_bytes(
                        artifact_session,
                        owner_id=owner_id,
                        tenant_id=tenant_id,
                        kind="checkpoint",
                        payload=evidence_payload,
                        media_type="application/json",
                        created_by_operation_id=operation_id,
                        retention_class="checkpoint",
                    )
                    artifact_refs["evidence"] = evidence_ref.model_dump(
                        mode="json", by_alias=True
                    )
                await artifact_session.commit()
        else:
            path = render_artifact_path(job_id, slide_index, attempt_token)
            if has_evidence:
                # Never mutate bytes reachable from the previous DB checkpoint.
                path = path.with_name(
                    f"{path.stem}-r{checkpoint_revision}-{uuid.uuid4().hex}.png"
                )
            atomic_write(path, png)
            render_path = str(path.relative_to(checkpoint_root())).replace(os.sep, "/")
            if has_evidence:
                write_valid_evidence(
                    path,
                    attempt_id=evidence_attempt_id(attempt_token),
                    slide_index=slide_index,
                    revision=checkpoint_revision,
                    html=html,
                    png=png,
                    metrics=dict(browser_metrics),
                )
    async with _checkpoint_mutation_session(session) as (mutation_session, owns_session):
        job = await _owned_job(
            mutation_session, job_id, attempt_token, fencing_token=fencing_token
        )
        checkpoint = await _checkpoint(mutation_session, job_id, slide_index)
        if has_evidence and (
            checkpoint.render_path != expected_render_path
            or checkpoint.artifact_refs != expected_artifact_refs
        ):
            raise SlideCheckpointConflict("checkpoint_render_pointer_conflict")
        setattr(checkpoint, _STATUS_FIELD[stage], "skipped" if skipped else "completed")
        setattr(checkpoint, f"{stage}_completed_at", get_current_utc_datetime())
        if stage == "author" and html is not None:
            checkpoint.author_html = html
        if stage == "illustrate" and html is not None:
            checkpoint.illustrated_html = html
        if stage == "render" and html is not None:
            checkpoint.illustrated_html = html
        if render_path is not None:
            checkpoint.render_path = render_path
            checkpoint.render_sha256 = render_sha256
            checkpoint.artifact_refs = None
        elif artifact_refs is not None:
            checkpoint.render_path = None
            checkpoint.render_sha256 = render_sha256
            checkpoint.artifact_refs = artifact_refs
        checkpoint.failed_stage = None
        checkpoint.error = None
        checkpoint.attempt_token = attempt_token
        checkpoint.updated_at = get_current_utc_datetime()
        if stage == "persist":
            await mutation_session.flush()
            job.completed_slides = await _completed_count(mutation_session, job_id)
        job.current_stage = stage
        job.updated_at = checkpoint.updated_at
        mutation_session.add_all((checkpoint, job))
        if owns_session:
            await mutation_session.commit()
        return checkpoint


async def fail_stage(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    stage: str,
    attempt_token: str,
    error: AuthoredStageError,
    request_id: str | None,
    fencing_token: int | None = None,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    _require_stage(stage)
    async with _checkpoint_mutation_session(session) as (mutation_session, owns_session):
        job = await _owned_job(
            mutation_session, job_id, attempt_token, fencing_token=fencing_token
        )
        safe = error.as_safe_dict(
            request_id=request_id, attempt_number=job.attempt_number
        )
        checkpoint = await _checkpoint(mutation_session, job_id, slide_index)
        setattr(checkpoint, _STATUS_FIELD[stage], "failed")
        checkpoint.failed_stage = stage
        checkpoint.error = safe
        checkpoint.failed_at = get_current_utc_datetime()
        checkpoint.attempt_token = attempt_token
        checkpoint.updated_at = get_current_utc_datetime()
        job.current_stage = stage
        job.resume_allowed = error.retryable
        job.last_error = safe
        job.completed_slides = await _completed_count(mutation_session, job_id)
        job.updated_at = checkpoint.updated_at
        mutation_session.add_all((checkpoint, job))
        if owns_session:
            await mutation_session.commit()
    log_authored_slide_failure_observation(
        stage=stage,
        code=error.code,
        slide_number=slide_index + 1,
        retryable=error.retryable,
        provider_attempt_count=error.provider_attempt_count,
    )
    return safe


async def progress_for_task(
    task_id: str,
    session: AsyncSession | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return authored progress, using the caller's read transaction when given."""

    if session is None:
        async with async_session_maker() as owned_session:
            return await progress_for_task(task_id, owned_session)

    job = (
        await session.execute(
            select(PresentationGenerationJob).where(
                PresentationGenerationJob.task_id == task_id
            )
        )
    ).scalar_one_or_none()
    if job is None:
        return None, None
    if job.request_payload.get("template") != "authored":
        if job.workspace_operation_id is None:
            return None, job.last_error
        operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
        raw = operation.progress if operation is not None else None
        if not isinstance(raw, dict) or not raw:
            return None, job.last_error

        def safe_count(value: Any) -> int:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                return 0

        total = safe_count(raw.get("total"))
        completed = min(total, safe_count(raw.get("current"))) if total else 0
        stage = str(raw.get("stage") or "generation_started")
        try:
            percent = max(0, min(100, int(raw.get("percent") or 0)))
        except (TypeError, ValueError):
            percent = 0
        published = job.state == "published"
        if published:
            completed = total
            percent = 100
            stage = "completed"
        return (
            {
                "completed_slides": completed,
                "total_slides": total,
                "percent": percent,
                "current_slide": (
                    completed + 1
                    if total and completed < total and stage.endswith("slide_generation")
                    else None
                ),
                "current_stage": stage,
                "stage_counts": {},
                "finalizing": bool(total and completed == total and not published),
            },
            job.last_error,
        )
    if not job.total_slides:
        return None, job.last_error
    total = max(0, job.total_slides or 0)
    completed = min(total, max(0, job.completed_slides or 0))
    percent = 0 if total == 0 else int(completed * 100 / total)
    finalizing = completed == total and job.state != "published"
    if job.state != "published":
        percent = min(percent, 99)
    counts: dict[str, int] = {}
    for stage in STAGES:
        field = getattr(AuthoredSlideCheckpoint, _STATUS_FIELD[stage])
        counts[stage] = int(
            await session.scalar(
                select(func.count()).select_from(AuthoredSlideCheckpoint).where(
                    AuthoredSlideCheckpoint.job_id == job.id,
                    field.in_(("completed", "skipped")),
                )
            )
            or 0
        )
    current = (
        await session.execute(
            select(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.job_id == job.id,
                AuthoredSlideCheckpoint.failed_stage.is_not(None),
            ).order_by(AuthoredSlideCheckpoint.slide_index)
        )
    ).scalars().first()
    progress = {
        "completed_slides": completed,
        "total_slides": total,
        "percent": 100 if job.state == "published" else percent,
        "current_slide": current.slide_index + 1 if current else None,
        "current_stage": job.current_stage,
        "stage_counts": counts,
        "finalizing": finalizing,
    }
    return progress, job.last_error


def should_inject_failure(
    checkpoint: AuthoredSlideCheckpoint, *, slide_index: int, stage: str
) -> bool:
    if os.getenv("AUTHORED_TEST_FAILURES_ENABLED", "").lower() not in {"1", "true"}:
        return False
    if os.getenv("ENVIRONMENT", "").lower() not in {
        "development",
        "test",
    } and not os.getenv("PYTEST_CURRENT_TEST"):
        return False
    try:
        target_slide = int(os.getenv("AUTHORED_TEST_FAILURE_SLIDE", "0"))
    except ValueError:
        return False
    target_stage = os.getenv("AUTHORED_TEST_FAILURE_STAGE", "").strip().lower()
    already_injected = bool(
        checkpoint.error and checkpoint.error.get("code") == "authored_injected_failure"
    )
    return target_slide == slide_index + 1 and target_stage == stage and not already_injected


async def _owned_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    attempt_token: str,
    *,
    fencing_token: int | None = None,
) -> PresentationGenerationJob:
    # Fence every checkpoint transaction, not just final publication.  A plain
    # SELECT leaves a race where cancellation/lease takeover can happen before
    # the checkpoint UPDATE commits.  This conditional no-op UPDATE acquires the
    # job-row write lock and is rechecked by the database before any stage data
    # is made visible.
    job = await session.get(PresentationGenerationJob, job_id)
    if job is None:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )
    now = get_current_utc_datetime()
    if job.workspace_operation_id is not None:
        if isinstance(fencing_token, bool) or not isinstance(fencing_token, int):
            await session.rollback()
            raise AuthoredStageError(
                slide_index=0,
                stage="persist",
                code="authored_workspace_operation_fencing_token_required",
                retryable=True,
            )
        admission = await _lock_public_admission_if_available(
            session,
            job.workspace_operation_id,
        )
        operation_result = await session.execute(
            update(WorkspaceOperation)
            .where(
                WorkspaceOperation.id == job.workspace_operation_id,
                WorkspaceOperation.state == "running",
                WorkspaceOperation.attempt_token == attempt_token,
                WorkspaceOperation.fencing_token == fencing_token,
            )
            .values(heartbeat_at=now, updated_at=now)
        )
        if operation_result.rowcount != 1:
            await session.rollback()
            raise AuthoredStageError(
                slide_index=0,
                stage="persist",
                code="authored_workspace_operation_fence_lost",
                retryable=True,
            )
        operation_contract = await session.scalar(
            select(WorkspaceOperation.contract_version).where(
                WorkspaceOperation.id == job.workspace_operation_id
            )
        )
        if operation_contract == "public-operation/v2" and (
            admission is None
            or admission.operation_kind != "generation"
            or admission.state != "active"
            or admission.fence != fencing_token
        ):
            await session.rollback()
            raise AuthoredStageError(
                slide_index=0,
                stage="persist",
                code="authored_workspace_operation_fence_lost",
                retryable=True,
            )
        result = await session.execute(
            update(PresentationGenerationJob)
            .where(
                PresentationGenerationJob.id == job_id,
                PresentationGenerationJob.state.in_(("running", "staging")),
            )
            .values(heartbeat_at=now, updated_at=now)
        )
    else:
        result = await session.execute(
            update(PresentationGenerationJob)
            .where(
                PresentationGenerationJob.id == job_id,
                PresentationGenerationJob.attempt_token == attempt_token,
                PresentationGenerationJob.state.in_(("running", "staging")),
            )
            .values(heartbeat_at=now, updated_at=now)
        )
    if result.rowcount != 1:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )
    job = await session.get(PresentationGenerationJob, job_id)
    if job is None:  # Defensive: the fenced row cannot normally disappear.
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )
    return job


async def _public_checkpoint_binding(
    job_id: uuid.UUID,
    attempt_token: str,
    *,
    fencing_token: int | None,
) -> tuple[uuid.UUID, str, uuid.UUID] | None:
    """Resolve a public generation's owner/tenant only from durable admission."""

    async with async_session_maker() as session:
        binding = (
            await session.execute(
                select(
                    PublicOperationAdmission.owner_id,
                    PublicOperationAdmission.tenant_id,
                    PublicOperationAdmission.operation_id,
                )
                .join(
                    PresentationGenerationJob,
                    PresentationGenerationJob.workspace_operation_id
                    == PublicOperationAdmission.operation_id,
                )
                .join(
                    WorkspaceOperation,
                    WorkspaceOperation.id
                    == PresentationGenerationJob.workspace_operation_id,
                )
                .where(
                    PresentationGenerationJob.id == job_id,
                    PresentationGenerationJob.state.in_(("running", "staging")),
                    WorkspaceOperation.state == "running",
                    WorkspaceOperation.attempt_token == attempt_token,
                    WorkspaceOperation.fencing_token == fencing_token,
                    PublicOperationAdmission.operation_kind == "generation",
                    PublicOperationAdmission.state == "active",
                )
            )
        ).one_or_none()
    if binding is None:
        return None
    return binding.owner_id, binding.tenant_id, binding.operation_id


async def _checkpoint(session, job_id: uuid.UUID, slide_index: int) -> AuthoredSlideCheckpoint:
    return (
        await session.execute(
            select(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.job_id == job_id,
                AuthoredSlideCheckpoint.slide_index == slide_index,
            )
        )
    ).scalar_one()


async def _completed_count(session, job_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.job_id == job_id,
                AuthoredSlideCheckpoint.persist_status == "completed",
            )
        )
        or 0
    )


def _require_stage(stage: str) -> None:
    if stage not in STAGES:
        raise ValueError("invalid_authored_stage")

