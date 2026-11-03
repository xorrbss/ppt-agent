from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
import pytest

from services.authored_vnext.narrative_ir.content_graph import (
    ContentGraphValidationError,
    seal_content_graph,
    seal_content_integrity_receipt,
    seal_preserve_source_input,
)
from services.authored_vnext.narrative_ir.validation import (
    NarrativeIRValidationError,
    canonical_digest,
    seal_narrative_ir,
)


def _content_page(page_index: int, *, image: bool = False) -> dict[str, object]:
    unit_id = f"unit-{page_index + 1:03d}"
    source_digest = canonical_digest({"sourcePage": page_index})
    if image:
        payload = {
            "text": "제품 구성도",
            "data": [],
            "assetOccurrenceId": "image-occurrence-001",
            "assetDigest": canonical_digest({"asset": "diagram.png"}),
        }
        kind = "image"
    else:
        payload = {"text": f"원문 본문 {page_index + 1}", "data": []}
        kind = "text"
    return {
        "pageId": f"page-{page_index + 1:03d}",
        "sourceUnitId": f"source-{page_index + 1:03d}",
        "contentUnits": [
            {
                "contentUnitId": unit_id,
                "kind": kind,
                "payload": payload,
                "required": True,
                "sourceOwnership": [
                    {
                        "sourceBindingId": f"binding-{page_index + 1:03d}",
                        "sourceContentDigest": source_digest,
                        "useMode": "verbatim",
                        "required": True,
                    }
                ],
            }
        ],
    }


def test_content_graph_dual_lane_seals_every_unit_and_fails_closed() -> None:
    operation_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    pages = [_content_page(0), _content_page(1, image=True)]
    preserve_graph = seal_content_graph(pages, generation_lane="preserve-source")
    preserve_input = seal_preserve_source_input(
        operation_id=operation_id, content_graph=preserve_graph
    )
    assert preserve_input.generation_lane == "preserve-source"
    assert [page.page_index for page in preserve_input.content_graph.pages] == [0, 1]
    assert preserve_input.content_graph.pages[1].content_units[0].payload.asset_occurrence_id == (
        "image-occurrence-001"
    )
    with pytest.raises(ValidationError):
        preserve_input.content_graph.pages[0].page_index = 9
    structural_page = _content_page(0)
    structural_page["contentUnits"][0]["kind"] = "chart"
    with pytest.raises(
        ContentGraphValidationError, match="preserve_source_structural_unit_forbidden"
    ):
        seal_content_graph([structural_page], generation_lane="preserve-source")

    graph = seal_content_graph(pages, generation_lane="new-authoring")
    draft = {
        "schemaVersion": "narrative-ir-draft/v1",
        "title": "신규 제안",
        "language": "ko-KR",
        "slides": [
            {
                "slideKey": f"slide-{index + 1:03d}",
                "sequenceIndex": index,
                "role": "content",
                "keyMessage": f"메시지 {index + 1}",
                "supportingPoints": [],
                "dataRepresentation": "none",
                "data": [],
                "sourceOwnership": [],
                "frameSemantics": ["body"],
                "density": "medium",
                "contentUnitIds": [f"unit-{index + 1:03d}"],
            }
            for index in range(2)
        ],
        "sourceUnitOrder": [
            {"sourceIndex": index, "sourceUnitId": f"source-{index + 1:03d}"}
            for index in range(2)
        ],
    }
    narrative = seal_narrative_ir(
        draft,
        operation_id=operation_id,
        admitted_slide_count=2,
        content_graph=graph,
    )
    assert narrative.generation_lane == "new-authoring"
    assert narrative.content_graph_digest == graph.content_graph_digest
    assert [
        unit.content_unit_id
        for slide in narrative.slides
        for unit in slide.sealed_content_units
    ] == ["unit-001", "unit-002"]
    assert narrative.slides[0].sealed_content_units[0].payload.text == "원문 본문 1"
    assert narrative.slides[1].sealed_content_units[0].payload.asset_digest == canonical_digest(
        {"asset": "diagram.png"}
    )

    duplicate = dict(draft)
    duplicate["slides"] = [dict(item) for item in draft["slides"]]
    duplicate["slides"][1]["contentUnitIds"] = ["unit-001"]
    with pytest.raises(
        NarrativeIRValidationError, match="narrative_ir_content_unit_duplicate"
    ):
        seal_narrative_ir(
            duplicate,
            operation_id=operation_id,
            admitted_slide_count=2,
            content_graph=graph,
        )
    provider_sealed = dict(draft)
    provider_sealed["slides"] = [dict(item) for item in draft["slides"]]
    provider_sealed["slides"][0]["sealedContentUnits"] = [
        graph.pages[0].content_units[0].model_dump(mode="json", by_alias=True)
    ]
    with pytest.raises(
        NarrativeIRValidationError, match="narrative_ir_server_authority_forbidden"
    ):
        seal_narrative_ir(
            provider_sealed,
            operation_id=operation_id,
            admitted_slide_count=2,
            content_graph=graph,
        )

    # The receipt is bound to one durable attempt (operation, job, attempt token
    # and lease fence) and carries the sealed provider call budget it consumed.
    job_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    attempt_binding_digest = canonical_digest(
        {
            "operationId": str(operation_id),
            "jobId": str(job_id),
            "attemptToken": "attempt-1",
            "fencingToken": 3,
        }
    )

    def provider_call_budget(*, actual_calls: int) -> dict[str, object]:
        return {
            "schemaVersion": "narrative-provider-call-budget/v1",
            "maximumLogicalCalls": 2,
            "maximumPhysicalCalls": 2,
            "actualLogicalCalls": actual_calls,
            "actualPhysicalCalls": actual_calls,
            "reservations": [
                {
                    "reservationId": f"{job_id}:narrative:{ordinal}",
                    "purpose": "narrative.initial" if ordinal == 1 else "narrative.recovery",
                    "logicalOrdinal": ordinal,
                    "physicalOrdinal": ordinal,
                }
                for ordinal in range(1, actual_calls + 1)
            ],
        }

    def integrity_pages_for(sealed_graph) -> list[dict[str, object]]:
        pages_out: list[dict[str, object]] = []
        for page in sealed_graph.pages:
            unit_ids = tuple(unit.content_unit_id for unit in page.content_units)
            digest = canonical_digest(
                [unit.content_digest for unit in page.content_units]
            )
            assets = tuple(
                unit.payload.asset_occurrence_id
                for unit in page.content_units
                if unit.payload.asset_occurrence_id is not None
            )
            protected_chrome_digest = canonical_digest(
                {"protectedChrome": page.page_id}
            )
            pages_out.append(
                {
                    "pageIndex": page.page_index,
                    "expectedContentUnitIds": unit_ids,
                    "boundContentUnitIds": unit_ids,
                    "renderedContentUnitIds": unit_ids,
                    "expectedDigest": digest,
                    "boundDigest": digest,
                    "renderedDigest": digest,
                    "expectedAssetOccurrences": assets,
                    "renderedAssetOccurrences": assets,
                    "sourceOwnershipDigest": canonical_digest(
                        [
                            ownership.model_dump(mode="json", by_alias=True)
                            for unit in page.content_units
                            for ownership in unit.source_ownership
                        ]
                    ),
                    "expectedProtectedChromeDigest": protected_chrome_digest,
                    "observedProtectedChromeDigest": protected_chrome_digest,
                    "browserReadbackDigest": canonical_digest(
                        {"browserReadback": page.page_id, "units": list(unit_ids)}
                    ),
                }
            )
        return pages_out

    integrity_pages = integrity_pages_for(graph)
    receipt = seal_content_integrity_receipt(
        operation_id=operation_id,
        attempt_binding_digest=attempt_binding_digest,
        source_revision=1,
        result_revision=2,
        generation_lane="new-authoring",
        content_graph_digest=graph.content_graph_digest,
        route_strategy_receipt_digest="sha256:" + "4" * 64,
        narrative_digest=narrative.narrative_digest,
        preserve_source_input_digest=None,
        provider_call_budget=provider_call_budget(actual_calls=1),
        pages=integrity_pages,
    )
    assert receipt.status == "passed" and receipt.required_coverage == 1.0
    assert receipt.attempt_binding_digest == attempt_binding_digest
    assert receipt.provider_call_budget.actual_physical_calls == 1
    assert receipt.provider_call_budget_digest == canonical_digest(
        receipt.provider_call_budget.model_dump(mode="json", by_alias=True)
    )

    # The preserve-source lane seals the same page evidence against the sealed
    # input digest instead of a narrative, and it may not consume provider calls.
    preserve_receipt = seal_content_integrity_receipt(
        operation_id=operation_id,
        attempt_binding_digest=attempt_binding_digest,
        source_revision=1,
        result_revision=2,
        generation_lane="preserve-source",
        content_graph_digest=preserve_graph.content_graph_digest,
        route_strategy_receipt_digest="sha256:" + "4" * 64,
        narrative_digest=None,
        preserve_source_input_digest=preserve_input.input_digest,
        provider_call_budget=provider_call_budget(actual_calls=0),
        pages=integrity_pages_for(preserve_graph),
    )
    assert preserve_receipt.status == "passed"
    assert preserve_receipt.preserve_source_input_digest == preserve_input.input_digest
    with pytest.raises(
        ContentGraphValidationError, match="content_integrity_receipt_invalid"
    ):
        seal_content_integrity_receipt(
            operation_id=operation_id,
            attempt_binding_digest=attempt_binding_digest,
            source_revision=1,
            result_revision=2,
            generation_lane="preserve-source",
            content_graph_digest=preserve_graph.content_graph_digest,
            route_strategy_receipt_digest="sha256:" + "4" * 64,
            narrative_digest=None,
            preserve_source_input_digest=preserve_input.input_digest,
            provider_call_budget=provider_call_budget(actual_calls=1),
            pages=integrity_pages_for(preserve_graph),
        )

    failed_page = dict(integrity_pages[0])
    failed_page["renderedContentUnitIds"] = ()
    with pytest.raises(
        ContentGraphValidationError,
        match="content_integrity_required_coverage_failed",
    ):
        seal_content_integrity_receipt(
            operation_id=operation_id,
            attempt_binding_digest=attempt_binding_digest,
            source_revision=1,
            result_revision=2,
            generation_lane="new-authoring",
            content_graph_digest=graph.content_graph_digest,
            route_strategy_receipt_digest="sha256:" + "4" * 64,
            narrative_digest=narrative.narrative_digest,
            preserve_source_input_digest=None,
            provider_call_budget=provider_call_budget(actual_calls=1),
            pages=[failed_page, integrity_pages[1]],
        )
