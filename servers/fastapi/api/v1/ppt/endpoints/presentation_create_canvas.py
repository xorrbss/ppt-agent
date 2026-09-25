"""Creation selector normalization shared with durable request admission."""
from fastapi import HTTPException, Request
from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.document_canvas import document_canvas_for_orientation
from services.document_canvas_capabilities import a4_writer_admission_enabled


async def admitted_create_canvas(
    http_request: Request | None, page_size: str | None,
    orientation: str | None, aspect_ratio: str,
) -> dict[str, object]:
    selectors = {"page_size": page_size, "orientation": orientation} if page_size is not None or orientation is not None else {}
    if http_request is not None:
        raw_body = await http_request.json()
        selectors = {key: raw_body[key] for key in ("page_size", "orientation", "aspect_ratio") if key in raw_body}
    try:
        GeneratePresentationRequest.validate_canvas_selectors(selectors)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if page_size is not None and not a4_writer_admission_enabled():
        raise HTTPException(status_code=422, detail="document_canvas_a4_not_available")
    canvas = document_canvas_for_orientation(orientation) if page_size == "A4" else canvas_metadata_for_aspect_ratio(aspect_ratio)
    return canvas.model_dump(mode="json", by_alias=True)
