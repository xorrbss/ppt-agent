from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Mapping
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
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
from services.authored_admission_checkpoint import (
    admit_checkpoint_write, checkpoint_receipt_metadata, enforce_receipts,
)
from services.authored_admission_receipt import AdmissionReceipt
from services.authored_checkpoint_fence import (
    checkpoint_mutation_session as _checkpoint_mutation_session,
    completed_count as _completed_count,
    load_checkpoint as _checkpoint,
    owned_job as _owned_job,
    public_checkpoint_binding as _public_checkpoint_binding,
    retained_provider_attempt_fence,
)
from services.authored_provider_attempt_ledger import (
    current_provider_attempt_reservations as current_provider_attempt_reservations,
    provider_attempt_count_in_epoch as provider_attempt_count_in_epoch,
    provider_attempt_epoch as provider_attempt_epoch,
    reserve_provider_attempt as reserve_provider_attempt,
    reserve_provider_overload_retry as reserve_provider_overload_retry,
    take_provider_attempt_reservations as take_provider_attempt_reservations,
)
from services.authored_checkpoint_stage_batch import (
    begin_author_stage,  # noqa: F401 -- re-exported entry point for callers
    complete_render_stage_batched as _complete_render_stage_batched,
    store_downstream_html as _store_downstream_html,
)
from services.authored_slide_checkpoint_bundle import (
    SlideCheckpointBundle,
    SlideCheckpointConflict,
)
from services.authored_generation_observability import log_authored_slide_failure_observation
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime
from utils.get_env_programme import is_authored_checkpoint_batching_enabled


STAGES = ("author", "illustrate", "render", "persist")
_STATUS_FIELD = {stage: f"{stage}_status" for stage in STAGES}


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
        recovered = None if enforce_receipts() else recover_unreferenced_evidence(checkpoint)
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
                checkpoint.error = retained_provider_attempt_fence(
                    checkpoint.error
                )
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
    admission_receipt: AdmissionReceipt | None = None,
    skipped: bool = False,
    fencing_token: int | None = None,
    session: AsyncSession | None = None,
) -> AuthoredSlideCheckpoint:
    _require_stage(stage)
    html, admission_receipt = admit_checkpoint_write(
        admission_receipt=admission_receipt, html=html, stage=stage,
        slide_index=slide_index, attempt_token=attempt_token, fencing_token=fencing_token,
        browser_metrics=browser_metrics, png=png,
    )
    render_path: str | None = None
    render_sha256: str | None = None
    expected_render_path: str | None = None
    expected_artifact_refs: dict | None = None
    artifact_refs: dict | None = None
    has_evidence = stage == "render" and html is not None and browser_metrics is not None
    if has_evidence and png is None:
        raise ValueError("checkpoint_evidence_requires_png")
    if (
        stage == "render"
        and png is not None
        and is_authored_checkpoint_batching_enabled()
    ):
        return await _complete_render_stage_batched(
            job_id=job_id,
            slide_index=slide_index,
            attempt_token=attempt_token,
            html=html,
            png=png,
            browser_metrics=browser_metrics,
            checkpoint_revision=checkpoint_revision,
            admission_receipt=admission_receipt,
            skipped=skipped,
            fencing_token=fencing_token,
            session=session,
        )
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
            # During render replacement, author/illustrate may already hold the
            # next HTML while the pointer still names the last valid revision.
            prior = await read_valid_evidence_artifact(
                previous, require_current_html_match=False
            )
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
        if stage in ("illustrate", "render") and html is not None:
            _store_downstream_html(checkpoint, html)
        if render_path is not None:
            checkpoint.render_path = render_path
            checkpoint.render_sha256 = render_sha256
            checkpoint.artifact_refs = None
        elif artifact_refs is not None:
            checkpoint.render_path = None
            checkpoint.render_sha256 = render_sha256
            checkpoint.artifact_refs = artifact_refs
        checkpoint.failed_stage = None
        # The slide no longer carries a failure, but it still carries every
        # provider ordinal it has bought: an extra call on an already
        # complete stage -- the style anchor's one remake -- must follow the
        # stored count rather than restart under it.
        checkpoint.error = checkpoint_receipt_metadata(
            checkpoint.error, retained_provider_attempt_fence(checkpoint.error), admission_receipt,
        )
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


def _require_stage(stage: str) -> None:
    if stage not in STAGES:
        raise ValueError("invalid_authored_stage")

