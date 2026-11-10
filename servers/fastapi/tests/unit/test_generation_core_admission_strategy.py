"""Decision table for resolve_generation_strategy (svg is the only strategy).

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
from models.public_v2_contracts import PresentationGenerationRequestV2
from models.generate_presentation_request import GeneratePresentationRequest
from services.generation_core.admission.strategy import (
    AUTHORED_STRATEGY_REMOVED,
    recorded_generation_strategy,
    removed_generation_strategy_code,
    require_generation_strategy_admission,
    resolve_generation_strategy,
    resolve_generation_strategy_with_reason,
)
from services.svg_generation_admission import require_svg_creation_admission
from services.presentation_generation_staging import (
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


def test_explicit_deck_ir_engine_is_not_a_strategy_choice():
    # The deck-ir engine ran authored generation; it is refused at admission
    # (require_generation_strategy_admission), not resolved to authored.
    request = _request(generation_engine="deck-ir")
    assert resolve_generation_strategy_with_reason(request) == ("svg", "default")
    assert removed_generation_strategy_code(request) == "authored_strategy_removed"


def test_omitted_strategy_resolves_svg_for_an_admissible_source_restyle():
    request = _request(
        source_mode="source_content_restyle",
        authored_style="screenshot-blue",
        content_policy=SimpleNamespace(text="verbatim", allow_summarization=False),
    )
    assert resolve_generation_strategy_with_reason(request) == ("svg", "default")


#: Each input an omitted strategy used to send to authored silently, with the
#: svg admission violation that is now its 422 code (None: svg admits it).
_FORMER_FALLBACKS = {
    "non_verbatim_source_restyle": (
        dict(
            source_mode="source_content_restyle",
            authored_style="screenshot-blue",
            content_policy=SimpleNamespace(text="summary", allow_summarization=True),
        ),
        "svg_source_verbatim_required",
    ),
    "canvas_override_source_restyle": (
        dict(
            source_mode="source_content_restyle",
            authored_style="screenshot-blue",
            content_policy=SimpleNamespace(text="verbatim", allow_summarization=False),
            page_size="A4",
            orientation="portrait",
        ),
        None,
    ),
    "invalid_canvas_selectors_source_restyle": (
        dict(
            source_mode="source_content_restyle",
            authored_style="screenshot-blue",
            content_policy=SimpleNamespace(text="verbatim", allow_summarization=False),
            page_size="A4",
            orientation=None,
        ),
        "document_canvas_selectors_invalid",
    ),
    "svg_unsupported_source_style_id": (
        dict(
            source_mode="source_content_restyle",
            # An authored-catalogue style id, not a bounded svg source-template id.
            authored_style="devax-first-ten",
            content_policy=SimpleNamespace(text="verbatim", allow_summarization=False),
        ),
        "svg_source_template_unavailable",
    ),
    "missing_source_style_id": (
        dict(
            source_mode="source_content_restyle",
            authored_style=None,
            content_policy=SimpleNamespace(text="verbatim", allow_summarization=False),
        ),
        "svg_source_template_selection_required",
    ),
    "pdf_export_source_restyle": (
        dict(
            source_mode="source_content_restyle",
            authored_style="screenshot-blue",
            content_policy=SimpleNamespace(text="verbatim", allow_summarization=False),
            export_as="pdf",
        ),
        "source_content_format_not_supported",
    ),
    "design_reference_conflict": (
        dict(svg_template_pin={"id": "t"}, master_pin={"id": "m"}),
        "svg_design_reference_conflict",
    ),
}


@pytest.mark.parametrize("name", sorted(_FORMER_FALLBACKS))
def test_omitted_strategy_resolves_svg_on_every_former_authored_fallback(name):
    overrides, _violation = _FORMER_FALLBACKS[name]
    assert resolve_generation_strategy_with_reason(_request(**overrides)) == ("svg", "default")


@pytest.mark.parametrize("name", sorted(_FORMER_FALLBACKS))
def test_former_authored_fallback_is_a_422_with_its_own_violation_code(name):
    overrides, violation = _FORMER_FALLBACKS[name]
    request = _request(svg_template_pin=None, svg_design_reference_snapshot_id=None, master_pin=None,
                       common_header_mode=None, full_page_preserve_pages=())
    for key, value in overrides.items():
        setattr(request, key, value)
    if violation is None:
        require_svg_creation_admission(request)
        return
    with pytest.raises(HTTPException) as excinfo:
        require_svg_creation_admission(request)
    assert (excinfo.value.status_code, excinfo.value.detail) == (422, violation)


def test_omitted_strategy_resolves_svg_for_an_admissible_raster_reconstruction():
    request = _request(source_mode="raster_reconstruction", authored_style="screenshot-blue")
    assert resolve_generation_strategy_with_reason(request) == ("svg", "default")


def test_omitted_strategy_uses_svg_for_an_exact_reconstruction_style():
    # Exact-reconstruction styles may serve as SVG design references
    # (docs/svg-all-canvas-free-composition-design-20261001.md, section 5.2).
    request = _request(authored_style="exact-private")
    style = SimpleNamespace(reference_mode="exact_reconstruction")
    with patch("utils.authored_styles.resolve_authored_style", return_value=style):
        assert resolve_generation_strategy_with_reason(request) == ("svg", "default")


def test_resolve_generation_strategy_is_the_reason_wrapper_dropping_it():
    assert resolve_generation_strategy(_request(strategy="authored")) == "authored"
    assert resolve_generation_strategy(_request()) == "svg"


def test_v1_request_model_still_parses_removed_choices():
    # The wire shape is unchanged: both removed choices parse, and admission
    # (not the model) rejects them, so durable payloads staged before the
    # removal still restore and fail cleanly on dispatch.
    request = GeneratePresentationRequest(content="Deck", generationEngine="deck-ir")
    assert request.generation_engine == "deck-ir"
    assert GeneratePresentationRequest(content="Deck", strategy="authored").strategy == "authored"


def test_v2_public_contract_rejects_explicit_deck_ir_engine_with_the_removal_code():
    with pytest.raises(ValidationError, match="authored_strategy_removed"):
        PresentationGenerationRequestV2(content="Deck", generationEngine="deck-ir")


@pytest.mark.parametrize("overrides", [{"generationEngine": "deck-ir"}, {"strategy": "authored"}])
def test_v1_direct_endpoint_admission_rejects_authored_generation(overrides):
    request = GeneratePresentationRequest(content="Generate a deck", template="authored", **overrides)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(endpoint.check_if_api_request_is_valid(request, None))
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == "authored_strategy_removed"


def test_require_generation_strategy_admission_carries_a_stable_non_retryable_code():
    with pytest.raises(HTTPException) as excinfo:
        require_generation_strategy_admission(_request(strategy="authored"))
    assert excinfo.value.status_code == 422
    assert excinfo.value.code == AUTHORED_STRATEGY_REMOVED == "authored_strategy_removed"
    assert excinfo.value.retryable is False
    require_generation_strategy_admission(_request(strategy="svg"))
    require_generation_strategy_admission(_request())


class _NoWriteSession:
    """Fails the test on any durable write an admission refusal must not make."""

    def add(self, _row):
        raise AssertionError("a refused request must not stage any row")

    async def commit(self):
        raise AssertionError("a refused request must not commit")

    async def flush(self):
        raise AssertionError("a refused request must not flush")


def test_create_endpoint_refuses_an_authored_deck_shell():
    from api.v1.ppt.endpoints import presentation_crud

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(presentation_crud.create_presentation(
            content="Authored deck", strategy="authored", sql_session=_NoWriteSession(),
        ))
    assert (excinfo.value.status_code, excinfo.value.detail) == (422, "authored_strategy_removed")


def test_workspace_retry_admission_refuses_an_operation_staged_with_authored():
    from services import presentation_generation_pipeline as generation_pipeline

    request = GeneratePresentationRequest(content="Retry an authored deck", strategy="authored")
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(generation_pipeline._admit_generation_operation_v2_compatible(
            _NoWriteSession(), request=request, presentation=None, operation=None,
        ))
    assert (excinfo.value.status_code, excinfo.value.detail) == (422, "authored_strategy_removed")


def test_async_admission_turns_a_former_authored_fallback_into_its_own_422():
    # Omitted strategy + a summarizing source restyle used to fall back to the
    # authored reflow silently; it is now svg's own 422 before any durable row.
    from fastapi import BackgroundTasks
    from services import presentation_generation_pipeline as generation_pipeline

    request = GeneratePresentationRequest(
        content="Restyle the uploaded deck",
        sourceMode="source_content_restyle",
        authored_style="screenshot-blue",
        sourceContentManifestPin={"manifestId": "scm_source-deck", "digest": "sha256:" + "b" * 64},
        contentPolicy={"text": "outline", "allowSummarization": True},
    )
    assert request.strategy is None
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(generation_pipeline.queue_presentation_generation(
            request, BackgroundTasks(), _NoWriteSession(),
        ))
    assert (excinfo.value.status_code, excinfo.value.detail) == (422, "svg_source_verbatim_required")
