import asyncio

import pytest
from pydantic import ValidationError

from api.v2.contracts import (
    GenerationRouteReceiptV1,
    PresentationGenerationRequestV2,
)
from models.generate_presentation_request import GeneratePresentationRequest
from services import public_v2_orchestrator


def test_generation_mode_contract_and_server_owned_route_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="design_pack_pin_required"):
        PresentationGenerationRequestV2(
            content="Registered template deck",
            generationMode="registered-template",
        )
    with pytest.raises(ValidationError, match="capability_not_supported"):
        PresentationGenerationRequestV2(
            content="Editable deck",
            generationMode="editable-first",
        )

    pin = {
        "designPackId": "registered.enterprise-blue",
        "version": 7,
        "digest": "sha256:" + ("1" * 64),
    }
    request = PresentationGenerationRequestV2(
        content="Registered template deck",
        generationMode="registered-template",
        designPackPin=pin,
        generationEngine="authored",
    )
    assert public_v2_orchestrator._effective_generation_engine(request) == "deck-ir"

    captured: dict[str, object] = {}

    def resolve(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "selectedRoute": "source-native",
            "narrativeIrDigest": "sha256:" + ("2" * 64),
        }

    receipt = asyncio.run(
        public_v2_orchestrator._resolve_generation_route_receipt(
            request,
            slide_count=10,
            resolver=resolve,
        )
    )
    assert captured == {
        "generation_mode": "registered-template",
        "design_pack_pin": pin,
        "slide_count": 10,
    }
    assert receipt.selected_route == "source-native"
    assert receipt.output_capability == "fidelity"
    assert receipt.design_pack_pin is not None
    sealed = receipt.model_dump(mode="json", by_alias=True)
    assert GenerationRouteReceiptV1.model_validate(sealed) == receipt
    with pytest.raises(ValidationError, match="receiptDigest"):
        GenerationRouteReceiptV1.model_validate(
            {**sealed, "receiptDigest": "sha256:" + ("0" * 64)}
        )

    monkeypatch.setattr(
        public_v2_orchestrator,
        "_installed_generation_route_resolver",
        lambda: None,
    )
    fallback_request = PresentationGenerationRequestV2(
        content="Brand style deck",
        generationMode="brand-style",
    )
    fallback = asyncio.run(
        public_v2_orchestrator._resolve_generation_route_receipt(
            fallback_request,
            slide_count=10,
            resolver=None,
        )
    )
    assert fallback.selected_route == "authored-fallback"
    assert fallback.fallback_reason == "route_resolver_unavailable"
    assert public_v2_orchestrator._effective_generation_engine(fallback_request) == (
        "deck-ir"
    )

    legacy = PresentationGenerationRequestV2(
        content="Compatibility deck",
        generationEngine="authored",
    )
    assert public_v2_orchestrator._effective_generation_engine(legacy) == "authored"
    durable = GeneratePresentationRequest(
        content=request.content,
        generationMode=request.generation_mode,
        designPackPin=pin,
        generationEngine="deck-ir",
    ).model_dump(mode="json", by_alias=True)
    assert durable["generationMode"] == "registered-template"
    assert durable["designPackPin"] == pin
