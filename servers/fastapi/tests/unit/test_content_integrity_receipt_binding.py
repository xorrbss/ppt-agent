from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import uuid

import pytest
from pydantic import ValidationError

from api.v2.contracts import GenerationRouteReceiptV1, OperationStatusV2
from services.authored_vnext.narrative_ir.content_graph import (
    ContentGraphValidationError,
    seal_content_integrity_receipt,
    validate_content_integrity_receipt_binding,
)


def _digest(payload: object) -> str:
    return "sha256:" + sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _route_receipt(*, design_digest: str) -> GenerationRouteReceiptV1:
    unsigned = {
        "schemaVersion": "generation-route-receipt/v1",
        "requestedMode": "registered-template",
        "selectedRoute": "source-native",
        "designPackPin": {
            "designPackId": "pack-1",
            "version": 1,
            "digest": design_digest,
        },
        "narrativeIrDigest": None,
        "fallbackReason": None,
        "outputCapability": "fidelity",
    }
    return GenerationRouteReceiptV1.model_validate(
        {**unsigned, "receiptDigest": _digest(unsigned)}
    )


def _provider_budget(*, actual_calls: int) -> dict[str, object]:
    return {
        "schemaVersion": "narrative-provider-call-budget/v1",
        "maximumLogicalCalls": 2,
        "maximumPhysicalCalls": 2,
        "actualLogicalCalls": actual_calls,
        "actualPhysicalCalls": actual_calls,
        "reservations": (
            [
                {
                    "reservationId": "job-1:narrative:1",
                    "purpose": "narrative.initial",
                    "logicalOrdinal": 1,
                    "physicalOrdinal": 1,
                }
            ]
            if actual_calls
            else []
        ),
    }


def _page() -> dict[str, object]:
    unit_digest = "sha256:" + "a" * 64
    return {
        "pageIndex": 0,
        "expectedContentUnitIds": ["unit-1"],
        "boundContentUnitIds": ["unit-1"],
        "renderedContentUnitIds": ["unit-1"],
        "expectedDigest": unit_digest,
        "boundDigest": unit_digest,
        "renderedDigest": unit_digest,
        "sourceOwnershipDigest": "sha256:" + "d" * 64,
        "expectedProtectedChromeDigest": "sha256:" + "4" * 64,
        "observedProtectedChromeDigest": "sha256:" + "4" * 64,
        "browserReadbackDigest": "sha256:" + "5" * 64,
    }


def _receipt(*, operation_id: uuid.UUID, route_digest: str):
    return seal_content_integrity_receipt(
        operation_id=operation_id,
        attempt_binding_digest="sha256:" + "6" * 64,
        source_revision=7,
        result_revision=8,
        generation_lane="new-authoring",
        content_graph_digest="sha256:" + "b" * 64,
        route_strategy_receipt_digest=route_digest,
        narrative_digest="sha256:" + "c" * 64,
        preserve_source_input_digest=None,
        provider_call_budget=_provider_budget(actual_calls=1),
        pages=[_page()],
    )


def test_content_integrity_receipt_rejects_cross_lineage_replay() -> None:
    operation_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    route = _route_receipt(design_digest="sha256:" + "1" * 64)
    other_route = _route_receipt(design_digest="sha256:" + "2" * 64)
    receipt = _receipt(operation_id=operation_id, route_digest=route.receipt_digest)
    expected = {
        "operation_id": operation_id,
        "attempt_binding_digest": "sha256:" + "6" * 64,
        "source_revision": 7,
        "result_revision": 8,
        "generation_lane": "new-authoring",
        "content_graph_digest": "sha256:" + "b" * 64,
        "route_strategy_receipt_digest": route.receipt_digest,
        "narrative_digest": "sha256:" + "c" * 64,
        "preserve_source_input_digest": None,
        "provider_call_budget_digest": receipt.provider_call_budget_digest,
    }
    assert validate_content_integrity_receipt_binding(receipt, **expected) == receipt

    replay_cases = (
        ("operation_id", uuid.uuid4(), "operation_binding_mismatch"),
        ("attempt_binding_digest", "sha256:" + "7" * 64, "attempt_binding_mismatch"),
        ("source_revision", 6, "source_revision_binding_mismatch"),
        ("result_revision", 9, "result_revision_binding_mismatch"),
        ("generation_lane", "preserve-source", "generation_lane_binding_mismatch"),
        (
            "content_graph_digest",
            "sha256:" + "e" * 64,
            "content_graph_binding_mismatch",
        ),
        (
            "route_strategy_receipt_digest",
            other_route.receipt_digest,
            "route_strategy_receipt_binding_mismatch",
        ),
        ("narrative_digest", "sha256:" + "8" * 64, "narrative_binding_mismatch"),
        (
            "preserve_source_input_digest",
            "sha256:" + "9" * 64,
            "preserve_source_input_binding_mismatch",
        ),
        (
            "provider_call_budget_digest",
            "sha256:" + "0" * 64,
            "provider_call_budget_binding_mismatch",
        ),
    )
    for field, replayed, error_code in replay_cases:
        replay_context = {**expected, field: replayed}
        with pytest.raises(ContentGraphValidationError, match=error_code):
            validate_content_integrity_receipt_binding(receipt, **replay_context)

    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    status = {
        "operationId": str(operation_id),
        "presentationId": "presentation-1",
        "kind": "presentation.generate",
        "status": "succeeded",
        "phase": "publishing",
        "progressPercent": 100,
        "sourceRevision": 8,
        "acceptedAt": now,
        "updatedAt": now + timedelta(seconds=1),
        "absoluteDeadlineAt": now + timedelta(minutes=5),
        "generationRouteReceipt": route.model_dump(mode="json", by_alias=True),
        "contentIntegrityReceipt": receipt.model_dump(mode="json", by_alias=True),
    }
    assert OperationStatusV2.model_validate(status).content_integrity_receipt == receipt

    public_replays = (
        ({"operationId": str(uuid.uuid4())}, "status operationId"),
        ({"sourceRevision": 9}, "resultRevision must match sourceRevision"),
        (
            {
                "generationRouteReceipt": other_route.model_dump(
                    mode="json", by_alias=True
                )
            },
            "route receipt binding mismatch",
        ),
    )
    for update, error_message in public_replays:
        with pytest.raises(ValidationError, match=error_message):
            OperationStatusV2.model_validate({**status, **update})

    preserve = seal_content_integrity_receipt(
        operation_id=operation_id,
        attempt_binding_digest="sha256:" + "6" * 64,
        source_revision=8,
        result_revision=8,
        generation_lane="preserve-source",
        content_graph_digest="sha256:" + "b" * 64,
        route_strategy_receipt_digest=route.receipt_digest,
        narrative_digest=None,
        preserve_source_input_digest="sha256:" + "9" * 64,
        provider_call_budget=_provider_budget(actual_calls=0),
        pages=[_page()],
    )
    assert preserve.provider_call_budget.actual_physical_calls == 0

    with pytest.raises(
        ContentGraphValidationError, match="content_integrity_receipt_invalid"
    ):
        seal_content_integrity_receipt(
            operation_id=operation_id,
            attempt_binding_digest="sha256:" + "6" * 64,
            source_revision=8,
            result_revision=8,
            generation_lane="preserve-source",
            content_graph_digest="sha256:" + "b" * 64,
            route_strategy_receipt_digest=route.receipt_digest,
            narrative_digest=None,
            preserve_source_input_digest="sha256:" + "9" * 64,
            provider_call_budget=_provider_budget(actual_calls=1),
            pages=[_page()],
        )

    protected_mismatch = {
        **_page(),
        "observedProtectedChromeDigest": "sha256:" + "3" * 64,
    }
    with pytest.raises(
        ContentGraphValidationError, match="content_integrity_protected_chrome_mismatch"
    ):
        seal_content_integrity_receipt(
            operation_id=operation_id,
            attempt_binding_digest="sha256:" + "6" * 64,
            source_revision=7,
            result_revision=8,
            generation_lane="new-authoring",
            content_graph_digest="sha256:" + "b" * 64,
            route_strategy_receipt_digest=route.receipt_digest,
            narrative_digest="sha256:" + "c" * 64,
            preserve_source_input_digest=None,
            provider_call_budget=_provider_budget(actual_calls=1),
            pages=[protected_mismatch],
        )
