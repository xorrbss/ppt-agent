"""Durable begin/complete/fail boundary for direct Next.js Hybrid exports."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import hmac
from pathlib import Path
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import (
    ExportPowerPointQaEvidence,
    PresentationExportAttempt,
    PresentationExportCoverageManifest,
)
from models.sql.slide import SlideModel
from models.sql.workspace import get_workspace_owner_scope
from services.presentation_export_attempt_service import (
    begin_export_attempt,
    build_export_asset_manifest_sha256,
    build_export_composition_sha256,
    build_export_html_source_sha256,
    build_export_source_snapshot_sha256,
    build_sync_export_hashes,
    initial_export_attempt_provenance,
    require_export_asset_digests,
    sha256_canonical_json,
    terminalize_export_attempt_failed,
    terminalize_export_attempt_succeeded,
)
from services.presentation_export_capabilities import (
    PresentationExportContractError,
    PresentationExportFreshnessError,
    analyze_presentation_export_capabilities,
)
from services.presentation_export_powerpoint_qa import (
    require_matching_hybrid_sidecars,
    resolve_hybrid_artifact,
    trusted_powerpoint_qa,
)
from utils.owner_context import get_current_owner_id


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def begin_direct_hybrid_export_attempt(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    expected_revision: int,
    renderer_source_sha256: str,
    idempotency_key: str,
) -> tuple[PresentationExportAttempt, dict[str, str | int]]:
    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise PresentationExportContractError("presentation_not_found")
    if presentation.revision != expected_revision:
        raise PresentationExportContractError("stale_presentation_revision")
    slides = (
        await session.execute(
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index, SlideModel.id)
        )
    ).scalars().all()
    canonical_digest = build_export_source_snapshot_sha256(presentation, slides)
    html_digest = build_export_html_source_sha256(slides)
    composition_digest = build_export_composition_sha256(presentation, slides)
    analysis = analyze_presentation_export_capabilities(
        export_format="pptx",
        persisted_slide_payloads=tuple(
            {"content": slide.content, "ui": slide.ui, "properties": slide.properties}
            for slide in slides
        ),
    )
    asset_manifest_digest = build_export_asset_manifest_sha256(analysis.asset_digests)
    await require_export_asset_digests(
        session, expected_asset_digests=analysis.asset_digests
    )
    request_hash, idempotency_key_hash = build_sync_export_hashes(
        presentation_id=presentation_id,
        export_format="pptx",
        source_revision=expected_revision,
        source_snapshot_sha256=canonical_digest,
        identity=idempotency_key,
    )
    attempt = await begin_export_attempt(
        session,
        presentation_id=presentation_id,
        owner_id=get_current_owner_id(),
        owner_scope=get_workspace_owner_scope(),
        workspace_operation_id=None,
        export_format="pptx",
        source_revision=expected_revision,
        source_snapshot_sha256=canonical_digest,
        request_hash=request_hash,
        idempotency_key_hash=idempotency_key_hash,
        requested_adapter="general",
        provenance=initial_export_attempt_provenance(
            analysis,
            source_revision=expected_revision,
            source_snapshot_sha256=canonical_digest,
            html_source_sha256=html_digest,
            composition_sha256=composition_digest,
            asset_manifest_sha256=asset_manifest_digest,
            renderer_source_sha256=renderer_source_sha256,
        ),
    )
    return attempt, {
        "presentation_revision": expected_revision,
        "canonical_source_digest": canonical_digest,
        "renderer_source_digest": renderer_source_sha256,
        "html_source_digest": html_digest,
        "composition_digest": composition_digest,
    }


async def complete_direct_hybrid_export_attempt(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    pptx_sha256: str,
    pptx_size_bytes: int,
    artifact_path: str,
    quality: dict[str, Any],
    coverage: dict[str, Any],
    powerpoint_qa: dict[str, Any] | None,
) -> tuple[PresentationExportAttempt, dict[str, Any]]:
    attempt = await session.get(PresentationExportAttempt, attempt_id)
    if attempt is None or attempt.presentation_id != presentation_id:
        raise PresentationExportContractError("presentation_export_attempt_not_found")
    if attempt.state != "running":
        raise PresentationExportContractError("presentation_export_attempt_not_running")
    manifest = PresentationExportCoverageManifest.model_validate(coverage)
    del powerpoint_qa  # Caller QA is never trusted.
    atomic = quality.get("atomicBinding")
    expected_binding = {
        "presentationRevision": attempt.source_revision,
        "canonicalSourceDigest": attempt.source_snapshot_sha256,
        "rendererSourceDigest": attempt.provenance["freshness"]["renderer_source_sha256"],
        "pptxSha256": pptx_sha256,
    }
    if (
        not isinstance(atomic, dict)
        or quality.get("exportAttemptId") != str(attempt.id)
        or any(atomic.get(key) != value for key, value in expected_binding.items())
    ):
        raise PresentationExportContractError("presentation_export_atomic_binding_mismatch")
    source = manifest.source
    if (
        source.export_attempt_id != attempt.id
        or source.presentation_revision != attempt.source_revision
        or source.canonical_source_digest != attempt.source_snapshot_sha256
        or source.source_digest != expected_binding["rendererSourceDigest"]
    ):
        raise PresentationExportContractError("presentation_export_coverage_binding_mismatch")
    presentation = await session.get(PresentationModel, presentation_id)
    slides = (
        await session.execute(
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index, SlideModel.id)
        )
    ).scalars().all()
    if presentation is None or presentation.revision != attempt.source_revision:
        raise PresentationExportFreshnessError("stale_presentation_revision")
    if build_export_source_snapshot_sha256(presentation, slides) != attempt.source_snapshot_sha256:
        raise PresentationExportFreshnessError("presentation_export_source_digest_mismatch")
    artifact = resolve_hybrid_artifact(artifact_path)
    actual_size = artifact.stat().st_size
    actual_sha256 = await asyncio.to_thread(_sha256_file, artifact)
    if actual_size != pptx_size_bytes or not hmac.compare_digest(actual_sha256, pptx_sha256):
        raise PresentationExportFreshnessError("presentation_export_artifact_digest_mismatch")
    require_matching_hybrid_sidecars(
        artifact,
        quality=quality,
        coverage=coverage,
        canonical_digest=sha256_canonical_json,
    )
    if manifest.ooxml_readback is None or manifest.ooxml_readback.status != "passed":
        raise PresentationExportContractError("presentation_export_ooxml_readback_required")
    qa = ExportPowerPointQaEvidence.model_validate(
        await trusted_powerpoint_qa(artifact, manifest.total_slides)
    )
    if qa.status == "passed" and (
        qa.native_text != manifest.ooxml_readback.native_text
        or qa.native_table != manifest.ooxml_readback.native_table
        or qa.native_chart != manifest.ooxml_readback.native_chart
    ):
        raise PresentationExportContractError(
            "presentation_export_powerpoint_native_readback_mismatch"
        )
    provenance = dict(attempt.provenance)
    provenance["compiler_runtime"] = {
        "compiler": "authored-hybrid",
        "compiler_version": "2",
        "runtime": "chromium-native-layer-export",
        "runtime_version": "presenton.render-artifact/v1",
    }
    freshness = dict(provenance["freshness"])
    freshness["package_sha256"] = pptx_sha256
    provenance["freshness"] = freshness
    provenance["coverage_manifest"] = manifest.model_dump(mode="json", by_alias=True)
    provenance["atomic_binding"] = {
        "export_attempt_id": str(attempt.id),
        "presentation_revision": attempt.source_revision,
        "canonical_source_sha256": attempt.source_snapshot_sha256,
        "renderer_source_sha256": expected_binding["rendererSourceDigest"],
        "pptx_sha256": pptx_sha256,
    }
    provenance["powerpoint_qa"] = qa.model_dump(mode="json")
    await terminalize_export_attempt_succeeded(
        session,
        attempt_id=attempt.id,
        presentation_id=presentation_id,
        expected_owner_id=attempt.owner_id,
        expected_owner_scope=attempt.owner_scope,
        expected_workspace_operation_id=None,
        source_revision=attempt.source_revision,
        source_snapshot_sha256=attempt.source_snapshot_sha256,
        selected_adapter="general",
        artifact_sha256=pptx_sha256,
        artifact_size_bytes=pptx_size_bytes,
        provenance=provenance,
    )
    await session.commit()
    await session.refresh(attempt)
    return attempt, qa.model_dump(mode="json", by_alias=True)


async def fail_direct_hybrid_export_attempt(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    failure_code: str,
) -> None:
    attempt = await session.get(PresentationExportAttempt, attempt_id)
    if attempt is None or attempt.presentation_id != presentation_id:
        raise PresentationExportContractError("presentation_export_attempt_not_found")
    await terminalize_export_attempt_failed(
        session,
        attempt_id=attempt.id,
        expected_owner_id=attempt.owner_id,
        expected_owner_scope=attempt.owner_scope,
        expected_workspace_operation_id=None,
        failure_code=failure_code,
        failure_path=None,
    )


__all__ = [
    "begin_direct_hybrid_export_attempt",
    "complete_direct_hybrid_export_attempt",
    "fail_direct_hybrid_export_attempt",
]
