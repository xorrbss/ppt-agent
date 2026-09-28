"""Public v2 admission error translation: findings 1 and 2.

Finding 1: a v2 raster_reconstruction request whose ``authored_style`` is not
the raster template (``screenshot-blue``) reaches
``models.generate_presentation_request.GeneratePresentationRequest``'s own
``source_content_template_required`` rejection. Before the fix, building that
internal bridge object in ``services/public_v2_orchestrator.py`` was outside
any try/except, so the pydantic ``ValidationError`` escaped as an unhandled
500 instead of the orchestrator's normal 422 envelope.

Finding 2: a ``registered-template`` + ``source_content_restyle`` request
(design pack pin + restyle) is a deterministic "combination not supported"
rejection (``services.authored_vnext.generation_route.GenerationRouteError``:
``source_content_design_pack_not_supported``), not a resolver outage. Before
the fix, ``_resolve_generation_route_receipt`` mapped every resolver
exception -- deterministic or not -- to 503
``generation_route_resolver_unavailable``.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from pydantic import ValidationError

from api.v2.contracts import PresentationGenerationRequestV2
from api.v2.orchestration import PublicV2OrchestrationError
from models.generate_presentation_request import GeneratePresentationRequest
from services import public_v2_orchestrator
from services.authored_vnext import generation_route
from services.public_v2_admission_errors import (
    generation_request_admission_error_code,
    generation_route_error_types,
)
from services.authored_canvas import validate_authored_canvas_admission
from services.public_v2_request_bridge import (
    resolve_canvas_style_id,
    resolve_internal_generation_request,
)
from utils.authored_styles import resolve_authored_style


_RASTER_APPROVAL_PIN = {
    "operationId": str(uuid.uuid4()),
    "analysisId": str(uuid.uuid4()),
    "analysisRevision": 1,
    "analysisDigest": "b" * 64,
    "sourceSha256": "b" * 64,
    "approvedRegionIds": [],
    "approvalDigest": "b" * 64,
}


def _raster_generation_request_v2(authored_style: str) -> PresentationGenerationRequestV2:
    return PresentationGenerationRequestV2(
        content="Raster reconstruction baseline",
        authoredStyle=authored_style,
        pageSize="A4",
        orientation="landscape",
        sourceMode="raster_reconstruction",
        rasterApprovalPin=dict(_RASTER_APPROVAL_PIN),
        referenceSnapshotIds=[str(uuid.uuid4())],
    )


def test_generation_request_admission_error_code_extracts_validator_message() -> None:
    with pytest.raises(ValidationError) as excinfo:
        GeneratePresentationRequest(
            content="Raster reconstruction baseline",
            template="authored",
            authored_style="default",
            sourceMode="raster_reconstruction",
            rasterApprovalPin=dict(_RASTER_APPROVAL_PIN),
            reference_snapshot_ids=[uuid.uuid4()],
            page_size="A4",
            orientation="landscape",
        )
    assert (
        generation_request_admission_error_code(excinfo.value)
        == "source_content_template_required"
    )


def test_raster_reconstruction_non_raster_style_is_422_not_500() -> None:
    request = _raster_generation_request_v2("default")
    reference_ids = [uuid.UUID(value) for value in request.reference_snapshot_ids]
    with pytest.raises(PublicV2OrchestrationError) as excinfo:
        resolve_internal_generation_request(
            request,
            slide_count=5,
            reference_ids=reference_ids,
            internal_generation_mode="free-design",
            internal_design_pack_pin=None,
            effective_generation_engine="authored",
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.reason_code == "source_content_template_required"


def test_raster_reconstruction_admits_the_raster_template_style() -> None:
    request = _raster_generation_request_v2("screenshot-blue")
    reference_ids = [uuid.UUID(value) for value in request.reference_snapshot_ids]
    internal_request = resolve_internal_generation_request(
        request,
        slide_count=5,
        reference_ids=reference_ids,
        internal_generation_mode="free-design",
        internal_design_pack_pin=None,
        effective_generation_engine="authored",
    )
    assert internal_request.authored_style == "screenshot-blue"


def test_pack_plus_restyle_route_resolution_is_422_not_503() -> None:
    owner_id = uuid.uuid4()

    def resolver(**kwargs: object) -> dict[str, object]:
        return generation_route.resolve_generation_route(owner_id=owner_id, **kwargs)

    request = PresentationGenerationRequestV2(
        content="Design pack restyle",
        generationMode="registered-template",
        designPackPin={
            "designPackId": "baseline-pack",
            "version": 1,
            "digest": "sha256:" + "a" * 64,
        },
        sourceMode="source_content_restyle",
        sourceContentManifestPin={
            "manifestId": "manifest-baseline",
            "digest": "sha256:" + "a" * 64,
        },
        contentPolicy={"text": "verbatim"},
        authoredStyle="default",
    )
    with pytest.raises(PublicV2OrchestrationError) as excinfo:
        asyncio.run(
            public_v2_orchestrator._resolve_generation_route_receipt(
                request, slide_count=10, resolver=resolver,
            )
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.reason_code == "source_content_design_pack_not_supported"


def test_generic_resolver_failure_is_still_503() -> None:
    def resolver(**kwargs: object) -> dict[str, object]:
        raise RuntimeError("boom")

    request = PresentationGenerationRequestV2(
        content="Brand style deck",
        generationMode="brand-style",
        strategy="authored",
    )
    with pytest.raises(PublicV2OrchestrationError) as excinfo:
        asyncio.run(
            public_v2_orchestrator._resolve_generation_route_receipt(
                request, slide_count=10, resolver=resolver,
            )
        )
    assert excinfo.value.status_code == 503
    assert excinfo.value.reason_code == "generation_route_resolver_unavailable"


def test_generation_route_error_types_resolves_the_installed_exception() -> None:
    assert generation_route_error_types() == (generation_route.GenerationRouteError,)


def test_resolve_canvas_style_id_resolves_alias_for_a_gated_aspect_ratio() -> None:
    request = GeneratePresentationRequest(
        content="Portrait deck", authored_style="no-such-style", aspect_ratio="9:16",
    )
    assert resolve_authored_style("no-such-style").id == "default"
    assert resolve_canvas_style_id(request) == "default"


def test_resolve_canvas_style_id_keeps_the_raw_id_for_16_9() -> None:
    request = GeneratePresentationRequest(
        content="Landscape deck", authored_style="no-such-style",
    )
    assert resolve_canvas_style_id(request) == "no-such-style"


def test_v2_canvas_style_resolution_admits_the_style_it_resolves_to() -> None:
    """An unresolved alias id must not get a different canvas stage than the
    canonical style it resolves to (services.authored_canvas.canvas_stage /
    supported_aspect_ratios_for_style are keyed by the canonical id)."""

    request = GeneratePresentationRequest(
        content="Portrait deck", authored_style="no-such-style", aspect_ratio="9:16",
    )
    with pytest.raises(ValueError, match="authored_portrait_combination_not_supported"):
        validate_authored_canvas_admission(request, style_id=request.authored_style)
    validate_authored_canvas_admission(request, style_id=resolve_canvas_style_id(request))
