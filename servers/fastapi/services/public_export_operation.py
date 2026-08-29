"""PPT Core adapter from a fenced WorkspaceOperation claim to isolated workers."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
from dataclasses import replace
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.public_artifact import ArtifactRef as PublicArtifactRef
from services.artifact_store import configured_public_artifact_service
from services.database import async_session_maker

from services.isolated_worker_contracts import (
    ArtifactRef,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
    WorkerRole,
    issue_worker_capability,
)
from services.isolated_worker_process import (
    IsolatedWorkerResult,
    execute_isolated_worker_job,
)
from services.workspace_operation_handler_registry import WorkspaceOperationClaim


class PublicExportOperationError(RuntimeError):
    pass


def load_worker_capability_signing_key() -> bytes:
    """Load a mounted workload key without copying it into child argv or env."""

    value = os.getenv("PUBLIC_WORKER_CAPABILITY_KEY_FILE", "").strip()
    if not value or not pathlib.Path(value).is_absolute():
        raise PublicExportOperationError("public_worker_capability_key_file_required")
    try:
        key = pathlib.Path(value).read_bytes()
    except OSError as error:
        raise PublicExportOperationError(
            "public_worker_capability_key_unavailable"
        ) from error
    if len(key) < 32 or len(key) > 4096:
        raise PublicExportOperationError("public_worker_capability_key_invalid")
    return key


def build_isolated_operation_job(
    claim: WorkspaceOperationClaim,
    *,
    role: WorkerRole,
    input_artifact: ArtifactRef,
    signing_key: bytes,
    deadline_seconds: int | None = None,
    now: int | None = None,
) -> IsolatedWorkerJob:
    """Bind immutable input to the claim; no session argument exists by design."""

    if role == "export" and claim.kind not in {"export.pptx", "export.pdf"}:
        raise PublicExportOperationError("public_export_operation_kind_invalid")
    if role == "render" and claim.kind not in {
        "presentation.generate",
        "export.pptx",
        "export.pdf",
    }:
        raise PublicExportOperationError("public_render_operation_kind_invalid")
    if claim.owner_id is None or input_artifact.owner_id != claim.owner_id:
        raise PublicExportOperationError("public_worker_artifact_owner_mismatch")
    if not claim.attempt_token or claim.fencing_token < 0:
        raise PublicExportOperationError("public_worker_operation_fence_invalid")
    effective_deadline = (
        (600 if role == "export" else 120)
        if deadline_seconds is None
        else deadline_seconds
    )
    maximum_deadline = 600 if role == "export" else 120
    if not 1 <= effective_deadline <= maximum_deadline:
        raise PublicExportOperationError("public_worker_deadline_invalid")
    issued_at = int(time.time()) if now is None else now
    job = IsolatedWorkerJob(
        role=role,
        operation_id=claim.operation_id,
        attempt_token=claim.attempt_token,
        fencing_token=claim.fencing_token,
        input_digest=input_artifact.sha256,
        input_artifact=input_artifact,
        capability="pending",
        deadline_epoch_seconds=issued_at + effective_deadline,
    )
    try:
        capability = issue_worker_capability(
            job,
            signing_key=signing_key,
            ttl_seconds=min(300, effective_deadline),
            now=issued_at,
        )
    except IsolatedWorkerContractError as error:
        raise PublicExportOperationError(error.args[0]) from error
    return replace(job, capability=capability)


async def execute_isolated_export_operation(
    claim: WorkspaceOperationClaim,
    *,
    input_artifact: ArtifactRef,
) -> IsolatedWorkerResult:
    """Run an export profile whose result still requires operation CAS publish."""

    job = build_isolated_operation_job(
        claim,
        role="export",
        input_artifact=input_artifact,
        signing_key=load_worker_capability_signing_key(),
    )
    return await execute_isolated_worker_job(job)


async def execute_isolated_render_operation(
    claim: WorkspaceOperationClaim,
    *,
    input_artifact: ArtifactRef,
) -> IsolatedWorkerResult:
    job = build_isolated_operation_job(
        claim,
        role="render",
        input_artifact=input_artifact,
        signing_key=load_worker_capability_signing_key(),
    )
    return await execute_isolated_worker_job(job)


def _worker_ref(reference: PublicArtifactRef) -> ArtifactRef:
    return ArtifactRef.from_wire(reference.model_dump(mode="json", by_alias=True))


def _public_worker_output(
    payload: object,
    *,
    retention_class: str,
) -> PublicArtifactRef:
    if not isinstance(payload, dict):
        raise PublicExportOperationError("public_worker_output_artifact_missing")
    value = dict(payload)
    value["artifactId"] = str(uuid.uuid4())
    value["retentionClass"] = retention_class
    try:
        return PublicArtifactRef.model_validate(value)
    except Exception as error:
        raise PublicExportOperationError("public_worker_output_artifact_invalid") from error


async def render_and_export_authored_htmls(
    claim: WorkspaceOperationClaim,
    *,
    tenant_id: str,
    htmls: list[str],
    export_format: str,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> PublicArtifactRef:
    """Materialize authored HTML through network-none render and export roles."""

    if claim.owner_id is None:
        raise PublicExportOperationError("public_export_owner_required")
    if export_format not in {"pptx", "pdf"} or claim.kind != f"export.{export_format}":
        raise PublicExportOperationError("public_export_operation_kind_invalid")
    if not htmls or len(htmls) > 100 or any(not isinstance(html, str) or not html for html in htmls):
        raise PublicExportOperationError("public_export_authored_slides_invalid")

    artifact_service = configured_public_artifact_service()
    html_refs: list[PublicArtifactRef] = []
    async with session_factory() as session:
        for html in htmls:
            html_refs.append(
                await artifact_service.persist_bytes(
                    session,
                    owner_id=claim.owner_id,
                    tenant_id=tenant_id,
                    kind="slide-html",
                    payload=html.encode("utf-8"),
                    media_type="text/html",
                    created_by_operation_id=claim.operation_id,
                    retention_class="ephemeral",
                )
            )
        await session.commit()

    concurrency = max(1, min(8, int(os.getenv("PUBLIC_RENDER_MAX_CONCURRENCY", "4"))))
    gate = asyncio.Semaphore(concurrency)

    async def render_one(reference: PublicArtifactRef) -> PublicArtifactRef:
        async with gate:
            result = await execute_isolated_render_operation(
                claim,
                input_artifact=_worker_ref(reference),
            )
        if not result.ok:
            raise PublicExportOperationError(
                result.error_code or "public_render_worker_failed"
            )
        rendered = _public_worker_output(
            dict(result.output_artifact or {}),
            retention_class="ephemeral",
        )
        async with session_factory() as session:
            registered = await artifact_service.register_ref(session, rendered)
            await session.commit()
            return registered

    png_refs = list(await asyncio.gather(*(render_one(reference) for reference in html_refs)))
    bundle = json.dumps(
        {
            "schemaVersion": "public-export-bundle/v1",
            "exportFormat": export_format,
            "slides": [
                _worker_ref(reference).to_wire() for reference in png_refs
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    async with session_factory() as session:
        bundle_ref = await artifact_service.persist_bytes(
            session,
            owner_id=claim.owner_id,
            tenant_id=tenant_id,
            kind="export-bundle",
            payload=bundle,
            media_type="application/vnd.presenton.export-bundle+json",
            created_by_operation_id=claim.operation_id,
            retention_class="ephemeral",
        )
        await session.commit()

    exported = await execute_isolated_export_operation(
        claim,
        input_artifact=_worker_ref(bundle_ref),
    )
    if not exported.ok:
        raise PublicExportOperationError(
            exported.error_code or "public_export_worker_failed"
        )
    published = _public_worker_output(
        dict(exported.output_artifact or {}),
        retention_class="published",
    )
    async with session_factory() as session:
        registered = await artifact_service.register_ref(session, published)
        await session.commit()
        return registered


__all__ = [
    "PublicExportOperationError",
    "build_isolated_operation_job",
    "execute_isolated_export_operation",
    "execute_isolated_render_operation",
    "load_worker_capability_signing_key",
    "render_and_export_authored_htmls",
]
