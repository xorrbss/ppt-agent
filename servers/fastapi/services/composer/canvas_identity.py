"""Bind Composer publication to the canvas that was used for its final HTML."""
from typing import Any

from services.authored_canvas import AuthoredCanvasMetadata, request_canvas_metadata
from services.document_canvas import DocumentCanvasMetadata


def publication_canvas_metadata(request: Any, canvas: Any) -> dict:
    if getattr(request, "page_size", None) == "A4":
        metadata = request_canvas_metadata(request)
        physical = DocumentCanvasMetadata.model_validate(metadata)
        if physical.canvas_contract() != canvas:
            raise ValueError("document_canvas_geometry_mismatch")
        return metadata
    if getattr(request, "_document_canvas", None) is not None:
        raise ValueError("document_canvas_request_mismatch")
    # Preserve legacy/custom master's existing serialization and dimensions.
    return AuthoredCanvasMetadata(
        contract_version="authored-canvas/v1",
        aspect_ratio=getattr(request, "aspect_ratio", "16:9"),
        width_px=canvas.width_px,
        height_px=canvas.height_px,
        locale=canvas.locale,
        safe_area=canvas.safe_area,
    ).model_dump(mode="json", by_alias=True)


def bind_canvas_identity(payload: dict, metadata: dict) -> dict:
    """Include physical geometry without changing any legacy identity bytes."""
    if metadata.get("contractVersion") == "document-canvas/v1":
        return {**payload, "documentCanvas": metadata}
    return payload
