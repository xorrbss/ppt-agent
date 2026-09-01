"""Provider-free orchestration contracts for immutable source restyling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence
from uuid import UUID

from services.authored_vnext.narrative_ir.contracts import (
    ContentUnitV1,
    PreserveSourceInputV1,
    ProviderCallBudgetV1,
)
from services.authored_vnext.narrative_ir.validation import canonical_digest


@dataclass(frozen=True, slots=True)
class PreserveSourceSlide:
    slide_key: str
    sequence_index: int
    role: str
    key_message: str
    supporting_points: tuple[str, ...]
    data: tuple[object, ...]
    source_ownership: tuple[object, ...]
    frame_semantics: tuple[str, ...]
    density: Literal["sparse", "medium", "dense"]
    content_unit_ids: tuple[str, ...]
    sealed_content_units: tuple[ContentUnitV1, ...]


@dataclass(frozen=True, slots=True)
class PreserveSourcePlan:
    operation_id: UUID
    generation_lane: Literal["preserve-source"]
    title: str
    language: str
    slides: tuple[PreserveSourceSlide, ...]
    content_graph_digest: str
    preserve_source_input_digest: str
    narrative_digest: None = None


@dataclass(frozen=True, slots=True)
class PreserveFrameSelection:
    selected_frame_id: str
    selected_frame_digest: str
    selected_source_part_digest: str


@dataclass(frozen=True, slots=True)
class PreserveSelectionReceipt:
    slides: tuple[PreserveFrameSelection, ...]
    receipt_digest: str


@dataclass(frozen=True, slots=True)
class PreserveRouteReceipt:
    provider_call_budget: ProviderCallBudgetV1
    receipt_digest: str


@dataclass(frozen=True, slots=True)
class PreserveCheckpoint:
    checkpoint_digest: str
    preserve_source_input_digest: str
    attempt_binding_digest: str


def _first_text(units: Sequence[ContentUnitV1], *, fallback: str) -> str:
    for unit in units:
        if unit.kind == "text" and unit.payload.text:
            return unit.payload.text
    return fallback


def build_preserve_source_plan(
    *,
    preserve_input: PreserveSourceInputV1,
    capacity_receipt: Mapping[str, object],
    frames: Sequence[object],
    language: str | None,
) -> tuple[PreserveSourcePlan, PreserveSelectionReceipt]:
    """Project immutable pages to composition inputs without Narrative or a provider."""

    pages = preserve_input.content_graph.pages
    raw_pages = capacity_receipt.get("pages")
    if not isinstance(raw_pages, Sequence) or isinstance(raw_pages, (str, bytes)):
        raise ValueError("preserve_source_capacity_receipt_invalid")
    if len(raw_pages) != len(pages):
        raise ValueError("preserve_source_page_cardinality_mismatch")
    frame_by_id = {
        str(getattr(frame, "frame_id", "")): frame
        for frame in frames
        if str(getattr(frame, "frame_id", ""))
    }
    slides: list[PreserveSourceSlide] = []
    selections: list[PreserveFrameSelection] = []
    for page, raw_page in zip(pages, raw_pages, strict=True):
        if not isinstance(raw_page, Mapping):
            raise ValueError("preserve_source_capacity_receipt_invalid")
        frame_id = str(raw_page.get("selectedFrameId") or "")
        frame_digest = str(raw_page.get("selectedFrameDigest") or "")
        frame = frame_by_id.get(frame_id)
        if frame is None or frame_digest != str(getattr(frame, "source_digest", "")):
            raise ValueError("preserve_source_frame_binding_mismatch")
        units = tuple(page.content_units)
        title = _first_text(units, fallback=f"Page {page.page_index + 1}")
        role = str(getattr(frame, "role", "content") or "content")
        slides.append(
            PreserveSourceSlide(
                slide_key=f"preserve-slide-{page.page_index + 1:03d}",
                sequence_index=page.page_index,
                role=role,
                key_message=title,
                supporting_points=(),
                data=(),
                source_ownership=(),
                frame_semantics=(),
                density="dense",
                content_unit_ids=tuple(unit.content_unit_id for unit in units),
                sealed_content_units=units,
            )
        )
        selections.append(
            PreserveFrameSelection(
                selected_frame_id=frame_id,
                selected_frame_digest=frame_digest,
                selected_source_part_digest=frame_digest,
            )
        )
    plan = PreserveSourcePlan(
        operation_id=preserve_input.operation_id,
        generation_lane="preserve-source",
        title=_first_text(tuple(pages[0].content_units), fallback="Preserved source"),
        language=str(language or "ko-KR"),
        slides=tuple(slides),
        content_graph_digest=preserve_input.content_graph.content_graph_digest,
        preserve_source_input_digest=preserve_input.input_digest,
    )
    receipt_digest = str(capacity_receipt.get("capacityAdmissionDigest") or "")
    if not receipt_digest.startswith("sha256:"):
        raise ValueError("preserve_source_capacity_receipt_invalid")
    return plan, PreserveSelectionReceipt(
        slides=tuple(selections), receipt_digest=receipt_digest
    )


def build_preserve_checkpoint(
    *,
    preserve_input: PreserveSourceInputV1,
    operation_id: UUID,
    job_id: UUID,
    attempt_token: str,
    fencing_token: int,
    strategy_receipt_digest: str,
    selection_receipt_digest: str,
) -> tuple[PreserveCheckpoint, dict[str, Any]]:
    attempt_binding_digest = canonical_digest(
        {
            "operationId": str(operation_id),
            "jobId": str(job_id),
            "attemptToken": attempt_token,
            "fencingToken": fencing_token,
        }
    )
    unsigned: dict[str, Any] = {
        "schemaVersion": "preserve-source-checkpoint/v1",
        "operationId": str(operation_id),
        "jobId": str(job_id),
        "generationLane": "preserve-source",
        "attemptBindingDigest": attempt_binding_digest,
        "fencingToken": fencing_token,
        "strategyReceiptDigest": strategy_receipt_digest,
        "preserveSourceInput": preserve_input.model_dump(mode="json", by_alias=True),
        "selectionReceiptDigest": selection_receipt_digest,
        "providerCallBudget": ProviderCallBudgetV1(
            actual_logical_calls=0,
            actual_physical_calls=0,
            reservations=[],
        ).model_dump(mode="json", by_alias=True),
    }
    checkpoint_digest = canonical_digest(unsigned)
    payload = {**unsigned, "checkpointDigest": checkpoint_digest}
    return (
        PreserveCheckpoint(
            checkpoint_digest=checkpoint_digest,
            preserve_source_input_digest=preserve_input.input_digest,
            attempt_binding_digest=attempt_binding_digest,
        ),
        payload,
    )


def zero_call_route(*, strategy_receipt_digest: str) -> PreserveRouteReceipt:
    return PreserveRouteReceipt(
        provider_call_budget=ProviderCallBudgetV1(
            actual_logical_calls=0,
            actual_physical_calls=0,
            reservations=[],
        ),
        receipt_digest=strategy_receipt_digest,
    )


__all__ = [
    "PreserveCheckpoint",
    "PreserveRouteReceipt",
    "PreserveSelectionReceipt",
    "PreserveSourcePlan",
    "build_preserve_checkpoint",
    "build_preserve_source_plan",
    "zero_call_route",
]
