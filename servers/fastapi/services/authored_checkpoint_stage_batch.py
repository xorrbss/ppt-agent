"""Batched checkpoint writes for one authored slide stage.

The unbatched paths in ``services/authored_checkpoint_state.py`` commit once
per checkpoint call: opening the author stage and buying its provider ordinal
were two transactions, and publishing a render was four (previous checkpoint,
public binding, artifact bytes, pointer).  Roughly a hundred round trips per
slide reached the database, each contending for the single SQLite writer lock
with the deck being generated.

This module merges the writes that were always adjacent and always for the
same slide.  Nothing about the durability contract moves: the fencing CAS is
still the first statement of every transaction, a stage transition is never
observable without it, and every crash point still lands in a state the resume
path already handles.

Lives beside the state module rather than inside it because that file is above
the file-size ratchet and must not grow.
"""

from __future__ import annotations

from collections.abc import Mapping
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from services.authored_admission_checkpoint import (
    admit_checkpoint_write, checkpoint_receipt_metadata, enforce_receipts,
)
from services.authored_admission_receipt import AdmissionReceipt
from services.authored_checkpoint_artifacts import (
    read_valid_evidence_artifact,
    recover_unreferenced_evidence,
)
from services.authored_checkpoint_fence import (
    checkpoint_mutation_session,
    load_checkpoint,
    owned_job,
    refence_job,
    retained_provider_attempt_fence,
)
from services.authored_checkpoint_html import collapses_to_author_html
from services.authored_checkpoint_render_artifacts import (
    persist_render_artifacts,
    write_render_artifact_files,
)
from services.authored_slide_checkpoint_bundle import (
    SlideCheckpointConflict,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.get_env_programme import is_authored_checkpoint_batching_enabled
from utils.hashing import sha256_hex


def store_downstream_html(checkpoint: AuthoredSlideCheckpoint, html: str) -> None:
    """Write the stage's document, or ``NULL`` when it repeats ``author_html``.

    Readers must resolve it through ``services.authored_checkpoint_html``; the
    collapse is off until every one of them does.
    """

    checkpoint.illustrated_html = (
        None if collapses_to_author_html(checkpoint, html) else html
    )


async def begin_author_stage(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    attempt_token: str,
    ordinal: int,
    fencing_token: int | None = None,
    session: AsyncSession | None = None,
) -> AuthoredSlideCheckpoint:
    """Open the author stage and buy its provider ordinal in one transaction.

    The two writes were always adjacent and always for the same slide, but each
    paid for its own session, its own fencing CAS and its own commit.  Merged,
    the fence still runs as the first statement and the ordinal is still
    durable before the call is admitted -- a crash between them now leaves the
    stage ``running`` with no ordinal spent, which is the state the resume path
    already handles (it re-opens the stage and reserves ordinal 1).
    """

    from services.authored_checkpoint_state import (
        reserve_provider_attempt,
        start_stage,
    )

    if not is_authored_checkpoint_batching_enabled():
        await start_stage(
            job_id=job_id,
            slide_index=slide_index,
            stage="author",
            attempt_token=attempt_token,
            fencing_token=fencing_token,
            session=session,
        )
        return await reserve_provider_attempt(
            job_id=job_id,
            slide_index=slide_index,
            attempt_token=attempt_token,
            ordinal=ordinal,
            fencing_token=fencing_token,
            session=session,
        )
    async with checkpoint_mutation_session(session) as (mutation_session, owns_session):
        await start_stage(
            job_id=job_id,
            slide_index=slide_index,
            stage="author",
            attempt_token=attempt_token,
            fencing_token=fencing_token,
            session=mutation_session,
        )
        checkpoint = await reserve_provider_attempt(
            job_id=job_id,
            slide_index=slide_index,
            attempt_token=attempt_token,
            ordinal=ordinal,
            fencing_token=fencing_token,
            session=mutation_session,
        )
        if owns_session:
            await mutation_session.commit()
        return checkpoint


async def complete_render_stage_batched(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    attempt_token: str,
    html: str | None,
    png: bytes,
    browser_metrics: Mapping[str, object] | None,
    checkpoint_revision: int | None,
    skipped: bool,
    fencing_token: int | None,
    session: AsyncSession | None,
    admission_receipt: AdmissionReceipt | None = None,
) -> AuthoredSlideCheckpoint:
    """Publish a render inside one transaction instead of four.

    The unbatched path read the previous checkpoint in its own session,
    resolved the public binding in a second, wrote the artifacts in a third and
    committed the pointer in a fourth -- about twenty-two round trips and four
    commits for one slide.  Here the fencing CAS is the first statement, every
    read and the artifact rows share that transaction, and a single commit
    publishes the pointer with the artifact rows that back it.

    The guarantees are unchanged.  ``owned_job`` holds the operation row write
    lock for the whole transaction, so no other attempt can move the render
    pointer between the read and the write; ``refence_job`` re-asserts the
    attempt's authority immediately before the pointer write for the window in
    which the artifact bytes were uploaded.  A crash anywhere before the commit
    leaves the previous pointer and an unreferenced artifact, which is exactly
    what ``recover_unreferenced_evidence`` and the artifact sweeper already
    handle.
    """

    html, admission_receipt = admit_checkpoint_write(
        admission_receipt=admission_receipt, html=html, stage="render",
        slide_index=slide_index, attempt_token=attempt_token, fencing_token=fencing_token,
        browser_metrics=browser_metrics, png=png,
    )
    has_evidence = html is not None and browser_metrics is not None
    async with checkpoint_mutation_session(session) as (mutation_session, owns_session):
        job = await owned_job(
            mutation_session, job_id, attempt_token, fencing_token=fencing_token
        )
        checkpoint = await load_checkpoint(mutation_session, job_id, slide_index)
        recovered = None if enforce_receipts() else recover_unreferenced_evidence(checkpoint)
        if recovered is not None:
            # Adopt a crash-orphaned bundle before deciding the next revision,
            # so revisions stay monotonic across a resumed attempt.
            recovered_path, recovered_sha256, recovered_bundle = recovered
            checkpoint.render_path = recovered_path
            checkpoint.render_sha256 = recovered_sha256
            store_downstream_html(checkpoint, recovered_bundle.html)
            checkpoint.render_status = "completed"
            checkpoint.render_completed_at = (
                checkpoint.render_completed_at or get_current_utc_datetime()
            )
        expected_render_path = checkpoint.render_path
        expected_artifact_refs = checkpoint.artifact_refs
        if has_evidence:
            # A legal re-author stores the next HTML before its replacement
            # render. Read the immutable prior revision without requiring that
            # transient new HTML to equal the previous bundle.
            prior = await read_valid_evidence_artifact(
                checkpoint, require_current_html_match=False
            )
            if checkpoint_revision is None:
                checkpoint_revision = (prior.revision + 1) if prior else 0
            elif prior is not None and checkpoint_revision <= prior.revision:
                raise SlideCheckpointConflict("checkpoint_revision_regressed")
        render_sha256 = sha256_hex(png)
        render_path: str | None = None
        artifact_refs: dict | None = None
        # Resolved through the state module so its existing monkeypatch seam
        # -- the lease-takeover regression test -- keeps working.
        from services import authored_checkpoint_state as _state

        public_binding = await _state._public_checkpoint_binding(
            job_id,
            attempt_token,
            fencing_token=fencing_token,
            session=mutation_session,
        )
        if public_binding is not None:
            artifact_refs = await persist_render_artifacts(
                mutation_session,
                public_binding,
                slide_index=slide_index,
                attempt_token=attempt_token,
                checkpoint_revision=checkpoint_revision,
                html=html,
                png=png,
                browser_metrics=browser_metrics,
                has_evidence=has_evidence,
            )
        else:
            render_path = write_render_artifact_files(
                job_id=job_id,
                slide_index=slide_index,
                attempt_token=attempt_token,
                checkpoint_revision=checkpoint_revision,
                html=html,
                png=png,
                browser_metrics=browser_metrics,
                has_evidence=has_evidence,
            )
        await refence_job(
            mutation_session, job, attempt_token, fencing_token=fencing_token
        )
        if has_evidence and (
            checkpoint.render_path != expected_render_path
            or checkpoint.artifact_refs != expected_artifact_refs
        ):
            raise SlideCheckpointConflict("checkpoint_render_pointer_conflict")
        checkpoint.render_status = "skipped" if skipped else "completed"
        checkpoint.render_completed_at = get_current_utc_datetime()
        if html is not None:
            store_downstream_html(checkpoint, html)
        if render_path is not None:
            checkpoint.render_path = render_path
            checkpoint.render_sha256 = render_sha256
            checkpoint.artifact_refs = None
        elif artifact_refs is not None:
            checkpoint.render_path = None
            checkpoint.render_sha256 = render_sha256
            checkpoint.artifact_refs = artifact_refs
        checkpoint.failed_stage = None
        checkpoint.error = checkpoint_receipt_metadata(
            checkpoint.error, retained_provider_attempt_fence(checkpoint.error), admission_receipt,
        )
        checkpoint.attempt_token = attempt_token
        checkpoint.updated_at = get_current_utc_datetime()
        job.current_stage = "render"
        job.updated_at = checkpoint.updated_at
        mutation_session.add_all((checkpoint, job))
        if owns_session:
            await mutation_session.commit()
        return checkpoint


__all__ = [
    "begin_author_stage",
    "complete_render_stage_batched",
    "store_downstream_html",
]
