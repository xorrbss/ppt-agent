"""Canonical immutable content admission and fail-closed integrity receipts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError

from .contracts import (
    ContentGraphV1,
    ContentIntegrityReceiptV1,
    ContentUnitV1,
    PageContentIntegrityV1,
    PageContentSnapshotV1,
    PreserveSourceInputV1,
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


def seal_content_integrity_receipt(
    *,
    operation_id: UUID,
    generation_lane: Literal["preserve-source", "new-authoring"],
    content_graph_digest: str,
    narrative_digest: str | None,
    pages: Sequence[Mapping[str, Any] | PageContentIntegrityV1],
) -> ContentIntegrityReceiptV1:
    """Seal only publication-safe evidence; any content defect fails closed."""

    try:
        page_models = tuple(
            item if isinstance(item, PageContentIntegrityV1) else PageContentIntegrityV1.model_validate(item)
            for item in pages
        )
    except ValidationError as error:
        raise ContentGraphValidationError("content_integrity_page_invalid") from error
    if [item.page_index for item in page_models] != list(range(len(page_models))):
        raise ContentGraphValidationError("content_integrity_page_order_error")
    for page in page_models:
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
            raise ContentGraphValidationError("content_integrity_required_coverage_failed")
    unsigned = {
        "schemaVersion": "content-integrity-receipt/v1",
        "operationId": str(operation_id),
        "generationLane": generation_lane,
        "contentGraphDigest": content_graph_digest,
        "narrativeDigest": narrative_digest,
        "pages": [model_payload(page) for page in page_models],
        "requiredCoverage": 1.0,
        "duplicateRequiredCount": 0,
        "visibleMissingCount": 0,
        "requiredAssetMissingCount": 0,
        "pageOrderErrorCount": 0,
        "status": "passed",
    }
    try:
        receipt = ContentIntegrityReceiptV1.model_validate(
            {**unsigned, "receiptDigest": canonical_digest(unsigned)}
        )
    except ValidationError as error:
        raise ContentGraphValidationError("content_integrity_receipt_invalid") from error
    return validate_content_integrity_receipt(receipt)


def validate_content_integrity_receipt(
    value: ContentIntegrityReceiptV1 | Mapping[str, Any],
) -> ContentIntegrityReceiptV1:
    try:
        receipt = (
            value
            if isinstance(value, ContentIntegrityReceiptV1)
            else ContentIntegrityReceiptV1.model_validate(value)
        )
    except ValidationError as error:
        raise ContentGraphValidationError("content_integrity_receipt_invalid") from error
    payload = model_payload(receipt)
    supplied = payload.pop("receiptDigest")
    if supplied != canonical_digest(payload):
        raise ContentGraphValidationError("content_integrity_receipt_digest_mismatch")
    return receipt


__all__ = [
    "ContentGraphValidationError",
    "seal_content_graph",
    "seal_content_integrity_receipt",
    "seal_preserve_source_input",
    "validate_content_graph",
    "validate_content_integrity_receipt",
]
