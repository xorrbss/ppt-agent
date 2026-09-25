from typing import Literal, Optional

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from utils.authored_styles import load_authored_styles, resolve_authored_style
from utils.authored_style_catalogue import (
    authored_style_catalogue,
    canonical_authored_style_id,
)


class AuthoredStylePreview(BaseModel):
    bg: str
    accent: str
    palette: Optional[list[str]] = None
    variant: Optional[str] = None
    image: Optional[str] = None


class AuthoredStyleSummary(BaseModel):
    id: str
    name: str
    description: str
    # The authored-style loader already validates category against
    # AUTHORED_STYLE_CATEGORIES; a plain str avoids a second (drift-prone) source
    # that would 500 this endpoint when a category is added to the loader only.
    category: str
    tags: list[str]
    use_cases: list[str]
    preview: AuthoredStylePreview
    aliases: Optional[list[str]] = None
    supported_aspect_ratios: list[Literal["16:9", "9:16", "9:13"]] = Field(
        alias="supportedAspectRatios"
    )


AUTHORED_ROUTER = APIRouter(prefix="/authored", tags=["Authored"])


@AUTHORED_ROUTER.get("/canvas-capabilities")
def get_canvas_capabilities(
    generation_strategy: Literal["authored", "svg"] | None = None,
    authored_style: str | None = None,
    source_content_mode: bool | None = None,
    has_company_standard: bool | None = None,
) -> dict:
    from services.document_canvas_capabilities import (
        a4_canvas_capabilities,
        select_a4_route,
    )

    selectors = (
        generation_strategy,
        authored_style,
        source_content_mode,
        has_company_standard,
    )
    if all(selector is not None for selector in selectors):
        style = resolve_authored_style(authored_style)
        selection = select_a4_route(
            strategy=generation_strategy,
            source_content=bool(source_content_mode),
            has_fixed_master=bool(has_company_standard),
            exact_source=getattr(style, "reference_mode", None) == "exact_reconstruction",
        )
        a4 = a4_canvas_capabilities(selection)
    else:
        a4 = a4_canvas_capabilities()
        a4["selection"] = {
            "routeId": None,
            "enabled": False,
            "reason": "document_canvas_selection_required",
        }

    from services.svg_generation_admission import source_svg_capability

    source_svg = source_svg_capability(
        strategy=generation_strategy,
        source_content=source_content_mode,
        has_company_standard=has_company_standard,
        style_id=authored_style,
        reference_mode=(
            getattr(resolve_authored_style(authored_style), "reference_mode", None)
            if authored_style else None
        ),
    )
    return {
        "contractVersions": ["authored-canvas/v1", "document-canvas/v1"],
        "a4": a4,
        "sourceContentSvg": source_svg,
    }


@AUTHORED_ROUTER.get(
    "/styles",
    response_model=list[AuthoredStyleSummary],
    response_model_exclude_none=True,
)
def get_authored_styles() -> list[dict]:
    """List selectable authored styles without exposing their generation briefs."""
    return authored_style_catalogue(load_authored_styles())


@AUTHORED_ROUTER.get("/styles/{style_id}/preview", response_class=FileResponse)
def get_authored_style_preview(style_id: str) -> FileResponse:
    """Serve only loader-validated preview assets from the authored asset root."""
    by_id = {item.id: item for item in load_authored_styles()}
    style = by_id.get(canonical_authored_style_id(style_id, by_id))
    if style is None or style.preview_image is None:
        raise HTTPException(status_code=404, detail="Authored style preview not found")
    return FileResponse(style.preview_image)
