"""Creation selector normalization shared with durable request admission."""

from fastapi import HTTPException, Request
from pydantic import ValidationError
from models.svg_canvas import SvgCanvasInput
from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.document_canvas import document_canvas_for_orientation
from services.document_canvas_capabilities import a4_writer_admission_enabled


async def admitted_create_canvas(
    http_request: Request | None,
    page_size: str | None,
    orientation: str | None,
    aspect_ratio: str,
    *,
    strategy: str = "authored",
    svg_canvas: SvgCanvasInput | None = None,
    svg_template_pinned: bool = False,
) -> dict[str, object]:
    selectors = (
        {"page_size": page_size, "orientation": orientation} if page_size is not None or orientation is not None else {}
    )
    if http_request is not None:
        raw_body = await http_request.json()
        selectors = {key: raw_body[key] for key in ("page_size", "orientation", "aspect_ratio", "svg_canvas", "svgCanvas") if key in raw_body}
    elif svg_canvas is not None:
        selectors = {"svg_canvas": svg_canvas.model_dump(mode="json")}
    try:
        GeneratePresentationRequest.validate_canvas_selectors(selectors)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if strategy == "svg":
        from services.svg_canvas import selected_svg_canvas

        try:
            request = GeneratePresentationRequest.model_validate({
                "content": "canvas", "strategy": "svg", **selectors,
                **({"aspect_ratio": aspect_ratio} if not selectors else {}),
            })
            if svg_template_pinned:
                # A template pin freezes the template's document canvas
                # (request_canvas_contract's pinned branch).
                return selected_svg_canvas(request).model_dump(mode="json", by_alias=True)
            # Otherwise store exactly what the later generation request for this
            # deck resolves (a plain 16:9 svg deck keeps the authored canvas
            # metadata), or generation refuses it with authored_canvas_request_mismatch.
            from services.authored_canvas import request_canvas_metadata

            return request_canvas_metadata(request)
        except (ValueError, ValidationError) as error:
            raise HTTPException(status_code=422, detail="svg_canvas_invalid") from error
    if svg_canvas is not None or aspect_ratio == "4:3":
        raise HTTPException(status_code=422, detail="svg_canvas_strategy_required")
    if page_size is not None and not a4_writer_admission_enabled():
        raise HTTPException(status_code=422, detail="document_canvas_a4_not_available")
    canvas = (
        document_canvas_for_orientation(orientation)
        if page_size == "A4"
        else canvas_metadata_for_aspect_ratio(aspect_ratio)
    )
    return canvas.model_dump(mode="json", by_alias=True)
