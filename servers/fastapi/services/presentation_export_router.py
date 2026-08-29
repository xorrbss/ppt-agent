"""Deterministic adapter routing with an UnsupportedCapability-only fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import hmac
import os
from pathlib import Path
from typing import Literal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import validate_export_attempt_provenance
from models.sql.slide import SlideModel
from services.authored_vnext.semantic.canonical import SemanticContractError
from services.authored_vnext.semantic.query import get_active_authored_emission
from services.presentation_export_adapters import (
    PresentationExportAdapterRequest,
    PresentationExportAdapterResult,
    run_general_adapter,
    run_native_clone_adapter,
    run_native_reconstructed_adapter,
)
from services.presentation_export_attempt_service import (
    begin_export_attempt,
    build_export_asset_manifest_sha256,
    build_export_composition_sha256,
    build_export_html_source_sha256,
    build_export_source_snapshot_sha256,
    initial_export_attempt_provenance,
    require_export_asset_digests,
    terminalize_export_attempt_failed,
    terminalize_export_attempt_succeeded,
)
from services.presentation_export_capabilities import (
    PresentationExportAdapterId,
    PresentationExportCapabilityAnalysis,
    PresentationExportContractError,
    PresentationExportFreshnessError,
    PresentationExportSlideInput,
    UnsupportedCapability,
    analyze_presentation_export_capabilities,
)
from services.template_v2_generation_service import (
    TemplateV2GenerationError,
    TemplateV2GenerationTarget,
    load_template_v2_generation_target,
)


PresentationExportAdapterRunner = Callable[
    [PresentationExportAdapterRequest],
    Awaitable[PresentationExportAdapterResult],
]


async def _resolve_attempt_source_snapshot_sha256(
    session: AsyncSession,
    *,
    presentation: PresentationModel,
    slides: Sequence[SlideModel],
    durable_presentation_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    export_format: Literal["pptx", "pdf"],
    pptx_mode: Literal["fidelity", "hybrid"],
) -> str:
    """Select the canonical source bound to this export attempt."""

    generic_digest = build_export_source_snapshot_sha256(
        presentation,
        slides,
        durable_presentation_id=durable_presentation_id,
    )
    if not (
        export_format == "pptx"
        and presentation.profile_driver_id == "authored"
        and pptx_mode == "hybrid"
    ):
        return generic_digest

    semantic_owner_id = owner_id or presentation.owner_id
    if semantic_owner_id is None or (
        presentation.owner_id is not None
        and presentation.owner_id != semantic_owner_id
    ):
        raise PresentationExportContractError(
            "authored_semantic_hybrid_owner_required"
        )
    try:
        active = await get_active_authored_emission(
            session,
            presentation_id=presentation.id,
            owner_id=semantic_owner_id,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
    except SemanticContractError as error:
        raise PresentationExportContractError(error.code) from error
    if (
        active.canonical_mode != "bundleGraph"
        or active.presentation_revision != presentation.revision
        or active.canonical_digest is None
    ):
        raise PresentationExportFreshnessError(
            "authored_semantic_hybrid_manifest_required"
        )
    return active.canonical_digest.removeprefix("sha256:")


DEFAULT_PRESENTATION_EXPORT_ADAPTERS: Mapping[
    PresentationExportAdapterId,
    PresentationExportAdapterRunner,
] = {
    "native_clone": run_native_clone_adapter,
    "native_reconstructed": run_native_reconstructed_adapter,
    "general": run_general_adapter,
}


@dataclass(frozen=True, slots=True)
class PresentationExportRouteResult:
    result: PresentationExportAdapterResult
    adapter_chain: tuple[PresentationExportAdapterId, ...]
    fallback_reason: str | None
    capability_evidence: dict
    asset_digests: dict[str, str]
    asset_digests_verified: bool = False

    def provenance(
        self,
        *,
        attempt_id: uuid.UUID | None = None,
        presentation_id: uuid.UUID | None = None,
        source_revision: int,
        source_snapshot_sha256: str,
        html_source_sha256: str | None = None,
        composition_sha256: str | None = None,
        asset_manifest_sha256: str | None = None,
    ) -> dict:
        return validate_export_attempt_provenance(
            {
                "schema_version": 1,
                "adapter_chain": self.adapter_chain,
                "capability_evidence": self.capability_evidence,
                "fallback_reason": self.fallback_reason,
                "asset_digests": self.asset_digests,
                "compiler_runtime": {
                    "compiler": self.result.compiler,
                    "compiler_version": self.result.compiler_version,
                    "runtime": self.result.runtime,
                    "runtime_version": self.result.runtime_version,
                },
                "freshness": {
                    "source_revision": source_revision,
                    "source_snapshot_sha256": source_snapshot_sha256,
                    "html_source_sha256": html_source_sha256,
                    "composition_sha256": composition_sha256,
                    "asset_manifest_sha256": asset_manifest_sha256,
                    "renderer_source_sha256": self.result.renderer_source_sha256,
                    "package_sha256": self.result.package_sha256,
                    "asset_digests_match": self.asset_digests_verified,
                    "package_digest_match": True,
                },
                "coverage_manifest": self.result.coverage_manifest,
                "atomic_binding": (
                    {
                        "presentation_id": str(presentation_id),
                        "export_attempt_id": str(attempt_id),
                        "presentation_revision": source_revision,
                        "canonical_source_sha256": source_snapshot_sha256,
                        "renderer_source_sha256": self.result.renderer_source_sha256,
                        "pptx_sha256": self.result.artifact_sha256,
                    }
                    if (
                        attempt_id is not None
                        and presentation_id is not None
                        and self.result.coverage_manifest is not None
                        and self.result.coverage_manifest.get("schemaVersion")
                        == "presenton.authored-hybrid-coverage/v2"
                    )
                    else None
                ),
            }
        )


@dataclass(frozen=True, slots=True)
class PresentationExportExecution:
    """Rendered bytes plus the durable identity awaiting publication."""

    attempt_id: uuid.UUID
    attempt_presentation_id: uuid.UUID
    owner_id: uuid.UUID | None
    owner_scope: str
    workspace_operation_id: uuid.UUID | None
    source_revision: int
    source_snapshot_sha256: str
    html_source_sha256: str
    composition_sha256: str
    asset_manifest_sha256: str
    route: PresentationExportRouteResult

    @property
    def presentation(self):
        return self.route.result.presentation

    @property
    def provenance(self) -> dict:
        return self.route.provenance(
            attempt_id=self.attempt_id,
            presentation_id=self.attempt_presentation_id,
            source_revision=self.source_revision,
            source_snapshot_sha256=self.source_snapshot_sha256,
            html_source_sha256=self.html_source_sha256,
            composition_sha256=self.composition_sha256,
            asset_manifest_sha256=self.asset_manifest_sha256,
        )


def _hash_export_artifact(path: Path) -> tuple[str, int]:
    digest = sha256()
    size_bytes = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


async def _require_standalone_artifact_integrity(
    execution: PresentationExportExecution,
) -> None:
    result = execution.route.result
    try:
        actual_sha256, actual_size = await asyncio.to_thread(
            _hash_export_artifact,
            Path(result.presentation.path),
        )
    except OSError as error:
        raise PresentationExportFreshnessError(
            "presentation_export_artifact_digest_mismatch",
            path="artifact",
        ) from error
    if actual_size != result.artifact_size_bytes or not hmac.compare_digest(
        actual_sha256,
        result.artifact_sha256,
    ):
        raise PresentationExportFreshnessError(
            "presentation_export_artifact_digest_mismatch",
            path="artifact",
        )


def _template_identity(
    presentation: PresentationModel,
) -> tuple[str, int, str] | None:
    theme = presentation.theme
    if not isinstance(theme, Mapping):
        return None
    raw = theme.get("template_v2")
    if not isinstance(raw, Mapping):
        return None
    template_id = raw.get("template_v2_id")
    revision = raw.get("template_v2_revision")
    digest = raw.get("template_snapshot_sha256")
    if (
        not isinstance(template_id, str)
        or not template_id.strip()
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or len(digest) != 71
    ):
        raise PresentationExportContractError(
            "presentation_export_template_provenance_invalid"
        )
    normalized_digest = digest.removeprefix("sha256:").lower()
    if any(character not in "0123456789abcdef" for character in normalized_digest):
        raise PresentationExportContractError(
            "presentation_export_template_provenance_invalid"
        )
    return template_id.strip(), revision, normalized_digest


def _slide_inputs(
    target: TemplateV2GenerationTarget,
    slides: Sequence[SlideModel],
) -> tuple:
    layouts = {item.layout.id: item.layout for item in target.layouts}
    if len(layouts) != len(target.layouts):
        raise PresentationExportContractError(
            "presentation_export_template_layout_duplicate"
        )
    result = []
    for index, slide in enumerate(sorted(slides, key=lambda item: item.index)):
        layout = layouts.get(slide.layout or "")
        if layout is None or not isinstance(slide.content, Mapping):
            raise PresentationExportContractError(
                "presentation_export_slide_contract_invalid",
                path=f"slides.{index}",
            )
        source_part = (
            target.native_source.layout_source_parts.get(layout.id)
            if target.native_source is not None
            else None
        )
        result.append(
            PresentationExportSlideInput(
                layout=layout,
                content=dict(slide.content),
                source_part=source_part,
            )
        )
    return tuple(result)


async def execute_presentation_export(
    session: AsyncSession,
    *,
    render_presentation_id: uuid.UUID,
    attempt_presentation_id: uuid.UUID,
    export_format: Literal["pptx", "pdf"],
    expected_render_revision: int,
    source_revision: int,
    title: str,
    cookie_header: str | None,
    owner_id: uuid.UUID | None,
    owner_scope: str,
    workspace_operation_id: uuid.UUID | None,
    request_hash: str,
    idempotency_key_hash: str,
    requested_adapter: str = "auto",
    template_target: TemplateV2GenerationTarget | None = None,
    pptx_mode: Literal["fidelity", "hybrid"] = "fidelity",
    font_embedding: bool = False,
) -> PresentationExportExecution:
    """Analyze a persisted snapshot, record intent, then execute one route.

    ``render_presentation_id`` may be an isolated staging deck while
    ``attempt_presentation_id`` remains the durable target identity.
    """

    presentation = await session.get(PresentationModel, render_presentation_id)
    if presentation is None:
        raise PresentationExportContractError("presentation_not_found")
    if presentation.revision != expected_render_revision:
        raise PresentationExportContractError("stale_presentation_revision")
    slides = (
        await session.execute(
            select(SlideModel)
            .where(SlideModel.presentation == render_presentation_id)
            .order_by(SlideModel.index, SlideModel.id)
        )
    ).scalars().all()
    source_snapshot_sha256 = await _resolve_attempt_source_snapshot_sha256(
        session,
        presentation=presentation,
        slides=slides,
        durable_presentation_id=attempt_presentation_id,
        owner_id=owner_id,
        export_format=export_format,
        pptx_mode=pptx_mode,
    )
    html_source_sha256 = build_export_html_source_sha256(slides)
    composition_sha256 = build_export_composition_sha256(presentation, slides)

    template_identity = _template_identity(presentation)
    if template_target is None and template_identity is not None:
        template_id, template_revision, _snapshot_digest = template_identity
        try:
            template_target = await load_template_v2_generation_target(
                session,
                template_id=template_id,
                revision=template_revision,
            )
        except TemplateV2GenerationError as error:
            raise PresentationExportContractError(error.code) from error
    if template_target is not None:
        if template_identity is None:
            raise PresentationExportContractError(
                "presentation_export_template_provenance_required"
            )
        template_id, template_revision, snapshot_digest = template_identity
        if (
            template_id != template_target.template_id
            or template_revision != template_target.revision
            or snapshot_digest != template_target.snapshot_sha256
        ):
            raise PresentationExportContractError(
                "presentation_export_template_snapshot_mismatch"
            )
        slide_inputs = _slide_inputs(template_target, slides)
    else:
        slide_inputs = ()

    analysis = analyze_presentation_export_capabilities(
        export_format=export_format,
        slides=slide_inputs,
        persisted_slide_payloads=(
            {"presentationTheme": presentation.theme},
            *(
                {
                    "content": slide.content,
                    "ui": slide.ui,
                    "properties": slide.properties,
                }
                for slide in slides
            ),
        ),
        native_clone_available=(
            template_target is not None and template_target.native_source is not None
        ),
    )
    analysis = PresentationExportCapabilityAnalysis(
        candidate_chain=analysis.candidate_chain,
        capability_evidence={
            **analysis.capability_evidence,
            "pptx_mode": pptx_mode if export_format == "pptx" else None,
            "font_embedding_requested": bool(font_embedding),
        },
        asset_digests=analysis.asset_digests,
    )
    asset_manifest_sha256 = build_export_asset_manifest_sha256(
        analysis.asset_digests
    )
    await require_export_asset_digests(
        session,
        expected_asset_digests=analysis.asset_digests,
    )
    attempt = await begin_export_attempt(
        session,
        presentation_id=attempt_presentation_id,
        owner_id=owner_id,
        owner_scope=owner_scope,
        workspace_operation_id=workspace_operation_id,
        export_format=export_format,
        source_revision=source_revision,
        source_snapshot_sha256=source_snapshot_sha256,
        request_hash=request_hash,
        idempotency_key_hash=idempotency_key_hash,
        requested_adapter=requested_adapter,
        provenance=initial_export_attempt_provenance(
            analysis,
            source_revision=source_revision,
            source_snapshot_sha256=source_snapshot_sha256,
            html_source_sha256=html_source_sha256,
            composition_sha256=composition_sha256,
            asset_manifest_sha256=asset_manifest_sha256,
        ),
    )
    try:
        routed = await route_presentation_export(
            PresentationExportAdapterRequest(
                presentation_id=render_presentation_id,
                title=title,
                export_format=export_format,
                expected_revision=expected_render_revision,
                cookie_header=cookie_header,
                profile_driver_id=presentation.profile_driver_id,
                pptx_mode=pptx_mode,
                font_embedding=font_embedding,
                slides=slide_inputs,
                template_target=template_target,
                workspace_operation_id=workspace_operation_id,
                export_attempt_id=attempt.id,
                canonical_source_digest=source_snapshot_sha256,
            ),
            analysis,
            asset_digests_verified=True,
        )
    except BaseException as error:
        code = getattr(error, "code", "presentation_export_unexpected_failure")
        path = getattr(error, "path", None)
        await terminalize_export_attempt_failed(
            session,
            attempt_id=attempt.id,
            expected_owner_id=owner_id,
            expected_owner_scope=owner_scope,
            expected_workspace_operation_id=workspace_operation_id,
            failure_code=code if isinstance(code, str) else "presentation_export_failed",
            failure_path=path if isinstance(path, str) else None,
        )
        raise
    return PresentationExportExecution(
        attempt_id=attempt.id,
        attempt_presentation_id=attempt_presentation_id,
        owner_id=owner_id,
        owner_scope=owner_scope,
        workspace_operation_id=workspace_operation_id,
        source_revision=source_revision,
        source_snapshot_sha256=source_snapshot_sha256,
        html_source_sha256=html_source_sha256,
        composition_sha256=composition_sha256,
        asset_manifest_sha256=asset_manifest_sha256,
        route=routed,
    )


async def terminalize_standalone_presentation_export(
    session: AsyncSession,
    execution: PresentationExportExecution,
) -> None:
    """Commit a synchronous export attempt after its immutable bytes exist."""

    try:
        await _require_standalone_artifact_integrity(execution)
        await terminalize_export_attempt_succeeded(
            session,
            attempt_id=execution.attempt_id,
            presentation_id=execution.attempt_presentation_id,
            expected_owner_id=execution.owner_id,
            expected_owner_scope=execution.owner_scope,
            expected_workspace_operation_id=execution.workspace_operation_id,
            source_revision=execution.source_revision,
            source_snapshot_sha256=execution.source_snapshot_sha256,
            selected_adapter=execution.route.result.selected_adapter,
            artifact_sha256=execution.route.result.artifact_sha256,
            artifact_size_bytes=execution.route.result.artifact_size_bytes,
            provenance=execution.provenance,
        )
        await session.commit()
    except BaseException as error:
        await session.rollback()
        await terminalize_export_attempt_failed(
            session,
            attempt_id=execution.attempt_id,
            expected_owner_id=execution.owner_id,
            expected_owner_scope=execution.owner_scope,
            expected_workspace_operation_id=execution.workspace_operation_id,
            failure_code=getattr(
                error,
                "code",
                "presentation_export_terminalization_failed",
            ),
            failure_path=getattr(error, "path", None),
        )
        raise


async def route_presentation_export(
    request: PresentationExportAdapterRequest,
    analysis: PresentationExportCapabilityAnalysis,
    *,
    asset_digests_verified: bool = False,
    adapters: Mapping[
        PresentationExportAdapterId,
        PresentationExportAdapterRunner,
    ] = DEFAULT_PRESENTATION_EXPORT_ADAPTERS,
) -> PresentationExportRouteResult:
    """Execute a bounded chain; every non-capability failure is a hard failure."""

    if not analysis.candidate_chain or len(analysis.candidate_chain) > 3:
        raise PresentationExportContractError(
            "presentation_export_adapter_chain_invalid"
        )
    attempted: list[PresentationExportAdapterId] = []
    unsupported: list[str] = []
    for adapter_id in analysis.candidate_chain:
        runner = adapters.get(adapter_id)
        if runner is None:
            raise PresentationExportContractError(
                "presentation_export_adapter_unregistered",
                path=adapter_id,
            )
        attempted.append(adapter_id)
        try:
            result = await runner(request)
        except UnsupportedCapability as error:
            if error.adapter != adapter_id:
                raise PresentationExportContractError(
                    "presentation_export_adapter_error_identity_mismatch",
                    path=adapter_id,
                ) from error
            unsupported.append(error.code)
            continue
        if result.selected_adapter != adapter_id:
            raise PresentationExportContractError(
                "presentation_export_adapter_result_identity_mismatch",
                path=adapter_id,
            )
        fallback_reason = ";".join(unsupported) if unsupported else None
        evidence = {
            **analysis.capability_evidence,
            "attempted_adapters": list(attempted),
            "selected_adapter": adapter_id,
            "unsupported_codes": unsupported,
        }
        return PresentationExportRouteResult(
            result=result,
            adapter_chain=tuple(attempted),
            fallback_reason=fallback_reason,
            capability_evidence=evidence,
            asset_digests=analysis.asset_digests,
            asset_digests_verified=asset_digests_verified,
        )
    raise UnsupportedCapability(
        unsupported[-1] if unsupported else "presentation_export_no_supported_adapter",
        adapter=attempted[-1],
    )


__all__ = [
    "DEFAULT_PRESENTATION_EXPORT_ADAPTERS",
    "PresentationExportExecution",
    "PresentationExportRouteResult",
    "execute_presentation_export",
    "route_presentation_export",
    "terminalize_standalone_presentation_export",
]
