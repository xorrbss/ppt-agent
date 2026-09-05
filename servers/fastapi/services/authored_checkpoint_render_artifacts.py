"""Artifact bytes for one authored render checkpoint.

Split out of ``authored_checkpoint_stage_batch`` to keep both modules inside
the file-size guidance.  The public path registers the artifacts on the
caller's transaction, so the render pointer and the rows that back it commit
together; the local path writes the same bytes to the checkpoint directory.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.artifact_store import configured_public_artifact_service
from services.authored_checkpoint_artifacts import (
    atomic_write,
    checkpoint_root,
    evidence_attempt_id,
    render_artifact_path,
    write_valid_evidence,
)
from services.authored_slide_checkpoint_bundle import SlideCheckpointBundle


def _evidence_payload(
    *,
    attempt_token: str,
    slide_index: int,
    checkpoint_revision: int | None,
    html: str | None,
    png: bytes,
    browser_metrics: Mapping[str, object] | None,
) -> bytes:
    bundle = SlideCheckpointBundle(
        evidence_attempt_id(attempt_token),
        slide_index,
        checkpoint_revision,
        html,
        bytes(png),
        dict(browser_metrics),
        SlideCheckpointBundle._digest_for_payload(html, png, browser_metrics),
    )
    return json.dumps(
        bundle.to_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


async def persist_render_artifacts(
    session: AsyncSession,
    public_binding: tuple[uuid.UUID, str, uuid.UUID],
    *,
    slide_index: int,
    attempt_token: str,
    checkpoint_revision: int | None,
    html: str | None,
    png: bytes,
    browser_metrics: Mapping[str, object] | None,
    has_evidence: bool,
) -> dict:
    """Register the render (and evidence) artifacts on the caller's transaction."""

    owner_id, tenant_id, operation_id = public_binding
    artifact_service = configured_public_artifact_service()
    render_ref = await artifact_service.persist_bytes(
        session,
        owner_id=owner_id,
        tenant_id=tenant_id,
        kind="slide-png",
        payload=png,
        media_type="image/png",
        created_by_operation_id=operation_id,
        retention_class="checkpoint",
    )
    artifact_refs = {"render": render_ref.model_dump(mode="json", by_alias=True)}
    if has_evidence:
        evidence_ref = await artifact_service.persist_bytes(
            session,
            owner_id=owner_id,
            tenant_id=tenant_id,
            kind="checkpoint",
            payload=_evidence_payload(
                attempt_token=attempt_token,
                slide_index=slide_index,
                checkpoint_revision=checkpoint_revision,
                html=html,
                png=png,
                browser_metrics=browser_metrics,
            ),
            media_type="application/json",
            created_by_operation_id=operation_id,
            retention_class="checkpoint",
        )
        artifact_refs["evidence"] = evidence_ref.model_dump(mode="json", by_alias=True)
    return artifact_refs


def write_render_artifact_files(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    attempt_token: str,
    checkpoint_revision: int | None,
    html: str | None,
    png: bytes,
    browser_metrics: Mapping[str, object] | None,
    has_evidence: bool,
) -> str:
    path = render_artifact_path(job_id, slide_index, attempt_token)
    if has_evidence:
        # Never mutate bytes reachable from the previous DB checkpoint.
        path = path.with_name(
            f"{path.stem}-r{checkpoint_revision}-{uuid.uuid4().hex}.png"
        )
    atomic_write(path, png)
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
    return str(path.relative_to(checkpoint_root())).replace(os.sep, "/")


__all__ = ["persist_render_artifacts", "write_render_artifact_files"]
