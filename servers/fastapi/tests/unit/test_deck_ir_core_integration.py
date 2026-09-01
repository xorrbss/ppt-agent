from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from services.authored_vnext.deck_ir.director import (
    DeterministicDevFixtureDeckDirector,
    run_balanced_deck_director,
)
from services.authored_vnext.deck_ir.integration import (
    DeckIrIntegrationError,
    configure_deck_ir_execution_adapter,
    deck_ir_execution_adapter_configured,
    ensure_product_deck_ir_execution_adapter,
)
from services.authored_vnext.deck_ir.product_adapter import _evaluate_generation_quality
from services.authored_vnext.deck_ir.strategy import (
    capability_preflight,
    seal_generation_strategy_decision,
    validate_sealed_generation_strategy,
)
from services.authored_vnext.deck_ir.style_profiles import (
    design_grammar_for_style,
    resolve_style_profile,
)
from services.workspace_operation_runner import sanitize_workspace_event_payload


@dataclass
class _Request:
    n_slides: int = 5
    template: str = "authored"
    output: object = None
    authored_style: str | None = None


def test_deck_ir_core_seals_preflight_before_bounded_three_call_director(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECK_IR_GENERATION_ENABLED", "true")
    monkeypatch.setenv("DECK_IR_ALLOW_DEV_FIXTURE", "true")
    configure_deck_ir_execution_adapter(None)
    assert ensure_product_deck_ir_execution_adapter() is True
    assert deck_ir_execution_adapter_configured() is True
    preflight = capability_preflight(
        request=_Request(),
        available_capabilities=(
            "structured-deck-ir",
            "constraint-compiler",
            "fidelity-html-semantic",
        ),
    )
    receipt = seal_generation_strategy_decision(
        operation_id="op-1",
        requirement_graph_digest="sha256:requirements",
        requested_profile="balanced-quality-v1",
        preflight=preflight,
    )
    assert validate_sealed_generation_strategy(receipt, operation_id="op-1") == "deck-ir"
    assert DeckIrIntegrationError.code == "deck_ir_execution_adapter_unavailable"

    reservations: list[str] = []

    async def reserve(purpose: str) -> None:
        reservations.append(purpose)

    adapter = DeterministicDevFixtureDeckDirector(
        responses={
            "global-plan": {"schemaVersion": "deck-plan/v1", "design": "fixed"},
            "slide-ir-batch": {"schemaVersion": "slide-ir-batch/v1", "slides": []},
        }
    )
    result = asyncio.run(
        run_balanced_deck_director(
            adapter=adapter,
            brief="test brief",
            slide_count=5,
            language="ko-KR",
            strategy_receipt=receipt,
            reserve=reserve,
        )
    )

    assert result["initialBatchPlan"] == [3, 2]
    assert result["logicalCalls"] == 3
    assert reservations == ["global-plan", "slide-ir-batch", "slide-ir-batch"]
    # Exercise the exact production composition-root quality callback without
    # a fixture provider, browser process, or database publication.
    browser_checks = {
        "viewportContainment": True, "overflow": True, "clipping": True,
        "overlap": True, "ghosting": True, "fontLoad": True,
        "imageDecode": True, "minimumFont": True, "contrast": True,
        "connectorClearance": True, "quiescence": True,
        "pngObservationBinding": True,
    }
    quality = _evaluate_generation_quality({
        "observations": [{"checks": browser_checks, "issues": []}],
        "requirementCoverageReceipt": {"status": "passed"},
        "deckIr": {
            "story": {"arc": ["opening"]},
            "designLanguage": {"grammarId": "default", "paletteId": "core", "typeScaleId": "core"},
            "slides": [{
                "slideKey": "s1", "role": "opening", "claim": "A claim",
                "sourceBindingIds": [], "layoutIntent": {"density": "medium"},
                "components": [{"sourceBindingId": None}],
            }],
        },
        "compiledSlides": [{
            "slideKey": "s1", "html": '<p aria-label="claim">A claim</p>',
            "semantic": {"elements": [{"qid": "s1.claim"}]},
            "compileReceipt": {"receiptDigest": "sha256:ok", "browserMeasurementDigest": "sha256:measure"},
        }],
    })
    assert quality["passed"] is True
    assert quality["schemaVersion"] == "generation-quality-receipt/v1"
    safe = sanitize_workspace_event_payload(
        {
            "deckIr": {
                "strategy": "deck-ir",
                "slideCount": 5,
                "providerLogicalCalls": 3,
                "deckIrDigest": "sha256:deck",
                "browserClosureReceiptDigests": ["sha256:one"],
                "html": "must-not-escape",
            },
            "generationQualityReceipt": quality,
        },
        operation_kind="presentation.generate",
    )
    assert safe["deckIr"]["strategy"] == "deck-ir"
    assert safe["deckIr"]["browserClosureReceiptDigests"] == ["sha256:one"]
    assert "html" not in safe["deckIr"]
    assert safe["generationQualityReceipt"]["passed"] is True
    configure_deck_ir_execution_adapter(None)


def test_explicit_deck_ir_admits_ten_slide_bounded_plan_and_binds_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECK_IR_ALLOW_DEV_FIXTURE", "true")
    style = resolve_style_profile("hyundai-culture-center")
    preflight = capability_preflight(
        request=_Request(n_slides=10, authored_style="hyundai-culture-center"),
        available_capabilities=(
            "structured-deck-ir",
            "constraint-compiler",
            "fidelity-html-semantic",
        ),
    )
    assert preflight["status"] == "passed"
    assert preflight["styleProfileDigest"] == style.profile_digest
    assert preflight["retainedReferenceDigest"] == style.retained_reference_digest
    assert preflight["grammarCapabilityDigest"] == design_grammar_for_style(style).manifest_digest
    receipt = seal_generation_strategy_decision(
        operation_id="op-10",
        requirement_graph_digest="sha256:requirements",
        requested_profile="balanced-quality-v1",
        preflight=preflight,
        force_strategy="deck-ir",
        requested_generation_engine="deck-ir",
    )
    assert validate_sealed_generation_strategy(receipt, operation_id="op-10") == "deck-ir"
    assert receipt["styleProfileDigest"] == style.profile_digest

    reservations: list[str] = []

    async def reserve(purpose: str) -> None:
        reservations.append(purpose)

    adapter = DeterministicDevFixtureDeckDirector(
        responses={
            "global-plan": {"schemaVersion": "deck-plan/v1", "design": "fixed"},
            "slide-ir-batch": {"schemaVersion": "slide-ir-batch/v1", "slides": []},
        }
    )
    result = asyncio.run(
        run_balanced_deck_director(
            adapter=adapter,
            brief="DevAX SI 구축 제안서",
            slide_count=10,
            language="ko-KR",
            strategy_receipt=receipt,
            reserve=reserve,
        )
    )
    assert result["initialBatchPlan"] == [3, 3, 4]
    assert result["logicalCalls"] == 4
    assert reservations == [
        "global-plan",
        "slide-ir-batch",
        "slide-ir-batch",
        "slide-ir-batch",
    ]

    unsupported = capability_preflight(
        request=_Request(n_slides=11, authored_style="hyundai-culture-center"),
        available_capabilities=(
            "structured-deck-ir",
            "constraint-compiler",
            "fidelity-html-semantic",
        ),
    )
    rejected = seal_generation_strategy_decision(
        operation_id="op-11",
        requirement_graph_digest="sha256:requirements",
        requested_profile="balanced-quality-v1",
        preflight=unsupported,
        force_strategy="reject",
        requested_generation_engine="deck-ir",
    )
    assert rejected["selectedStrategy"] == "reject"
    assert rejected["missingCapabilities"] == ["slide-count"]
