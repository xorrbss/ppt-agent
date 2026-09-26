"""Decision table for resolve_generation_strategy (svg is the default).

Each test name states the branch it pins. See
docs/generation-core-architecture-20260925.md Part A for the design.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.v1.ppt.endpoints import presentation_generate as endpoint
from api.v2.contracts import PresentationGenerationRequestV2
from models.generate_presentation_request import GeneratePresentationRequest
from services.generation_core.admission.strategy import (
    recorded_generation_strategy,
    resolve_generation_strategy,
    resolve_generation_strategy_with_reason,
)
from api.v1.ppt.endpoints.presentation_generation_staging import (
    _durable_request_payload, _request_from_durable_payload,
)


def _request(**overrides):
    base = dict(
        strategy=None,
        generation_mode="auto",
        design_pack_pin=None,
        source_mode=None,
        content_policy=None,
        authored_style=None,
        export_as="pptx",
        page_size=None,
        orientation=None,
        generation_engine="authored",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_explicit_svg_is_honoured_verbatim():
    request = _request(strategy="svg", generation_mode="registered-template", design_pack_pin={"id": "x"})
    assert resolve_generation_strategy_with_reason(request) == ("svg", "explicit")


def test_explicit_authored_is_honoured_verbatim():
    request = _request(strategy="authored")
    assert resolve_generation_strategy_with_reason(request) == ("authored", "explicit")


def test_omitted_strategy_defaults_to_svg():
    request = _request()
    assert resolve_generation_strategy_with_reason(request) == ("svg", "default")


def test_omitted_strategy_keeps_default_reason_after_durable_rehydration():
    request = GeneratePresentationRequest(content="Smoke deck", slides_markdown=["one"])
    payload = _durable_request_payload(request)
    restored = _request_from_durable_payload(payload)
    assert payload["strategy"] == "svg"
    assert payload["_internal_generation_strategy_reason"] == "default"
    assert restored.strategy == "svg"
    assert recorded_generation_strategy(restored) == ("svg", "default")
    assert _durable_request_payload(restored)["_internal_generation_strategy_reason"] == "default"


def test_omitted_strategy_stays_authored_for_registered_template():
    request = _request(generation_mode="registered-template")
    assert resolve_generation_strategy_with_reason(request) == ("authored", "registered_template")


def test_omitted_strategy_stays_authored_with_a_design_pack_pin():
    request = _request(design_pack_pin={"designPackId": "acme", "version": 1})
    assert resolve_generation_strategy_with_reason(request) == ("authored", "design_pack_pin")


def test_omitted_strategy_stays_authored_for_deck_ir_engine_conflict():
    request = _request(generation_engine="deck-ir")
    assert resolve_generation_strategy_with_reason(request) == ("authored", "svg_engine_conflict")


def test_omitted_strategy_resolves_svg_for_an_admissible_source_restyle():
    request = _request(
        source_mode="source_content_restyle",
        authored_style="screenshot-blue",
        content_policy=SimpleNamespace(text="verbatim", allow_summarization=False),
    )
    assert resolve_generation_strategy_with_reason(request) == ("svg", "default")


def test_omitted_strategy_stays_authored_for_a_non_verbatim_source_restyle():
    # svg admission requires a verbatim, non-summarizing content policy; the
    # new default must fall back to authored here instead of 422ing a
    # combination that used to work fine under the old (authored) default.
    request = _request(
        source_mode="source_content_restyle",
        authored_style="screenshot-blue",
        content_policy=SimpleNamespace(text="summary", allow_summarization=True),
    )
    assert resolve_generation_strategy_with_reason(request) == ("authored", "svg_source_verbatim_required")


def test_omitted_strategy_stays_authored_for_a_canvas_override_source_restyle():
    request = _request(
        source_mode="source_content_restyle",
        authored_style="screenshot-blue",
        content_policy=SimpleNamespace(text="verbatim", allow_summarization=False),
        page_size="A4",
        orientation="portrait",
    )
    assert resolve_generation_strategy_with_reason(request) == (
        "authored", "svg_source_canvas_override_not_supported",
    )


def test_omitted_strategy_stays_authored_for_an_svg_unsupported_style_id():
    # "devax-first-ten" is an authored-catalogue style id, not one of the
    # bounded svg source-template ids -- svg admission would reject it.
    request = _request(
        source_mode="source_content_restyle",
        authored_style="devax-first-ten",
        content_policy=SimpleNamespace(text="verbatim", allow_summarization=False),
    )
    assert resolve_generation_strategy_with_reason(request) == (
        "authored", "svg_source_template_unsupported",
    )


def test_omitted_strategy_stays_authored_for_raster_export_source_restyle():
    request = _request(
        source_mode="source_content_restyle",
        authored_style="screenshot-blue",
        content_policy=SimpleNamespace(text="verbatim", allow_summarization=False),
        export_as="pdf",
    )
    assert resolve_generation_strategy_with_reason(request) == (
        "authored", "source_content_format_not_supported",
    )


def test_omitted_strategy_resolves_svg_for_an_admissible_raster_reconstruction():
    request = _request(source_mode="raster_reconstruction", authored_style="screenshot-blue")
    assert resolve_generation_strategy_with_reason(request) == ("svg", "default")


def test_omitted_strategy_stays_authored_for_an_exact_reconstruction_style():
    request = _request(authored_style="exact-private")
    style = SimpleNamespace(reference_mode="exact_reconstruction")
    with patch("utils.authored_styles.resolve_authored_style", return_value=style):
        assert resolve_generation_strategy_with_reason(request) == (
            "authored", "svg_exact_reconstruction_not_supported",
        )


def test_resolve_generation_strategy_is_the_reason_wrapper_dropping_it():
    request = _request(generation_mode="registered-template")
    assert resolve_generation_strategy(request) == "authored"


def test_v1_request_model_itself_still_accepts_deck_ir_for_internal_construction():
    # The internal engine-routing bridge (services.public_generation_route.
    # effective_generation_engine) constructs a GeneratePresentationRequest
    # with generationEngine="deck-ir" for its own auto-routed decision -- a
    # model-level rejection here would break that. The client-facing reject
    # lives at admission time instead: see
    # api.v1.ppt.endpoints.presentation_generate.check_if_api_request_is_valid.
    request = GeneratePresentationRequest(content="Deck", generationEngine="deck-ir")
    assert request.generation_engine == "deck-ir"


def test_v2_public_contract_rejects_explicit_deck_ir_engine_with_a_stable_code():
    with pytest.raises(ValidationError, match="generation_engine_deck_ir_retired"):
        PresentationGenerationRequestV2(content="Deck", generationEngine="deck-ir")


def test_v1_direct_endpoint_admission_rejects_explicit_deck_ir_engine():
    request = GeneratePresentationRequest(
        content="Generate a deck", template="authored", generationEngine="deck-ir",
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(endpoint.check_if_api_request_is_valid(request, None))
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "generation_engine_deck_ir_retired"
