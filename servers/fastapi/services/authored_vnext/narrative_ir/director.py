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
from .validation import NarrativeIRValidationError, seal_narrative_ir


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
