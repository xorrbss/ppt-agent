from typing import Literal, Optional

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from utils.authored_styles import load_authored_styles


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
    category: Literal[
        "general",
        "business",
        "technology",
        "research",
        "editorial",
        "creative",
    ]
    tags: list[str]
    use_cases: list[str]
    preview: AuthoredStylePreview


AUTHORED_ROUTER = APIRouter(prefix="/authored", tags=["Authored"])


@AUTHORED_ROUTER.get(
    "/styles",
    response_model=list[AuthoredStyleSummary],
    response_model_exclude_none=True,
)
def get_authored_styles() -> list[dict]:
    """List selectable authored styles without exposing their generation briefs."""
    return [style.public_dict() for style in load_authored_styles()]


@AUTHORED_ROUTER.get("/styles/{style_id}/preview", response_class=FileResponse)
def get_authored_style_preview(style_id: str) -> FileResponse:
    """Serve only loader-validated preview assets from the authored asset root."""
    style = next((item for item in load_authored_styles() if item.id == style_id), None)
    if style is None or style.preview_image is None:
        raise HTTPException(status_code=404, detail="Authored style preview not found")
    return FileResponse(style.preview_image)
