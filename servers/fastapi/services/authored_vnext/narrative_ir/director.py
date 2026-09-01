"""Bounded structured provider boundary for deck-wide Narrative IR."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from .contracts import (
    ContentGraphV1,
    NarrativeIRV1,
    ProviderCallBudgetV1,
    ProviderCallReservationV1,
    model_payload,
)
from .validation import NarrativeIRValidationError, canonical_digest, seal_narrative_ir


class NarrativeDirectorError(RuntimeError):
    code = "narrative_director_failed"
    retryable = False

    def __init__(self, code: str | None = None) -> None:
        resolved = code or self.code
        super().__init__(resolved)
        self.code = resolved


class NarrativeProvider(Protocol):
    """One invocation must equal exactly one physical provider request."""

    single_physical_call: bool

    async def generate(self, request: Mapping[str, object]) -> Mapping[str, object]: ...


Reservation = Callable[
    [str, int, int], Awaitable[Mapping[str, object] | ProviderCallReservationV1]
]


@dataclass(frozen=True, slots=True)
class NarrativeDirectorResult:
    narrative: NarrativeIRV1
    provider_call_budget: ProviderCallBudgetV1


def _generated_content_graph(
    narrative: NarrativeIRV1,
    *,
    source_unit_ids: Sequence[str],
) -> ContentGraphV1:
    """Seal provider-authored meaning before any composition can consume it."""

    from .content_graph import seal_content_graph

    if source_unit_ids and len(source_unit_ids) != len(narrative.slides):
        raise NarrativeIRValidationError("narrative_ir_source_unit_order_mismatch")
    pages: list[dict[str, object]] = []
    for slide_index, slide in enumerate(narrative.slides):
        page_number = slide_index + 1
        page_id = f"generated-page-{page_number:03d}"
        source_unit_id = (
            str(source_unit_ids[slide_index])
            if source_unit_ids
            else f"generated-source-{page_number:03d}"
        )
        units: list[dict[str, object]] = []

        def append_unit(
            *,
            unit_id: str,
            kind: str,
            text: str,
            data: Sequence[object] = (),
            inherited_ownership: Sequence[object] = (),
        ) -> None:
            payload = {
                "text": text,
                "data": [model_payload(item) for item in data],
            }
            generated_binding_id = unit_id.replace(
                "generated:", "generated-binding:", 1
            )
            ownership = [
                {
                    "sourceBindingId": generated_binding_id,
                    "sourceContentDigest": canonical_digest(payload),
                    "useMode": "meaning-preserving",
                    "required": True,
                }
            ]
            ownership.extend(model_payload(item) for item in inherited_ownership)
            units.append(
                {
                    "contentUnitId": unit_id,
                    "kind": kind,
                    "payload": payload,
                    "required": True,
                    "sourceOwnership": ownership,
                }
            )

        append_unit(
            unit_id=f"generated:{page_number:03d}:key-message",
            kind="text",
            text=slide.key_message,
        )
        for point_index, point in enumerate(slide.supporting_points, start=1):
            append_unit(
                unit_id=f"generated:{page_number:03d}:supporting:{point_index:03d}",
                kind="text",
                text=point,
            )
        ownership_by_id = {
            item.source_binding_id: item for item in slide.source_ownership
        }
        for datum_index, datum in enumerate(slide.data, start=1):
            inherited = (
                (ownership_by_id[datum.source_binding_id],)
                if datum.source_binding_id in ownership_by_id
                else ()
            )
            suffix = f" {datum.unit}" if datum.unit else ""
            append_unit(
                unit_id=f"generated:{page_number:03d}:datum:{datum_index:03d}",
                kind=(
                    "number"
                    if isinstance(datum.value, (int, float))
                    and not isinstance(datum.value, bool)
                    else "text"
                ),
                text=f"{datum.label}: {datum.value}{suffix}",
                data=(datum,),
                inherited_ownership=inherited,
            )
        pages.append(
            {
                "pageId": page_id,
                "sourceUnitId": source_unit_id,
                "contentUnits": units,
            }
        )
    return seal_content_graph(pages, generation_lane="new-authoring")


def _draft_payload_bound_to_graph(
    narrative: NarrativeIRV1,
    graph: ContentGraphV1,
) -> dict[str, object]:
    payload = model_payload(narrative)
    for key in (
        "operationId",
        "generationLane",
        "contentGraphDigest",
        "narrativeDigest",
    ):
        payload.pop(key, None)
    payload["schemaVersion"] = "narrative-ir-draft/v1"
    slides = payload.get("slides")
    if not isinstance(slides, list) or len(slides) != len(graph.pages):
        raise NarrativeIRValidationError("narrative_ir_slide_count_mismatch")
    for slide, page in zip(slides, graph.pages, strict=True):
        if not isinstance(slide, dict):
            raise NarrativeIRValidationError("narrative_ir_schema_invalid")
        slide.pop("sealedContentUnits", None)
        slide["contentUnitIds"] = [
            unit.content_unit_id for unit in page.content_units
        ]
    payload["sourceUnitOrder"] = [
        model_payload(item) for item in graph.source_unit_order
    ]
    return payload


def _reservation(
    value: Mapping[str, object] | ProviderCallReservationV1,
    *,
    purpose: str,
    ordinal: int,
) -> ProviderCallReservationV1:
    try:
        model = (
            value
            if isinstance(value, ProviderCallReservationV1)
            else ProviderCallReservationV1.model_validate(value)
        )
    except ValidationError as error:
        raise NarrativeDirectorError("narrative_provider_reservation_invalid") from error
    if (
        model.purpose != purpose
        or model.logical_ordinal != ordinal
        or model.physical_ordinal != ordinal
    ):
        raise NarrativeDirectorError("narrative_provider_reservation_invalid")
    return model


async def run_narrative_director(
    *,
    operation_id: UUID,
    brief: str,
    slide_count: int,
    language: str | None,
    source_unit_ids: Sequence[str] = (),
    content_graph: ContentGraphV1 | Mapping[str, Any] | None = None,
    provider: NarrativeProvider,
    reserve: Reservation,
) -> NarrativeDirectorResult:
    """Make one deck call plus at most one explicit, separately reserved recovery.

    The provider adapter is forbidden from hiding parse, transient, multimodal,
    or schema retries inside one invocation.  Invalid provider output is never
    echoed into the recovery request and is never persisted.
    """

    if not 1 <= slide_count <= 100:
        raise NarrativeDirectorError("narrative_ir_slide_count_unsupported")
    graph: ContentGraphV1 | None = None
    if content_graph is not None:
        from .content_graph import validate_content_graph

        graph = validate_content_graph(content_graph)
        if graph.generation_lane != "new-authoring":
            raise NarrativeDirectorError("narrative_ir_generation_lane_invalid")
        graph_source_ids = tuple(item.source_unit_id for item in graph.source_unit_order)
        if source_unit_ids and tuple(source_unit_ids) != graph_source_ids:
            raise NarrativeDirectorError("narrative_ir_source_unit_order_mismatch")
        source_unit_ids = graph_source_ids
    if getattr(provider, "single_physical_call", None) is not True:
        raise NarrativeDirectorError("narrative_provider_physical_call_contract_missing")

    reservations: list[ProviderCallReservationV1] = []
    last_validation_error: NarrativeIRValidationError | None = None
    for ordinal, purpose in ((1, "narrative.initial"), (2, "narrative.recovery")):
        reserved = _reservation(
            await reserve(purpose, ordinal, ordinal),
            purpose=purpose,
            ordinal=ordinal,
        )
        reservations.append(reserved)
        request: dict[str, object] = {
            "schemaVersion": "narrative-provider-request/v1",
            "purpose": purpose,
            "operationId": str(operation_id),
            "brief": brief,
            "slideCount": slide_count,
            "language": language,
            "sourceUnitIds": list(source_unit_ids),
            "admittedContentGraph": model_payload(graph) if graph is not None else None,
            "outputContract": "narrative-ir-draft/v1",
            "contentUnitPolicy": {
                "allContentUnitIdsExactlyOnce": True,
                "rewriteOrSummarizeAfterSeal": False,
                "splitOrMergeAfterSeal": False,
            },
            "forbiddenOutputAuthorities": [
                "html",
                "css",
                "javascript",
                "coordinates",
                "absolute-positioning",
                "safety-policy",
            ],
        }
        if last_validation_error is not None:
            request["recoveryReason"] = last_validation_error.code
        try:
            raw = await provider.generate(request)
        except Exception as error:
            # Provider transport failures are not silently reclassified as a
            # content recovery. A higher operation retry owns that decision.
            raise NarrativeDirectorError("narrative_provider_call_failed") from error
        if not isinstance(raw, Mapping):
            last_validation_error = NarrativeIRValidationError(
                "narrative_ir_schema_invalid"
            )
        else:
            try:
                if graph is None:
                    validated_draft = seal_narrative_ir(
                        raw,
                        operation_id=operation_id,
                        admitted_slide_count=slide_count,
                        expected_source_unit_ids=source_unit_ids,
                    )
                    generated_graph = _generated_content_graph(
                        validated_draft,
                        source_unit_ids=source_unit_ids,
                    )
                    narrative = seal_narrative_ir(
                        _draft_payload_bound_to_graph(
                            validated_draft, generated_graph
                        ),
                        operation_id=operation_id,
                        admitted_slide_count=slide_count,
                        expected_source_unit_ids=tuple(
                            item.source_unit_id
                            for item in generated_graph.source_unit_order
                        ),
                        content_graph=generated_graph,
                    )
                else:
                    narrative = seal_narrative_ir(
                        raw,
                        operation_id=operation_id,
                        admitted_slide_count=slide_count,
                        expected_source_unit_ids=source_unit_ids,
                        content_graph=graph,
                    )
            except NarrativeIRValidationError as error:
                last_validation_error = error
            else:
                budget = ProviderCallBudgetV1(
                    actual_logical_calls=ordinal,
                    actual_physical_calls=ordinal,
                    reservations=reservations,
                )
                return NarrativeDirectorResult(narrative, budget)
    raise NarrativeDirectorError(
        last_validation_error.code if last_validation_error is not None else None
    )


__all__ = [
    "NarrativeDirectorError",
    "NarrativeDirectorResult",
    "NarrativeProvider",
    "Reservation",
    "run_narrative_director",
]
