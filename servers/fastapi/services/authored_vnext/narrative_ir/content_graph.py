"""Canonical immutable content admission and fail-closed integrity receipts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError

from models.authored_vnext.narrative_ir_contracts import (
    ContentGraphV1,
    ContentIntegrityReceiptV2,
    ContentUnitV1,
    PageContentIntegrityV1,
    PageContentSnapshotV1,
    PreserveSourceInputV1,
    ProviderCallBudgetV1,
    SourceUnitOrderV1,
    model_payload,
)
from .validation import canonical_digest


class ContentGraphValidationError(ValueError):
    code = "content_graph_invalid"

    def __init__(self, code: str | None = None) -> None:
        resolved = code or self.code
        super().__init__(resolved)
        self.code = resolved


def _without_digest(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    payload = dict(value)
    payload.pop(key, None)
    return payload


def _seal_unit(
    value: Mapping[str, Any] | ContentUnitV1,
    *,
    page_id: str,
    page_index: int,
    source_unit_index: int,
    source_order: int,
) -> ContentUnitV1:
    raw = model_payload(value) if isinstance(value, ContentUnitV1) else dict(value)
    raw.update(
        {
            "sourcePageId": page_id,
            "sourcePageIndex": page_index,
            "sourceUnitIndex": source_unit_index,
            "sourceOrder": source_order,
        }
    )
    unsigned = _without_digest(raw, "contentDigest")
    try:
        normalized = ContentUnitV1.model_validate(
            {**unsigned, "contentDigest": canonical_digest({})}
        )
        unsigned = model_payload(normalized)
        unsigned.pop("contentDigest")
        return ContentUnitV1.model_validate(
            {**unsigned, "contentDigest": canonical_digest(unsigned)}
        )
    except ValidationError as error:
        raise ContentGraphValidationError("content_unit_schema_invalid") from error


def seal_content_graph(
    pages: Sequence[Mapping[str, Any] | PageContentSnapshotV1],
    *,
    generation_lane: Literal["preserve-source", "new-authoring"],
) -> ContentGraphV1:
    """Admit ordered source pages and replace all caller-supplied digest/order authority."""

    if not pages:
        raise ContentGraphValidationError("content_graph_pages_missing")
    sealed_pages: list[PageContentSnapshotV1] = []
    source_order = 0
    for page_index, value in enumerate(pages):
        raw = model_payload(value) if isinstance(value, PageContentSnapshotV1) else dict(value)
        page_id = raw.get("pageId")
        source_unit_id = raw.get("sourceUnitId")
        units = raw.get("contentUnits", ())
        if not isinstance(page_id, str) or not isinstance(source_unit_id, str):
            raise ContentGraphValidationError("page_content_identity_invalid")
        if not isinstance(units, Sequence) or isinstance(units, (str, bytes, bytearray)):
            raise ContentGraphValidationError("page_content_units_invalid")
        sealed_units: list[ContentUnitV1] = []
        for source_unit_index, unit in enumerate(units):
            if not isinstance(unit, (Mapping, ContentUnitV1)):
                raise ContentGraphValidationError("content_unit_schema_invalid")
            sealed_unit = _seal_unit(
                unit,
                page_id=page_id,
                page_index=page_index,
                source_unit_index=source_unit_index,
                source_order=source_order,
            )
            if generation_lane == "preserve-source" and sealed_unit.kind in {
                "table",
                "chart",
            }:
                raise ContentGraphValidationError("preserve_source_structural_unit_forbidden")
            sealed_units.append(sealed_unit)
            source_order += 1
        unsigned_page = {
            "schemaVersion": "page-content-snapshot/v1",
            "pageId": page_id,
            "pageIndex": page_index,
            "sourceUnitId": source_unit_id,
            "contentUnits": [model_payload(unit) for unit in sealed_units],
        }
        try:
            sealed_pages.append(
                PageContentSnapshotV1.model_validate(
                    {**unsigned_page, "pageDigest": canonical_digest(unsigned_page)}
                )
            )
        except ValidationError as error:
            raise ContentGraphValidationError("page_content_snapshot_invalid") from error
    source_unit_order = tuple(
        SourceUnitOrderV1(source_index=index, source_unit_id=page.source_unit_id)
        for index, page in enumerate(sealed_pages)
    )
    required_ids = tuple(
        unit.content_unit_id
        for page in sealed_pages
        for unit in page.content_units
        if unit.required
    )
    unsigned_graph = {
        "schemaVersion": "content-graph/v1",
        "generationLane": generation_lane,
        "pages": [model_payload(page) for page in sealed_pages],
        "sourceUnitOrder": [model_payload(item) for item in source_unit_order],
        "requiredContentUnitIds": list(required_ids),
    }
    try:
        graph = ContentGraphV1.model_validate(
            {**unsigned_graph, "contentGraphDigest": canonical_digest(unsigned_graph)}
        )
    except ValidationError as error:
        raise ContentGraphValidationError("content_graph_schema_invalid") from error
    return validate_content_graph(graph)


def validate_content_graph(
    value: ContentGraphV1 | Mapping[str, Any],
) -> ContentGraphV1:
    try:
        graph = value if isinstance(value, ContentGraphV1) else ContentGraphV1.model_validate(value)
    except ValidationError as error:
        raise ContentGraphValidationError("content_graph_schema_invalid") from error
    for page in graph.pages:
        for unit in page.content_units:
            unit_payload = model_payload(unit)
            supplied_unit_digest = unit_payload.pop("contentDigest")
            if supplied_unit_digest != canonical_digest(unit_payload):
                raise ContentGraphValidationError("content_unit_digest_mismatch")
        page_payload = model_payload(page)
        supplied_page_digest = page_payload.pop("pageDigest")
        if supplied_page_digest != canonical_digest(page_payload):
            raise ContentGraphValidationError("page_content_digest_mismatch")
    graph_payload = model_payload(graph)
    supplied_graph_digest = graph_payload.pop("contentGraphDigest")
    if supplied_graph_digest != canonical_digest(graph_payload):
        raise ContentGraphValidationError("content_graph_digest_mismatch")
    return graph


def seal_preserve_source_input(
    *, operation_id: UUID, content_graph: ContentGraphV1 | Mapping[str, Any]
) -> PreserveSourceInputV1:
    graph = validate_content_graph(content_graph)
    if graph.generation_lane != "preserve-source":
        raise ContentGraphValidationError("preserve_source_content_graph_lane_invalid")
    unsigned = {
        "schemaVersion": "preserve-source-input/v1",
        "operationId": str(operation_id),
        "generationLane": "preserve-source",
        "contentGraph": model_payload(graph),
    }
    try:
        return PreserveSourceInputV1.model_validate(
            {**unsigned, "inputDigest": canonical_digest(unsigned)}
        )
    except ValidationError as error:
        raise ContentGraphValidationError("preserve_source_input_invalid") from error


def _validate_content_integrity_pages(
    pages: Sequence[PageContentIntegrityV1],
) -> None:
    if [item.page_index for item in pages] != list(range(len(pages))):
        raise ContentGraphValidationError("content_integrity_page_order_error")
    all_content_unit_ids: list[str] = []
    all_asset_occurrences: list[str] = []
    for page in pages:
        expected_ids = list(page.expected_content_unit_ids)
        expected_assets = list(page.expected_asset_occurrences)
        if (
            len(expected_ids) != len(set(expected_ids))
            or len(expected_assets) != len(set(expected_assets))
        ):
            raise ContentGraphValidationError(
                "content_integrity_expected_identity_duplicate"
            )
        if page.expected_protected_chrome_digest != page.observed_protected_chrome_digest:
            raise ContentGraphValidationError(
                "content_integrity_protected_chrome_mismatch"
            )
        if (
            page.unmapped_required_units
            or page.duplicate_required_units
            or page.transformed_required_units
            or page.hidden_required_units
            or page.off_canvas_required_units
            or page.page_order_error
            or page.expected_content_unit_ids != page.bound_content_unit_ids
            or page.expected_content_unit_ids != page.rendered_content_unit_ids
            or page.expected_digest != page.bound_digest
            or page.expected_digest != page.rendered_digest
            or page.expected_asset_occurrences != page.rendered_asset_occurrences
        ):
            raise ContentGraphValidationError(
                "content_integrity_required_coverage_failed"
            )
        all_content_unit_ids.extend(expected_ids)
        all_asset_occurrences.extend(expected_assets)
    if (
        len(all_content_unit_ids) != len(set(all_content_unit_ids))
        or len(all_asset_occurrences) != len(set(all_asset_occurrences))
    ):
        raise ContentGraphValidationError(
            "content_integrity_cross_page_identity_duplicate"
        )


def seal_content_integrity_receipt(
    *,
    operation_id: UUID,
    attempt_binding_digest: str,
    source_revision: int,
    result_revision: int,
    generation_lane: Literal["preserve-source", "new-authoring"],
    content_graph_digest: str,
    route_strategy_receipt_digest: str,
    narrative_digest: str | None,
    preserve_source_input_digest: str | None,
    provider_call_budget: Mapping[str, Any] | ProviderCallBudgetV1,
    pages: Sequence[Mapping[str, Any] | PageContentIntegrityV1],
) -> ContentIntegrityReceiptV2:
    """Seal only publication-safe evidence; any content defect fails closed."""

    try:
        budget_model = (
            provider_call_budget
            if isinstance(provider_call_budget, ProviderCallBudgetV1)
            else ProviderCallBudgetV1.model_validate(provider_call_budget)
        )
        page_models = tuple(
            item if isinstance(item, PageContentIntegrityV1) else PageContentIntegrityV1.model_validate(item)
            for item in pages
        )
    except ValidationError as error:
        raise ContentGraphValidationError("content_integrity_page_invalid") from error
    _validate_content_integrity_pages(page_models)
    budget_payload = model_payload(budget_model)
    unsigned = {
        "schemaVersion": "content-integrity-receipt/v2",
        "operationId": str(operation_id),
        "attemptBindingDigest": attempt_binding_digest,
        "sourceRevision": source_revision,
        "resultRevision": result_revision,
        "generationLane": generation_lane,
        "contentGraphDigest": content_graph_digest,
        "routeStrategyReceiptDigest": route_strategy_receipt_digest,
        "narrativeDigest": narrative_digest,
        "preserveSourceInputDigest": preserve_source_input_digest,
        "providerCallBudget": budget_payload,
        "providerCallBudgetDigest": canonical_digest(budget_payload),
        "pages": [model_payload(page) for page in page_models],
        "requiredCoverage": 1.0,
        "duplicateRequiredCount": 0,
        "visibleMissingCount": 0,
        "requiredAssetMissingCount": 0,
        "pageOrderErrorCount": 0,
        "status": "passed",
    }
    try:
        receipt = ContentIntegrityReceiptV2.model_validate(
            {**unsigned, "receiptDigest": canonical_digest(unsigned)}
        )
    except ValidationError as error:
        raise ContentGraphValidationError("content_integrity_receipt_invalid") from error
    return validate_content_integrity_receipt(receipt)


def validate_content_integrity_receipt(
    value: ContentIntegrityReceiptV2 | Mapping[str, Any],
) -> ContentIntegrityReceiptV2:
    try:
        receipt = (
            value
            if isinstance(value, ContentIntegrityReceiptV2)
            else ContentIntegrityReceiptV2.model_validate(value)
        )
    except ValidationError as error:
        raise ContentGraphValidationError("content_integrity_receipt_invalid") from error
    _validate_content_integrity_pages(receipt.pages)
    if receipt.provider_call_budget_digest != canonical_digest(
        model_payload(receipt.provider_call_budget)
    ):
        raise ContentGraphValidationError(
            "content_integrity_provider_call_budget_digest_mismatch"
        )
    payload = model_payload(receipt)
    supplied = payload.pop("receiptDigest")
    if supplied != canonical_digest(payload):
        raise ContentGraphValidationError("content_integrity_receipt_digest_mismatch")
    return receipt


def validate_content_integrity_receipt_binding(
    value: ContentIntegrityReceiptV2 | Mapping[str, Any],
    *,
    operation_id: UUID,
    attempt_binding_digest: str,
    source_revision: int,
    result_revision: int,
    generation_lane: Literal["preserve-source", "new-authoring"],
    content_graph_digest: str,
    route_strategy_receipt_digest: str,
    narrative_digest: str | None,
    preserve_source_input_digest: str | None,
    provider_call_budget_digest: str,
) -> ContentIntegrityReceiptV2:
    """Validate a receipt against the exact operation lineage that consumes it."""

    receipt = validate_content_integrity_receipt(value)
    expected = (
        ("operation", receipt.operation_id, operation_id),
        ("attempt", receipt.attempt_binding_digest, attempt_binding_digest),
        ("source_revision", receipt.source_revision, source_revision),
        ("result_revision", receipt.result_revision, result_revision),
        ("generation_lane", receipt.generation_lane, generation_lane),
        ("content_graph", receipt.content_graph_digest, content_graph_digest),
        (
            "route_strategy_receipt",
            receipt.route_strategy_receipt_digest,
            route_strategy_receipt_digest,
        ),
        ("narrative", receipt.narrative_digest, narrative_digest),
        (
            "preserve_source_input",
            receipt.preserve_source_input_digest,
            preserve_source_input_digest,
        ),
        (
            "provider_call_budget",
            receipt.provider_call_budget_digest,
            provider_call_budget_digest,
        ),
    )
    for binding, actual, required in expected:
        if actual != required:
            raise ContentGraphValidationError(
                f"content_integrity_{binding}_binding_mismatch"
            )
    return receipt


__all__ = [
    "ContentGraphValidationError",
    "seal_content_graph",
    "seal_content_integrity_receipt",
    "seal_preserve_source_input",
    "validate_content_graph",
    "validate_content_integrity_receipt",
    "validate_content_integrity_receipt_binding",
]
