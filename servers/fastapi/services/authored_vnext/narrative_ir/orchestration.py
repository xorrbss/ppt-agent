"""Whole-operation C-path orchestration for Narrative IR generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from .checkpoint import (
    NarrativeCheckpointRepository,
    reseal_checkpoint_for_retry,
    seal_checkpoint,
    validate_checkpoint,
)
from models.authored_vnext.narrative_ir_contracts import (
    ContentGraphV1,
    FrameSearchReceiptV1,
    FrameSelectionReceiptV1,
    NarrativeCheckpointV1,
    NarrativeIRV1,
    ProviderCallBudgetV1,
    RouteResolutionReceiptV1,
    model_payload,
)
from .director import NarrativeProvider, Reservation, run_narrative_director
from .frame_search import search_source_native_frames, select_source_native_frames
from .validation import canonical_digest


class NarrativeOrchestrationError(RuntimeError):
    code = "narrative_ir_orchestration_failed"
    retryable = False

    def __init__(self, code: str | None = None) -> None:
        resolved = code or self.code
        super().__init__(resolved)
        self.code = resolved


class SourceNativeExecutor(Protocol):
    execution_mode: str

    async def execute(
        self,
        *,
        narrative: NarrativeIRV1,
        selection: FrameSelectionReceiptV1,
        route: RouteResolutionReceiptV1,
        checkpoint: NarrativeCheckpointV1,
    ) -> Mapping[str, object]: ...


class AuthoredSceneFallbackExecutor(Protocol):
    execution_mode: str
    provider_html_enabled: bool

    async def execute(
        self,
        *,
        narrative: NarrativeIRV1,
        route: RouteResolutionReceiptV1,
        checkpoint: NarrativeCheckpointV1,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class NarrativeOperationResult:
    route: RouteResolutionReceiptV1
    checkpoint: NarrativeCheckpointV1
    execution: Mapping[str, object]
    resumed: bool

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "narrative-ir-operation-result/v1",
            "routeResolutionReceipt": model_payload(self.route),
            "checkpointDigest": self.checkpoint.checkpoint_digest,
            "lineageDigest": self.checkpoint.lineage_digest,
            "execution": dict(self.execution),
            "resumed": self.resumed,
        }


def _route_receipt(
    *,
    narrative: NarrativeIRV1,
    call_budget: ProviderCallBudgetV1,
    search: FrameSearchReceiptV1 | None,
    selection: FrameSelectionReceiptV1 | None,
) -> RouteResolutionReceiptV1:
    if search is None:
        final_route = "authored-scene-fallback"
        fallback_reason = "design-pack-unavailable"
    elif search.status == "no-compatible-frame":
        final_route = "authored-scene-fallback"
        fallback_reason = "no-compatible-frame"
    else:
        if selection is None:
            raise NarrativeOrchestrationError("narrative_frame_selection_missing")
        final_route = "source-native"
        fallback_reason = None
    unsigned: dict[str, object] = {
        "schemaVersion": "narrative-route-resolution-receipt/v1",
        "operationId": str(narrative.operation_id),
        "narrativeDigest": narrative.narrative_digest,
        "designPackId": search.design_pack_id if search else None,
        "designPackVersion": search.design_pack_version if search else None,
        "designPackManifestDigest": search.design_pack_manifest_digest if search else None,
        "frameRegistryDigest": search.frame_registry_digest if search else None,
        "frameSearchReceiptDigest": search.receipt_digest if search else None,
        "frameSelectionReceiptDigest": selection.receipt_digest if selection else None,
        "providerCallBudget": model_payload(call_budget),
        "finalRoute": final_route,
        "fallbackReason": fallback_reason,
        "wholeOperation": True,
        "legacyProviderHtmlAllowed": False,
    }
    try:
        return RouteResolutionReceiptV1.model_validate(
            {**unsigned, "receiptDigest": canonical_digest(unsigned)}
        )
    except ValidationError as error:
        raise NarrativeOrchestrationError("narrative_route_receipt_invalid") from error


def _validate_executors(
    source_native: SourceNativeExecutor,
    fallback: AuthoredSceneFallbackExecutor,
) -> None:
    if getattr(source_native, "execution_mode", None) != "source-native":
        raise NarrativeOrchestrationError("source_native_executor_invalid")
    if (
        getattr(fallback, "execution_mode", None) != "server-owned-authored-scene"
        or getattr(fallback, "provider_html_enabled", None) is not False
    ):
        raise NarrativeOrchestrationError("legacy_provider_html_fallback_forbidden")


async def _execute_route(
    *,
    checkpoint: NarrativeCheckpointV1,
    source_native: SourceNativeExecutor,
    fallback: AuthoredSceneFallbackExecutor,
) -> Mapping[str, object]:
    route = checkpoint.route_resolution_receipt
    if route.final_route == "source-native":
        selection = checkpoint.frame_selection_receipt
        if selection is None or len(selection.slides) != len(checkpoint.narrative.slides):
            raise NarrativeOrchestrationError("narrative_frame_selection_incomplete")
        result = await source_native.execute(
            narrative=checkpoint.narrative,
            selection=selection,
            route=route,
            checkpoint=checkpoint,
        )
    elif route.final_route == "authored-scene-fallback":
        result = await fallback.execute(
            narrative=checkpoint.narrative,
            route=route,
            checkpoint=checkpoint,
        )
    else:
        raise NarrativeOrchestrationError("narrative_route_rejected")
    if not isinstance(result, Mapping):
        raise NarrativeOrchestrationError("narrative_execution_result_invalid")
    return dict(result)


async def run_narrative_ir_operation(
    *,
    operation_id: UUID,
    job_id: UUID,
    attempt_token: str,
    fencing_token: int,
    strategy_receipt_digest: str,
    brief: str,
    slide_count: int,
    language: str | None,
    source_unit_ids: Sequence[str] = (),
    provider: NarrativeProvider,
    reserve: Reservation,
    design_pack: Mapping[str, Any] | object | None,
    source_native_executor: SourceNativeExecutor,
    authored_scene_fallback_executor: AuthoredSceneFallbackExecutor,
    checkpoint_repository: NarrativeCheckpointRepository,
    generation_lane: str = "new-authoring",
    content_graph: ContentGraphV1 | Mapping[str, Any] | None = None,
) -> NarrativeOperationResult:
    """Resolve the new-authoring lane; preserve-source must bypass Narrative/LLM."""

    if generation_lane != "new-authoring":
        raise NarrativeOrchestrationError("preserve_source_narrative_provider_forbidden")
    _validate_executors(source_native_executor, authored_scene_fallback_executor)
    existing = await checkpoint_repository.load(operation_id)
    if existing is not None:
        existing = validate_checkpoint(
            existing,
            operation_id=operation_id,
            job_id=job_id,
            strategy_receipt_digest=strategy_receipt_digest,
        )
        retried = reseal_checkpoint_for_retry(
            existing,
            attempt_token=attempt_token,
            fencing_token=fencing_token,
        )
        checkpoint = await checkpoint_repository.commit(
            retried,
            expected_checkpoint_digest=existing.checkpoint_digest,
        )
        execution = await _execute_route(
            checkpoint=checkpoint,
            source_native=source_native_executor,
            fallback=authored_scene_fallback_executor,
        )
        return NarrativeOperationResult(
            route=checkpoint.route_resolution_receipt,
            checkpoint=checkpoint,
            execution=execution,
            resumed=True,
        )

    directed = await run_narrative_director(
        operation_id=operation_id,
        brief=brief,
        slide_count=slide_count,
        language=language,
        source_unit_ids=source_unit_ids,
        content_graph=content_graph,
        provider=provider,
        reserve=reserve,
    )
    search = (
        search_source_native_frames(
            narrative=directed.narrative,
            design_pack=design_pack,
        )
        if design_pack is not None
        else None
    )
    selection = (
        select_source_native_frames(search)
        if search is not None and search.status == "passed"
        else None
    )
    route = _route_receipt(
        narrative=directed.narrative,
        call_budget=directed.provider_call_budget,
        search=search,
        selection=selection,
    )
    checkpoint = seal_checkpoint(
        operation_id=operation_id,
        job_id=job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
        strategy_receipt_digest=strategy_receipt_digest,
        narrative=directed.narrative,
        provider_call_budget=directed.provider_call_budget,
        frame_search_receipt=search,
        frame_selection_receipt=selection,
        route_resolution_receipt=route,
    )
    checkpoint = await checkpoint_repository.commit(
        checkpoint,
        expected_checkpoint_digest=None,
    )
    execution = await _execute_route(
        checkpoint=checkpoint,
        source_native=source_native_executor,
        fallback=authored_scene_fallback_executor,
    )
    return NarrativeOperationResult(
        route=route,
        checkpoint=checkpoint,
        execution=execution,
        resumed=False,
    )


__all__ = [
    "AuthoredSceneFallbackExecutor",
    "NarrativeOperationResult",
    "NarrativeOrchestrationError",
    "SourceNativeExecutor",
    "run_narrative_ir_operation",
]
