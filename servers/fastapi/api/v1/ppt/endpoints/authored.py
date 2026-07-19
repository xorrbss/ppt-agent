from typing import Literal, Optional

from pydantic import BaseModel
from fastapi import APIRouter

from utils.authored_styles import load_authored_styles


class AuthoredStylePreview(BaseModel):
    bg: str
    accent: str
    palette: Optional[list[str]] = None
    variant: Optional[str] = None


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
