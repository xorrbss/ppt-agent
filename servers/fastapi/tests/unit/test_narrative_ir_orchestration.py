from __future__ import annotations

import asyncio
from uuid import UUID

from services.authored_vnext.narrative_ir.checkpoint import (
    InMemoryNarrativeCheckpointRepository,
)
from services.authored_vnext.narrative_ir.orchestration import (
    run_narrative_ir_operation,
)
from services.authored_vnext.narrative_ir.validation import canonical_digest


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
