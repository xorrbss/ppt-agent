from __future__ import annotations

import asyncio
from uuid import UUID

from pydantic import ValidationError
import pytest

from services.authored_vnext.narrative_ir.content_graph import (
    ContentGraphValidationError,
    seal_content_graph,
    seal_content_integrity_receipt,
    seal_preserve_source_input,
)
from services.authored_vnext.narrative_ir.checkpoint import (
    InMemoryNarrativeCheckpointRepository,
)
from services.authored_vnext.narrative_ir.orchestration import (
    run_narrative_ir_operation,
)
from services.authored_vnext.narrative_ir.director import run_narrative_director
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


def test_narrative_director_seals_generated_content_graph_exactly_once() -> None:
    operation_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    provider_payload = {
        "schemaVersion": "narrative-ir-draft/v1",
        "title": "신규 사업 제안",
        "language": "ko-KR",
        "slides": [
            {
                "slideKey": "slide-001",
                "sequenceIndex": 0,
                "role": "content",
                "keyMessage": "AI 전환의 실행 기반을 마련합니다",
                "supportingPoints": ["표준 개발 체계", "품질 자동화"],
                "dataRepresentation": "metric",
                "data": [
                    {"datumKey": "lead-time", "label": "리드타임", "value": 30, "unit": "%"},
                    {"datumKey": "coverage", "label": "자동화 범위", "value": "전 과정"},
                ],
                "sourceOwnership": [],
                "frameSemantics": ["body"],
                "density": "medium",
            },
            {
                "slideKey": "slide-002",
                "sequenceIndex": 1,
                "role": "closing",
                "keyMessage": "단계적으로 성과를 확장합니다",
                "supportingPoints": ["진단부터 확산까지 연결"],
                "dataRepresentation": "none",
                "data": [],
                "sourceOwnership": [],
                "frameSemantics": ["body"],
                "density": "medium",
            },
        ],
        "sourceUnitOrder": [],
    }

    class Provider:
        single_physical_call = True

        async def generate(self, request):
            assert request["admittedContentGraph"] is None
            return provider_payload

    async def reserve(purpose: str, logical_ordinal: int, physical_ordinal: int):
        return {
            "reservationId": f"reservation-{physical_ordinal}",
            "purpose": purpose,
            "logicalOrdinal": logical_ordinal,
            "physicalOrdinal": physical_ordinal,
        }

    result = asyncio.run(
        run_narrative_director(
            operation_id=operation_id,
            brief="신규 콘텐츠로 2장 제안서를 작성",
            slide_count=2,
            language="ko-KR",
            provider=Provider(),
            reserve=reserve,
        )
    )
    narrative = result.narrative
    units = [
        unit for slide in narrative.slides for unit in slide.sealed_content_units
    ]
    assert narrative.content_graph_digest is not None
    assert [
        unit_id for slide in narrative.slides for unit_id in slide.content_unit_ids
    ] == [unit.content_unit_id for unit in units]
    assert len(units) == len({unit.content_unit_id for unit in units}) == 7
    assert all(unit.source_ownership for unit in units)


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


def test_narrative_ir_43_slide_whole_operation_fallback_is_safe_and_retry_stable() -> None:
    operation_id = UUID("11111111-1111-4111-8111-111111111111")
    job_id = UUID("22222222-2222-4222-8222-222222222222")
    source_units = [f"source-{index + 1:03d}" for index in range(43)]

    def valid_narrative() -> dict[str, object]:
        return {
            "schemaVersion": "narrative-ir-draft/v1",
            "title": "43페이지 제안서",
            "language": "ko-KR",
            "slides": [
                {
                    "slideKey": f"slide-{index + 1:03d}",
                    "sequenceIndex": index,
                    "role": "closing" if index == 42 else "content",
                    "keyMessage": f"페이지 {index + 1} 핵심 메시지",
                    "supportingPoints": ["원문 순서와 의미를 유지한다"],
                    "dataRepresentation": "none",
                    "data": [],
                    "sourceOwnership": [],
                    "frameSemantics": ["body"],
                    "density": "medium",
                }
                for index in range(43)
            ],
            "sourceUnitOrder": [
                {"sourceIndex": index, "sourceUnitId": source_unit}
                for index, source_unit in enumerate(source_units)
            ],
        }

    class Provider:
        single_physical_call = True

        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                # Provider rendering/safety authority is rejected before it can
                # enter a checkpoint; recovery receives only the stable code.
                return {**valid_narrative(), "css": "position:absolute;left:10px"}
            if self.calls == 2:
                assert request["purpose"] == "narrative.recovery"
                assert request["recoveryReason"] == "narrative_ir_layout_or_policy_forbidden"
                return valid_narrative()
            raise AssertionError("retry must restore the sealed checkpoint without a provider call")

    reservations: list[str] = []

    async def reserve(purpose: str, logical_ordinal: int, physical_ordinal: int):
        reservations.append(purpose)
        return {
            "reservationId": f"reservation-{physical_ordinal}",
            "purpose": purpose,
            "logicalOrdinal": logical_ordinal,
            "physicalOrdinal": physical_ordinal,
        }

    class SourceNative:
        execution_mode = "source-native"

        async def execute(self, **_kwargs):
            raise AssertionError("one missing frame must prevent every source-native slide")

    class AuthoredScene:
        execution_mode = "server-owned-authored-scene"
        provider_html_enabled = False

        def __init__(self) -> None:
            self.slide_counts: list[int] = []

        async def execute(self, *, narrative, route, checkpoint):
            assert route.legacy_provider_html_allowed is False
            assert checkpoint.narrative.narrative_digest == narrative.narrative_digest
            self.slide_counts.append(len(narrative.slides))
            return {"engine": self.execution_mode, "slideCount": len(narrative.slides)}

    provider = Provider()
    fallback = AuthoredScene()
    repository = InMemoryNarrativeCheckpointRepository()
    design_pack = {
        "schemaVersion": "design-pack-snapshot/v1",
        "designPackId": "registered-proposal-pack",
        "version": 7,
        "manifestDigest": canonical_digest({"pack": "registered-proposal-pack", "version": 7}),
        "frameRegistryDigest": canonical_digest({"registry": "registered-proposal-pack", "version": 7}),
        "frames": [
            {
                "frameId": "content-frame",
                "frameDigest": canonical_digest({"frame": "content-frame"}),
                "sourcePartDigest": canonical_digest({"sourcePart": "ppt/slides/slide2.xml"}),
                "sourceNative": True,
                "roles": ["content"],
                "dataRepresentations": ["none"],
                "densities": ["medium"],
                "frameSemantics": ["body"],
                "maxSupportingPoints": 4,
                "supportsSourceOwnedContent": True,
            }
        ],
    }
    strategy_digest = canonical_digest({"strategy": "narrative-ir"})

    async def scenario():
        first = await run_narrative_ir_operation(
            operation_id=operation_id,
            job_id=job_id,
            attempt_token="attempt-one",
            fencing_token=1,
            strategy_receipt_digest=strategy_digest,
            brief="43페이지를 같은 수와 순서로 구성",
            slide_count=43,
            language="ko-KR",
            source_unit_ids=source_units,
            provider=provider,
            reserve=reserve,
            design_pack=design_pack,
            source_native_executor=SourceNative(),
            authored_scene_fallback_executor=fallback,
            checkpoint_repository=repository,
        )
        second = await run_narrative_ir_operation(
            operation_id=operation_id,
            job_id=job_id,
            attempt_token="attempt-two",
            fencing_token=2,
            strategy_receipt_digest=strategy_digest,
            brief="this value is not reread after checkpoint restore",
            slide_count=43,
            language="ko-KR",
            source_unit_ids=source_units,
            provider=provider,
            reserve=reserve,
            design_pack=design_pack,
            source_native_executor=SourceNative(),
            authored_scene_fallback_executor=fallback,
            checkpoint_repository=repository,
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert first.resumed is False and second.resumed is True
    assert provider.calls == 2
    assert reservations == ["narrative.initial", "narrative.recovery"]
    assert first.route.final_route == "authored-scene-fallback"
    assert first.route.fallback_reason == "no-compatible-frame"
    assert first.route.provider_call_budget.actual_logical_calls == 2
    assert first.route.provider_call_budget.actual_physical_calls == 2
    assert first.checkpoint.frame_search_receipt.status == "no-compatible-frame"
    assert first.checkpoint.frame_selection_receipt is None
    assert [item.source_unit_id for item in first.checkpoint.narrative.source_unit_order] == source_units
    assert fallback.slide_counts == [43, 43]
    assert second.route.receipt_digest == first.route.receipt_digest
    assert second.checkpoint.lineage_digest == first.checkpoint.lineage_digest
    assert second.checkpoint.checkpoint_digest != first.checkpoint.checkpoint_digest
