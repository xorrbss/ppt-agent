"""Durable begin/complete/fail boundary for direct Next.js Hybrid exports."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
from pathlib import Path
from typing import Any
import uuid
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import (
    ExportAtomicBinding,
    ExportPowerPointQaEvidence,
    PresentationExportAttempt,
    PresentationExportCoverageManifest,
)
from models.sql.slide import SlideModel
from models.authored_vnext.semantic.contracts import (
    AuthoredEmissionBundleV1,
    ExportAttemptDigestBindingV1,
    SemanticUnavailableReceiptV1,
)
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
    publish_finalized_hybrid_sidecars,
    require_matching_hybrid_sidecars,
    resolve_hybrid_artifact,
    trusted_powerpoint_qa,
)
from services.authored_vnext.semantic.canonical import (
    canonical_digest as semantic_canonical_digest,
)
from services.authored_vnext.semantic.export_binding import (
    bind_existing_export_attempt,
)
from services.authored_vnext.semantic.query import get_active_authored_emission
from utils.owner_context import get_current_owner_id


HYBRID_ATTEMPT_RETENTION_DAYS = 180
HYBRID_EVIDENCE_RETENTION_DAYS = 30


def _prefixed_digest(value: str) -> str:
    return value if value.startswith("sha256:") else f"sha256:{value}"


async def _active_semantic_emission(
    session: AsyncSession,
    presentation_id: uuid.UUID,
):
    owner_id = get_current_owner_id()
    if owner_id is None:
        return None
    return await get_active_authored_emission(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
    )


async def _current_export_canonical_digest(
    session: AsyncSession,
    *,
    presentation: PresentationModel,
    slides: list[SlideModel],
) -> str:
    """Resolve the same canonical source selected by the begin boundary."""

    active = await _active_semantic_emission(session, presentation.id)
    if active is not None and active.canonical_mode == "bundleGraph":
        if (
            active.presentation_revision != presentation.revision
            or active.canonical_digest is None
        ):
            raise PresentationExportFreshnessError(
                "authored_semantic_export_stale_manifest"
            )
        return active.canonical_digest.removeprefix("sha256:")
    return build_export_source_snapshot_sha256(presentation, slides)


async def _bind_completed_semantic_export_attempt(
    session: AsyncSession,
    *,
    attempt: PresentationExportAttempt,
    mode: str,
    renderer_source_sha256: str,
    pptx_sha256: str,
    receipt_payloads: list[dict[str, Any]],
) -> str | None:
    active = await _active_semantic_emission(session, attempt.presentation_id)
    owner_id = get_current_owner_id()
    if (
        active is None
        or owner_id is None
        or active.canonical_mode != "bundleGraph"
        or active.manifest is None
        or active.manifest.canonical_digest is None
    ):
        return None
    manifest = active.manifest
    if (
        active.presentation_revision != attempt.source_revision
        or active.canonical_digest is None
        or active.canonical_digest.removeprefix("sha256:")
        != attempt.source_snapshot_sha256
    ):
        raise PresentationExportFreshnessError(
            "authored_semantic_export_stale_manifest"
        )
    semantic_digests: list[str] = []
    origins: list[uuid.UUID | None] = []
    for bundle in active.bundles:
        if not isinstance(bundle, AuthoredEmissionBundleV1):
            raise PresentationExportContractError(
                "authored_semantic_export_bundle_kind_unsupported"
            )
        if bundle.semantic_component.digest is None:
            raise PresentationExportContractError(
                "authored_semantic_export_component_digest_missing"
            )
        semantic_digests.append(bundle.semantic_component.digest)
        origins.append(bundle.origin_generation_attempt_id)
    binding = ExportAttemptDigestBindingV1(
        export_attempt_id=attempt.id,
        presentation_id=attempt.presentation_id,
        presentation_revision=attempt.source_revision,
        operation_id=manifest.operation_id,
        generation_attempt_id=manifest.generation_attempt_id,
        slide_origin_generation_attempt_ids=origins,
        canonical_digest=active.canonical_digest,
        manifest_digest=manifest.canonical_digest,
        semantic_digests=semantic_digests,
        master_contract_digest=manifest.master_contract_digest,
        composition_digest=manifest.composition_digest,
        font_manifest_digest=manifest.font_manifest_digest,
        asset_manifest_digest=manifest.asset_manifest_digest,
        quality_policy_digest=semantic_canonical_digest(
            {"qualityPolicyVersion": manifest.quality_policy_version}
        ),
        mode=mode,
        renderer_source_digest=_prefixed_digest(renderer_source_sha256),
        serializer_id=("authored-hybrid" if mode == "hybrid" else "authored-fidelity"),
        serializer_version=("2" if mode == "hybrid" else "1"),
        receipt_digests=[
            semantic_canonical_digest(payload) for payload in receipt_payloads
        ],
        pptx_digest=_prefixed_digest(pptx_sha256),
    )
    sealed = await bind_existing_export_attempt(
        session,
        owner_id=owner_id,
        binding=binding,
        tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
    )
    return sealed.binding_digest


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_hybrid_atomic_binding(
    value: Any,
    expected: dict[str, Any],
    *,
    export_attempt_id: uuid.UUID,
) -> ExportAtomicBinding:
    """Reject sidecars whose atomic identity is not exactly this export."""

    try:
        if not isinstance(value, dict) or set(value) != {
            "presentationId",
            "presentationRevision",
            "canonicalSourceDigest",
            "rendererSourceDigest",
            "pptxSha256",
        }:
            raise ValueError("hybrid atomic binding keys are invalid")
        supplied = ExportAtomicBinding.model_validate(
            {
                "presentation_id": value["presentationId"],
                "export_attempt_id": export_attempt_id,
                "presentation_revision": value["presentationRevision"],
                "canonical_source_sha256": value["canonicalSourceDigest"],
                "renderer_source_sha256": value["rendererSourceDigest"],
                "pptx_sha256": value["pptxSha256"],
            }
        )
        expected_model = ExportAtomicBinding.model_validate(
            {
                "presentation_id": expected["presentationId"],
                "export_attempt_id": export_attempt_id,
                "presentation_revision": expected["presentationRevision"],
                "canonical_source_sha256": expected["canonicalSourceDigest"],
                "renderer_source_sha256": expected["rendererSourceDigest"],
                "pptx_sha256": expected["pptxSha256"],
            }
        )
    except (TypeError, ValueError) as error:
        raise PresentationExportContractError(
            "presentation_export_atomic_binding_mismatch"
        ) from error
    if supplied != expected_model:
        raise PresentationExportContractError(
            "presentation_export_atomic_binding_mismatch"
        )
    return supplied


async def _begin_direct_authored_export_attempt(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    expected_revision: int,
    renderer_source_sha256: str,
    idempotency_key: str,
    export_mode: str,
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
    active_semantic = await _active_semantic_emission(session, presentation_id)
    if active_semantic is not None and active_semantic.canonical_mode == "bundleGraph":
        if (
            active_semantic.presentation_revision != expected_revision
            or active_semantic.canonical_digest is None
        ):
            raise PresentationExportFreshnessError(
                "authored_semantic_export_stale_manifest"
            )
        canonical_digest = active_semantic.canonical_digest.removeprefix("sha256:")
        if export_mode == "hybrid" and any(
            not isinstance(bundle, AuthoredEmissionBundleV1)
            or isinstance(
                bundle.semantic_component,
                SemanticUnavailableReceiptV1,
            )
            for bundle in active_semantic.bundles
        ):
            raise PresentationExportContractError(
                "authored_semantic_hybrid_degraded_fidelity_only"
            )
        if active_semantic.manifest is not None:
            composition_digest = (
                active_semantic.manifest.composition_digest.removeprefix("sha256:")
            )
    elif (
        export_mode == "hybrid"
        and os.getenv("AUTHORED_SEMANTIC_BUNDLE_WRITES_ENABLED", "false").lower()
        == "true"
    ):
        raise PresentationExportContractError(
            "authored_semantic_hybrid_manifest_required"
        )
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
    provenance = initial_export_attempt_provenance(
        analysis,
        source_revision=expected_revision,
        source_snapshot_sha256=canonical_digest,
        html_source_sha256=html_digest,
        composition_sha256=composition_digest,
        asset_manifest_sha256=asset_manifest_digest,
        renderer_source_sha256=renderer_source_sha256,
    )
    provenance["capability_evidence"]["export_mode"] = export_mode
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
        provenance=provenance,
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
    if (
        attempt is None
        or attempt.presentation_id != presentation_id
        or attempt.owner_id != get_current_owner_id()
        or attempt.owner_scope != get_workspace_owner_scope()
    ):
        raise PresentationExportContractError("presentation_export_attempt_not_found")
    if attempt.state != "running":
        raise PresentationExportContractError("presentation_export_attempt_not_running")
    if (attempt.provenance.get("capability_evidence") or {}).get("export_mode") != "hybrid":
        raise PresentationExportContractError("presentation_export_attempt_mode_mismatch")
    manifest = PresentationExportCoverageManifest.model_validate(coverage)
    del powerpoint_qa  # Caller QA is never trusted.
    atomic = quality.get("atomicBinding")
    expected_binding = {
        "presentationId": str(presentation_id),
        "presentationRevision": attempt.source_revision,
        "canonicalSourceDigest": attempt.source_snapshot_sha256,
        "rendererSourceDigest": attempt.provenance["freshness"]["renderer_source_sha256"],
        "pptxSha256": pptx_sha256,
    }
    _validate_hybrid_atomic_binding(
        atomic,
        expected_binding,
        export_attempt_id=attempt.id,
    )
    if quality.get("exportAttemptId") != str(attempt.id):
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
    if (
        await _current_export_canonical_digest(
            session, presentation=presentation, slides=slides
        )
        != attempt.source_snapshot_sha256
    ):
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
        or qa.native_image != manifest.ooxml_readback.native_image
        or qa.native_shape != manifest.ooxml_readback.native_shape
        or qa.native_table != manifest.ooxml_readback.native_table
        or qa.native_chart != manifest.ooxml_readback.native_chart
    ):
        qa = qa.model_copy(update={"status": "failed"})
    visual_parity_status = qa.status
    finalized_quality = deepcopy(quality)
    finalized_quality["atomicBinding"] = expected_binding
    finalized_quality["powerPointQa"] = qa.model_dump(mode="json", by_alias=True)
    finalized_coverage = PresentationExportCoverageManifest.model_validate(
        manifest.model_copy(
            update={
                "visual_parity": visual_parity_status,
                "slides": [
                    slide.model_copy(
                        update={"visual_parity": visual_parity_status}
                    )
                    for slide in manifest.slides
                ],
            }
        ).model_dump(mode="json", by_alias=True)
    )
    finalized_coverage_json = finalized_coverage.model_dump(mode="json", by_alias=True)
    atomic_binding = {
        "presentation_id": str(presentation_id),
        "export_attempt_id": str(attempt.id),
        "presentation_revision": attempt.source_revision,
        "canonical_source_sha256": attempt.source_snapshot_sha256,
        "renderer_source_sha256": expected_binding["rendererSourceDigest"],
        "pptx_sha256": pptx_sha256,
    }
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
    provenance["quality_sidecar"] = finalized_quality
    provenance["coverage_manifest"] = finalized_coverage_json
    provenance["atomic_binding"] = atomic_binding
    provenance["powerpoint_qa"] = qa.model_dump(mode="json")
    provenance["visual_parity"] = {
        "status": visual_parity_status,
        "binding": atomic_binding,
        "quality_sidecar_sha256": sha256_canonical_json(finalized_quality),
        "coverage_sidecar_sha256": sha256_canonical_json(finalized_coverage_json),
        "powerpoint_qa": qa.model_dump(mode="json"),
    }
    publish_finalized_hybrid_sidecars(
        artifact,
        quality=finalized_quality,
        coverage=finalized_coverage_json,
    )
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
    await session.refresh(attempt)
    semantic_binding_digest = await _bind_completed_semantic_export_attempt(
        session,
        attempt=attempt,
        mode="hybrid",
        renderer_source_sha256=expected_binding["rendererSourceDigest"],
        pptx_sha256=pptx_sha256,
        receipt_payloads=[finalized_quality, finalized_coverage_json, qa.model_dump(mode="json")],
    )
    await session.commit()
    await session.refresh(attempt)
    return attempt, {
        "powerPointQa": qa.model_dump(mode="json", by_alias=True),
        "visualParity": visual_parity_status,
        "quality": finalized_quality,
        "coverage": finalized_coverage_json,
        "semanticBindingDigest": semantic_binding_digest,
        "qualitySidecarSha256": provenance["visual_parity"][
            "quality_sidecar_sha256"
        ],
        "coverageSidecarSha256": provenance["visual_parity"][
            "coverage_sidecar_sha256"
        ],
    }


async def begin_direct_hybrid_export_attempt(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    expected_revision: int,
    renderer_source_sha256: str,
    idempotency_key: str,
) -> tuple[PresentationExportAttempt, dict[str, str | int]]:
    return await _begin_direct_authored_export_attempt(
        session,
        presentation_id=presentation_id,
        expected_revision=expected_revision,
        renderer_source_sha256=renderer_source_sha256,
        idempotency_key=idempotency_key,
        export_mode="hybrid",
    )


async def begin_direct_fidelity_export_attempt(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    expected_revision: int,
    renderer_source_sha256: str,
    idempotency_key: str,
) -> tuple[PresentationExportAttempt, dict[str, str | int]]:
    return await _begin_direct_authored_export_attempt(
        session,
        presentation_id=presentation_id,
        expected_revision=expected_revision,
        renderer_source_sha256=renderer_source_sha256,
        idempotency_key=idempotency_key,
        export_mode="fidelity",
    )


async def complete_direct_fidelity_export_attempt(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    pptx_sha256: str,
    pptx_size_bytes: int,
    artifact_path: str,
) -> tuple[PresentationExportAttempt, dict[str, Any]]:
    attempt = await session.get(PresentationExportAttempt, attempt_id)
    if (
        attempt is None
        or attempt.presentation_id != presentation_id
        or attempt.owner_id != get_current_owner_id()
        or attempt.owner_scope != get_workspace_owner_scope()
    ):
        raise PresentationExportContractError("presentation_export_attempt_not_found")
    if attempt.state != "running":
        raise PresentationExportContractError("presentation_export_attempt_not_running")
    capability_evidence = attempt.provenance.get("capability_evidence") or {}
    if capability_evidence.get("export_mode") != "fidelity":
        raise PresentationExportContractError("presentation_export_attempt_mode_mismatch")

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
    if (
        await _current_export_canonical_digest(
            session, presentation=presentation, slides=slides
        )
        != attempt.source_snapshot_sha256
    ):
        raise PresentationExportFreshnessError("presentation_export_source_digest_mismatch")

    artifact = resolve_hybrid_artifact(artifact_path)
    actual_size = artifact.stat().st_size
    actual_sha256 = await asyncio.to_thread(_sha256_file, artifact)
    if actual_size != pptx_size_bytes or not hmac.compare_digest(actual_sha256, pptx_sha256):
        raise PresentationExportFreshnessError("presentation_export_artifact_digest_mismatch")

    renderer_source_sha256 = attempt.provenance["freshness"]["renderer_source_sha256"]
    atomic_binding = {
        "presentation_id": str(presentation_id),
        "export_attempt_id": str(attempt.id),
        "presentation_revision": attempt.source_revision,
        "canonical_source_sha256": attempt.source_snapshot_sha256,
        "renderer_source_sha256": renderer_source_sha256,
        "pptx_sha256": pptx_sha256,
    }
    provenance = dict(attempt.provenance)
    provenance["compiler_runtime"] = {
        "compiler": "authored-fidelity",
        "compiler_version": "1",
        "runtime": "chromium-dom-export",
        "runtime_version": "presenton.render-artifact/v1",
    }
    freshness = dict(provenance["freshness"])
    freshness["package_sha256"] = pptx_sha256
    provenance["freshness"] = freshness
    provenance["atomic_binding"] = atomic_binding
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
    await session.refresh(attempt)
    semantic_binding_digest = await _bind_completed_semantic_export_attempt(
        session,
        attempt=attempt,
        mode="fidelity",
        renderer_source_sha256=renderer_source_sha256,
        pptx_sha256=pptx_sha256,
        receipt_payloads=[atomic_binding],
    )
    await session.commit()
    await session.refresh(attempt)
    return attempt, {
        "visualParity": "not-verified",
        "promotionReady": False,
        "semanticBindingDigest": semantic_binding_digest,
        "atomicBinding": {
            "presentationId": str(presentation_id),
            "exportAttemptId": str(attempt.id),
            "presentationRevision": attempt.source_revision,
            "canonicalSourceDigest": attempt.source_snapshot_sha256,
            "rendererSourceDigest": renderer_source_sha256,
            "pptxSha256": pptx_sha256,
        },
    }


def _is_hybrid_attempt(attempt: PresentationExportAttempt) -> bool:
    provenance = attempt.provenance or {}
    evidence = provenance.get("capability_evidence") or {}
    compiler = (provenance.get("compiler_runtime") or {}).get("compiler")
    return (
        evidence.get("export_mode") == "hybrid"
        or compiler == "authored-hybrid"
        or provenance.get("coverage_manifest") is not None
    )


def _projected_value(value: dict[str, Any], snake: str, camel: str) -> Any:
    return value.get(snake) if snake in value else value.get(camel)


async def list_direct_hybrid_export_attempt_history(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
) -> dict[str, Any]:
    """Project immutable attempts into derived active/resolved/superseded history."""

    owner_id = get_current_owner_id()
    owner_scope = get_workspace_owner_scope()
    attempts = (
        await session.execute(
            select(PresentationExportAttempt)
            .where(
                PresentationExportAttempt.presentation_id == presentation_id,
                PresentationExportAttempt.owner_id == owner_id,
                PresentationExportAttempt.owner_scope == owner_scope,
                PresentationExportAttempt.format == "pptx",
                PresentationExportAttempt.state.in_(("succeeded", "failed")),
            )
            .order_by(
                PresentationExportAttempt.created_at.desc(),
                PresentationExportAttempt.id.desc(),
            )
        )
    ).scalars().all()
    attempts = [attempt for attempt in attempts if _is_hybrid_attempt(attempt)]
    newest_success: dict[tuple[int, str], PresentationExportAttempt] = {}
    for attempt in attempts:
        key = (attempt.source_revision, attempt.format)
        if attempt.state == "succeeded" and key not in newest_success:
            newest_success[key] = attempt

    projected: list[dict[str, Any]] = []
    for attempt in attempts:
        successor = newest_success.get((attempt.source_revision, attempt.format))
        successor_is_later = bool(
            successor is not None
            and successor.id != attempt.id
            and successor.created_at > attempt.created_at
        )
        if successor_is_later:
            resolution = "resolved" if attempt.state == "failed" else "superseded"
        else:
            resolution = "active"
        provenance = attempt.provenance or {}
        freshness = provenance.get("freshness") or {}
        parity = provenance.get("visual_parity") or {}
        binding = parity.get("binding") or {}
        qa = parity.get("powerpoint_qa") or provenance.get("powerpoint_qa") or {}
        rendered = qa.get("rendered_png_sha256") or []
        coverage = provenance.get("coverage_manifest") or {}
        quality = provenance.get("quality_sidecar") or {}
        expires_at = (
            attempt.completed_at + timedelta(days=HYBRID_EVIDENCE_RETENTION_DAYS)
            if attempt.completed_at
            else None
        )
        expired = bool(
            expires_at
            and datetime.now(timezone.utc)
            >= expires_at.replace(tzinfo=expires_at.tzinfo or timezone.utc)
        )
        evidence_complete = (
            attempt.state == "succeeded"
            and bool(parity)
            and binding.get("presentation_id") == str(presentation_id)
            and binding.get("export_attempt_id") == str(attempt.id)
            and binding.get("presentation_revision") == attempt.source_revision
            and binding.get("canonical_source_sha256")
            == attempt.source_snapshot_sha256
            and binding.get("renderer_source_sha256")
            == freshness.get("renderer_source_sha256")
            and binding.get("pptx_sha256") == attempt.artifact_sha256
        )
        projected.append(
            {
                "exportAttemptId": str(attempt.id),
                "state": attempt.state,
                "resolution": resolution,
                **(
                    {"supersededByAttemptId": str(successor.id)}
                    if successor_is_later and successor is not None
                    else {}
                ),
                "presentationRevision": attempt.source_revision,
                "format": attempt.format,
                "createdAt": attempt.created_at.isoformat(),
                "completedAt": (
                    attempt.completed_at.isoformat() if attempt.completed_at else None
                ),
                "failureCode": attempt.failure_code,
                "evidence": {
                    "status": (
                        "expired"
                        if evidence_complete and expired
                        else "complete" if evidence_complete else "incomplete"
                    ),
                    "attemptId": str(attempt.id),
                    "presentationRevision": attempt.source_revision,
                    "canonicalSourceDigest": attempt.source_snapshot_sha256,
                    "rendererSourceDigest": freshness.get("renderer_source_sha256"),
                    "pptxSha256": attempt.artifact_sha256,
                    "pptxSizeBytes": attempt.artifact_size_bytes,
                    "coverageManifestSha256": parity.get(
                        "coverage_sidecar_sha256"
                    ),
                    "renderManifestSha256": (
                        sha256_canonical_json(rendered) if rendered else None
                    ),
                    "reportManifestSha256": parity.get(
                        "quality_sidecar_sha256"
                    ),
                    "bundleManifestSha256": (
                        sha256_canonical_json(parity) if parity else None
                    ),
                    "slidePngSha256": rendered,
                    "visualParity": parity.get(
                        "status", "failed" if attempt.state == "failed" else "not-verified"
                    ),
                    "nativeText": _projected_value(
                        coverage, "native_text", "nativeText"
                    ),
                    "nativeImage": _projected_value(
                        coverage, "native_image", "nativeImage"
                    ),
                    "nativeShape": _projected_value(
                        coverage, "native_shape", "nativeShape"
                    ),
                    "nativeTable": _projected_value(
                        coverage, "native_table", "nativeTable"
                    ),
                    "nativeChart": _projected_value(
                        coverage, "native_chart", "nativeChart"
                    ),
                    "rasterRemaining": _projected_value(
                        coverage, "raster_remaining", "rasterRemaining"
                    ),
                    "typographyIssues": _projected_value(
                        coverage, "typography_issues", "typographyIssues"
                    )
                    or [],
                    "fallbackReasonCounts": _projected_value(
                        quality, "fallback_reason_counts", "fallbackReasonCounts"
                    )
                    or {},
                    "expiresAt": expires_at.isoformat() if expires_at else None,
                },
            }
        )
    return {
        "schemaVersion": "presenton.authored-hybrid-export-history/v1",
        "retention": {
            "attemptRetentionDays": HYBRID_ATTEMPT_RETENTION_DAYS,
            "evidenceRetentionDays": HYBRID_EVIDENCE_RETENTION_DAYS,
            "expiryAction": "mark_expired_retain_metadata",
            "deletionAuthorized": False,
        },
        "attempts": projected,
    }


async def fail_direct_hybrid_export_attempt(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    failure_code: str,
) -> None:
    attempt = await session.get(PresentationExportAttempt, attempt_id)
    if (
        attempt is None
        or attempt.presentation_id != presentation_id
        or attempt.owner_id != get_current_owner_id()
        or attempt.owner_scope != get_workspace_owner_scope()
    ):
        raise PresentationExportContractError("presentation_export_attempt_not_found")
    if (attempt.provenance.get("capability_evidence") or {}).get("export_mode") != "hybrid":
        raise PresentationExportContractError("presentation_export_attempt_mode_mismatch")
    await terminalize_export_attempt_failed(
        session,
        attempt_id=attempt.id,
        expected_owner_id=attempt.owner_id,
        expected_owner_scope=attempt.owner_scope,
        expected_workspace_operation_id=None,
        failure_code=failure_code,
        failure_path=None,
    )


async def fail_direct_fidelity_export_attempt(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    failure_code: str,
) -> None:
    attempt = await session.get(PresentationExportAttempt, attempt_id)
    if (
        attempt is None
        or attempt.presentation_id != presentation_id
        or attempt.owner_id != get_current_owner_id()
        or attempt.owner_scope != get_workspace_owner_scope()
    ):
        raise PresentationExportContractError("presentation_export_attempt_not_found")
    if (attempt.provenance.get("capability_evidence") or {}).get("export_mode") != "fidelity":
        raise PresentationExportContractError("presentation_export_attempt_mode_mismatch")
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
    "begin_direct_fidelity_export_attempt",
    "complete_direct_hybrid_export_attempt",
    "complete_direct_fidelity_export_attempt",
    "fail_direct_hybrid_export_attempt",
    "fail_direct_fidelity_export_attempt",
    "list_direct_hybrid_export_attempt_history",
]
