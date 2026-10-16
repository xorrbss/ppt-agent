"""Builds the internal ``GeneratePresentationRequest`` bridge object.

Split out of ``public_v2_orchestrator.py`` (file-size ratchet): this is a
straight field-by-field translation from the public v2 wire contract to the
internal request model, with no orchestration state of its own. It raises
``pydantic.ValidationError`` on rejection (e.g. an authored-style/strategy
combination the internal model's validators reject); the caller maps that to
a 422 admission response.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import uuid

from pydantic import ValidationError

from models.public_v2_contracts import PresentationGenerationRequestV2
from services.public_v2_orchestration_types import PublicV2OrchestrationError
from models.generate_presentation_request import GeneratePresentationRequest
from services.public_v2_admission_errors import generation_request_admission_error_code
from services.ppt_writing_policy import WRITING_POLICY_ID


def resolve_internal_generation_request(
    request: PresentationGenerationRequestV2,
    *,
    slide_count: int,
    reference_ids: list[uuid.UUID],
    internal_generation_mode: str | None,
    internal_design_pack_pin: Mapping[str, Any] | None,
    effective_generation_engine: str,
) -> GeneratePresentationRequest:
    """Raises ``PublicV2OrchestrationError(422, <code>)`` on a rejected combination."""

    try:
        return build_internal_generation_request(
            request,
            slide_count=slide_count,
            reference_ids=reference_ids,
            internal_generation_mode=internal_generation_mode,
            internal_design_pack_pin=internal_design_pack_pin,
            effective_generation_engine=effective_generation_engine,
        )
    except ValidationError as error:
        raise PublicV2OrchestrationError(
            status_code=422,
            reason_code=generation_request_admission_error_code(error),
        ) from error


def build_internal_generation_request(
    request: PresentationGenerationRequestV2,
    *,
    slide_count: int,
    reference_ids: list[uuid.UUID],
    internal_generation_mode: str | None,
    internal_design_pack_pin: Mapping[str, Any] | None,
    effective_generation_engine: str,
) -> GeneratePresentationRequest:
    return GeneratePresentationRequest(
        content=request.content,
        writingPolicy=WRITING_POLICY_ID,
        slides_markdown=request.slides_markdown,
        instructions=request.instructions,
        n_slides=slide_count,
        language=request.language,
        template="authored",
        strategy=request.strategy,
        common_header_mode=request.common_header_mode,
        common_header_cover_exception=request.common_header_cover_exception,
        common_header_design=request.common_header_design,
        qualityProfile=(
            "release" if request.quality_policy == "release-v1" else "development"
        ),
        visionQa=request.quality_policy == "release-v1",
        reference_snapshot_ids=reference_ids,
        authored_style=request.authored_style,
        **({"svg_canvas": request.svg_canvas} if request.svg_canvas is not None
           else {"page_size": request.page_size, "orientation": request.orientation}
           if request.page_size is not None
           else {"aspect_ratio": request.aspect_ratio} if "aspect_ratio" in request.model_fields_set
           else {}),
        svg_design_reference_snapshot_id=request.svg_design_reference_snapshot_id,
        svg_design_reference_pin=(request.svg_design_reference_pin.model_dump(mode="json", by_alias=True)
                                  if request.svg_design_reference_pin is not None else None),
        masterPin=(
            request.master_pin.model_dump(mode="json", by_alias=True)
            if request.master_pin is not None
            else None
        ),
        sourceMode=request.source_mode,
        sourceContentManifestPin=(
            request.source_content_manifest_pin.model_dump(mode="json", by_alias=True)
            if request.source_content_manifest_pin is not None
            else None
        ),
        rasterApprovalPin=(
            request.raster_approval_pin.model_dump(mode="json")
            if request.raster_approval_pin is not None
            else None
        ),
        contentPolicy=(
            request.content_policy.model_dump(mode="json", by_alias=True)
            if request.content_policy is not None
            else None
        ),
        generationMode=internal_generation_mode,
        designPackPin=internal_design_pack_pin,
        generationEngine=effective_generation_engine,
    )


def resolve_canvas_style_id(request: GeneratePresentationRequest) -> str:
    """Mirror the sync endpoint's admission style-id resolution.

    ``services.authored_canvas.canvas_stage``/``supported_aspect_ratios_for_style``
    are keyed by the canonical style id (``api/v1/ppt/endpoints/presentation_generate.py``
    resolves it via ``resolve_authored_style(...).id`` for any non-16:9 aspect
    ratio). Passing the raw, unresolved style id here would let an alias or
    unknown id get a different canvas stage than the style it resolves to.
    """

    from utils.authored_styles import resolve_authored_style

    if request.aspect_ratio != "16:9":
        return resolve_authored_style(request.authored_style).id
    return request.authored_style or "default"


__all__ = [
    "build_internal_generation_request",
    "resolve_canvas_style_id",
    "resolve_internal_generation_request",
]
